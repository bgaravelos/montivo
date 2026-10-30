"""Close-ups (30/10, runs 013 / d1-usgs): SAM's head box takes in long hair down to the shoulders, so every caption
line went "over the head" and the head-in-frame check read 51 % outside while the face was in frame. A SAM face
track (only when a line has no room) places those lines against the face; background heads leave the speaker map."""
from montivo.engine.captions import build_overlays
from montivo.engine.speakers import foreground
from tests.test_look import scene


def _track(box):  # records every 0.2 s: interp_box gives nothing across a gap over 0.6 s
    return {"source": {"width": 960, "height": 540},
            "objects": {"0": [{"t": round(117 + k * 0.2, 1), "box": box} for k in range(41)]}}


def test_a_line_with_no_room_under_the_head_box_goes_under_the_face(tmp_path):
    edl, words, seg, path, _, person = scene()
    head = _track([330, 5, 620, 539])  # hair to the bottom of the frame, as on run 013
    fields, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path)
    assert {c["placement"] for c in fields["captions"]} == {"over_head"}  # without a face track: as before
    fields, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path, face_track=_track([400, 60, 560, 330]))
    caps = fields["captions"]
    assert all(c["placement"] in ("below_face", "lower_third", "above_face") and not c["over_face"] for c in caps)
    assert all(c["face_box"] and c["box"][1] >= c["face_box"][3] for c in caps if c["placement"] == "below_face")


def test_a_face_track_changes_nothing_where_the_head_box_left_room(tmp_path):
    edl, words, seg, path, _, person = scene()
    head = _track([400, 40, 540, 250])
    a, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path)
    b, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path, face_track=_track([420, 60, 520, 200]))
    assert [(c["box"], c["placement"]) for c in a["captions"]] == [(c["box"], c["placement"]) for c in b["captions"]]
    assert not any("face_box" in c for c in b["captions"])


def test_a_background_head_is_never_a_speaker():
    recs = lambda w: [{"f": k, "t": k / 5, "box": [0, 0, w, w]} for k in range(10)]  # noqa: E731
    assert set(foreground({"1": recs(430), "5": recs(25), "8": recs(37)})) == {"1"}  # run 013's passers-by
    assert set(foreground({str(k): recs(w) for k, w in enumerate([54, 80, 100, 122])})) == {"0", "1", "2", "3"}  # NASA
