"""Track: SAM 3.1 over raw SSE, one request per concept, boxes pulled out with a regex, masks kept opaque.

Measured on 26/10/2026: the OpenAI SDK stream dropped mid-way twice, curl and httpx did not; the official parser
spent 5 min on 900 frames of masks, a regex over the text gets the boxes in 0.07 s. Ids stay stable while an object
is in frame and do not reset on cuts. The proxy is resampled to 5 fps before upload (TRACK_RESAMPLE_FPS, 900 frames
for 3 min, $0.18 per concept), boxes are interpolated later by the reframe step.
"""
from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..config import SAM_MODEL, TRACK_RESAMPLE_FPS, sam_cost
from ..ledger import Entry, Ledger, Timer
from .api import dump_json, stream_responses, upload_file
from .ingest import Probe, ffprobe

FRAME_RE = re.compile(r"^<(\d+)f>")
RECORD_RE = re.compile(
    r"(\d+)<\|box;x1=(-?\d+);y1=(-?\d+);x2=(-?\d+);y2=(-?\d+);w=(\d+);h=(\d+)(?:;c=([0-9.]+))?\|>(?:<\|mask;(.*?)\|>)?")


@dataclass
class TrackSource:
    """The clip SAM actually sees: the proxy itself or a resampled copy, uploaded once for all concepts."""
    path: Path
    probe: Probe
    file_id: str
    fps: float
    resampled: bool
    upload_seconds: float


@dataclass
class ConceptTrack:
    concept: str
    tracks_path: Path
    masks_path: Path
    objects: dict[str, list[dict]] = field(default_factory=dict)
    frames_processed: int = 0
    frames_with_records: int = 0
    status: str = "ok"

    def summary(self) -> dict:
        objs = {}
        for oid, recs in self.objects.items():
            idx = [r["f"] for r in recs]
            objs[oid] = {"first_frame": idx[0], "last_frame": idx[-1], "visible": len(set(idx)),
                         "coverage": round(len(set(idx)) / (idx[-1] - idx[0] + 1), 3),
                         "t_first": recs[0]["t"], "t_last": recs[-1]["t"],
                         "box_mean_w": round(sum(r["box"][2] - r["box"][0] for r in recs) / len(recs), 1),
                         "box_mean_h": round(sum(r["box"][3] - r["box"][1] for r in recs) / len(recs), 1)}
        return {"concept": self.concept, "status": self.status, "frames_processed": self.frames_processed,
                "frames_with_records": self.frames_with_records, "objects": objs, "tracks": str(self.tracks_path)}


def load_concept(tracks_path: Path) -> ConceptTrack:
    data = json.loads(tracks_path.read_text())
    ct = ConceptTrack(concept=data["concept"], tracks_path=tracks_path, masks_path=tracks_path.with_name("masks.jsonl"),
                      objects=data["objects"], frames_processed=data["frames_processed"],
                      frames_with_records=data["frames_with_records"], status=data["status"])
    return ct


def load_all(out_dir: Path) -> dict[str, ConceptTrack]:
    return {ct.concept: ct for ct in (load_concept(p) for p in sorted(out_dir.glob("*/tracks.json")))}


def reuse_source(ct: ConceptTrack, proxy: Path) -> TrackSource:
    """The clip and upload a finished track ran on (tracks.json keeps the file id), so one more concept lands on the
    same frames without a second resample or upload."""
    data = json.loads(ct.tracks_path.read_text())
    fps, resampled = float(data["track_fps"]), bool((data.get("source") or {}).get("resampled"))
    path = ct.tracks_path.parent.parent / f"track_source_{fps:g}fps.mp4" if resampled else proxy
    return TrackSource(path=path, probe=ffprobe(path), file_id=data["file_id"], fps=fps, resampled=resampled,
                       upload_seconds=0.0)


def track_fps(source_fps: float, asked: float | None = None) -> float:
    """The rate SAM sees: --track-fps when given, else TRACK_RESAMPLE_FPS, never above the source's own rate."""
    return min(source_fps, asked or TRACK_RESAMPLE_FPS)


