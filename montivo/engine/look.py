"""The editor's style preview: one frame of a version as the render would burn it with another look, in about 0.3 s.

The frame comes from the source footage, cropped and scaled the way render.plan_paths plans that version's crop, and
the captions and callouts on screen at that cut second are drawn by the same code the render uses
(captions.build_overlays with `draw_at`), so the still shows exactly the pixels a new version would have. Punch-in
zooms are left out (the captions and callouts never zoom). A cut second on the title card returns the card from the
cut itself. $0, ffmpeg and Pillow only.
"""
from __future__ import annotations

import copy
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .captions import OUT_H, OUT_W, build_overlays
from .graphics import normalize_look
from .render import load_reframe_track, load_speaker_ids, load_track, plan_paths


def source_of(run_dir: Path, edl: dict) -> Path:
    p = Path(edl["source"]["path"])
    if not p.is_file() and not p.is_absolute():  # a run folder used from another working directory
        alt = run_dir.resolve().parent.parent / p
        p = alt if alt.is_file() else p
    if not p.is_file():
        raise FileNotFoundError(f"source footage {p} not found")
    return p


def grab(media: Path, t: float, vf: str) -> Image.Image:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.4f}", "-i", str(media),
                           "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"],
                          capture_output=True, check=True)
    return Image.open(io.BytesIO(proc.stdout)).convert("RGBA")


def with_style(edl: dict, look: dict | None, callouts: dict[str, list[float] | None] | None = None) -> dict:
    """A copy of the EDL with another look and callout places (the Style panel's staged edits); raises ValueError for
    a bad look."""
    out = copy.deepcopy(edl)
    if look is not None:
        out["look"] = normalize_look(look).to_json()
    by_id = {s["id"]: s for s in out["spans"]}
    for sid, xy in (callouts or {}).items():
        if sid in by_id:
            if xy is None:
                by_id[sid].pop("callout_xy", None)
            else:
                by_id[sid]["callout_xy"] = [round(float(xy[0])), round(float(xy[1]))]
    return out


def preview(run_dir: Path, edl: dict, t: float, cut: Path | None = None) -> tuple[Image.Image, dict]:
    """The 1080x1920 frame at cut second t of `edl` (its spans, crop and look) and the caption and callout entries on
    screen then (their boxes in output pixels, for the editor's handles)."""
    render = edl.get("render") or {}
    title_s = float((render.get("title") or {}).get("seconds") or 0.0)
    if t < title_s and cut is not None and cut.is_file():
        return grab(cut, t, f"scale={OUT_W}:{OUT_H}"), {"captions": [], "callouts": [], "title": True}
    concept, track = load_reframe_track(run_dir, edl)
    segments, paths = plan_paths(edl, track, load_speaker_ids(run_dir, concept, edl))
    vpath = run_dir / "voice" / "voice.json"
    words = json.loads(vpath.read_text())["words"] if vpath.is_file() else []
    tmp = Path(tempfile.mkdtemp(prefix="preview_", dir=run_dir / "ui" if (run_dir / "ui").is_dir() else None))
    try:
        fields, overlays = build_overlays(edl, words, segments, paths, load_track(run_dir, edl, "head"),
                                          load_track(run_dir, edl, "person"), tmp, title_s=title_s,
                                          captions=bool(words), reframe_concept=concept, draw_at=t)
        start = title_s
        i = len(segments) - 1
        for k, seg in enumerate(segments):
            if t < start + seg.duration:
                i = k
                break
            start += seg.duration
        else:
            start -= segments[-1].duration
        seg, path = segments[i], paths[i]
        local = min(max(0.0, t - start), seg.duration - 1e-3)
        k = min(len(path.xs) - 1, int(local * seg.fps))
        img = grab(source_of(run_dir, edl), seg.t_in + k / seg.fps,
                   f"crop={path.crop_w}:{path.crop_h}:{path.xs[k]}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1")
        for ov in overlays.get(seg.index, []):
            if ov.t_in <= local < ov.t_out:
                png = Image.open(ov.png).convert("RGBA")
                img.alpha_composite(png, (int(ov.x), int(ov.y)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    on = lambda e: e["t_in"] <= t < e["t_out"]  # noqa: E731
    return img, {"captions": [c for c in fields["captions"] if on(c)], "callouts": [c for c in fields["callouts"] if on(c)],
                 "look": fields["caption_style"]["look"], "title": False}
