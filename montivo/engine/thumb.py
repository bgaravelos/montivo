"""Thumb: the designed cover of a version, a clean still with a headline laid out around the speaker's head.

Templates (Pillow, the brand's fonts and colours, nothing generated):
- `frame`: the still as it is (the old cover, without the captions of that second).
- `headline`: the face punched in (the head about 30 % of the frame height), an ink gradient at the top and the
  headline over it, one word on a marker block.
- `sticker`: the face larger, the edges darkened, the headline on a marker sticker tilted 3 degrees above the head.
- `cutout`: a giant headline behind the head: SAM 3.1 segments the person in that one still (`still_mask`, one image
  request, about $0.01, cached per still) and the person is laid back over the text, the background darkened.

Every layout keeps the text inside the Instagram profile grid's 3:4 window and off the head box (the caption layout's
head box of the line nearest that second, the same box the captions keep clear of), and reports measured checks: text
in the grid window, off the face, the cap height at grid-tile size (about 130 px wide on a phone), and for the cutout
how much of the text the person leaves visible.

`design` is the agent: Muse Spark picks the exact still from a numbered contact sheet of candidates around its cover
moments (eyes open, mouth closed or smiling: the 1 fps video it watches cannot), writes three headlines with the word to
highlight and suggests a template; the cover is rendered, then Spark looks at the cover and its grid tile at real size
and passes it or names one fix (a shorter headline, another template, another still), applied once. About $0.03-0.05.
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ..config import SAM_MODEL, sam_cost
from ..ledger import Entry, Timer
from .api import dump_json, stream_responses
from .frame import with_aspect
from .graphics import font
from .look import frame_at, layout

TEMPLATES = ("frame", "headline", "sticker", "cutout")
INK, PAPER, MARKER = (20, 20, 20), (243, 240, 232), (255, 198, 41)  # assets/brand/tokens.json
TILE_W = 130  # a profile-grid tile on a phone, in px
TILE_CAP_MIN = 9.0  # cap height at tile size below which a headline stops reading
WORD_VISIBLE = 0.8  # the cutout: the least of any one word the person may leave visible
HEAD_GAP = 36


# --------------------------------------------------------------------------- geometry

def grid_window(w: int, h: int) -> tuple[int, int, int, int]:
    """The profile grid's 3:4 window of a w x h cover (export.grid_crop as a box)."""
    if w * 4 <= h * 3:
        gh = w * 4 // 3 // 2 * 2
        return 0, (h - gh) // 2, w, (h + gh) // 2
    gw = h * 3 // 4 // 2 * 2
    return (w - gw) // 2, 0, (w + gw) // 2, h


_layout_cache: dict[tuple, list[dict]] = {}


def head_at(run_dir: Path, edl: dict, t: float, edl_key: tuple | None = None) -> list[float] | None:
    """The head box (output pixels of the EDL's frame) of the caption line nearest cut second t."""
    key = edl_key or (str(run_dir), json.dumps(edl.get("spans")), (edl.get("target") or {}).get("aspect"))
    if key not in _layout_cache:
        _layout_cache.clear()
        _layout_cache[key] = [c for c in layout(run_dir, edl).get("captions") or [] if c.get("head_box")]
    lines = _layout_cache[key]
    if not lines:
        return None
    best = min(lines, key=lambda c: 0.0 if c["t_in"] <= t < c["t_out"] else min(abs(t - c["t_in"]), abs(t - c["t_out"])))
    return [float(v) for v in best["head_box"]]


def place(still: Image.Image, head: list[float], z: float, cy: float, cx: float | None = None) -> tuple[Image.Image, list[float]]:
    """The still scaled by z (>= 1) and cropped back to its size so the head's centre lands at (cx, cy) as near as the
    picture allows; returns the image and the head box in it."""
    w, h = still.size
    z = max(1.0, z)
    big = still.resize((round(w * z), round(h * z)), Image.LANCZOS) if z > 1.0 else still
    hx, hy = (head[0] + head[2]) / 2 * z, (head[1] + head[3]) / 2 * z
    x0 = min(max(0.0, hx - (cx if cx is not None else w / 2)), big.width - w)
    y0 = min(max(0.0, hy - cy), big.height - h)
    img = big.crop((round(x0), round(y0), round(x0) + w, round(y0) + h))
    if z > 1.15:
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    return img, [head[0] * z - x0, head[1] * z - y0, head[2] * z - x0, head[3] * z - y0]


def place_mask(mask: Image.Image, head: list[float], z: float, cy: float, size: tuple[int, int]) -> Image.Image:
    """The person mask through the same scale and crop as `place`."""
    m = mask.resize(size, Image.BILINEAR) if mask.size != size else mask
    img, _ = place(m.convert("RGB"), head, z, cy)
    return img.convert("L")


