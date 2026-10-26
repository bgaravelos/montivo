#!/usr/bin/env python3
"""Kill test (b): a 30 s clip into SAM 3.1 with the concept "person".

    .venv/bin/python kt_b_sam_person.py --video clip.mp4 --seconds 30 --concept person

Reports: frames accepted, wall time, cost, mask format, track stability (ids, gaps, box jitter).
Preparation mirrors the cookbook: longest edge 960 px, source fps kept, H.264 mp4.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import time
from collections import defaultdict
from contextlib import aclosing
from pathlib import Path

from meta_sam_parser import parse_responses_stream, video_segmentation_format
from openai import AsyncOpenAI

from common import BASE_URL, PRICE, api_key, ffprobe, save


def prepare(src: Path, dst: Path, seconds: float, start: float, max_dim: int, fps: float | None) -> dict:
    p = ffprobe(src)
    scale = min(1.0, max_dim / max(p["width"], p["height"]))
    w = max(2, round(p["width"] * scale / 2) * 2)
    h = max(2, round(p["height"] * scale / 2) * 2)
    vf = [f"scale={w}:{h}:flags=lanczos"]
    if fps:
        vf.insert(0, f"fps={fps}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(seconds), "-i", str(src),
                    "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium",
                    "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    str(dst)], check=True)
    return ffprobe(dst)


async def stream_events(stream):
    """Same lane fix as the cookbook: the API sends content_part.done, the parser wants output_text.done."""
    done = set()
    async for event in stream:
        data = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        lane = (data.get("item_id"), data.get("output_index"), data.get("content_index"))
        if data.get("type") == "response.output_text.done":
            if lane not in done:
                done.add(lane)
                yield data
            continue
        yield data
        part = data.get("part")
        if data.get("type") == "response.content_part.done" and lane not in done and isinstance(part, dict) \
                and part.get("type") == "output_text" and isinstance(part.get("text"), str):
            done.add(lane)
            yield {"type": "response.output_text.done", "item_id": data.get("item_id"),
                   "output_index": data.get("output_index"), "content_index": data.get("content_index"),
                   "text": part["text"]}


async def run(prepared: Path, concept: str, encoding: str, confidence: bool) -> dict:
    timings = {}
    async with AsyncOpenAI(base_url=BASE_URL, api_key=api_key(), timeout=1800) as api:
        t0 = time.perf_counter()
        up = await api.files.create(file=(prepared.name, prepared.read_bytes(), "video/mp4"), purpose="user_data")
        timings["upload_s"] = round(time.perf_counter() - t0, 1)
        meta = {"mask_encoding": encoding}
        if confidence:
            meta["include_confidence"] = "true"
        t1 = time.perf_counter()
        first_frame_s = None
        stream = await api.responses.create(
            model="sam-3.1", stream=True, metadata=meta,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept}, {"type": "input_video", "file_id": up.id}]}])
        async with stream, aclosing(stream_events(stream)) as events:
            parsed = parse_responses_stream(events, video_segmentation_format())
            async with parsed:
                async for snap in parsed:
                    if first_frame_s is None and snap.records:
                        first_frame_s = round(time.perf_counter() - t1, 1)
                result = await parsed.final_result()
        timings["sam_total_s"] = round(time.perf_counter() - t1, 1)
        timings["first_frame_s"] = first_frame_s
    return {"file_id": up.id, "timings": timings, "result": result}


def analyse(result, fps: float) -> dict:
    boxes = [r for r in result.records if r.kind == "box" and r.frame is not None]
    masks = [r for r in result.records if r.kind == "mask" and r.frame is not None]
    frames_with_records = sorted({r.frame.frame_index for r in boxes})
    tracks: dict[str, list] = defaultdict(list)
    for r in boxes:
        tracks[r.object_id].append(r)
    track_stats = []
    for oid, recs in sorted(tracks.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else kv[0]):
        recs.sort(key=lambda r: r.frame.frame_index)
        idx = [r.frame.frame_index for r in recs]
        gaps = sum(1 for a, b in zip(idx, idx[1:]) if b - a > 1)
        cx = [(r.left + r.right) / 2 for r in recs]
        cy = [(r.top + r.bottom) / 2 for r in recs]
        jitter = [abs(b - a) for a, b in zip(cx, cx[1:])]
        track_stats.append({
            "object_id": oid, "first_frame": idx[0], "last_frame": idx[-1], "visible_frames": len(set(idx)),
            "coverage": round(len(set(idx)) / (idx[-1] - idx[0] + 1), 3), "gaps": gaps,
            "centre_x_median": round(statistics.median(cx), 1), "centre_y_median": round(statistics.median(cy), 1),
            "centre_x_jitter_px_mean": round(statistics.mean(jitter), 2) if jitter else 0,
            "centre_x_jitter_px_p95": round(sorted(jitter)[int(len(jitter) * 0.95)], 2) if len(jitter) > 1 else 0,
            "mean_box_w": round(statistics.mean(r.right - r.left for r in recs), 1),
            "mean_box_h": round(statistics.mean(r.bottom - r.top for r in recs), 1),
        })
    sample_mask = masks[0].mask if masks else None
    return {
        "outcome": result.outcome.status,
        "media": getattr(result, "media", None) and json.loads(json.dumps(result.media, default=lambda o: getattr(o, "__dict__", str(o)))),
        "records": len(result.records), "box_records": len(boxes), "mask_records": len(masks),
        "frames_with_records": len(frames_with_records),
        "first_frame_index": frames_with_records[0] if frames_with_records else None,
        "last_frame_index": frames_with_records[-1] if frames_with_records else None,
        "tracks": track_stats,
        "mask_sample": None if sample_mask is None else {
            "encoding": sample_mask.encoding, "width": sample_mask.width, "height": sample_mask.height,
            "x": getattr(sample_mask, "x", None), "y": getattr(sample_mask, "y", None),
        },
        "diagnostics": [{"severity": d.severity, "code": d.code, "line": d.line, "message": d.message} for d in result.diagnostics][:20],
        "raw_output_chars": len(result.raw_output or ""),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--concept", default="person")
    ap.add_argument("--max-dim", type=int, default=960)
    ap.add_argument("--fps", type=float, default=None, help="resample fps before upload (default keep source)")
    ap.add_argument("--encoding", default="one_bit", choices=["one_bit", "lossless"])
    ap.add_argument("--confidence", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    work = Path(__file__).resolve().parent / "results" / "kt_b_work"
    work.mkdir(parents=True, exist_ok=True)
    prepared = work / f"prepared_{int(args.seconds)}s_{args.max_dim}px{'_' + str(args.fps) + 'fps' if args.fps else ''}.mp4"
    p = prepare(args.video, prepared, args.seconds, args.start, args.max_dim, args.fps)
    print(f"prepared: {p['duration']:.1f}s {p['width']}x{p['height']} {p['fps']:.2f}fps {p['frames']} frames {p['bytes']/1e6:.1f} MB")

    out = asyncio.run(run(prepared, args.concept, args.encoding, args.confidence))
    result = out["result"]
    (work / f"raw_{args.concept}{'_' + args.tag if args.tag else ''}.txt").write_text(result.raw_output or "")
    a = analyse(result, p["fps"])
    billed = max(50, p["frames"])
    summary = {
        "concept": args.concept, "encoding": args.encoding, "file_id": out["file_id"], "prepared": p,
        "timings": out["timings"], "seconds_per_frame": round(out["timings"]["sam_total_s"] / max(1, p["frames"]), 3),
        "cost_usd": round(billed * PRICE["sam-video-frame"], 4), "analysis": a,
    }
    save(f"kt_b_sam_{args.concept}{'_' + args.tag if args.tag else ''}", summary)
    print(json.dumps({k: summary[k] for k in ("timings", "seconds_per_frame", "cost_usd")}, indent=2))
    print(json.dumps({k: a[k] for k in ("outcome", "records", "frames_with_records", "mask_sample")}, indent=2))
    for t in a["tracks"]:
        print(f"  track {t['object_id']}: frames {t['first_frame']}-{t['last_frame']} coverage {t['coverage']} gaps {t['gaps']} jitter mean {t['centre_x_jitter_px_mean']}px p95 {t['centre_x_jitter_px_p95']}px box {t['mean_box_w']}x{t['mean_box_h']}")


if __name__ == "__main__":
    main()
