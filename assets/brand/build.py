"""Build the Montivo brand files from one source: symbol, outlined wordmark, lockups and icons as SVG, then PNG exports.

    python3 assets/brand/build.py            # SVG only (needs fonttools + uharfbuzz)
    python3 assets/brand/build.py --png      # also PNG/ICO exports through a headless Chromium (CHROME=... to pick one)

The symbol is an in-point and an out-point bracket with a marker-yellow V between them: it reads as an M, and as [v], a
version between an in and an out, which is the loop (v1, v2, v3) the product runs. It is drawn on a 64 grid; the
brackets are 8 units thick, the same weight as the wordmark's stems at the lockup size. The wordmark is Inter Tight 700
(OFL, fonts/), shaped with HarfBuzz so the font's kerning applies, tracked -0.02 em and outlined, so no file depends on
the font being installed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
FONT = HERE / "fonts" / "InterTight-700.ttf"
SVG = HERE / "svg"
PNG = HERE / "png"
TOKENS = json.loads((HERE / "tokens.json").read_text())
C = {k: v["hex"] for k, v in TOKENS["color"].items()}

# ------------------------------------------------------------------ geometry (64 grid)
BRACKETS = "M6 6H24V14H14V50H24V58H6Z M58 6H40V14H50V50H40V58H58Z"
VEE = "M14 14H24L32 28L40 14H50L32 44Z"
MARK_BOX = (6, 6, 52, 52)  # tight bounds of the drawn symbol
WORD = "montivo"
WORD_SIZE = 56  # font size in grid units: stems come out at the brackets' 8-unit weight
TRACKING = -0.02  # em
BASELINE = 58  # the wordmark sits on the brackets' bottom edge
GAP = 15  # between the symbol and the first letter's ink
STACK_SIZE = 36  # the stacked lockup's wordmark: about 2.5 symbol widths


def wordmark_path(size: float = WORD_SIZE, x0: float = 0, baseline: float = 0) -> tuple[str, tuple[float, float, float, float]]:
    """The wordmark as one SVG path in user units, and its ink bounds (xmin, ymin, xmax, ymax)."""
    blob = hb.Blob.from_file_path(str(FONT))
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(WORD)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": False})
    tt = TTFont(FONT)
    upm = tt["head"].unitsPerEm
    gs = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    s = size / upm
    pen = SVGPathPen(gs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    bounds = BoundsPen(gs)
    x = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = order[info.codepoint]
        t = (s, 0, 0, -s, x0 + (x + pos.x_offset) * s, baseline - pos.y_offset * s)
        gs[name].draw(TransformPen(pen, t))
        gs[name].draw(TransformPen(bounds, t))
        x += pos.x_advance + TRACKING * upm
    return pen.getCommands(), bounds.bounds


def svg(view: tuple[float, float, float, float], body: str, title: str) -> str:
    x, y, w, h = (round(v, 2) for v in view)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{title}"><title>{title}</title>{body}</svg>\n')


def symbol_body(bracket: str, vee: str) -> str:
    return f'<path fill="{bracket}" d="{BRACKETS}"/><path fill="{vee}" d="{VEE}"/>'


def tile_body(tile: str, bracket: str, vee: str, size: float = 64, radius: float = 14.4, fill: float = 0.64) -> str:
    """The symbol centred on a rounded square: `fill` is the share of the tile the 52-unit mark spans."""
    k = size * fill / MARK_BOX[2]
    off = (size - MARK_BOX[2] * k) / 2 - MARK_BOX[0] * k
    return (f'<rect width="{size}" height="{size}" rx="{radius}" fill="{tile}"/>'
            f'<g transform="translate({off:.3f} {off:.3f}) scale({k:.4f})">{symbol_body(bracket, vee)}</g>')


# colour schemes: (brackets, V, wordmark)
SCHEMES = {
    "dark": (C["paper"], C["marker"], C["paper"]),       # on ink and dark imagery (primary)
    "light": (C["ink"], C["marker"], C["ink"]),          # on paper and white, at 32 px and up
    "mono-ink": (C["ink"], C["ink"], C["ink"]),          # one-colour print, small sizes on light
    "mono-paper": (C["paper"], C["paper"], C["paper"]),  # one-colour on dark photos
}


def build_svg() -> list[Path]:
    SVG.mkdir(exist_ok=True)
    out: list[Path] = []

    def write(name: str, text: str) -> None:
        p = SVG / name
        p.write_text(text)
        out.append(p)

    wm_d, (bx0, by0, bx1, by1) = wordmark_path()
    word_x = MARK_BOX[0] + MARK_BOX[2] + GAP - bx0  # first letter's ink starts GAP after the mark
    lock_d, (lx0, ly0, lx1, ly1) = wordmark_path(x0=word_x, baseline=BASELINE)
    assert ly0 >= MARK_BOX[1], "the wordmark rises above the symbol"
    # round letters overshoot the baseline by a hair; the view keeps them
    lock_view = (MARK_BOX[0], MARK_BOX[1], lx1 - MARK_BOX[0], max(ly1, BASELINE) - MARK_BOX[1])

    # stacked: symbol above, a smaller wordmark centred under it
    _, (tx0, ty0, tx1, _) = wordmark_path(size=STACK_SIZE)
    stack_w = tx1 - tx0
    stack_base = MARK_BOX[1] + MARK_BOX[3] + 8 - ty0
    stack_d, (_, _, _, sy1) = wordmark_path(size=STACK_SIZE, x0=-tx0 - stack_w / 2, baseline=stack_base)
    mark_shift = -(MARK_BOX[0] + MARK_BOX[2] / 2)  # centre the symbol on x = 0

    for scheme, (br, vee, ink) in SCHEMES.items():
        write(f"montivo-symbol-{scheme}.svg", svg(MARK_BOX, symbol_body(br, vee), "Montivo"))
        write(f"montivo-wordmark-{scheme}.svg",
              svg((bx0, by0, bx1 - bx0, by1 - by0), f'<path fill="{ink}" d="{wm_d}"/>', "Montivo"))
        write(f"montivo-lockup-{scheme}.svg",
              svg(lock_view, symbol_body(br, vee) + f'<path fill="{ink}" d="{lock_d}"/>', "Montivo"))
        half = max(stack_w, MARK_BOX[2]) / 2
        write(f"montivo-stacked-{scheme}.svg",
              svg((-half, MARK_BOX[1], 2 * half, sy1 - MARK_BOX[1]),
                  f'<g transform="translate({mark_shift} 0)">{symbol_body(br, vee)}</g><path fill="{ink}" d="{stack_d}"/>',
                  "Montivo"))

    # app icons: ink tile (primary) and marker tile (avatars, social)
    write("montivo-app-icon.svg", svg((0, 0, 64, 64), tile_body(C["ink"], C["paper"], C["marker"]), "Montivo"))
    write("montivo-app-icon-marker.svg", svg((0, 0, 64, 64), tile_body(C["marker"], C["ink"], C["ink"]), "Montivo"))
    # favicon: less padding so the mark survives 16 px
    write("favicon.svg", svg((0, 0, 64, 64), tile_body(C["ink"], C["paper"], C["marker"], radius=12, fill=0.8), "Montivo"))

    (SVG / "README.txt").write_text(
        "Generated by assets/brand/build.py from tokens.json and fonts/InterTight-700.ttf. Do not edit by hand.\n")
    return out


# ------------------------------------------------------------------ PNG exports through headless Chromium
PNG_EXPORTS = [
    # (svg, png name, width px, height px or None for the svg's aspect)
    ("favicon.svg", "favicon-16.png", 16, 16),
    ("favicon.svg", "favicon-32.png", 32, 32),
    ("favicon.svg", "favicon-48.png", 48, 48),
    ("montivo-app-icon.svg", "apple-touch-icon.png", 180, 180),
    ("montivo-app-icon.svg", "montivo-app-icon-512.png", 512, 512),
    ("montivo-app-icon.svg", "montivo-app-icon-1024.png", 1024, 1024),
    ("montivo-app-icon-marker.svg", "montivo-app-icon-marker-1024.png", 1024, 1024),
    ("montivo-lockup-dark.svg", "montivo-lockup-dark.png", None, 256),
    ("montivo-lockup-light.svg", "montivo-lockup-light.png", None, 256),
    ("montivo-stacked-dark.svg", "montivo-stacked-dark.png", None, 512),
    ("montivo-stacked-light.svg", "montivo-stacked-light.png", None, 512),
    ("montivo-symbol-dark.svg", "montivo-symbol-dark-512.png", 512, 512),
    ("montivo-symbol-light.svg", "montivo-symbol-light-512.png", 512, 512),
]

# the compositions page (src/compositions.html), one frame per #hash
COMPOSITIONS = [
    ("social", "montivo-social-preview.png", 1280, 640),
    ("thumb", "montivo-thumbnail-1920x1080.png", 1920, 1080),
    ("title-16x9", "montivo-title-16x9.png", 1920, 1080),
    ("end-16x9", "montivo-end-16x9.png", 1920, 1080),
    ("title-9x16", "montivo-title-9x16.png", 1080, 1920),
    ("end-9x16", "montivo-end-9x16.png", 1080, 1920),
    ("sheet", "montivo-brand-sheet.png", 1600, 880),
]


def find_chrome() -> str:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    cache = Path.home() / "Library/Caches/ms-playwright"
    for p in sorted(cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"), reverse=True):
        return str(p)
    for name in ("chromium", "google-chrome", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    sys.exit("no Chromium found: set CHROME=/path/to/chrome")


def screenshot(chrome: str, html: Path, out: Path, w: int, h: int, transparent: bool = True) -> None:
    args = [chrome, "--headless", "--hide-scrollbars", "--allow-file-access-from-files", "--force-device-scale-factor=1",
            f"--window-size={w},{h}", f"--screenshot={out}"]
    if transparent:
        args.append("--default-background-color=00000000")
    subprocess.run(args + [html.as_uri()], check=True, capture_output=True)


def export_png() -> None:
    from PIL import Image  # only the exports need Pillow

    chrome = find_chrome()
    PNG.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for src, name, w, h in PNG_EXPORTS:
            vb = [float(v) for v in (SVG / src).read_text().split('viewBox="')[1].split('"')[0].split()]
            if w is None:
                w = round(h * vb[2] / vb[3])
            page = Path(tmp) / f"{name}.html"
            page.write_text(f'<!doctype html><html><body style="margin:0;background:transparent">'
                            f'<img src="{(SVG / src).as_uri()}" style="display:block;width:{w}px;height:{h}px"></body></html>')
            screenshot(chrome, page, PNG / name, w, h)
            print(f"png {name} {w}x{h}")
    page = HERE / "src" / "compositions.html"
    for frame, name, w, h in COMPOSITIONS:
        args = [chrome, "--headless", "--hide-scrollbars", "--allow-file-access-from-files", "--force-device-scale-factor=1",
                f"--window-size={w},{h}", f"--screenshot={PNG / name}", "--virtual-time-budget=2000", f"{page.as_uri()}#{frame}"]
        subprocess.run(args, check=True, capture_output=True)
        print(f"png {name} {w}x{h}")
    ico = [Image.open(PNG / f"favicon-{s}.png") for s in (16, 32, 48)]
    ico[2].save(PNG / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=ico[:2])
    print("ico favicon.ico")
    # the editor UI serves these from web/public (vite copies them into montivo/server/static)
    public = HERE.parent.parent / "web" / "public"
    public.mkdir(exist_ok=True)
    for src in (SVG / "favicon.svg", SVG / "montivo-lockup-dark.svg", PNG / "favicon.ico", PNG / "apple-touch-icon.png"):
        shutil.copy(src, public / src.name)
        print(f"web/public/{src.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--png", action="store_true", help="also export PNG and ICO through headless Chromium")
    a = ap.parse_args()
    for p in build_svg():
        print(f"svg {p.relative_to(HERE)}")
    if a.png:
        export_png()


if __name__ == "__main__":
    main()
