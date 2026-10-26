"""Graphics: caption and callout PNGs drawn with Pillow, burned in later by ffmpeg `overlay`.

This ffmpeg build (Homebrew 9.0.2 on Billy's Mac) has neither libass nor libfreetype, so `subtitles`/`ass` and
`drawtext` are not available. Pillow draws every caption state and callout as a transparent PNG with a fixed font
file, which is deterministic (same font file, same pixels) and keeps the layout maths in Python where the head
boxes are.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# first match wins; MONTIVO_FONT overrides
FONT_CANDIDATES = [
    "~/Library/Fonts/InterTight-800.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
ACCENT = (255, 212, 0, 255)  # active word pill and highlighted names
CALLOUT_BG = (255, 212, 0, 240)


def font_path() -> str | None:
    env = os.environ.get("MONTIVO_FONT")
    for cand in ([env] if env else []) + FONT_CANDIDATES:
        p = Path(cand).expanduser()
        if p.is_file():
            return str(p)
    return None


@lru_cache(maxsize=32)
def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    p = font_path()
    return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)


def text_w(text: str, size: int) -> int:
    l, _, r, _ = font(size).getbbox(text)
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

    def height(self) -> int:
        a, d = font(self.size).getmetrics()
        return a + d + 2 * self.pad_y + 2 * self.stroke


def line_width(words: list[str], style: CaptionStyle) -> int:
    return sum(text_w(w, style.size) for w in words) + style.space * max(0, len(words) - 1) + 2 * (style.pad_x + style.stroke)


def draw_caption(words: list[str], active: int | None, highlight: list[bool], style: CaptionStyle, out: Path) -> tuple[int, int]:
    """One caption line as a transparent PNG: white words with a black stroke, names in the accent colour, the word
    being spoken (`active`) in black on an accent pill. Returns (w, h)."""
    f = font(style.size)
    w, h = line_width(words, style), style.height()
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ascent, _ = f.getmetrics()
    x = style.pad_x + style.stroke
    y = style.pad_y + style.stroke
    for i, word in enumerate(words):
        l, _, r, _ = f.getbbox(word)
        ww = int(r - l)
        if i == active:
            d.rounded_rectangle([x - style.pad_x, y - style.pad_y + 4, x + ww + style.pad_x, y + ascent + style.pad_y + 2],
                                radius=18, fill=ACCENT)
            d.text((x - l, y), word, font=f, fill=BLACK)
        else:
            d.text((x - l, y), word, font=f, fill=ACCENT if highlight[i] else WHITE, stroke_width=style.stroke,
                   stroke_fill=BLACK)
        x += ww + style.space
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return w, h


def callout_size(text: str, size: int = 58) -> tuple[int, int]:
    f = font(size)
    a, dsc = f.getmetrics()
    return text_w(text, size) + 2 * 34, a + dsc + 2 * 20


def draw_callout(text: str, out: Path, size: int = 58) -> tuple[int, int]:
    """A callout pill: black text on the accent colour with a thin black outline."""
    f = font(size)
    w, h = callout_size(text, size)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=CALLOUT_BG, outline=BLACK, width=4)
    l, _, _, _ = f.getbbox(text)
    d.text((34 - l, 20), text, font=f, fill=BLACK)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return w, h
