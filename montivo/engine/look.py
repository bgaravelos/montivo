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
from .frame import frame_for
from .graphics import _HEX, ACTIVE_MODES, CASES, FONTS, PRESET_NAMES, PRESETS, TOP_RANGE, Look, normalize_look, restyle
from .render import load_track, plan_paths, reframe_inputs


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


def with_style(edl: dict, look: dict | None, callouts: dict[str, list[float] | None] | None = None,
               clips: dict[str, dict | None] | None = None) -> dict:
    """A copy of the EDL with another look and callout places (the Style panel's staged edits); `clips` gives spans a
    look of their own (None: back to the cut's look), as human.style_edit stores them. Raises ValueError for a bad
    look."""
    out = copy.deepcopy(edl)
    if look is not None:
        out["look"] = restyle(edl.get("look"), look).to_json()
    by_id = {s["id"]: s for s in out["spans"]}
    for sid, lk in (clips or {}).items():
        if sid in by_id:
            by_id[sid].pop("look", None)
            if lk is not None and (own := restyle(out.get("look"), lk).to_json()) != restyle(out.get("look"), out.get("look")).to_json():
                by_id[sid]["look"] = own
    for sid, xy in (callouts or {}).items():
        if sid in by_id:
            if xy is None:
                by_id[sid].pop("callout_xy", None)
            else:
                by_id[sid]["callout_xy"] = [round(float(xy[0])), round(float(xy[1]))]
    return out


def frame_at(run_dir: Path, edl: dict, t: float, plan=None) -> Image.Image:
    """The frame (1080x1920, or the EDL's other aspect) at cut second t of `edl` without any graphics: the source
    cropped as the render crops it."""
    if plan is None:
        _, track, speaker_ids, fallback = reframe_inputs(run_dir, edl)
        plan = plan_paths(edl, track, speaker_ids, fallback=fallback)
    segments, paths = plan
    title_s = float(((edl.get("render") or {}).get("title") or {}).get("seconds") or 0.0)
    start, i = title_s, len(segments) - 1
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
    fr = frame_for(edl)
    return grab(source_of(run_dir, edl), seg.t_in + k / seg.fps,
                f"crop={path.crop_w}:{path.crop_h}:{path.xs[k]}:{path.crop_y}:exact=1,scale={fr.w}:{fr.h}:flags=lanczos,setsar=1")