def zoom_for(head: list[float], h: int, frac: float, cap: float = 1.7) -> float:
    return min(cap, max(1.0, frac * h / max(1.0, head[3] - head[1])))


# --------------------------------------------------------------------------- text

def words_of(text: str, upper: bool = True) -> list[str]:
    ws = [w for w in re.split(r"\s+", text.strip()) if w]
    return [w.upper() for w in ws] if upper else ws


def greedy(widths: list[float], sp: float, max_w: float) -> list[int]:
    """Line breaks (start indexes) of a greedy fill to max_w."""
    starts, cw = [0], 0.0
    for k, ww in enumerate(widths):
        add = ww + (sp if k > starts[-1] else 0)
        if k > starts[-1] and cw + add > max_w:
            starts.append(k)
            cw = ww
        else:
            cw += add
    return starts


def wrap(words: list[str], size: int, key: str, max_w: int, max_lines: int) -> list[list[str]] | None:
    """The words in as few lines as fit max_w at this size (None past max_lines), balanced: the narrowest width that
    keeps that line count."""
    f = font(size, key)
    sp = f.getlength(" ")
    widths = [f.getlength(w) for w in words]
    if not words or max(widths) > max_w:
        return None
    n = len(greedy(widths, sp, max_w))
    if n > max_lines:
        return None
    lo, hi = max(widths), float(max_w)
    for _ in range(14):
        mid = (lo + hi) / 2
        if len(greedy(widths, sp, mid)) <= n:
            hi = mid
        else:
            lo = mid
    starts = greedy(widths, sp, hi) + [len(words)]
    return [words[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]


def line_w(line: list[str], f) -> float:
    return f.getlength(" ".join(line))


def fit(words: list[str], key: str, max_w: int, sizes: range, max_lines: int) -> tuple[list[list[str]], int]:
    for s in sizes:
        lines = wrap(words, s, key, max_w, max_lines)
        if lines:
            return lines, s
    s = sizes[-1] if len(sizes) else 80
    return wrap(words, s, key, max_w, 99) or [words], s


def hi_index(words: list[str], highlight: str | None) -> int | None:
    if not highlight:
        return None
    want = re.sub(r"[^\w]", "", highlight).lower()
    for k, w in enumerate(words):
        if re.sub(r"[^\w]", "", w).lower() == want:
            return k
    return None


def draw_lines(d: ImageDraw.ImageDraw, lines: list[list[str]], size: int, key: str, top: float, cx: float,
               hi: int | None, fill, hi_fill, hi_bg=None, lead: float = 1.0, word_boxes: list | None = None) -> list[float]:
    """Centred lines from `top`; the word at flat index `hi` in hi_fill (on a hi_bg block when given). The text box;
    each word's box goes into `word_boxes` when given."""
    f = font(size, key)
    asc, _ = f.getmetrics()
    cap = f.getbbox("H")
    sp = f.getlength(" ")
    k, y = 0, top
    box = [1e9, 1e9, -1e9, -1e9]
    for ln in lines:
        pad = size * 0.14
        extra = 2 * pad if hi_bg and hi is not None and k <= hi < k + len(ln) else 0
        lw = line_w(ln, f) + extra
        x = cx - lw / 2
        for w in ln:
            ww = f.getlength(w)
            if k == hi and hi_bg:
                x += pad
                d.rounded_rectangle((x - pad, y + cap[1] - pad, x + ww + pad, y + cap[3] + pad), radius=size * 0.12, fill=hi_bg)
            d.text((x, y), w, font=f, fill=hi_fill if k == hi else fill)
            if word_boxes is not None:
                word_boxes.append((round(x), round(y + cap[1]), round(x + ww), round(y + cap[3])))
            x += ww + sp + (pad if k == hi and hi_bg else 0)
            k += 1
        box = [min(box[0], cx - lw / 2), min(box[1], y + cap[1]), max(box[2], cx + lw / 2), max(box[3], y + cap[3])]
        y += size * lead
    return box


def block_h(n: int, size: int, key: str, lead: float) -> float:
    cap = font(size, key).getbbox("H")
    return (n - 1) * size * lead + cap[3]


# --------------------------------------------------------------------------- templates

def render(still: Image.Image, head: list[float] | None, template: str, text: str, highlight: str | None = None,
           mask: Image.Image | None = None) -> tuple[Image.Image, dict]:
    """The cover of `still` (the clean frame, any aspect) as `template` with `text`; returns it and its layout record
    (text box, head box, measured checks)."""
    if template not in TEMPLATES:
        raise ValueError(f"template: one of {', '.join(TEMPLATES)}")
    w, h = still.size
    still = still.convert("RGB")
    head = head or [w * 0.3, h * 0.3, w * 0.7, h * 0.6]
    g = grid_window(w, h)
    words = words_of(text) if text.strip() else []
    if template == "frame" or not words:
        return still, {"template": "frame", "text_box": None, "head_box": head, "checks": []}
    # 9:16: below the grid window's top and the apps' top bar; a feed frame: its 6 % margin
    top = max(g[1] + int(0.04 * h), int(0.13 * h) + 20) if h > w * 1.4 else g[1] + int(0.06 * h)
    gw = g[2] - g[0]
    hi = hi_index(words, highlight)
    if template == "headline":
        img, info = _headline(still, head, words, hi, top, g, gw)
    elif template == "sticker":
        img, info = _sticker(still, head, words, hi, top, g, gw)
    else:
        if mask is None:
            raise NeedMask("the cutout template needs the person mask of this still")
        img, info = _cutout(still, head, words, hi, top, g, gw, mask)
    info.update(template=template, text=" ".join(words), highlight=words[hi] if hi is not None else None)
    info["checks"] = measured(info, g, w, h)
    return img, info


class NeedMask(Exception):
    pass


def _above_head(still, head, words, key, max_w, size_sets, max_lines, lead, frac, top, g, extra=lambda bh, size: bh,
                slack=0.15):
    """The largest text size and zoom that keep `extra(bh, size)` px of text above the head (from `top`, plus HEAD_GAP)
    and the head's bottom near the grid window: (lines, size, bh, image, head box)."""
    h = still.height
    last = None
    for sizes in size_sets:
        lines, size = fit(words, key, max_w, sizes, max_lines)
        bh = block_h(len(lines), size, key, lead)
        need = extra(bh, size)
        z = zoom_for(head, h, frac)
        while True:
            hh = (head[3] - head[1]) * z
            img, hb = place(still, head, z, top + need + HEAD_GAP + hh / 2)
            last = (lines, size, bh, img, hb)
            if hb[1] >= top + need + HEAD_GAP - 1 and hb[3] <= g[3] + hh * slack:
                return last
            if z <= 1.0:
                break
            z = max(1.0, z - 0.05)
    return last


def _headline(still, head, words, hi, top, g, gw):
    w, h = still.size
    key, lead = "inter-tight", 1.04
    lines, size, bh, img, hb = _above_head(still, head, words, key, int(gw * 0.86), (range(190, 100, -8), range(100, 70, -6)),
                                           3, lead, 0.40, top, g)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    fade_to = min(h, int(top + bh + HEAD_GAP + 140))
    grad = Image.linear_gradient("L").resize((1, 256))
    alpha = Image.new("L", (w, h), 0)
    solid = max(0, int(top + bh))
    alpha.paste(235, (0, 0, w, solid))
    if fade_to > solid:
        ramp = grad.transpose(Image.FLIP_TOP_BOTTOM).resize((w, fade_to - solid)).point(lambda v: v * 235 // 255)
        alpha.paste(ramp, (0, solid))
    img = Image.composite(Image.new("RGB", (w, h), INK), img, alpha)
    d = ImageDraw.Draw(img)
    box = draw_lines(d, lines, size, key, top, w / 2, hi, PAPER, INK, MARKER, lead)
    return img, {"text_box": box, "head_box": hb, "size": size, "lines": [" ".join(l) for l in lines], "font": key}


def _sticker(still, head, words, hi, top, g, gw):
    w, h = still.size
    key, lead, pad, tilt = "inter-tight", 1.06, 44, 3
    lines, size, bh, img, hb = _above_head(still, head, words, key, int(gw * 0.78) - 2 * pad,
                                           (range(150, 84, -6), range(84, 60, -4)), 3, lead, 0.46, top, g,
                                           extra=lambda bh, size: bh + 2 * pad + gw * 0.05, slack=0.2)
    vig = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vig).ellipse((-w * 0.25, h * 0.12, w * 1.25, h * 1.05), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(w * 0.15))
    dark = ImageEnhance.Brightness(img.filter(ImageFilter.GaussianBlur(5))).enhance(0.45)
    img = Image.composite(ImageEnhance.Contrast(img).enhance(1.08), dark, vig)
    f = font(size, key)
    tw = max(line_w(l, f) for l in lines)
    sw, shh = int(tw + 2 * pad), int(bh + 2 * pad)
    st = Image.new("RGBA", (sw, shh), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    sd.rounded_rectangle((0, 0, sw - 1, shh - 1), radius=int(size * 0.3), fill=MARKER + (255,))
    draw_lines(sd, lines, size, key, pad - f.getbbox("H")[1], sw / 2, hi, INK, MARKER, INK, lead)
    st = st.rotate(tilt, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", st.size, (0, 0, 0, 0))
    shadow.putalpha(st.getchannel("A").point(lambda v: v * 110 // 255).filter(ImageFilter.GaussianBlur(14)))
    x, y = int((w - st.width) / 2), int(top)
    img = img.convert("RGBA")
    img.alpha_composite(shadow, (x + 8, y + 14))
    img.alpha_composite(st, (x, y))
    return img.convert("RGB"), {"text_box": [x, y, x + st.width, y + st.height], "head_box": hb, "size": size,
                                "lines": [" ".join(l) for l in lines], "font": key}


def _cutout(still, head, words, hi, top, g, gw, mask):
    w, h = still.size
    key, lead = "anton", 0.98
    lines, size = fit(words, key, int(gw * 0.92), range(420, 150, -10), 2 if len(words) <= 4 else 3)
    bh = block_h(len(lines), size, key, lead)
    z = zoom_for(head, h, 0.40)
    hh = (head[3] - head[1]) * z
    shift = font(size, key).getbbox("H")[1]
    for overlap in (0.4, 0.3, 0.2, 0.1, 0.0, -0.1):  # the head over the lower part of the text, less until every word reads
        head_top = top + bh - shift - overlap * bh
        img, hb = place(still, head, z, head_top + hh / 2)
        person = place_mask(mask, head, z, head_top + hh / 2, (w, h)).filter(ImageFilter.GaussianBlur(1.2))
        bg = ImageEnhance.Brightness(img.filter(ImageFilter.GaussianBlur(4))).enhance(0.42)
        bg = ImageEnhance.Color(bg).enhance(0.7)
        text = Image.new("L", (w, h), 0)
        layer = Image.new("RGB", (w, h), INK)
        box = draw_lines(ImageDraw.Draw(layer), lines, size, key, top - shift, w / 2, hi, PAPER, MARKER, None, lead)
        boxes: list = []
        draw_lines(ImageDraw.Draw(text), lines, size, key, top - shift, w / 2, None, 255, 255, None, lead, boxes)
        shown = Image.composite(Image.new("L", (w, h), 0), text, person)  # the text the person leaves visible
        per_word = []
        for b in boxes:
            tpx = sum(text.crop(b).histogram()[128:])
            per_word.append(sum(shown.crop(b).histogram()[128:]) / tpx if tpx else 1.0)
        tpx = sum(text.histogram()[128:])
        visible = sum(shown.histogram()[128:]) / tpx if tpx else 1.0
        if visible >= 0.8 and min(per_word) >= WORD_VISIBLE:
            break
    out = Image.composite(layer, bg, text)
    out = Image.composite(ImageEnhance.Contrast(img).enhance(1.1), out, person)
    return out, {"text_box": box, "head_box": hb, "size": size, "lines": [" ".join(l) for l in lines], "font": key,
                 "text_visible": round(visible, 3), "word_visible": round(min(per_word), 3)}


def measured(info: dict, g: tuple[int, int, int, int], w: int, h: int) -> list[dict]:
    """The checks a person could measure on the cover: the text inside the grid window, off the face, readable at tile
    size, and (cutout) left visible by the person."""
    out = []
    tb, hb = info.get("text_box"), info.get("head_box")
    if not tb:
        return out
    inside = tb[0] >= g[0] - 2 and tb[1] >= g[1] - 2 and tb[2] <= g[2] + 2 and tb[3] <= g[3] + 2
    out.append({"id": "in_grid", "ok": inside, "detail": "text inside the profile grid's 3:4 window" if inside
                else "part of the text falls outside the profile grid's 3:4 window"})
    if info["template"] != "cutout" and hb:
        ov = max(0.0, min(tb[2], hb[2]) - max(tb[0], hb[0])) * max(0.0, min(tb[3], hb[3]) - max(tb[1], hb[1]))
        out.append({"id": "off_face", "ok": ov <= 0, "detail": "text clear of the face" if ov <= 0 else "text over the face"})
    cap = font(info["size"], info["font"]).getbbox("H")
    tile = (cap[3] - cap[1]) * TILE_W / (g[2] - g[0])
    out.append({"id": "tile_size", "ok": tile >= TILE_CAP_MIN, "value": round(tile, 1),
                "detail": f"letters {tile:.0f} px tall on a {TILE_W} px grid tile"})
    if "text_visible" in info:
        v, wv = info["text_visible"], info.get("word_visible", 1.0)
        out.append({"id": "text_visible", "ok": v >= 0.72 and wv >= WORD_VISIBLE, "value": v,
                    "detail": f"{v * 100:.0f} % of the headline visible around the person, every word at least {wv * 100:.0f} %"})
    n = len(words_of(info.get("text") or ""))
    out.append({"id": "words", "ok": 2 <= n <= 6, "value": n, "detail": f"{n} words (2 to 6 read at a glance)"})
    return out


def tile(img: Image.Image) -> Image.Image:
    g = grid_window(*img.size)
    t = img.crop(g)
    return t.resize((TILE_W, round(TILE_W * t.height / t.width)), Image.LANCZOS)


# --------------------------------------------------------------------------- SAM 3.1 on one still

MASK_RE = re.compile(r"(\d+)<\|box;x1=(-?\d+);y1=(-?\d+);x2=(-?\d+);y2=(-?\d+);w=(\d+);h=(\d+)(?:;c=([0-9.]+))?\|>"
                     r"<\|mask;x=0;y=0;data=(\d+),(\d+),([!~][^|]+)\|>")
SAM_EDGE = 960  # the cookbook's longest edge


def decode_raster(payload: str, w: int, h: int) -> bytes:
    """A SAM mask payload as w x h bytes of 0 / 1, with Meta's parser (pip meta-sam-parser)."""
    try:
        from meta_sam_parser import SegmentationMask, decode_mask_to_raster
    except ImportError as e:
        raise SystemExit("the cutout cover needs Meta's SAM mask parser: pip install meta-sam-parser==0.1.0") from e
    enc = "lossless" if payload.startswith("~") else "one_bit"
    return decode_mask_to_raster(SegmentationMask(encoding=enc, payload=payload, width=w, height=h))


def parse_masks(text: str, scale: float) -> list[dict]:
    """The person records of a SAM image response: box (in the still's pixels) and the box-local raster."""
    out = []
    for m in MASK_RE.finditer(text):
        x1, y1, x2, y2 = (int(m.group(k)) for k in range(2, 6))
        mh, mw = int(m.group(9)), int(m.group(10))
        out.append({"id": m.group(1), "box": [x1 * scale, y1 * scale, (x2 + 1) * scale, (y2 + 1) * scale],
                    "c": float(m.group(8)) if m.group(8) else None, "mh": mh, "mw": mw, "payload": m.group(11)})
    return out


def mask_image(rec: dict, size: tuple[int, int]) -> Image.Image:
    raster = decode_raster(rec["payload"], rec["mw"], rec["mh"])
    local = Image.frombytes("L", (rec["mw"], rec["mh"]), bytes(255 if b else 0 for b in raster))
    x1, y1, x2, y2 = (round(v) for v in rec["box"])
    full = Image.new("L", size, 0)
    full.paste(local.resize((max(1, x2 - x1), max(1, y2 - y1)), Image.BILINEAR), (x1, y1))
    return full


def mask_path(run_dir: Path, n: int, t: float, aspect: str) -> Path:
    return run_dir / "ui" / "cache" / "cover" / f"mask_v{n}_{aspect.replace(':', 'x')}_{t:.2f}.png"


def still_mask(run_dir: Path, n: int, t: float, aspect: str, ledger, log=print) -> Path:
    """SAM 3.1 "person" on the still of version n at cut second t (one image request, cached): the person nearest the
    head box as a grey-level PNG the size of the cover."""
    dst = mask_path(run_dir, n, t, aspect)
    if dst.is_file():
        return dst
    edl = with_aspect(json.loads((run_dir / f"edl_v{n}.json").read_text()), aspect)
    still = frame_at(run_dir, edl, t).convert("RGB")
    head = head_at(run_dir, edl, t)
    scale = max(still.size) / SAM_EDGE
    small = still.resize((round(still.width / scale), round(still.height / scale)), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=92)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {"model": SAM_MODEL, "metadata": {"mask_encoding": "lossless"},
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "person"}, {"type": "input_image", "image_url": url}]}]}
    work = dst.parent / dst.stem
    work.mkdir(parents=True, exist_ok=True)
    dump_json(work / "request.json", {**body, "input": [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "person"}, {"type": "input_image", "image_url": f"<inline image, {len(url)} chars>"}]}]})
    text, status, usage = "", "ok", {}
    log(f"cover: SAM 3.1 person mask of v{n} at {t:.2f}s ...")
    with Timer() as tm:
        for ev in stream_responses(body, raw_sink=work / "events.sse", timeout_s=300):
            et = ev.get("type")
            if et == "response.output_text.delta":
                text += ev.get("delta") or ""
            elif et == "response.completed":
                usage = (ev.get("response") or {}).get("usage") or {}
            elif et in ("response.failed", "response.incomplete", "error"):
                status = et
    cost = sam_cost(1)
    recs = parse_masks(text, scale)
    ledger.add(Entry(stage="track.cover_mask", model=SAM_MODEL, seconds=tm.seconds, cost_usd=round(cost, 4), frames=1,
                     status=status, note=f"person on the v{n} cover still at {t:.2f}s: {len(recs)} masks"))
    if not recs:
        raise RuntimeError(f"SAM found no person in the still ({status}, {text[:200]!r})")

    def near(r):
        b = r["box"]
        if not head:
            return -(b[2] - b[0]) * (b[3] - b[1])
        return abs((b[0] + b[2]) / 2 - (head[0] + head[2]) / 2) - (min(b[3], head[3]) - max(b[1], head[1]))
    rec = min(recs, key=near)
    img = mask_image(rec, still.size)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst)
    dump_json(work / "result.json", {"t_s": t, "aspect": aspect, "persons": len(recs), "box": rec["box"], "c": rec["c"],
                                     "usage": usage, "cost_usd": round(cost, 4), "seconds": tm.seconds})
    log(f"cover: mask of {len(recs)} person(s), kept the one at the head, ${cost:.3f}, {tm.seconds}s")
    return dst


