"""Render: EDL spans -> 9:16 segments from the SOURCE file -> concat -> cut_v1.mp4.

Spans flagged `contiguous_with_previous` (or adjacent in time inside one turn) are merged into one segment first,
so no cut lands inside continuous speech (Voice word edges inside a turn carry up to 0.3 s of slop). Each segment
is snapped to whole source frames: t_in to the frame grid, duration to a frame count, so video and audio of a
segment are exactly as long as each other and no drift builds up across cuts.

Video: per segment, accurate input seek on the source, crop x driven per frame by a sendcmd file from the reframe
path, full-height crop scaled to 1080x1920, libx264 crf 18, no audio; then the concat demuxer joins the segments
with stream copy. Audio: one pass over the source with atrim per segment, a 20 ms fade in and out against clicks,
the concat filter, AAC 192k 48 kHz. The two are muxed with stream copy. Captions, title card and loudnorm come later.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REFRAME_CONCEPT
from ..ledger import Entry, Ledger, Timer
from .api import dump_json
from .reframe import ReframeParams, SegmentPath, plan_segment

OUT_W, OUT_H = 1080, 1920
AUDIO_RATE = 48000
AUDIO_FADE_S = 0.02
CONTIGUOUS_GAP_S = 0.05


@dataclass
class Segment:
    index: int
    t_in: float
    t_out: float
    span_ids: list[str]
    roles: list[str] = field(default_factory=list)
    frames: int = 0  # whole source frames, set by frame_align
    fps: float = 0.0

    @property
    def duration(self) -> float:
        return round(self.frames / self.fps if self.frames else self.t_out - self.t_in, 4)


def merge_segments(spans: list[dict]) -> list[Segment]:
    """Consecutive spans that continue the previous one (flagged contiguous_with_previous, or the same turn with
    no gap between them) join the previous segment; the order of spans is kept."""
    segments: list[Segment] = []
    prev: dict | None = None
    for s in spans:
        joins = bool(prev) and abs(prev["t_out"] - s["t_in"]) < CONTIGUOUS_GAP_S and (
            s.get("contiguous_with_previous") or s.get("turn_id") == prev.get("turn_id"))
        if joins:
            seg = segments[-1]
            seg.t_out = s["t_out"]
            seg.span_ids.append(s["id"])
            seg.roles.append(s["role"])
        else:
            segments.append(Segment(index=len(segments), t_in=s["t_in"], t_out=s["t_out"], span_ids=[s["id"]], roles=[s["role"]]))
        prev = s
    return segments


def frame_align(segments: list[Segment], fps: float) -> list[Segment]:
    """Snap each segment's in point to the source frame grid and its length to a whole number of frames."""
    for seg in segments:
        f_in = math.floor(seg.t_in * fps + 1e-6)
        n = max(1, int(round((seg.t_out - f_in / fps) * fps)))
        seg.t_in = round(f_in / fps, 6)
        seg.t_out = round((f_in + n) / fps, 6)
        seg.frames = n
        seg.fps = fps
    return segments


def load_track(run_dir: Path, edl: dict, concept: str) -> dict | None:
    """tracks.json for one concept: the path in the EDL when it resolves, else run_dir/track/<concept>/."""
    cands: list[Path] = []
    rec = (edl.get("tracks") or {}).get(concept) or {}
    if rec.get("tracks"):
        cands.append(Path(rec["tracks"]))
    cands.append(run_dir / "track" / concept / "tracks.json")
    for p in cands:
        if p.is_file():
            return json.loads(p.read_text())
    return None


def load_reframe_track(run_dir: Path, edl: dict) -> tuple[str | None, dict | None]:
    """The track the crop follows: the EDL's reframe concept (default "head"), then "person", else a centre crop."""
    order = [edl.get("reframe_concept") or REFRAME_CONCEPT]
    if "person" not in order:
        order.append("person")
    for concept in order:
        track = load_track(run_dir, edl, concept)
        if track and track.get("objects"):
            return concept, track
    return None, None


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr[-2000:]}")


