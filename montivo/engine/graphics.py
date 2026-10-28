"""Graphics: caption and callout PNGs drawn with Pillow, burned in later by ffmpeg `overlay`.

This ffmpeg build (Homebrew 9.0.2 on Billy's Mac) has neither libass nor libfreetype, so `subtitles`/`ass` and
`drawtext` are not available. Pillow draws every caption state and callout as a transparent PNG with a fixed font
file, which is deterministic (same font file, same pixels) and keeps the layout maths in Python where the head
boxes are.

The look of the captions and callouts is the EDL's `look` (a person's choice in the editor's Style panel, or a
preset): `normalize_look` fills it from one of PRESETS and checks every value, `CaptionStyle.from_look` turns it into
the layout numbers. Without a `look` the render keeps the marker preset it always had.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
# key -> (name shown in the editor, file in montivo/fonts/); all SIL OFL 1.1 (montivo/fonts/licenses/). Inter Tight 800
# and Montserrat 900 are static instances of the Google Fonts variable fonts.
FONTS: dict[str, tuple[str, str]] = {
    "inter-tight": ("Inter Tight", "InterTight-800.ttf"),
    "montserrat": ("Montserrat Black", "Montserrat-900.ttf"),
    "anton": ("Anton", "Anton-Regular.ttf"),
    "poppins": ("Poppins Bold", "Poppins-Bold.ttf"),
    "dm-serif": ("DM Serif Display", "DMSerifDisplay-Regular.ttf"),
}
DEFAULT_FONT = "inter-tight"
# first match wins; MONTIVO_FONT overrides the default font. The bundled Inter Tight 800 comes first so captions break
# and render the same on every machine.
BUNDLED_FONT = FONT_DIR / FONTS[DEFAULT_FONT][1]
FONT_CANDIDATES = [
    str(BUNDLED_FONT),
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
ACCENT = (255, 198, 41, 255)  # the brand's marker yellow #FFC629 (assets/brand/tokens.json): active word pill, names
CALLOUT_BG = (255, 198, 41, 240)

# the caption top may sit anywhere between the platform UI at the top and the bottom (captions.SAFE_TOP / SAFE_BOTTOM)
TOP_RANGE = (160, 1700)
ACTIVE_MODES = ("pill", "color", "karaoke", "none")
CASES = ("as_said", "upper")


@dataclass
class Look:
    """Every choice of the Style panel. `active`: how the word being spoken shows: on a pill (`active_bg` fill,
    `active_fg` text), in the `active_bg` colour, karaoke (every word said so far in `active_bg`), or not at all.
    `highlight` colours the names (captions.HIGHLIGHT, plus `highlight_add`, minus `highlight_off`); "" = no colour.
    `box` is a colour behind the whole line ("#000000B3"), "" none. `position` auto keeps the line off the head
    (captions.place_caption from `top`), fixed puts every line at `top`, a person's decision the review loop keeps."""
    preset: str = "marker"
    font: str = DEFAULT_FONT
    size: int = 84
    case: str = "as_said"
    max_words: int = 4
    text: str = "#FFFFFF"
    stroke: str = "#000000"
    stroke_w: int = 7
    shadow: bool = False
    active: str = "pill"
    active_fg: str = "#000000"
    active_bg: str = "#FFC629"
    highlight: str = "#FFC629"
    box: str = ""
    position: str = "auto"
    top: int = 1300
    callout_bg: str = "#FFC629"
    callout_fg: str = "#000000"
    highlight_add: list[str] = field(default_factory=list)
    highlight_off: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


