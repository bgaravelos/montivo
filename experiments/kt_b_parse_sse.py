#!/usr/bin/env python3
"""Parse a SAM events.sse file (curl transport) with meta_sam_parser and report track stability."""
import argparse, asyncio, json, statistics
from collections import defaultdict
from pathlib import Path
from meta_sam_parser import parse_responses_stream, video_segmentation_format, decode_mask_to_raster
from common import PRICE, ffprobe, save

ap = argparse.ArgumentParser()
ap.add_argument("--sse", type=Path, required=True)
ap.add_argument("--video", type=Path, required=True, help="the prepared clip that was uploaded")
ap.add_argument("--tag", default="curl")
ap.add_argument("--sam-seconds", type=float, default=None)
args = ap.parse_args()

raw = args.sse.read_text().replace("\r\n", "\n")
events = []
for block in raw.split("\n\n"):
    payload = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
    if payload and payload != "[DONE]":
        events.append(json.loads(payload))

async def gen():
    done = set()
    for e in events:
        lane = (e.get("item_id"), e.get("output_index"), e.get("content_index"))
        if e.get("type") == "response.output_text.done":
            if lane in done:
                continue
            done.add(lane)
        yield e
        part = e.get("part")
        if e.get("type") == "response.content_part.done" and isinstance(part, dict) and part.get("type") == "output_text" and lane not in done:
            done.add(lane)
            yield {"type": "response.output_text.done", "item_id": e.get("item_id"), "output_index": e.get("output_index"), "content_index": e.get("content_index"), "text": part["text"]}

async def main():
    parsed = parse_responses_stream(gen(), video_segmentation_format())
    async with parsed:
        async for _ in parsed:
            pass
        return await parsed.final_result()

result = asyncio.run(main())
p = ffprobe(args.video)
usage = next((e.get("response", {}).get("usage") for e in events if e.get("type") == "response.completed"), None)
boxes = [r for r in result.records if r.kind == "box" and r.frame is not None]
masks = [r for r in result.records if r.kind == "mask" and r.frame is not None]
frames = sorted({r.frame.frame_index for r in boxes})
tracks = defaultdict(list)
for r in boxes:
    tracks[r.object_id].append(r)
stats = []
for oid, recs in tracks.items():
    recs.sort(key=lambda r: r.frame.frame_index)
    idx = [r.frame.frame_index for r in recs]
    cx = [(r.left + r.right) / 2 for r in recs]
    cy = [(r.top + r.bottom) / 2 for r in recs]
    jit = [abs(b - a) for a, b in zip(cx, cx[1:])]
    stats.append({"object_id": oid, "first_frame": idx[0], "last_frame": idx[-1], "visible_frames": len(set(idx)),
                  "coverage": round(len(set(idx)) / (idx[-1] - idx[0] + 1), 3), "gaps": sum(1 for a, b in zip(idx, idx[1:]) if b - a > 1),
                  "cx_median": round(statistics.median(cx), 1), "cy_median": round(statistics.median(cy), 1),
                  "cx_jitter_mean_px": round(statistics.mean(jit), 2) if jit else 0, "cx_jitter_p95_px": round(sorted(jit)[int(len(jit) * 0.95)], 2) if len(jit) > 1 else 0,
                  "cx_jitter_max_px": round(max(jit), 1) if jit else 0,
                  "box_w_mean": round(statistics.mean(r.right - r.left for r in recs), 1), "box_h_mean": round(statistics.mean(r.bottom - r.top for r in recs), 1)})
m0 = masks[0] if masks else None
raster_info = None
if m0:
    ras = decode_mask_to_raster(m0.mask)
    raster_info = {"encoding": m0.mask.encoding, "w": m0.mask.width, "h": m0.mask.height, "raster_len": len(ras), "fg_pixels": int(sum(ras)), "bounds": None if getattr(m0, "bounds", None) is None else {k: getattr(m0.bounds, k) for k in ("left", "top", "right", "bottom") if hasattr(m0.bounds, k)}}
summary = {"sse": str(args.sse), "prepared": p, "events": len(events), "usage": usage, "outcome": result.outcome.status, "media": str(result.media),
           "records": len(result.records), "box_records": len(boxes), "mask_records": len(masks), "frames_with_records": len(frames),
           "first_frame": frames[0] if frames else None, "last_frame": frames[-1] if frames else None, "tracks": stats, "mask_sample": raster_info,
           "cost_usd": round(max(50, p["frames"]) * PRICE["sam-video-frame"], 4),
           "diagnostics": [{"severity": d.severity, "code": d.code, "line": d.line, "message": d.message} for d in result.diagnostics][:10]}
save(f"kt_b_sam_person_{args.tag}", summary)
print(json.dumps({k: summary[k] for k in ("events", "usage", "outcome", "records", "frames_with_records", "first_frame", "last_frame", "mask_sample", "cost_usd")}, indent=1, default=str))
for t in stats:
    print(f"track {t['object_id']}: frames {t['first_frame']}-{t['last_frame']} visible {t['visible_frames']} coverage {t['coverage']} gaps {t['gaps']} centre ({t['cx_median']},{t['cy_median']}) jitter mean {t['cx_jitter_mean_px']} p95 {t['cx_jitter_p95_px']} max {t['cx_jitter_max_px']} box {t['box_w_mean']}x{t['box_h_mean']}")
