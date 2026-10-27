"""Offline: the 9:16 crop follows the head of whoever speaks (engine/speakers.py + reframe prefer_x / prefer_id)."""
from montivo.engine.reframe import plan_segment
from montivo.engine.speakers import follow_for, label, segment_speaker

from tests.test_reframe import SRC, recs, track

# a two-shot on a 960 px proxy: the interviewer small on the left, the guest large and nearer the centre on the right
TWO_SHOT = track({"1": recs(240, 0, 10, w=160, h=200), "2": recs(600, 0, 10, w=260, h=320)})


def edl(speaker="A", position="left", on_camera=True, pins=None):
    e = {"spans": [{"id": "s1", "speaker": speaker, "t_in": 2.0, "t_out": 4.0},
                   {"id": "s2", "speaker": "B", "t_in": 4.0, "t_out": 4.5}],
         "speaker_map": [{"speaker": "speaker A", "who": "interviewer", "on_camera": on_camera, "position": position},
                         {"speaker": "B", "who": "guest", "on_camera": True, "position": "right"}]}
    if pins:
        e["reframe_speaker_ids"] = pins
    return e


def path_for(e, span_ids=("s1",)):
    f = follow_for(e, list(span_ids))
    return f, plan_segment(2.0, 4.0, SRC, TWO_SHOT, span_ids=list(span_ids), prefer_x=f.prefer_x, prefer_id=f.prefer_id)


def test_default_pick_follows_the_larger_head():
    assert plan_segment(2.0, 4.0, SRC, TWO_SHOT).track_id == "2"


def test_speaker_on_the_left_is_followed_not_the_larger_listener():
    f, p = path_for(edl())
    assert (f.speaker, f.by) == ("A", "position left") and p.track_id == "1"
    centre_px = (p.xs[0] + p.crop_w / 2) / (SRC["width"] / 960)
    assert abs(centre_px - 240) < 20  # the crop sits on the interviewer


def test_the_other_speaker_gets_their_own_head():
    f, p = path_for(edl(), span_ids=("s2",))
    assert (f.speaker, f.by, p.track_id) == ("B", "position right", "2")


def test_off_camera_centre_or_unmapped_speaker_keeps_the_default():
    for e in (edl(on_camera=False, position="off_camera"), edl(position="centre"), edl(position="varies"),
              edl(speaker="C")):
        f, p = path_for(e)
        assert f.by == "default" and f.prefer_x is None and p.track_id == "2"


def test_a_pin_wins_while_its_head_is_in_frame():
    f, p = path_for(edl(position="right", pins={"a": 1}))
    assert (f.by, f.prefer_id, p.track_id) == ("pin", "1", "1")
    f, p = path_for(edl(pins={"A": "9"}))  # the pinned id is not in frame: the side decides
    assert p.track_id == "1"


def test_labels_and_the_segment_speaker():
    assert label("speaker a") == "A" and label("Speaker: B") == "B" and label(" A ") == "A" and label(None) == ""
    spans = [{"id": "s1", "speaker": "A", "t_in": 0, "t_out": 1}, {"id": "s2", "speaker": "B", "t_in": 1, "t_out": 3},
             {"id": "s3", "speaker": "?", "t_in": 3, "t_out": 9}]
    assert segment_speaker(spans, ["s1", "s2", "s3"]) == "B"
    assert segment_speaker(spans, ["s3"]) is None