# each preset is the marker defaults plus these; a person's own changes go on top of the preset they started from
PRESETS: dict[str, dict] = {
    "marker": {},
    "clean": {"stroke_w": 0, "shadow": True, "active": "color", "highlight": "", "size": 80},
    "box": {"font": "poppins", "size": 70, "stroke_w": 0, "box": "#000000C8", "active": "color", "highlight": ""},
    "karaoke": {"font": "montserrat", "size": 78, "stroke_w": 6, "active": "karaoke", "active_bg": "#FFC629",
                "highlight": ""},
    "bold_pop": {"font": "anton", "size": 124, "case": "upper", "max_words": 2, "stroke_w": 9, "active": "color",
                 "highlight": "#FF5A4E"},
    "minimal": {"font": "dm-serif", "size": 72, "stroke_w": 2, "shadow": True, "active": "none", "highlight": "",
                "top": 1400, "callout_bg": "#F3F0E8"},
}
PRESET_NAMES = {"marker": "Marker", "clean": "Clean", "box": "Box", "karaoke": "Karaoke", "bold_pop": "Bold pop",
                "minimal": "Minimal"}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def normalize_look(d: dict | None) -> Look:
    """The Look of an EDL `look` dict: its preset's values, then every key it sets, each checked. Raises ValueError
    for an unknown preset, key, font or mode, a colour that is not #RRGGBB(AA), or a number out of range."""
    d = dict(d or {})
    preset = d.pop("preset", None) or "marker"
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r} ({', '.join(PRESETS)})")
    vals = {**asdict(Look()), **PRESETS[preset], "preset": preset}
    known = {f.name for f in fields(Look)}
    for k, v in d.items():
        if k not in known:
            raise ValueError(f"unknown look key {k!r}")
        vals[k] = v
    for k in ("text", "stroke", "active_fg", "active_bg", "callout_bg", "callout_fg"):
        if not isinstance(vals[k], str) or not _HEX.match(vals[k]):
            raise ValueError(f"{k} must be a colour like #FFC629, not {vals[k]!r}")
    for k in ("highlight", "box"):
        if vals[k] and (not isinstance(vals[k], str) or not _HEX.match(vals[k])):
            raise ValueError(f"{k} must be a colour like #FFC629 or empty, not {vals[k]!r}")
    if vals["font"] not in FONTS:
        raise ValueError(f"unknown font {vals['font']!r} ({', '.join(FONTS)})")
    if vals["active"] not in ACTIVE_MODES:
        raise ValueError(f"active must be one of {', '.join(ACTIVE_MODES)}")
    if vals["case"] not in CASES:
        raise ValueError(f"case must be one of {', '.join(CASES)}")
    if vals["position"] not in ("auto", "fixed"):
        raise ValueError("position must be auto or fixed")
    for k, lo, hi in (("size", 40, 160), ("stroke_w", 0, 16), ("max_words", 1, 6), ("top", *TOP_RANGE)):
        try:
            vals[k] = int(vals[k])
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number")
        if not lo <= vals[k] <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}, not {vals[k]}")
    vals["shadow"] = bool(vals["shadow"])
    for k in ("highlight_add", "highlight_off"):
        if not isinstance(vals[k], list) or not all(isinstance(w, str) for w in vals[k]):
            raise ValueError(f"{k} must be a list of words")
        vals[k] = sorted({w.strip().lower() for w in vals[k] if w.strip()})
    return Look(**vals)


def rgba(hexstr: str) -> tuple[int, int, int, int]:
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) if len(h) == 8 else 255)


def font_path(key: str = DEFAULT_FONT) -> str | None:
    if key != DEFAULT_FONT:
        p = FONT_DIR / FONTS[key][1]
        return str(p) if p.is_file() else font_path()
    env = os.environ.get("MONTIVO_FONT")
    for cand in ([env] if env else []) + FONT_CANDIDATES:
        p = Path(cand).expanduser()
        if p.is_file():
            return str(p)
    return None


@lru_cache(maxsize=64)
def font(size: int, key: str = DEFAULT_FONT) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    p = font_path(key)
    return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)


def text_w(text: str, size: int, key: str = DEFAULT_FONT) -> int:
    l, _, r, _ = font(size, key).getbbox(text)
    return int(r - l)


