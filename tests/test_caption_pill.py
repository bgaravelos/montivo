"""The active word's pill is centred on the line's ink (a Discord report, 29/10: the pill sat high, about twice as
much yellow above the word as below it)."""
from PIL import Image

from montivo.engine.graphics import CaptionStyle, Look, draw_caption, pill_rows


def _yellow_and_ink_rows(png, x0, x1):
    im = Image.open(png).convert("RGBA")
    yellow, ink = [], []
    for y in range(im.height):
        row = [im.getpixel((x, y)) for x in range(x0, x1)]
        if any(p[3] > 200 and p[0] > 200 and p[1] > 150 and p[2] < 120 for p in row):
            yellow.append(y)
        if any(p[3] > 200 and p[0] < 60 and p[1] < 60 and p[2] < 60 for p in row):
            ink.append(y)
    return yellow, ink


def test_the_pill_has_as_much_yellow_above_the_word_as_below(tmp_path):
    style = CaptionStyle(look=Look())
    out = tmp_path / "line.png"
    w, h = draw_caption(["meta"], 0, [False], style, out)
    yellow, ink = _yellow_and_ink_rows(out, style.pad_x + style.stroke, w - style.pad_x - style.stroke)
    above, below = ink[0] - yellow[0], yellow[-1] - ink[-1]
    assert abs(above - below) <= 3, (above, below)  # was 31 / 10
    top, bottom = pill_rows(["meta"], style)
    assert 0 <= top and bottom < h and bottom - top == 100  # the pill's height at 84 px as before


def test_the_pill_keeps_one_place_along_a_line():
    style = CaptionStyle(look=Look())
    assert pill_rows(["Hi.", "I'm", "Billy."], style) == pill_rows(["Hi.", "I'm", "Billy."], style)
    big = CaptionStyle.from_look(Look(size=120, box="#000000"))
    top, bottom = pill_rows(["every", "single", "day."], big)
    assert 0 <= top and bottom <= big.height() - 1