# --------------------------------------------------------------------------- one cover from a design

def cover_image(run_dir: Path, n: int, design: dict, aspect: str = "9:16") -> tuple[Image.Image, dict]:
    """The cover of version n for a design {"t_s", "template", "text", "highlight"} at that aspect ($0; the cutout
    needs its mask already made by `still_mask`, else NeedMask)."""
    edl = with_aspect(json.loads((run_dir / f"edl_v{n}.json").read_text()), aspect)
    t = float(design.get("t_s") or 0.0)
    still = frame_at(run_dir, edl, t).convert("RGB")
    template = design.get("template") or "frame"
    mask = None
    if template == "cutout":
        mp = mask_path(run_dir, n, t, aspect)
        if not mp.is_file():
            raise NeedMask(f"no person mask for v{n} at {t:.2f}s ({aspect}) yet")
        mask = Image.open(mp).convert("L")
    head = head_at(run_dir, edl, t)
    img, info = render(still, head, template, design.get("text") or "", design.get("highlight"), mask)
    info["t_s"] = t
    return img, info


# --------------------------------------------------------------------------- the agent

STILL_PROMPT = """These are numbered candidate stills from a vertical talking-head video, for its cover (the image people
see in the profile grid before they tap). Each number is in the yellow chip of its still.

1. Pick the ONE best still for a cover: eyes open and towards the camera, the mouth closed or smiling (not caught
mid-word in an odd shape), a confident or expressive look, sharp, no motion blur. Judge only what you see.
2. Write {k} cover headlines of 2 to 5 words each, in the language the speaker speaks. A headline is a promise or a
curiosity hook about what the video gives the viewer, NOT a caption line copied from the transcript and not clickbait
the video does not deliver. For each, name the single word to highlight (it must be one of the headline's words).
3. Suggest a template: "headline" (headline over the top of the frame, calm and clear), "sticker" (headline on a tilted
yellow sticker, playful), or "cutout" (huge headline behind the speaker's head, the boldest; needs a short headline of
2 to 4 words).

What the video says (its caption lines in order): {transcript}
The creator's brief: {brief}"""