@dataclass
class CaptionStyle:
    size: int = 84
    stroke: int = 7
    space: int = 22  # px between words
    pad_x: int = 18  # active-word pill padding
    pad_y: int = 10
    max_w: int = 920  # widest line in a 1080 px frame
    line_h: int = 0  # filled from the font
    font: str = DEFAULT_FONT
    upper: bool = False
    look: Look | None = None  # colours and modes for draw_caption; None = the marker look

    @classmethod
    def from_look(cls, look: Look) -> "CaptionStyle":
        k = look.size / 84
        box = 1.6 if look.box else 1.0  # a boxed line needs more room around the words
        return cls(size=look.size, stroke=look.stroke_w, space=max(8, round(22 * k)), pad_x=round(18 * k * box),
                   pad_y=round(10 * k * box), font=look.font, upper=look.case == "upper", look=look)

    def shown(self, w: str) -> str:
        return w.upper() if self.upper else w

    def height(self) -> int:
        a, d = font(self.size, self.font).getmetrics()
        return a + d + 2 * self.pad_y + 2 * self.stroke


def line_width(words: list[str], style: CaptionStyle) -> int:
    return (sum(text_w(style.shown(w), style.size, style.font) for w in words) + style.space * max(0, len(words) - 1)
            + 2 * (style.pad_x + style.stroke))


def draw_caption(words: list[str], active: int | None, highlight: list[bool], style: CaptionStyle, out: Path) -> tuple[int, int]:
    """One caption line as a transparent PNG in the style's look. The marker look (the default): white words with a
    black stroke, names in the accent colour, the word being spoken (`active`) in black on an accent pill.
    Returns (w, h)."""
    look = style.look or Look()
    f = font(style.size, style.font)
    w, h = line_width(words, style), style.height()
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if look.box:
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=max(8, h // 4), fill=rgba(look.box))
    ascent, _ = f.getmetrics()
    y = style.pad_y + style.stroke
    text_c, stroke_c, act_bg, act_fg = rgba(look.text), rgba(look.stroke), rgba(look.active_bg), rgba(look.active_fg)
    hl_c = rgba(look.highlight) if look.highlight else None
    shown = [style.shown(x) for x in words]
    xs, x = [], style.pad_x + style.stroke
    for word in shown:
        l, _, r, _ = f.getbbox(word)
        xs.append((x, l, int(r - l)))
        x += int(r - l) + style.space
    if look.shadow:
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        off = max(2, style.size // 24)
        for word, (x, l, _) in zip(shown, xs):
            sd.text((x - l, y + off), word, font=f, fill=(0, 0, 0, 230), stroke_width=style.stroke + max(2, style.size // 20),
                    stroke_fill=(0, 0, 0, 230))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(max(2, style.size // 18))))
        d = ImageDraw.Draw(img)
    for i, (word, (x, l, ww)) in enumerate(zip(shown, xs)):
        stroke = {"stroke_width": style.stroke, "stroke_fill": stroke_c} if style.stroke else {}
        if i == active and look.active == "pill":
            d.rounded_rectangle([x - style.pad_x, y - style.pad_y + 4, x + ww + style.pad_x, y + ascent + style.pad_y + 2],
                                radius=max(8, round(18 * style.size / 84)), fill=act_bg)
            d.text((x - l, y), word, font=f, fill=act_fg)
            continue
        said = active is not None and (i == active if look.active == "color" else look.active == "karaoke" and i <= active)
        fill = act_bg if said else hl_c if (hl_c and highlight[i]) else text_c
        d.text((x - l, y), word, font=f, fill=fill, **stroke)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return w, h


def callout_size(text: str, size: int = 58, key: str = DEFAULT_FONT) -> tuple[int, int]:
    f = font(size, key)
    a, dsc = f.getmetrics()
    return text_w(text, size, key) + 2 * 34, a + dsc + 2 * 20


def draw_callout(text: str, out: Path, size: int = 58, look: Look | None = None) -> tuple[int, int]:
    """A callout pill: text in `callout_fg` on `callout_bg` (black on the accent colour by default) with a thin
    outline in the text colour, in the look's font."""
    look = look or Look()
    f = font(size, look.font)
    w, h = callout_size(text, size, look.font)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = rgba(look.callout_bg)
    fg = rgba(look.callout_fg)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=bg[:3] + (min(bg[3], 240),), outline=fg, width=4)
    l, _, _, _ = f.getbbox(text)
    d.text((34 - l, 20), text, font=f, fill=fg)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return w, h
