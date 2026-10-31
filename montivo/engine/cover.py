"""Cover suggestions: Muse Spark watches a version and names the moments that make the best still for the profile grid.

The video Spark sees is the version's review copy (720x1280 with the cut-time clock in the corner, the one the blind
review uploaded, reused when its file is still there, else made and uploaded again), so its t_s land within a second.
Each moment is then settled locally on the sharpest frame within +-0.25 s (the variance of an edge filter over the
middle of the frame, where the face is), because a frame mid-blink or mid-turn reads worse than the one beside it.
One Spark call, about $0.02-0.03; the record goes to <run>/cover_vN.json and the call to the run's ledger.
"""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from .api import dump_json, upload_file

PROMPT = """This is a vertical short-form video (a talking-head cut). A clock in the top-left corner shows the cut time in
seconds. Pick the {n} best moments for its cover image: the still people see in the profile grid before they tap.

A good cover moment: the speaker's face fills a good part of the frame and is sharp, eyes open and towards the
camera, an expressive or confident look (a smile, raised brows, a gesture), the mouth not caught mid-word in a strange
shape, no motion blur, and if possible a caption or callout on screen that makes someone want to watch. Avoid the
first {skip:.1f} s (the title card, when there is one) and the last second. Spread the moments at least 2 s apart.

The video is {duration:.1f} s long. Return the moments best first, each with the clock time you read (t_s) and one
short reason a person can check by looking."""

SCHEMA = {"type": "object", "properties": {"moments": {"type": "array", "items": {
    "type": "object", "properties": {"t_s": {"type": "number"}, "why": {"type": "string"}},
    "required": ["t_s", "why"], "additionalProperties": False}}},
    "required": ["moments"], "additionalProperties": False}


def frame(cut: Path, t: float, w: int = 360) -> Image.Image:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(cut),
                           "-frames:v", "1", "-vf", f"scale={w}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                          capture_output=True, check=True)
    return Image.open(io.BytesIO(proc.stdout)).convert("L")


def sharpness(im: Image.Image) -> float:
    """Edge energy in the middle of the frame (the face's band in a 9:16 talking head)."""
    w, h = im.size
    mid = im.crop((int(w * 0.15), int(h * 0.25), int(w * 0.85), int(h * 0.7)))
    return ImageStat.Stat(mid.filter(ImageFilter.FIND_EDGES)).var[0]


TITLE_CLEAR_S = 0.1  # a settled moment stays this far past the title card (its last frames fade out)


def settle(cut: Path, t: float, duration: float, reach: float = 0.25, steps: int = 7,
           after: float = 0.0) -> tuple[float, float]:
    """The sharpest frame within +-reach of t, never before `after` (the end of the title card: a moment 0.1 s past
    the card used to settle back onto it): (time, sharpness)."""
    best = (t, -1.0)
    lo = max(0.0, after + TITLE_CLEAR_S) if after > 0 else 0.0
    for k in range(steps):
        x = min(max(lo, t - reach + 2 * reach * k / (steps - 1)), max(lo, duration - 0.05))
        s = sharpness(frame(cut, x))
        if s > best[1]:
            best = (round(x, 2), round(s, 1))
    return best


def video_for_spark(run_dir: Path, n: int, log=print) -> str:
    """The file id of version n's review copy: the blind review's when its upload is still there, else a new one."""
    rv = run_dir / f"review_v{n}.json"
    if rv.is_file():
        r = json.loads(rv.read_text())
        if r.get("file_id") and r.get("clock") and Path(r.get("cut") or "").name == f"cut_v{n}.mp4":
            return r["file_id"]
    return _fresh_upload(run_dir, n, log)


def suggest(run_dir: Path, n: int, ledger, log=print, k: int = 3) -> dict:
    from .spark import Spark  # the API client only when a cover is asked for
    edl = json.loads((run_dir / f"edl_v{n}.json").read_text())
    cut = run_dir / f"cut_v{n}.mp4"
    render = edl.get("render") or {}
    duration = float(render.get("duration_s") or edl.get("runtime_s") or 0.0)
    skip = float((render.get("title") or {}).get("seconds") or 0.0)
    prompt = PROMPT.format(n=k, skip=skip, duration=duration)
    spark = Spark(run_dir, ledger, log=log)
    try:
        call = spark.call(f"cover_v{n}", prompt, SCHEMA, file_id=video_for_spark(run_dir, n, log), max_output_tokens=4000,
                          reasoning_effort="low", stage="spark.cover")
    except Exception as e:  # an expired upload: make the copy again once
        if "file" not in str(e).lower():
            raise
        log(f"cover: the review's upload is gone ({str(e)[:80]}), uploading again")
        call = spark.call(f"cover_v{n}", prompt, SCHEMA, file_id=_fresh_upload(run_dir, n, log), max_output_tokens=4000,
                          reasoning_effort="low", stage="spark.cover")
    moments = []
    for m in call.parsed.get("moments") or []:
        t = float(m.get("t_s") or 0.0)
        if not skip <= t <= duration:
            continue
        st, sh = settle(cut, t, duration, after=skip)
        moments.append({"t_s": st, "spark_t_s": round(t, 2), "why": str(m.get("why") or "").strip(), "sharpness": sh})
    # the record is merged: the Cover maker's `design` (thumb.design) and anything else already in it stays
    path = run_dir / f"cover_v{n}.json"
    try:
        old = json.loads(path.read_text()) if path.is_file() else {}
    except json.JSONDecodeError:
        old = {}
    rec = {**(old if isinstance(old, dict) else {}), "version": n, "moments": moments[:k], "cost_usd": call.cost_usd,
           "seconds": call.seconds}
    dump_json(path, rec)
    log(f"cover: v{n} " + "; ".join(f"{m['t_s']:.2f}s {m['why'][:60]}" for m in rec["moments"]) + f", ${call.cost_usd:.4f}")
    return rec


def _fresh_upload(run_dir: Path, n: int, log) -> str:
    from .review import review_copy
    copy = review_copy(run_dir / f"cut_v{n}.mp4", run_dir / "review" / f"cover_v{n}", clock=True)
    up = upload_file(copy, "video/mp4")
    log(f"cover: uploaded {copy.name} ({copy.stat().st_size / 1e6:.1f} MB) as {up.get('id')}")
    return up["id"]
