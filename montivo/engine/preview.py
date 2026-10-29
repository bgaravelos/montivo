"""The editor's live preview: what the browser needs to play a version (or a draft EDL) without a render.

The browser plays the cut from the edit proxy (ingest.make_edit_proxy: short GOP, video only) and the edit audio
(FLAC), segment by segment: the audio leads (Web Audio, one AudioBufferSourceNode per segment), two <video> elements
take turns on the picture, and the 9:16 crop is a CSS transform per frame from the same crop path the render burns
(render.plan_paths). Nothing here encodes the cut; the render stays the one source of truth for the exported file.
$0, ffmpeg only for the two edit files (once per source).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .api import dump_json
from .frame import frame_for
from .ingest import EDIT_PROXY_SHORT, ffprobe, make_edit_proxy
from .render import TITLE_S, plan_paths, reframe_inputs

EDIT_DIR = Path("ui") / "cache" / "edit"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(run_dir: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(run_dir.resolve()), threading.Lock())


def edit_media(run_dir: Path, edl: dict, log=None) -> dict:
    """The edit proxy and edit audio of the EDL's source, made once (about 4 s for 69 s of 1080p on the Mac) and
    kept in ui/cache/edit/ until the source changes (path, size, mtime)."""
    src = Path(edl["source"]["path"])
    if not src.is_file():
        raise FileNotFoundError(f"source footage {src} not found")
    st = src.stat()
    key = {"source": src.as_posix(), "bytes": st.st_size, "mtime": round(st.st_mtime, 3), "short": EDIT_PROXY_SHORT}
    d = run_dir / EDIT_DIR
    video, audio, meta = d / "proxy.mp4", d / "audio.flac", d / "meta.json"
    with _lock(run_dir):
        if meta.is_file() and video.is_file() and audio.is_file():
            old = json.loads(meta.read_text())
            if old.get("key") == key:
                return old
        d.mkdir(parents=True, exist_ok=True)
        tmp_v, tmp_a = d / "proxy.part.mp4", d / "audio.part.flac"
        p = make_edit_proxy(src, tmp_v, tmp_a, ffprobe(src), log=log)
        tmp_v.replace(video)
        tmp_a.replace(audio)
        rec = {"key": key, "width": p.width, "height": p.height, "fps": p.fps, "duration_s": p.duration,
               "video": (EDIT_DIR / "proxy.mp4").as_posix(), "audio": (EDIT_DIR / "audio.flac").as_posix(),
               "bytes": video.stat().st_size + audio.stat().st_size}
        dump_json(meta, rec)
        return rec


def title_seconds(run_dir: Path, edl: dict) -> tuple[float, str | None]:
    """(seconds, png) of the opening card: what the render recorded, else what a render of this EDL would put there
    (the card only when the EDL wants it and title/title_card.png exists: a draft never generates one)."""
    rec = edl.get("render")
    if rec is not None and "title" in rec:
        t = rec["title"] or {}
        return float(t.get("seconds") or 0.0), t.get("png")
    if not (edl.get("title_card", True) and edl.get("title_card_text")):
        return 0.0, None
    png = run_dir / "title" / "title_card.png"
    if not png.is_file():
        return 0.0, None
    fps = float(edl["source"]["fps"])
    return round(round(TITLE_S * fps) / fps, 4), png.as_posix()


def preview_data(run_dir: Path, edl: dict) -> dict:
    """Segments in source and cut time and the crop path of each, exactly as render.plan_paths plans them for the
    render (one crop x per source frame, in source pixels); about 15 ms on run 015."""
    concept, track, speaker_ids, fallback = reframe_inputs(run_dir, edl)
    segments, paths = plan_paths(edl, track, speaker_ids, fallback=fallback)
    fps = float(edl["source"]["fps"])
    title_s, title_png = title_seconds(run_dir, edl)
    frame = frame_for(edl)
    out, t = [], title_s
    for seg, path in zip(segments, paths):
        dur = seg.frames / fps
        out.append({"index": seg.index, "t_in": seg.t_in, "t_out": seg.t_out, "frames": seg.frames,
                    "cut_t0": round(t, 6), "span_ids": seg.span_ids,
                    "crop": {"w": path.crop_w, "h": path.crop_h, "y": path.crop_y, "xs": path.xs}})
        t += dur
    src = edl["source"]
    return {"fps": fps, "source": {"width": src["width"], "height": src["height"]},
            "frame": {"aspect": frame.aspect, "width": frame.w, "height": frame.h},
            "title": {"seconds": title_s, "png": title_png} if title_s else None,
            "duration_s": round(t, 6), "segments": out, "reframe_concept": concept}
