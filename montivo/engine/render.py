"""Render: EDL spans -> 9:16 segments from the SOURCE file -> concat -> cut_v1.mp4.

Spans flagged `contiguous_with_previous` (or adjacent in time inside one turn) are merged into one segment first,
so no cut lands inside continuous speech (Voice word edges inside a turn carry up to 0.3 s of slop). Each segment
is snapped to whole source frames: t_in to the frame grid, duration to a frame count, so video and audio of a
segment are exactly as long as each other and no drift builds up across cuts.

Video: per segment, accurate input seek on the source, crop x driven per frame by a sendcmd file from the reframe
path, full-height crop scaled to 1080x1920, libx264 crf 18, no audio; then the concat demuxer joins the segments
with stream copy. Audio: one pass over the source with atrim per segment, a 20 ms fade in and out against clicks,
the concat filter, one loudnorm pass to -14 LUFS / -1 dBTP, AAC 192k 48 kHz. The two are muxed with stream copy.

Finishing (evening 3), still one video encode per segment: caption lines and callouts are transparent PNGs
(captions.py / graphics.py) burned in with `overlay` after the crop and scale, each gated by its segment-local time;
the title card (image.py) is its own 1.5 s segment in front, with a short fade to black, and the audio gets the same
1.5 s of silence in front before loudnorm.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REFRAME_CONCEPT
from ..ledger import Entry, Ledger, Timer
from .api import dump_json
from .captions import Overlay, build_overlays
from .graphics import font_path
from .reframe import ReframeParams, SegmentPath, plan_segment

OUT_W, OUT_H = 1080, 1920
AUDIO_RATE = 48000
AUDIO_FADE_S = 0.02
CONTIGUOUS_GAP_S = 0.05
TITLE_S = 1.5
TITLE_FADE_S = 0.3
# TP is asked for at -1.5: loudnorm holds -1.0 on the PCM, the AAC encode then measured -0.8 dBTP (run 011)
LOUDNORM = {"I": -14.0, "TP": -1.5, "LRA": 11.0}
X264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        "-map_metadata", "-1", "-movflags", "+faststart"]


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


def _overlay_graph(base: str, overlays: list[Overlay], first_input: int) -> tuple[list[str], str]:
    """Input args and filter chain that burn `overlays` onto the stream labelled `base`; returns (args, graph) whose
    last output label is [v]. Times are segment-local; each overlay shows on frames with t_in <= t < t_out."""
    args: list[str] = []
    chain: list[str] = []
    cur = base
    for n, ov in enumerate(overlays):
        idx = first_input + n
        if ov.fade_s > 0:
            args += ["-loop", "1", "-framerate", "30", "-t", f"{ov.t_out + 0.1:.4f}", "-i", str(ov.png)]
            src = f"[{idx}:v]format=rgba,fade=t=in:st={ov.t_in:.4f}:d={ov.fade_s}:alpha=1," \
                  f"fade=t=out:st={max(ov.t_in, ov.t_out - ov.fade_s):.4f}:d={ov.fade_s}:alpha=1[o{n}]"
            chain.append(src)
            src_lbl = f"[o{n}]"
        else:
            args += ["-i", str(ov.png)]
            src_lbl = f"[{idx}:v]"
        nxt = f"[m{n}]"
        chain.append(f"{cur}{src_lbl}overlay=x={ov.x}:y={ov.y}:format=auto:eof_action=repeat:"
                     f"enable='gte(t,{ov.t_in:.4f})*lt(t,{ov.t_out:.4f})'{nxt}")
        cur = nxt
    chain.append(f"{cur}format=yuv420p[v]")
    return args, ";".join(chain)


def render_segment_video(source: Path, seg: Segment, path: SegmentPath, render_dir: Path,
                         overlays: list[Overlay] | None = None) -> Path:
    cmds = render_dir / f"seg_{seg.index + 1:02d}_crop.cmd"
    cmds.write_text(path.sendcmd_text())
    out = render_dir / f"seg_{seg.index + 1:02d}.mp4"
    # seek half a frame early so the frame on the grid is never lost to rounding, then rebase pts to 0
    seek = max(0.0, seg.t_in - 0.5 / seg.fps)
    vf = (f"setpts=PTS-STARTPTS,sendcmd=f={cmds.as_posix()},crop={path.crop_w}:{path.crop_h}:{path.xs[0]}:0,"
          f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1")
    ov_args, ov_graph = _overlay_graph("[base]", overlays or [], 1)
    graph = f"[0:v]{vf}[base];{ov_graph}"
    _ffmpeg(["-ss", f"{seek:.6f}", "-t", f"{(seg.frames + 2) / seg.fps:.6f}", "-i", str(source), *ov_args,
             "-filter_complex", graph, "-map", "[v]", "-an", "-frames:v", str(seg.frames), "-r", f"{seg.fps:g}",
             "-fps_mode", "cfr", *X264, str(out)])
    return out


def render_title_video(png: Path, fps: float, render_dir: Path, seconds: float = TITLE_S, fade_s: float = TITLE_FADE_S) -> tuple[Path, int]:
    """The title card as a segment of whole frames: scaled to cover 1080x1920, fade to black over the last fade_s."""
    frames = int(round(seconds * fps))
    out = render_dir / "seg_00_title.mp4"
    d = frames / fps
    vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={OUT_W}:{OUT_H},setsar=1,"
          f"fade=t=out:st={d - fade_s:.4f}:d={fade_s},format=yuv420p")
    _ffmpeg(["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{d + 1 / fps:.6f}", "-i", str(png), "-vf", vf,
             "-frames:v", str(frames), "-r", f"{fps:g}", "-fps_mode", "cfr", *X264, str(out)])
    return out, frames


_LOUD_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def render_audio(source: Path, segments: list[Segment], render_dir: Path, lead_s: float = 0.0,
                 loudnorm: bool = True) -> tuple[Path, dict | None]:
    """All segments' audio in one pass: atrim per segment, 20 ms fades, `lead_s` of silence in front (title card),
    concat, one loudnorm pass to -14 LUFS / -1 dBTP, AAC. Returns the file and loudnorm's measured values."""
    out = render_dir / "audio.m4a"
    fmt = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo"
    parts = []
    labels = []
    if lead_s > 0:
        parts.append(f"anullsrc=r={AUDIO_RATE}:cl=stereo,atrim=duration={lead_s:.6f},{fmt}[lead]")
        labels.append("[lead]")
    for i, seg in enumerate(segments):
        d = seg.duration
        parts.append(f"[0:a]aresample={AUDIO_RATE},atrim=start={seg.t_in:.6f}:duration={d:.6f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d={AUDIO_FADE_S},afade=t=out:st={max(0.0, d - AUDIO_FADE_S):.6f}:d={AUDIO_FADE_S},{fmt}[a{i}]")
        labels.append(f"[a{i}]")
    post = ""
    if loudnorm:
        post = (f",loudnorm=I={LOUDNORM['I']}:TP={LOUDNORM['TP']}:LRA={LOUDNORM['LRA']}:print_format=json,"
                f"aresample={AUDIO_RATE}")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1{post}[a]"
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "info", "-i", str(source),
                           "-filter_complex", graph, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE),
                           "-ac", "2", "-map_metadata", "-1", str(out)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio failed ({proc.returncode})\n{proc.stderr[-2000:]}")
    measured = None
    if loudnorm:
        m = _LOUD_JSON.findall(proc.stderr)
        if m:
            raw = json.loads(m[-1])
            measured = {"target_i": LOUDNORM["I"], "target_tp": LOUDNORM["TP"], "target_lra": LOUDNORM["LRA"],
                        **{k: (float(v) if _num(v) else v) for k, v in raw.items()}}
    return out, measured