def prepare_source(proxy: Path, proxy_probe: Probe, proxy_file_id: str, out_dir: Path, ledger: Ledger,
                   fps: float | None = None, log=print) -> TrackSource:
    """Resample to `fps` (default TRACK_RESAMPLE_FPS) and upload; a proxy already at that rate is reused."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = track_fps(proxy_probe.fps, fps)
    if abs(fps - proxy_probe.fps) < 0.01:
        return TrackSource(path=proxy, probe=proxy_probe, file_id=proxy_file_id, fps=proxy_probe.fps, resampled=False, upload_seconds=0.0)
    dst = out_dir / f"track_source_{fps:g}fps.mp4"
    with Timer() as t:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(proxy), "-an", "-vf", f"fps={fps}",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(dst)], check=True)
        probe = ffprobe(dst)
        up = upload_file(dst, "video/mp4")
    ledger.add(Entry(stage="track.prepare", model="ffmpeg+files", seconds=t.seconds, cost_usd=0.0,
                     note=f"resampled to {fps:g} fps ({dst.stat().st_size / 1e6:.1f} MB), file_id {up['id']}"))
    log(f"track: resampled proxy to {fps:g} fps and uploaded as {up['id']} in {t.seconds}s")
    return TrackSource(path=dst, probe=probe, file_id=up["id"], fps=probe.fps, resampled=True, upload_seconds=up["upload_seconds"])


def parse_line(line: str) -> tuple[int, list[tuple[str, tuple[int, int, int, int], int, int, float | None, str | None]]] | None:
    m = FRAME_RE.match(line)
    if not m:
        return None
    frame = int(m.group(1))
    recs = []
    for r in RECORD_RE.finditer(line, m.end()):
        oid = r.group(1)
        x1, y1, x2, y2, w, h = (int(r.group(k)) for k in range(2, 8))
        conf = float(r.group(8)) if r.group(8) else None
        recs.append((oid, (x1, y1, x2, y2), w, h, conf, r.group(9)))
    return frame, recs


def track_concept(source: TrackSource, concept: str, out_dir: Path, ledger: Ledger, log=print) -> ConceptTrack:
    cdir = out_dir / re.sub(r"[^a-z0-9]+", "_", concept.lower()).strip("_")
    cdir.mkdir(parents=True, exist_ok=True)
    body = {"model": SAM_MODEL, "metadata": {"mask_encoding": "one_bit"},
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept}, {"type": "input_video", "file_id": source.file_id}]}]}
    dump_json(cdir / "request.json", body)
    result = ConceptTrack(concept=concept, tracks_path=cdir / "tracks.json", masks_path=cdir / "masks.jsonl")

    usage: dict = {}
    status = "ok"
    final_status = None
    frames_seen: set[int] = set()
    with Timer() as t, (cdir / "masks.jsonl").open("w") as masks:
        buf = ""
        lines = 0

        def handle(line: str) -> None:
            nonlocal lines
            parsed = parse_line(line)
            if parsed is None:
                return
            frame, recs = parsed
            lines += 1
            if recs:
                frames_seen.add(frame)
            for oid, box, w, h, conf, mask in recs:
                rec = {"f": frame, "t": round(frame / source.fps, 3), "box": list(box)}
                if conf is not None:
                    rec["c"] = conf
                result.objects.setdefault(oid, []).append(rec)
                if mask:
                    masks.write(json.dumps({"f": frame, "id": oid, "w": w, "h": h, "mask": mask}) + "\n")

        try:
            for ev in stream_responses(body, raw_sink=cdir / "events.sse"):
                et = ev.get("type")
                if et == "response.output_text.delta":
                    buf += ev.get("delta") or ""
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        handle(line)
                elif et == "response.completed":
                    resp = ev.get("response") or {}
                    usage = resp.get("usage") or {}
                    final_status = resp.get("status")
                elif et in ("response.failed", "response.incomplete", "error"):
                    status = et
            if buf.strip():
                handle(buf)
        except Exception as exc:  # keep what we have, the ledger says it was partial
            status = f"stream_error: {type(exc).__name__}: {str(exc)[:200]}"
            log(f"track: {concept}: {status}")

    for recs in result.objects.values():
        recs.sort(key=lambda r: r["f"])
    frames_processed = int(usage.get("video_frames_processed") or 0) or int(round(source.probe.duration * source.fps))
    result.frames_processed = frames_processed
    result.frames_with_records = len(frames_seen)
    result.status = status if status != "ok" else (final_status or "completed")
    cost = sam_cost(frames_processed)
    dump_json(result.tracks_path, {
        "model": SAM_MODEL, "concept": concept, "file_id": source.file_id, "track_fps": source.fps,
        "source": {"width": source.probe.width, "height": source.probe.height, "duration": source.probe.duration, "resampled": source.resampled},
        "frames_processed": frames_processed, "frames_with_records": len(frames_seen), "status": result.status,
        "usage": usage, "cost_usd": round(cost, 4), "seconds": t.seconds,
        "summary": result.summary()["objects"], "objects": result.objects})
    ledger.add(Entry(stage="track", model=SAM_MODEL, seconds=t.seconds, cost_usd=round(cost, 4), frames=frames_processed,
                     status=result.status, note=f"{concept}: {len(result.objects)} ids over {len(frames_seen)} frames"))
    log(f"track: {concept}: {len(result.objects)} ids, {len(frames_seen)}/{frames_processed} frames, ${cost:.3f}, {t.seconds}s, {result.status}")
    return result


def track_all(source: TrackSource, concepts: list[str], out_dir: Path, ledger: Ledger, log=print) -> dict[str, ConceptTrack]:
    """One request per concept, run in parallel."""
    with ThreadPoolExecutor(max_workers=max(1, len(concepts))) as pool:
        results = list(pool.map(lambda c: track_concept(source, c, out_dir, ledger, log), concepts))
    return {r.concept: r for r in results}