STILL_SCHEMA = {"type": "object", "properties": {
    "still": {"type": "integer"}, "still_why": {"type": "string"},
    "headlines": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"}, "highlight": {"type": "string"}, "why": {"type": "string"}},
        "required": ["text", "highlight", "why"], "additionalProperties": False}},
    "template": {"type": "string", "enum": ["headline", "sticker", "cutout"]}, "template_why": {"type": "string"}},
    "required": ["still", "still_why", "headlines", "template", "template_why"], "additionalProperties": False}

CHECK_PROMPT = """Image 1 is a cover for a vertical short video; image 2 shows its profile-grid tile at the real size a
phone shows it (the middle tile of three). Judge it as a viewer scrolling a profile:
- readable_small: can the headline be read on the tile at that size (0-10)?
- face: is the face clear, sharp, with a good expression (0-10)?
- clear_of_face: does the text stay off the eyes and mouth?
- fits_video: does the headline make you want to watch this talking-head video, without lying (0-10)? Headline: "{text}".
Pass it when all three scores are 7 or more and the text is clear of the face. Otherwise name the one fix that would help
most: "shorter_text" (and write it in better_text, 2 to 4 words), "other_template", or "other_still"."""

CHECK_SCHEMA = {"type": "object", "properties": {
    "readable_small": {"type": "integer"}, "face": {"type": "integer"}, "clear_of_face": {"type": "boolean"},
    "fits_video": {"type": "integer"}, "pass": {"type": "boolean"},
    "fix": {"type": "string", "enum": ["none", "shorter_text", "other_template", "other_still"]},
    "better_text": {"type": "string"}, "note": {"type": "string"}},
    "required": ["readable_small", "face", "clear_of_face", "fits_video", "pass", "fix", "better_text", "note"],
    "additionalProperties": False}

