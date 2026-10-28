"""B-roll stills: a Muse Image picture laid over a few seconds of the cut (EDL `overlays`), only when a person asks for
one (the editor's Overlays track, `montivo.human overlay`, or "show a diagram here" to Ask Montivo); the planner never
adds one on its own (Billy, 28/10).

An overlay is anchored to the recording, not to the cut: `src_t` is a source second inside a kept clip (the word on
screen when it was added), so an edit elsewhere moves it with its words, and when those words leave the cut it is not
drawn (the render records why). `dur` is how long it stays, cut seconds, running on across the cuts after it.

The picture (broll/<id>.png, one muse-image-1.0 call, $0.01, in the brand's colours) is laid out per `placement` into a
PNG of the output frame's size (render/overlays/broll_<id>.png): `full` covers the frame with a soft ink gradient under
the caption rows; `upper` is a card with rounded corners above the caption rows on the dimmed video; `pip` a small card
in the corner the speaker's head is not in (the caption layout's head boxes over its time). The render burns it in
the segment's overlay graph under the captions and callouts, with a 0.2 s fade in and out (no fade at a cut it runs
across), so the segment cache keys on its bytes like every caption PNG.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..ledger import Ledger
from .captions import Overlay, SAFE_TOP
from .frame import FRAME_916, Frame
from .image import generate_image

PLACEMENTS = ("full", "upper", "pip")
STYLES = ("photo", "diagram", "icon")
DEFAULT_DUR_S = 2.5
DUR_RANGE = (0.8, 8.0)
FADE_S = 0.2
INK = (20, 20, 20)
MARKER = (255, 198, 41)

# layout in the 1080x1920 frame (frame.Frame.y scales the y's to a feed aspect)
CARD = {"upper": (60, SAFE_TOP + 40, 960, 760, 40), "pip": (0, 0, 400, 400, 32)}  # x, y, w, h, corner radius
PIP_SPOTS = [(632, SAFE_TOP + 40), (48, SAFE_TOP + 40), (632, 840), (48, 840)]  # top right first
CARD_MIN_H = 480  # an upper card ends HEAD_GAP above the head when that leaves it this tall (run 015: the forehead)
HEAD_GAP = 28
CALLOUT_MIN_S = 0.5  # what is left of a callout beside a full-frame still, else it is not drawn
DIM = 72  # alpha of the ink over the video around an `upper` card (a quarter darker: the speaker stays readable)
SIZE = {"full": "1080x1920", "upper": "1080x1080", "pip": "1080x1080"}  # Muse Image keeps the aspect, not the size

PROMPT = (
    "A B-roll still for a short vertical social video, on screen for two or three seconds while the speaker talks. "
    "It shows: {prompt}. {style} Colours: near-black #141414, off-white #F3F0E8 and one marker-yellow #FFC629 accent. "
    "{frame}No captions, no watermark, no logos, no borders, no device frames.")
STYLE_TEXT = {
    "photo": "Style: a cinematic photograph, natural light, shallow depth of field, a muted grade with warm highlights "
             "and one marker-yellow #FFC629 detail in the scene.",
    "diagram": "Style: a clean flat diagram on a near-black #141414 background with off-white #F3F0E8 lines, arrows and "
               "shapes, marker-yellow #FFC629 on the one key element, at most four labels of one or two words each, "
               "large and legible on a phone.",
    "icon": "Style: one bold flat icon centred on a near-black #141414 background, off-white #F3F0E8 with "
            "marker-yellow #FFC629 accents, generous margins, no text."}
FRAME_TEXT = {"full": "Vertical 9:16 frame; keep the lower third calm (captions go there). ",
              "upper": "Landscape-ish frame, the subject centred with margins. ",
              "pip": "Square frame, one clear subject centred, readable at a small size. "}
_ICON = re.compile(r"\b(icons?|logo|symbol|emoji|εικονίδιο)\b", re.I)
_DIAGRAM = re.compile(r"\b(diagrams?|charts?|graphs?|flowcharts?|infographic|schema|διάγραμμα)", re.I)


def style_for(prompt: str) -> str:
    """The style when the person names none: an icon when they ask for one, a diagram for a diagram or chart, else a
    photo (run 015: "a laptop showing a video timeline" is a photo, "a circular arrow loop icon" an icon)."""
    return "icon" if _ICON.search(prompt) else "diagram" if _DIAGRAM.search(prompt) else "photo"


def image_prompt(prompt: str, style: str, placement: str) -> str:
    return PROMPT.format(prompt=prompt.strip().rstrip("."), style=STYLE_TEXT[style], frame=FRAME_TEXT[placement])


def next_id(edl: dict, run_dir: Path) -> str:
    """b1, b2, ...: past every overlay of the EDL and every picture in broll/ (an earlier version may still use one)."""
    taken = [int(m.group(1)) for x in [*(o.get("id", "") for o in edl.get("overlays") or []),
                                       *(p.stem for p in (run_dir / "broll").glob("b*.png"))]
             if (m := re.fullmatch(r"b(\d+)", x))]
    return f"b{max(taken, default=0) + 1}"


def generate(run_dir: Path, oid: str, prompt: str, style: str, placement: str, ledger: Ledger, log=print) -> Path:
    """broll/<oid>.png from Muse Image ($0.01), its request and response next to it."""
    out = run_dir / "broll"
    out.mkdir(exist_ok=True)
    return generate_image(image_prompt(prompt, style, placement), out / f"{oid}.png", out / f"{oid}.request.json",
                          out / f"{oid}.response.json", ledger, log, size=SIZE[placement], stage="image.overlay",
                          label=f"B-roll {oid} ({style}, {placement}) \"{prompt[:60]}\"")


def _card(img: Image.Image, w: int, h: int, r: int, outline: int) -> tuple[Image.Image, int]:
    """The picture cover-cropped to w x h with rounded corners, a marker-yellow outline and a soft shadow; returns
    (RGBA image, the padding around the card the shadow takes)."""
    pad = max(12, r)
    pic = ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("L", out.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + pad // 3, pad + w, pad + h + pad // 3), radius=r, fill=150)
    out.putalpha(shadow.filter(ImageFilter.GaussianBlur(pad / 2)))
    out.paste(pic, (pad, pad), mask)
    ImageDraw.Draw(out).rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), radius=r, outline=MARKER + (255,),
                                          width=outline)
    return out, pad


def compose(src: Path, placement: str, out: Path, frame: Frame = FRAME_916, spot: tuple[int, int] | None = None,
            bottom: int | None = None) -> Path:
    """The frame-sized PNG the render overlays for one still (see the module docstring); `spot` is the pip's top-left
    in the 1080x1920 frame (pip_spot), `bottom` the lowest row (frame pixels) an upper card may reach (card_bottom).
    Deterministic: the same picture and layout give the same bytes."""
    img = Image.open(src)
    W, H = frame.w, frame.h
    if placement == "full":
        canvas = ImageOps.fit(img.convert("RGB"), (W, H), Image.LANCZOS).convert("RGBA")
        grad = Image.new("L", (1, 256), 0)
        for k in range(256):  # ink from 0 to 55 % over the lower 35 % (the caption rows)
            grad.putpixel((0, k), int(140 * max(0.0, (k / 255 - 0.65) / 0.35)))
        shade = Image.new("RGBA", (W, H), INK + (0,))
        shade.putalpha(grad.resize((W, H)))
        canvas = Image.alpha_composite(canvas, shade)
    else:
        x, y, w, h, r = CARD[placement]
        if placement == "pip":
            x, y = spot or PIP_SPOTS[0]
        canvas = Image.new("RGBA", (W, H), INK + (DIM if placement == "upper" else 0,))
        h = min(h, frame.y(1920 - 300) - frame.y(y))  # a feed frame is shorter: the card stays above its caption rows
        if bottom is not None and placement == "upper":
            h = max(min(h, CARD_MIN_H), min(h, bottom - frame.y(y)))
        card, pad = _card(img, w, h, r, 6 if placement == "upper" else 5)
        canvas.alpha_composite(card, (x - pad, frame.y(y) - pad))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, optimize=False)
    return out


def pip_spot(head_boxes: list[list[float]], frame: Frame = FRAME_916) -> tuple[int, int]:
    """The corner card's place: of PIP_SPOTS, the one the head boxes (frame pixels, [x0, y0, x1, y1]) cover least,
    top right on a tie."""
    w, h = CARD["pip"][2], CARD["pip"][3]

    def cover(spot):
        x, y = spot[0], frame.y(spot[1])
        return sum(max(0.0, min(x + w, b[2]) - max(x, b[0])) * max(0.0, min(y + h, b[3]) - max(y, b[1])) for b in head_boxes)
    return min(PIP_SPOTS, key=lambda s: (cover(s), PIP_SPOTS.index(s)))


def card_bottom(head_boxes: list[list[float]]) -> int | None:
    """The lowest row an upper card reaches: HEAD_GAP above the highest head top over its time (None: no head)."""
    return int(min(b[1] for b in head_boxes)) - HEAD_GAP if head_boxes else None


def clear_callouts(overlays: list[Overlay], windows: list[tuple[float, float]]) -> tuple[list[Overlay], list[str]]:
    """A segment's overlays with the callouts kept out of its full-frame stills' windows (segment-local seconds): a
    callout pill over a cutaway sat on the diagram's labels (run 015). The part before or after a window stays when it
    is CALLOUT_MIN_S or longer. Returns (overlays, the callout PNG names it cut)."""
    out, cut = [], []
    for ov in overlays:
        hit = [(a, b) for a, b in windows if a < ov.t_out and b > ov.t_in]
        if not ov.png.name.startswith("callout_") or not hit:
            out.append(ov)
            continue
        a, b = min(w[0] for w in hit), max(w[1] for w in hit)
        cut.append(ov.png.name)
        before, after = a - ov.t_in, ov.t_out - b
        if before >= max(after, CALLOUT_MIN_S):
            out.append(dataclasses.replace(ov, t_out=round(a, 4)))
        elif after >= CALLOUT_MIN_S:
            out.append(dataclasses.replace(ov, t_in=round(b, 4)))
    return out, cut


def normalize(o: dict) -> dict:
    """One EDL overlay checked: id, src_t, dur in DUR_RANGE, placement, image path. Raises ValueError."""
    if not re.fullmatch(r"b\d+", str(o.get("id") or "")):
        raise ValueError(f"overlay id {o.get('id')!r} is not b<N>")
    if o.get("placement") not in PLACEMENTS:
        raise ValueError(f"overlay placement {o.get('placement')!r} is not one of {', '.join(PLACEMENTS)}")
    dur = float(o.get("dur") or DEFAULT_DUR_S)
    if not DUR_RANGE[0] <= dur <= DUR_RANGE[1]:
        raise ValueError(f"overlay {o['id']} lasts {dur:g} s: {DUR_RANGE[0]:g} to {DUR_RANGE[1]:g} s")
    return {**o, "src_t": float(o["src_t"]), "dur": dur}


def home_span(edl: dict, o: dict) -> dict | None:
    """The kept clip that plays the overlay's source second (its own span first: words play once, so at most one
    other can)."""
    t = float(o["src_t"])
    inside = [s for s in edl["spans"] if s["t_in"] <= t < s["t_out"]]
    return next((s for s in inside if s["id"] == o.get("span")), inside[0] if inside else None)


def place(edl: dict, segments: list, title_s: float, run_dir: Path, out_dir: Path,
          frame: Frame = FRAME_916) -> tuple[dict[int, list[Overlay]], list[dict]]:
    """The EDL's overlays as render overlays per segment index (segment-local seconds, under the captions) and the
    render's record of each ({id, t_in, t_out in cut seconds, placement, png, shown, why}). `segments` are the
    render's frame-aligned Segments; the EDL's `captions` (already laid out) give the head boxes for a pip."""
    per_seg: dict[int, list[Overlay]] = {}
    records: list[dict] = []
    seg_cut, t = [], title_s
    for seg in segments:
        seg_cut.append(t)
        t += seg.duration
    end = t
    seg_of = {sid: i for i, seg in enumerate(segments) for sid in seg.span_ids}
    for raw in edl.get("overlays") or []:
        o = normalize(raw)
        rec = {"id": o["id"], "placement": o["placement"], "prompt": o.get("prompt"), "image": o.get("image"),
               "src_t": o["src_t"], "dur": o["dur"], "t_in": None, "t_out": None, "shown": False}
        records.append(rec)
        src = run_dir / (o.get("image") or f"broll/{o['id']}.png")
        s = home_span(edl, o)
        if not src.is_file():
            rec["why"] = f"{src.name} not found"
            continue
        if s is None or s["id"] not in seg_of:
            rec["why"] = "the words it was on are no longer in the cut"
            continue
        i = seg_of[s["id"]]
        a = round(seg_cut[i] + min(segments[i].duration, max(0.0, o["src_t"] - segments[i].t_in)), 3)
        b = round(min(end, a + o["dur"]), 3)
        if b - a < 0.2:
            rec["why"] = "it would start at the very end of the cut"
            continue
        spot = low = None
        heads = [c["head_box"] for c in edl.get("captions") or [] if c.get("head_box") and c["t_in"] < b and c["t_out"] > a]
        if o["placement"] == "pip":
            spot = pip_spot(heads, frame)
            rec["spot"] = list(spot)
        elif o["placement"] == "upper":
            low = card_bottom(heads)
        png = compose(src, o["placement"], out_dir / f"broll_{o['id']}.png", frame, spot, low)
        rec.update({"t_in": a, "t_out": b, "shown": True, "span": s["id"], "png": png.as_posix()})
        for k, seg in enumerate(segments):
            c0, c1 = seg_cut[k], seg_cut[k] + seg.duration
            lo, hi = max(a, c0), min(b, c1)
            if hi - lo < 0.5 / seg.fps:
                continue
            per_seg.setdefault(seg.index, []).append(Overlay(png=png, x=0, y=0, t_in=round(lo - c0, 4),
                                                             t_out=round(hi - c0, 4),
                                                             fade_s=FADE_S if lo - a < 1e-3 or b - hi < 1e-3 else 0.0,
                                                             fade_in=lo - a < 1e-3, fade_out=b - hi < 1e-3))
    return per_seg, records
