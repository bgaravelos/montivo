"""Offline: feed outputs next to the 9:16 cut (engine/frame.py). The frame sizes and their safe band, the crop of a
4:5 / 1:1 render (full height, narrower; a phone clip keeps its width and loses height around the head), captions and
callouts laid out in the feed frame, the punch-in zoom size, the export's grid crop and the pre-flight's feed rows."""
import json
from types import SimpleNamespace

import pytest

from montivo import export as X
from montivo.engine.captions import CAPTION_TOP, build_overlays, place_caption
from montivo.engine.frame import FRAME_916, Frame, frame_for, frame_of, with_aspect
from montivo.engine.punch import zoom_filter
from montivo.engine.reframe import crop_size, crop_top, plan_segment
from montivo.server import preflight as P
from tests.test_look import scene


def test_frames():
    assert frame_for({}) == FRAME_916 and frame_for({"target": {"aspect": "9:16"}}) == FRAME_916
    f45, f11 = frame_of("4:5"), frame_of("1:1")
    assert (f45.w, f45.h, f45.ratio, f45.tag) == (1080, 1350, (4, 5), "4x5") and (f11.w, f11.h) == (1080, 1080)
    assert FRAME_916.y(1300) == 1300 and f45.y(1300) == 914 and f11.y(1300) == 731
    assert FRAME_916.band(220, 1620) == (220, 1620)  # the Reels / TikTok UI
    assert f45.band(220, 1620) == (81, 1269) and f11.band(220, 1620) == (65, 1015)  # no app UI over a feed post
    assert with_aspect({}, "1:1")["target"] == {"aspect": "1:1", "width": 1080, "height": 1080}
    with pytest.raises(ValueError, match="aspect"):
        frame_of("16:9")


def test_crop_follows_the_aspect():
    assert crop_size(1920, 1080) == (608, 1080)  # 9:16 as before
    assert crop_size(1920, 1080, (4, 5)) == (864, 1080) and crop_size(1920, 1080, (1, 1)) == (1080, 1080)
    assert crop_size(1080, 1920) == (1080, 1920)  # a 9:16 phone clip is the 9:16 frame itself
    assert crop_size(1080, 1920, (4, 5)) == (1080, 1350)  # a phone clip in 4:5: full width, the height cut


def test_a_phone_clip_in_4x5_keeps_the_head_in_the_crop():
    src = {"width": 1080, "height": 1920, "fps": 30.0}
    # one head, its centre at y 700 of the 1920 source (SAM box in a 540 x 960 proxy)
    track = {"track_fps": 5.0, "source": {"width": 540, "height": 960},
             "objects": {"0": [{"t": t / 5, "box": [200, 300, 340, 400]} for t in range(0, 51)]}}
    p = plan_segment(0.0, 10.0, src, track, aspect=(4, 5))
    assert (p.crop_w, p.crop_h) == (1080, 1350) and p.crop_y == 160  # 700 - 0.4 * 1350, even
    assert crop_top(track, "0", 0.0, 10.0, 1920, 1350) == 160
    assert crop_top(None, None, 0.0, 10.0, 1920, 1350) == 284  # no track: the middle
    assert plan_segment(0.0, 10.0, {"width": 1920, "height": 1080, "fps": 30.0}, None, aspect=(4, 5)).crop_y == 0


def test_captions_in_a_feed_frame(tmp_path):
    edl, words, seg, path, head, person = scene()
    path45 = SimpleNamespace(xs=[528] * 185, crop_w=864, crop_h=1080, crop_y=0)
    fields, _ = build_overlays(with_aspect(edl, "4:5"), words, [seg], [path45], head, person, tmp_path)
    f = frame_of("4:5")
    for c in fields["captions"]:
        x, y, w, h = c["box"]
        assert 0 <= x and x + w <= 1080 and y >= 81 and y + h <= 1269 and not c["over_face"]
    assert {c["box"][1] for c in fields["captions"] if c["placement"] == "lower_third"} <= {f.y(CAPTION_TOP)}
    call = fields["callouts"][0]
    assert call["box"][1] + call["box"][3] <= 1350 and not call["over_face"]


def test_fixed_top_and_pinned_callout_scale_with_the_frame(tmp_path):
    edl, words, seg, path, head, person = scene({"position": "fixed", "top": 600}, callout_xy=[50, 960])
    path11 = SimpleNamespace(xs=[420] * 185, crop_w=1080, crop_h=1080)
    fields, _ = build_overlays(with_aspect(edl, "1:1"), words, [seg], [path11], head, person, tmp_path)
    assert {c["box"][1] for c in fields["captions"]} == {338}  # 600 of 1920 -> 338 of 1080
    assert fields["callouts"][0]["box"][:2] == [50, 540] and fields["callouts"][0]["placement"] == "pinned"


def test_place_caption_in_a_feed_frame_goes_below_a_low_head():
    # a head that reaches the scaled default (914 of 1350): below it fits inside the feed band, not inside 9:16's
    assert place_caption(500, 137, [400, 500, 680, 950], frame=frame_of("4:5")) == (987, "below_head")
    assert place_caption(500, 137, [400, 500, 680, 950])[1] == "lower_third"  # 9:16 untouched (1300 is clear)


def test_zoom_filter_size():
    p = {"base": 1.06, "anchor": (540.0, 500.0), "events": []}
    assert ":s=1080x1920:" in zoom_filter(p, 30.0) and ":s=1080x1350:" in zoom_filter(p, 30.0, (1080, 1350))


def test_grid_crop_of_every_aspect():
    assert X.grid_crop(1080, 1920) == "crop=1080:1440:0:(ih-1440)/2"  # as before
    assert X.grid_crop(1080, 1350) == "crop=1012:1350:(iw-1012)/2:0"
    assert X.grid_crop(1080, 1080) == "crop=810:1080:(iw-810)/2:0"


def test_export_refuses_another_aspect(tmp_path):
    (tmp_path / "edl_v1.json").write_text("{}")
    (tmp_path / "cut_v1.mp4").write_bytes(b"x")
    with pytest.raises(SystemExit, match="aspect"):
        X.export(tmp_path, 1, tmp_path / "out", "n", ["srt"], aspect="16:9")


def test_preflight_for_a_feed_frame(tmp_path):
    from tests.test_export import edl
    (tmp_path / "edl_v5.json").write_text(json.dumps(edl()))
    rows = {r["id"]: r for r in P.checks(tmp_path, 5, "4:5")}
    assert rows["format"]["status"] == "ok" and "1080 x 1350 (4:5)" in rows["format"]["detail"]
    assert rows["safe_feed"]["status"] == "info" and "for 9:16" in rows["safe_feed"]["detail"]
    assert not any(k.startswith("safe_") and k != "safe_feed" for k in rows)
    assert "Reels" not in rows["duration"]["detail"] and "Reels" not in rows["loudness"]["detail"]
    assert rows["layout"]["status"] == "info"  # this EDL has no spans to lay out: said, not guessed