OFFSETS = (-0.6, -0.36, -0.12, 0.12, 0.36, 0.6)


def candidates(run_dir: Path, edl: dict, moments: list[dict], lo: float, hi: float) -> list[dict]:
    """Stills around each moment (OFFSETS), clean, numbered from 1; close duplicates dropped."""
    out: list[dict] = []
    for mi, m in enumerate(moments[:3]):
        for off in OFFSETS:
            t = round(min(max(lo, float(m["t_s"]) + off), hi), 2)
            if any(abs(t - c["t_s"]) < 0.1 for c in out):
                continue
            out.append({"n": len(out) + 1, "t_s": t, "moment": mi})
    return out


def contact_sheet(run_dir: Path, edl: dict, cands: list[dict], dst: Path, tw: int = 216) -> Path:
    th = tw * 16 // 9
    cols = 6
    rows = (len(cands) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 8, rows * th + (rows + 1) * 8), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    f = font(44)
    for k, c in enumerate(cands):
        im = frame_at(run_dir, edl, c["t_s"]).convert("RGB").resize((tw, th), Image.LANCZOS)
        x, y = 8 + (k % cols) * (tw + 8), 8 + (k // cols) * (th + 8)
        sheet.paste(im, (x, y))
        label = str(c["n"])
        lw = f.getlength(label)
        d.rounded_rectangle((x + 6, y + 6, x + 6 + lw + 20, y + 62), radius=8, fill=MARKER)
        d.text((x + 16, y + 8), label, font=f, fill=INK)
    sheet.save(dst, quality=88)
    return dst


def tile_sheet(img: Image.Image, dst: Path) -> Path:
    """Three tiles in a row as a profile grid shows them, the cover in the middle."""
    t = tile(img)
    sheet = Image.new("RGB", (3 * TILE_W + 4 * 3, t.height + 6), (250, 250, 250))
    for k in range(3):
        if k == 1:
            sheet.paste(t, (3 + k * (TILE_W + 3), 3))
        else:
            ImageDraw.Draw(sheet).rectangle((3 + k * (TILE_W + 3), 3, 3 + k * (TILE_W + 3) + TILE_W - 1, 3 + t.height - 1),
                                            fill=(200, 200, 200))
    sheet.save(dst, quality=92)
    return dst


def transcript_of(edl: dict, limit: int = 1500) -> str:
    text = " | ".join(c["text"] for c in edl.get("captions") or [])
    return text[:limit] or "(no captions)"


def design(run_dir: Path, n: int, ledger, log=print, aspect: str = "9:16", k: int = 3) -> dict:
    """The agent's cover for version n: moments (cover.suggest, reused), the exact still and headlines from a contact
    sheet, a render, Spark's check of the cover at grid-tile size and one fix. Writes cover_vN.json's `design` and
    returns the whole record."""
    from .cover import suggest
    from .spark import Spark
    rec_path = run_dir / f"cover_v{n}.json"
    rec = json.loads(rec_path.read_text()) if rec_path.is_file() else {}
    if not rec.get("moments"):
        rec = suggest(run_dir, n, ledger, log)
    edl = json.loads((run_dir / f"edl_v{n}.json").read_text())
    render_info = edl.get("render") or {}
    dur = float(render_info.get("duration_s") or edl.get("runtime_s") or 0.0)
    skip = float((render_info.get("title") or {}).get("seconds") or 0.0)
    work = run_dir / "cover" / f"v{n}"
    work.mkdir(parents=True, exist_ok=True)
    cands = candidates(run_dir, edl, rec["moments"], skip + 0.05, max(skip, dur - 0.3))
    if not cands:
        raise SystemExit(f"cover: no moments to pick from in v{n}")
    sheet = contact_sheet(run_dir, edl, cands, work / "candidates.jpg")
    brief = ""
    rj = run_dir / "run.json"
    if rj.is_file():
        brief = str(json.loads(rj.read_text()).get("brief") or "")[:600]
    spark = Spark(run_dir, ledger, log=log)
    cost, secs = 0.0, 0.0
    pick = spark.call(f"cover_design_v{n}", STILL_PROMPT.format(k=k, transcript=transcript_of(edl), brief=brief or "(none)"),
                      STILL_SCHEMA, images=[sheet], max_output_tokens=4000, reasoning_effort="low", stage="spark.cover_design")
    cost, secs = cost + pick.cost_usd, secs + pick.seconds
    p = pick.parsed
    by_n = {c["n"]: c for c in cands}
    still = by_n.get(int(p.get("still") or 0)) or cands[0]
    heads = [{"text": " ".join(str(h.get("text") or "").split()), "highlight": str(h.get("highlight") or "").strip(),
              "why": str(h.get("why") or "").strip()} for h in p.get("headlines") or [] if str(h.get("text") or "").strip()]
    if not heads:
        heads = [{"text": "Watch this", "highlight": "", "why": "fallback"}]
    for h in heads:
        if hi_index(words_of(h["text"]), h["highlight"]) is None:
            h["highlight"] = ""
    order = [p.get("template") or "headline"] + [t for t in ("headline", "sticker", "cutout") if t != p.get("template")]
    still_order = [still] + [c for c in cands if c is not still and c["moment"] != still["moment"]][:2]
    cur = {"t_s": still["t_s"], "template": order[0], "text": heads[0]["text"], "highlight": heads[0]["highlight"]}
    attempts = []
    for attempt in range(2):
        if cur["template"] == "cutout":
            try:
                still_mask(run_dir, n, cur["t_s"], aspect, ledger, log)
            except (RuntimeError, SystemExit) as e:  # no person found, or no parser: the next template
                log(f"cover: cutout skipped ({str(e)[:120]})")
                order = [t for t in order if t != "cutout"]
                cur["template"] = order[0]
        img, info = cover_image(run_dir, n, cur, aspect)
        out = work / f"attempt{attempt + 1}.jpg"
        img.save(out, quality=92)
        ts = tile_sheet(img, work / f"attempt{attempt + 1}_tile.jpg")
        small = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
        small.save(work / f"attempt{attempt + 1}_small.jpg", quality=88)
        chk = spark.call(f"cover_check_v{n}_{attempt + 1}", CHECK_PROMPT.format(text=cur["text"]), CHECK_SCHEMA,
                         images=[work / f"attempt{attempt + 1}_small.jpg", ts], max_output_tokens=2000,
                         reasoning_effort="low", stage="spark.cover_check")
        cost, secs = cost + chk.cost_usd, secs + chk.seconds
        c = chk.parsed
        measured_ok = all(x["ok"] for x in info["checks"])
        score = int(c.get("readable_small") or 0) + int(c.get("face") or 0) + int(c.get("fits_video") or 0)
        passed = bool(c.get("pass")) and bool(c.get("clear_of_face")) and measured_ok
        attempts.append({**cur, "file": out.name, "check": c, "measured": info["checks"], "pass": passed, "score": score})
        log(f"cover: attempt {attempt + 1} {cur['template']} \"{cur['text']}\" at {cur['t_s']:.2f}s: "
            f"{'pass' if passed else 'no pass'} ({score}/30, fix {c.get('fix')})")
        if passed:
            break
        fix = c.get("fix") or "none"
        if not measured_ok and fix == "none":
            fix = "shorter_text"
        nxt = dict(cur)
        if fix == "shorter_text":
            better = " ".join(str(c.get("better_text") or "").split())
            short = [h for h in heads if len(words_of(h["text"])) < len(words_of(cur["text"]))]
            if better and 1 < len(words_of(better)) <= 4:
                nxt.update(text=better, highlight=next((w for w in words_of(better) if len(w) > 3), ""))
            elif short:
                nxt.update(text=short[0]["text"], highlight=short[0]["highlight"])
            else:
                nxt.update(template=order[1] if len(order) > 1 else cur["template"])
        elif fix == "other_template":
            nxt["template"] = next((t for t in order if t != cur["template"]), cur["template"])
        elif fix == "other_still":
            nxt["t_s"] = next((s["t_s"] for s in still_order if s["t_s"] != cur["t_s"]), cur["t_s"])
        if nxt == cur:
            break
        cur = nxt
    best = max(attempts, key=lambda a: (a["pass"], a["score"]))
    rec["design"] = {
        "aspect": aspect, "candidates": cands, "still": still["n"], "still_why": p.get("still_why"),
        "headlines": heads, "template": p.get("template"), "template_why": p.get("template_why"),
        "attempts": attempts, "final": {k2: best[k2] for k2 in ("t_s", "template", "text", "highlight")},
        "pass": best["pass"], "cost_usd": round(cost, 4), "seconds": round(secs, 1)}
    dump_json(rec_path, rec)
    log(f"cover: v{n} {best['template']} \"{best['text']}\" at {best['t_s']:.2f}s, "
        f"{'pass' if best['pass'] else 'best of ' + str(len(attempts))}, ${cost:.4f}")
    return rec