def _num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


_EBU = re.compile(r"Integrated loudness:\s+I:\s+(-?[0-9.]+) LUFS.*?Loudness range:\s+LRA:\s+(-?[0-9.]+) LU.*?"
                  r"True peak:\s+Peak:\s+(-?[0-9.]+|-inf) dBFS", re.S)


def measure_loudness(media: Path) -> dict | None:
    """Integrated loudness, LRA and true peak of a file's audio with ffmpeg's ebur128 scanner (peak=true)."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(media), "-map", "0:a:0", "-af",
                           "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    m = _EBU.search(proc.stderr[proc.stderr.rfind("Summary:"):])
    if not m:
        return None
    return {"integrated_lufs": float(m.group(1)), "lra_lu": float(m.group(2)),
            "true_peak_dbtp": float(m.group(3)) if m.group(3) != "-inf" else None}


def mux(segment_files: list[Path], audio: Path, out: Path, render_dir: Path) -> Path:
    lst = render_dir / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segment_files))
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
             "-c", "copy", "-movflags", "+faststart", str(out)])
    return out


def render(run_dir: Path, edl: dict, source: Path, ledger: Ledger, out_name: str = "cut_v1.mp4",
           params: ReframeParams | None = None, log=print, edl_path: Path | None = None, words: list[dict] | None = None,
           title_png: Path | None = None, captions: bool = True, callouts: bool = True, loudnorm: bool = True) -> dict:
    """Render the EDL to run_dir/<out_name>; writes segments, cmd files, overlay PNGs and the audio under run_dir/render/
    and fills the EDL's `reframe` keyframes, `captions` / `callouts` / `caption_corrections` and a `render` block
    (written back to edl_path when given). `words` are the Voice words (voice.json) the captions come from; without
    them, or with captions=False, the cut has no captions. `title_png` puts the title card in front."""
    if not source.is_file():
        raise FileNotFoundError(f"source footage not found: {source}")
    if not edl.get("spans"):
        raise RuntimeError("EDL has no spans to render")
    render_dir = run_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    for stale in render_dir.glob("seg_*"):
        stale.unlink()
    ov_dir = render_dir / "overlays"
    shutil.rmtree(ov_dir, ignore_errors=True)
    ov_dir.mkdir()
    fps = float(edl["source"]["fps"])
    concept, track = load_reframe_track(run_dir, edl)
    segments = frame_align(merge_segments(edl["spans"]), fps)
    log(f"render: {len(edl['spans'])} spans -> {len(segments)} segments, source {source.name}, "
        f"track {concept or 'none (centre crop)'}")

    keyframes: list[dict] = []
    seg_info: list[dict] = []
    files: list[Path] = []
    with Timer() as t:
        paths: list[SegmentPath] = []
        for seg in segments:
            path = plan_segment(seg.t_in, seg.t_out, edl["source"], track, params, span_ids=seg.span_ids)
            path.xs = path.xs[:seg.frames] + [path.xs[-1]] * max(0, seg.frames - len(path.xs))
            paths.append(path)
        title_frames = 0
        title_s = 0.0
        if title_png is not None:
            title_frames = int(round(TITLE_S * fps))
            title_s = title_frames / fps
        overlays: dict[int, list[Overlay]] = {}
        if (captions and words) or callouts:
            fields, overlays = build_overlays(edl, words or [], segments, paths, load_track(run_dir, edl, "head"),
                                              load_track(run_dir, edl, "person"), ov_dir, title_s=title_s,
                                              captions=bool(captions and words), callouts=callouts)
            fields["caption_style"]["font_path"] = font_path()
            edl.update(fields)
            log(f"render: {len(fields['captions'])} caption lines "
                f"({sum(1 for c in fields['captions'] if c['placement'] != 'lower_third')} moved off the head, "
                f"{sum(1 for c in fields['captions'] if c['over_face'])} over it), {len(fields['callouts'])} callouts, "
                f"{len(fields['caption_corrections'])} corrections: "
                + "; ".join(f"{f['from']} -> {f['to']}" for f in fields["caption_corrections"]))
        else:
            edl.update({"captions": [], "callouts": [], "caption_corrections": []})
        if title_png is not None:
            tfile, _ = render_title_video(title_png, fps, render_dir)
            files.append(tfile)
            log(f"render: title card {title_png.name} {title_s:.2f}s ({title_frames} frames, fade {TITLE_FADE_S}s)")
        for seg, path in zip(segments, paths):
            ovs = overlays.get(seg.index, [])
            f = render_segment_video(source, seg, path, render_dir, ovs)
            files.append(f)
            keyframes.extend(path.keyframes)
            moving = sum(1 for a, b in zip(path.xs, path.xs[1:]) if a != b)
            seg_info.append({"index": seg.index, "t_in": seg.t_in, "t_out": seg.t_out, "duration": seg.duration, "frames": seg.frames,
                             "spans": seg.span_ids, "track_concept": concept, "track_id": path.track_id, "crop": [path.crop_w, path.crop_h],
                             "x_first": path.xs[0], "x_min": min(path.xs), "x_max": max(path.xs),
                             "frames_moving": moving, "overlays": len(ovs), "file": f.as_posix()})
            log(f"render: seg {seg.index + 1} {seg.t_in:.3f}-{seg.t_out:.3f}s ({seg.frames} frames, {'+'.join(seg.span_ids)}) "
                f"track {path.track_id} crop {path.crop_w}x{path.crop_h} x {min(path.xs)}..{max(path.xs)}, {len(ovs)} overlays")
        audio, loud = render_audio(source, segments, render_dir, lead_s=title_s, loudnorm=loudnorm)
        out = mux(files, audio, run_dir / out_name, render_dir)
    measured = measure_loudness(out)
    size_mb = out.stat().st_size / 1e6
    total = round(title_s + sum(s.duration for s in segments), 3)
    edl["reframe"] = keyframes
    edl["render"] = {"output": out.as_posix(), "width": OUT_W, "height": OUT_H, "duration_s": total, "segments": seg_info,
                     "title": {"png": title_png.as_posix(), "seconds": round(title_s, 4), "frames": title_frames,
                               "fade_out_s": TITLE_FADE_S, "text": edl.get("title_card_text", "")} if title_png else None,
                     "loudnorm": loud, "loudness_ebur128": measured,
                     "captions": len(edl.get("captions") or []), "callouts": len(edl.get("callouts") or []),
                     "audio": audio.as_posix(), "seconds": t.seconds, "bytes": out.stat().st_size}
    if edl_path:
        dump_json(edl_path, edl)
    loud_note = f", {measured['integrated_lufs']} LUFS / {measured['true_peak_dbtp']} dBTP" if measured else ""
    ledger.add(Entry(stage="render", model="ffmpeg", seconds=t.seconds, cost_usd=0.0,
                     note=f"{len(segments)} segments{' + title' if title_png else ''}, {total} s, "
                          f"{len(edl.get('captions') or [])} captions, {out.name} {size_mb:.1f} MB{loud_note}"))
    log(f"render: {out} ({total} s, {size_mb:.1f} MB{loud_note}) in {t.seconds}s")
    return edl["render"]