def backdrop(run_dir: Path, edl: dict, band: tuple[int, int], samples: int = 5) -> dict:
    """How light the picture is behind a horizontal band of the frame (the captions' rows), over a few cut seconds:
    mean luma 0..1 and its spread, for the style prompt to pick colours that read."""
    lines = edl.get("captions") or []
    if not lines:
        return {}
    pick = [lines[int(j * (len(lines) - 1) / max(1, samples - 1))] for j in range(min(samples, len(lines)))]
    lumas = []
    for c in pick:
        im = frame_at(run_dir, edl, (c["t_in"] + c["t_out"]) / 2).convert("L").crop((0, band[0], OUT_W, band[1]))
        px = list(im.resize((54, max(1, (band[1] - band[0]) // 20))).getdata())
        lumas.append(sum(px) / len(px) / 255)
    m = sum(lumas) / len(lumas)
    return {"band_y": list(band), "mean_luma": round(m, 2), "min_luma": round(min(lumas), 2), "max_luma": round(max(lumas), 2),
            "reads_as": "light" if m > 0.6 else "dark" if m < 0.35 else "mid-tone"}


def preview(run_dir: Path, edl: dict, t: float, cut: Path | None = None) -> tuple[Image.Image, dict]:
    """The frame at cut second t of `edl` (its spans, crop, look and aspect) and the caption and callout entries on
    screen then (their boxes in output pixels, for the editor's handles). On the title card it is the card from `cut`
    (the 9:16 cut: its middle for a feed aspect, as the render crops the card)."""
    render = edl.get("render") or {}
    title_s = float((render.get("title") or {}).get("seconds") or 0.0)
    if t < title_s and cut is not None and cut.is_file():
        fr = frame_for(edl)
        vf = (f"scale={OUT_W}:{OUT_H}" if (fr.w, fr.h) == (OUT_W, OUT_H) else
              f"scale={fr.w}:{fr.h}:force_original_aspect_ratio=increase,crop={fr.w}:{fr.h}")
        return grab(cut, t, vf), {"captions": [], "callouts": [], "title": True}
    concept, track, speaker_ids, fallback = reframe_inputs(run_dir, edl)
    segments, paths = plan_paths(edl, track, speaker_ids, fallback=fallback)
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
        seg = segments[i]
        local = min(max(0.0, t - start), seg.duration - 1e-3)
        img = frame_at(run_dir, edl, t, plan=(segments, paths))
        for ov in overlays.get(seg.index, []):
            if ov.t_in <= local < ov.t_out:
                png = Image.open(ov.png).convert("RGBA")
                img.alpha_composite(png, (int(ov.x), int(ov.y)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    on = lambda e: e["t_in"] <= t < e["t_out"]  # noqa: E731
    return img, {"captions": [c for c in fields["captions"] if on(c)], "callouts": [c for c in fields["callouts"] if on(c)],
                 "look": fields["caption_style"]["look"], "title": False}


def layout(run_dir: Path, edl: dict) -> dict:
    """The caption and callout entries a render of `edl` would write (boxes in its frame's pixels), without drawing or
    rendering anything: the Export page's pre-flight for a feed aspect."""
    concept, track, speaker_ids, fallback = reframe_inputs(run_dir, edl)
    segments, paths = plan_paths(edl, track, speaker_ids, fallback=fallback)
    vpath = run_dir / "voice" / "voice.json"
    words = json.loads(vpath.read_text())["words"] if vpath.is_file() else []
    title_s = float(((edl.get("render") or {}).get("title") or {}).get("seconds") or 0.0)
    fields, _ = build_overlays(edl, words, segments, paths, load_track(run_dir, edl, "head"), load_track(run_dir, edl, "person"),
                               run_dir / "ui" / "cache" / "layout", title_s=title_s, captions=bool(words),
                               reframe_concept=concept, draw_at=-1.0)
    return fields


# --------------------------------------------------------------------------- the look from a sentence

PROMPT = """You style the burned-in captions and callouts of a vertical (1080 x 1920) talking-head cut. A person asked:

"{text}"

Return the whole look as JSON. Start from the current look and change only what the request asks for (and what it
needs to read well); keep everything else as it is.

Current look: {current}

What each field does:
- preset: the starting point it is closest to: {presets}.
- font: {fonts}.
- size: caption text height in px, 48-140 (84 is a bold, readable default; 110+ only with 1-2 words per line).
- case: "as_said" or "upper". max_words: words per caption line, 1-6 (1-2 = punchy word-by-word, 3-4 = calm).
- text / stroke: #RRGGBB colours of the words and their outline; stroke_w: outline px, 0-16 (0 = none).
- shadow: a soft drop shadow (helps light text on busy or light backgrounds when there is no outline).
- active: how the word being spoken shows: "pill" (active_fg text on an active_bg pill), "color" (the word in
  active_bg), "karaoke" (every word said so far in active_bg), "none".
- highlight: #RRGGBB for names and key product words, or "" for none. highlight_add: more words to colour (lowercase).
- box: "" or a colour with alpha (#RRGGBBAA, e.g. #000000C8) drawn behind each whole line.
- position: "auto" keeps every line off the speaker's face starting from `top`; "fixed" puts every line at `top`.
  top: y of the caption line, 160-1700. The apps cover the bottom 440-480 px with their own caption and buttons, so
  keep top at or under 1300 unless the person asks for low captions; 220-400 is the top of the frame.
- callout_bg / callout_fg: #RRGGBB fill and text of the callout pills.
- why: one short sentence on what you changed and why.

Measured: the picture behind the caption rows (y {band}) is {backdrop}. Light backgrounds need dark or outlined text
(or a box); dark backgrounds suit white or bright text. {face}
"""


def face_band(edl: dict) -> tuple[int, int] | None:
    """The rows the speaker's head covers on most caption lines (median of the EDL's head boxes, frame px)."""
    boxes = sorted((c["head_box"][1], c["head_box"][3]) for c in edl.get("captions") or [] if c.get("head_box"))
    if not boxes:
        return None
    tops, bots = sorted(b[0] for b in boxes), sorted(b[1] for b in boxes)
    return int(tops[len(tops) // 2]), int(bots[len(bots) // 2])


def face_note(edl: dict, size: int) -> str:
    band = face_band(edl)
    if not band:
        return ""
    h = round(size * 1.6)
    below, above = band[1] + 36, band[0] - 36 - h
    return (f"The speaker's head covers y {band[0]}-{band[1]} on most lines: a fixed top must stay at or below {below} "
            f"(under the chin) or at or above {max(160, above)} (over the head) so the words never cover the face; "
            f"when neither fits what the person asked, use position auto.")


def schema() -> dict:
    s = lambda **k: {"type": "string", **k}  # noqa: E731
    i = {"type": "integer"}
    props = {"preset": s(enum=list(PRESETS)), "font": s(enum=list(FONTS)), "size": i, "case": s(enum=list(CASES)),
             "max_words": i, "text": s(), "stroke": s(), "stroke_w": i, "shadow": {"type": "boolean"},
             "active": s(enum=list(ACTIVE_MODES)), "active_fg": s(), "active_bg": s(), "highlight": s(), "box": s(),
             "position": s(enum=["auto", "fixed"]), "top": i, "callout_bg": s(), "callout_fg": s(),
             "highlight_add": {"type": "array", "items": s()}, "why": s()}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def settle(raw: dict, current: Look) -> tuple[dict, list[str]]:
    """The model's look made valid: numbers clamped into range, a colour that is not #RRGGBB(AA) kept as it was.
    Returns (look dict, what was corrected)."""
    fixed: list[str] = []
    cur = current.to_json()
    out = {**cur, **{k: raw[k] for k in cur if k in raw}}
    for k, lo, hi in (("size", 40, 160), ("stroke_w", 0, 16), ("max_words", 1, 6), ("top", *TOP_RANGE)):
        try:
            v = int(out.get(k, cur[k]))
        except (TypeError, ValueError):
            v = cur[k]
        if not lo <= v <= hi:
            fixed.append(f"{k} {v} -> {min(hi, max(lo, v))}")
        out[k] = min(hi, max(lo, v))
    for k in ("text", "stroke", "active_fg", "active_bg", "callout_bg", "callout_fg", "highlight", "box"):
        v = out.get(k, cur[k])
        empty_ok = k in ("highlight", "box")
        if not (isinstance(v, str) and ((empty_ok and v == "") or _HEX.match(v))):
            fixed.append(f"{k} {v!r} kept as {cur[k]!r}")
            out[k] = cur[k]
        else:
            out[k] = v.upper() if v else v
    out["highlight_off"] = cur["highlight_off"]
    return out, fixed


def suggest(run_dir: Path, edl: dict, text: str, ledger, log=print, model: str | None = None) -> tuple[dict, str, list[str]]:
    """One Spark call (about $0.008): the look a sentence asks for, on top of the version's current look, checked by
    normalize_look. Returns (look dict, the model's one-line why, corrections made to its answer)."""
    from .spark import Spark  # the API client only when a sentence is styled
    current = normalize_look(edl.get("look"))
    band = (max(TOP_RANGE[0], current.top - 50), min(OUT_H, current.top + 300))
    back = backdrop(run_dir, edl, band)
    prompt = PROMPT.format(text=text.replace('"', "'"), current=json.dumps(current.to_json()),
                           presets=", ".join(f"{k} ({PRESET_NAMES[k]}: {json.dumps(v) if v else 'the default'})"
                                             for k, v in PRESETS.items()),
                           fonts=", ".join(f"{k} ({v[0]})" for k, v in FONTS.items()), band=list(band),
                           backdrop=f"{back['reads_as']} (mean luma {back['mean_luma']})" if back else "unknown",
                           face=face_note(edl, current.size))
    kw = {"model": model} if model else {}
    call = Spark(run_dir, ledger, log=log, **kw).call("look", prompt, schema(), max_output_tokens=6000, reasoning_effort="low",
                                                       stage="spark.look", schema_name="caption_look")
    raw = dict(call.parsed)
    why = str(raw.pop("why", "")).strip()
    look, fixed = settle(raw, current)
    band = face_band(edl)
    h = round(look["size"] * 1.6)
    if look["position"] == "fixed" and band and band[0] - 36 - h < look["top"] < band[1] + 36:
        fixed.append(f"fixed top {look['top']} is on the face (y {band[0]}-{band[1]}): position auto")
        look["position"] = "auto"
    normalize_look(look)  # raises ValueError if anything is still off
    return look, why, fixed