def render_segment_video(source: Path, seg: Segment, path: SegmentPath, render_dir: Path) -> Path:
    cmds = render_dir / f"seg_{seg.index + 1:02d}_crop.cmd"
    cmds.write_text(path.sendcmd_text())
    out = render_dir / f"seg_{seg.index + 1:02d}.mp4"
    # seek half a frame early so the frame on the grid is never lost to rounding, then rebase pts to 0
    seek = max(0.0, seg.t_in - 0.5 / seg.fps)
    vf = (f"setpts=PTS-STARTPTS,sendcmd=f={cmds.as_posix()},crop={path.crop_w}:{path.crop_h}:{path.xs[0]}:0,"
          f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,format=yuv420p")
    _ffmpeg(["-ss", f"{seek:.6f}", "-t", f"{(seg.frames + 2) / seg.fps:.6f}", "-i", str(source), "-map", "0:v:0", "-an",
             "-vf", vf, "-frames:v", str(seg.frames), "-r", f"{seg.fps:g}", "-fps_mode", "cfr",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-video_track_timescale", "90000", "-map_metadata", "-1", "-movflags", "+faststart", str(out)])
    return out


def render_audio(source: Path, segments: list[Segment], render_dir: Path) -> Path:
    """All segments' audio in one pass: atrim per segment, 20 ms fades, concat, AAC."""
    out = render_dir / "audio.m4a"
    parts = []
    for i, seg in enumerate(segments):
        d = seg.duration
        parts.append(f"[0:a]aresample={AUDIO_RATE},atrim=start={seg.t_in:.6f}:duration={d:.6f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d={AUDIO_FADE_S},afade=t=out:st={max(0.0, d - AUDIO_FADE_S):.6f}:d={AUDIO_FADE_S}[a{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[a]"
    _ffmpeg(["-i", str(source), "-filter_complex", graph, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE),
             "-ac", "2", "-map_metadata", "-1", str(out)])
    return out


def mux(segment_files: list[Path], audio: Path, out: Path, render_dir: Path) -> Path:
    lst = render_dir / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segment_files))
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
             "-c", "copy", "-movflags", "+faststart", str(out)])
    return out


def render(run_dir: Path, edl: dict, source: Path, ledger: Ledger, out_name: str = "cut_v1.mp4",
           params: ReframeParams | None = None, log=print, edl_path: Path | None = None) -> dict:
    """Render the EDL to run_dir/<out_name>; writes segments, cmd files and the audio under run_dir/render/ and
    fills the EDL's `reframe` keyframes and a `render` block (written back to edl_path when given)."""
    if not source.is_file():
        raise FileNotFoundError(f"source footage not found: {source}")
    if not edl.get("spans"):
        raise RuntimeError("EDL has no spans to render")
    render_dir = run_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    fps = float(edl["source"]["fps"])
    concept, track = load_reframe_track(run_dir, edl)
    segments = frame_align(merge_segments(edl["spans"]), fps)
    log(f"render: {len(edl['spans'])} spans -> {len(segments)} segments, source {source.name}, "
        f"track {concept or 'none (centre crop)'}")

    keyframes: list[dict] = []
    seg_info: list[dict] = []
    files: list[Path] = []
    with Timer() as t:
        for seg in segments:
            path = plan_segment(seg.t_in, seg.t_out, edl["source"], track, params, span_ids=seg.span_ids)
            path.xs = path.xs[:seg.frames] + [path.xs[-1]] * max(0, seg.frames - len(path.xs))
            f = render_segment_video(source, seg, path, render_dir)
            files.append(f)
            keyframes.extend(path.keyframes)
            moving = sum(1 for a, b in zip(path.xs, path.xs[1:]) if a != b)
            seg_info.append({"index": seg.index, "t_in": seg.t_in, "t_out": seg.t_out, "duration": seg.duration, "frames": seg.frames,
                             "spans": seg.span_ids, "track_concept": concept, "track_id": path.track_id, "crop": [path.crop_w, path.crop_h],
                             "x_first": path.xs[0], "x_min": min(path.xs), "x_max": max(path.xs),
                             "frames_moving": moving, "file": f.as_posix()})
            log(f"render: seg {seg.index + 1} {seg.t_in:.3f}-{seg.t_out:.3f}s ({seg.frames} frames, {'+'.join(seg.span_ids)}) "
                f"track {path.track_id} crop {path.crop_w}x{path.crop_h} x {min(path.xs)}..{max(path.xs)}")
        audio = render_audio(source, segments, render_dir)
        out = mux(files, audio, run_dir / out_name, render_dir)
    size_mb = out.stat().st_size / 1e6
    total = round(sum(s.duration for s in segments), 3)
    edl["reframe"] = keyframes
    edl["render"] = {"output": out.as_posix(), "width": OUT_W, "height": OUT_H, "duration_s": total, "segments": seg_info,
                     "audio": audio.as_posix(), "seconds": t.seconds, "bytes": out.stat().st_size}
    if edl_path:
        dump_json(edl_path, edl)
    ledger.add(Entry(stage="render", model="ffmpeg", seconds=t.seconds, cost_usd=0.0,
                     note=f"{len(segments)} segments, {total} s, {out.name} {size_mb:.1f} MB"))
    log(f"render: {out} ({total} s, {size_mb:.1f} MB) in {t.seconds}s")
    return edl["render"]
