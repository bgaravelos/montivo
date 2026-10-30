"""A3: a caption line that place_caption could only put over the head (a close-up with no room under or above it at
full size) is tried again at smaller sizes before it gives up; every line placed before stays exactly where it was,
and the Live preview draws the smaller size the render used (the caption entry's `size`)."""
from types import SimpleNamespace

from montivo.engine.captions import (SAFE_BOTTOM, SAFE_TOP, CaptionStyle, at_size, build_overlays, fit_line,
                                     line_width, overlaps, place_caption, room_for)
from montivo.engine.preview import _line
from tests.test_captions import BILLY

STYLE = CaptionStyle()
TEXT = ["and", "so", "he", "always"]


def test_a_smaller_line_fits_under_a_head_that_leaves_too_little_room():
    head = [200, 300, 880, 1450]  # the full-size line is 137 px tall: under the head it would end at 1624 > 1620
    w, h = line_width(TEXT, STYLE), STYLE.height()
    assert place_caption(w, h, head)[1] == "over_head"
    y, where, st = room_for(TEXT, STYLE, head, 1300)
    assert where == "below_head" and st.size < STYLE.size and st.size >= 0.7 * STYLE.size
    w2, h2 = line_width(TEXT, st), st.height()
    assert y + h2 <= SAFE_BOTTOM and not overlaps([(1080 - w2) / 2, y, (1080 + w2) / 2, y + h2], head, 36)


def test_or_above_it():
    head = [200, 368, 880, 1700]
    y, where, st = room_for(TEXT, STYLE, head, 1300)
    assert where == "above_head" and y >= SAFE_TOP and y + st.height() < head[1] and st.size < STYLE.size


def test_a_head_that_fills_the_frame_leaves_no_room_at_any_size():
    # runs 013 and d1-usgs: SAM's head box with the long hair, wider than the frame, y 156 to the bottom edge
    assert room_for(TEXT, STYLE, [-208, 156, 1298, 1916], 1300) is None


def _overlays(tmp_path, box, name):
    words = [{"w": w, "t_start": a, "t_end": b, "turn_id": 2, "i": 71 + k} for k, (w, a, b) in enumerate(BILLY)]
    edl = {"source": {"width": 1920, "height": 1080, "fps": 30.0},
           "spans": [{"id": "s6", "turn_id": 2, "word_from": 71, "word_to": 82, "t_in": 117.9, "t_out": 124.06,
                      "duration": 6.16, "callout": ""}]}
    seg = SimpleNamespace(t_in=117.9, t_out=124.067, duration=6.1667, fps=30.0, span_ids=["s6"], frames=185)
    path = SimpleNamespace(xs=[640] * 185, crop_w=608, crop_h=1080, track_id="0")
    head = {"source": {"width": 960, "height": 540}, "objects": {"0": [{"t": 117.0 + 0.5 * k, "box": box} for k in range(17)]}}
    return build_overlays(edl, words, [seg], [path], head, None, tmp_path / name, callouts=False,
                          reframe_concept="head")[0]["captions"]


def test_build_overlays_shrinks_only_the_lines_that_had_no_room(tmp_path):
    # the head in output pixels about [200, 300, 880, 1450]: no room for a full-size line under or above it
    caps = _overlays(tmp_path, [376, 84, 568, 408], "low")
    assert caps and all(c["placement"] in ("below_head", "above_head") and not c["over_face"] for c in caps)
    assert all(70 <= c["size"] < STYLE.size and c["box"][3] == at_size(STYLE, c["size"]).height() for c in caps)
    # the Live preview lays the line out at the size the render drew it
    st = CaptionStyle()
    assert all(_line(c, st, set())["size"] == c["size"] for c in caps)
    # a head that leaves room at full size: nothing changes, no size recorded
    caps = _overlays(tmp_path, [400, 40, 540, 250], "room")
    assert caps and all(c["placement"] == "lower_third" and "size" not in c for c in caps)
    # a head that fills the frame: still over the head at full size, as before
    caps = _overlays(tmp_path, [0, 0, 960, 540], "full")
    assert caps and all(c["placement"] == "over_head" and "size" not in c for c in caps)


def test_fit_line_takes_the_layouts_size():
    st = fit_line(TEXT, STYLE, 72)
    assert st.size == 72 and st.stroke < STYLE.stroke and fit_line(TEXT, STYLE, None) == fit_line(TEXT, STYLE)
