"""Offline: speaker -> head mapping from mouth motion (synthetic frames, two heads, two voices), the watch-position
fallback, the same-screen rule, and the reframe / render steering that follows whoever talks."""
import json
import random
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from montivo.engine.reframe import id_at, plan_segment
from montivo.engine.render import Segment, follow_pieces, load_speaker_ids
from montivo.engine.speakers import assign, follow_ids, inverted_heads, is_mic, map_speakers, mic_votes

FPS, W, H, N = 5.0, 320, 180, 60  # 12 s of track frames
LEFT, RIGHT = [40, 30, 100, 110], [190, 20, 270, 120]  # the right head is larger: the plain rule follows it
# A talks 0-4 s and 8-10 s, B 4-8 s and 10-12 s
TALK = [("A", 0, 4), ("B", 4, 8), ("A", 8, 10), ("B", 10, 12)]


def who_talks(t: float) -> str | None:
    return next((s for s, a, b in TALK if a <= t < b), None)


def words() -> list[dict]:
    out = []
    for spk, a, b in TALK:
        t = a
        while t + 0.35 <= b:
            out.append({"w": "word", "t_start": round(t, 2), "t_end": round(t + 0.35, 2), "speaker": spk})
            t += 0.4
    return out


def head_track(objects=None) -> dict:
    objs = objects or {"1": LEFT, "2": RIGHT}
    return {"track_fps": FPS, "source": {"width": W, "height": H, "resampled": True},
            "objects": {oid: [{"f": f, "t": round(f / FPS, 3), "box": list(box)} for f in range(N)]
                        for oid, box in objs.items()}}


def mic_track(holder=lambda spk: {"A": LEFT, "B": RIGHT}.get(spk)) -> dict:
    """A handheld mic just under the mouth of whoever talks (none in the gaps), moving a little in the hand."""
    objs = []
    for f in range(N):
        box = holder(who_talks(f / FPS))
        if box:
            cx, y2, j = (box[0] + box[2]) / 2, box[3], (f % 5) * 3
            objs.append({"f": f, "t": round(f / FPS, 3), "box": [cx - 6 + j, y2 + 4 + j, cx + 6 + j, y2 + 24 + j]})
    return {"concept": "microphone", "track_fps": FPS, "objects": {"0": objs}}


def frames(talking: bool = True, seed: int = 7, inverted: tuple[str, ...] = ()) -> list[Image.Image]:
    """Grey frames with a global flicker (a camera's auto exposure) on both heads; the mouth of whoever talks is
    redrawn with noise every frame, the other mouth stays still."""
    rnd = random.Random(seed)
    out = []
    for f in range(N):
        g = 110 + rnd.randint(-4, 4)
        img = Image.new("L", (W, H), g)
        d = ImageDraw.Draw(img)
        spk = who_talks(f / FPS)
        for box, me in ((LEFT, "A"), (RIGHT, "B")):
            d.rectangle(box, fill=g + 40)
            x1, y1, x2, y2 = box
            bw, bh = x2 - x1, y2 - y1
            mouth = (int(x1 + 0.3 * bw), int(y1 + 0.7 * bh), int(x1 + 0.7 * bw), int(y1 + 0.9 * bh))
            if me in inverted:  # upside down: the mouth at the top of the box
                mouth = (mouth[0], int(y1 + 0.1 * bh), mouth[2], int(y1 + 0.3 * bh))
            if talking and spk == me:
                for x in range(mouth[0], mouth[2]):
                    for y in range(mouth[1], mouth[3]):
                        img.putpixel((x, y), rnd.randint(0, 255))
            else:
                d.rectangle(mouth, fill=g - 30)
        out.append(img)
    return out


def test_mouth_motion_maps_each_voice_to_its_head():
    res = map_speakers(words(), head_track(), video=None, frames=frames())
    assert res["speakers"]["A"]["ids"] == ["1"] and res["speakers"]["B"]["ids"] == ["2"]
    assert {s["method"] for s in res["speakers"].values()} == {"mouth_motion"}
    assert res["heads"]["1"]["scores"]["A"] > 1.0 > res["heads"]["1"]["scores"]["B"]
    assert follow_ids(res) == {"A": ["1"], "B": ["2"]}


def test_still_mouths_fall_back_to_the_watch_position():
    smap = [{"speaker": "A", "who": "host", "on_camera": True, "position": "left of frame"},
            {"speaker": "B", "who": "guest", "on_camera": True, "position": "on the right"}]
    res = map_speakers(words(), head_track(), video=None, speaker_map=smap, frames=frames(talking=False))
    assert res["speakers"]["A"] == {"ids": ["1"], "method": "position", "position": "left of frame", "who": "host"}
    assert res["speakers"]["B"]["ids"] == ["2"] and res["speakers"]["B"]["method"] == "position"


def test_off_camera_voice_stays_unmapped():
    smap = [{"speaker": "A", "who": "narrator", "on_camera": False, "position": ""}]
    res = map_speakers(words(), head_track(), video=None, speaker_map=smap, frames=frames(talking=False))
    assert res["speakers"]["A"]["ids"] == [] and res["speakers"]["A"]["method"] == "none"


def test_two_heads_on_screen_never_share_a_speaker():
    objects = {"1": [{"f": f, "box": LEFT} for f in range(N)], "2": [{"f": f, "box": RIGHT} for f in range(N)]}
    scores = {"1": {"A": 1.8, "B": 0.1}, "2": {"A": 1.1, "B": 0.2}}
    res = assign(scores, objects, {"A": set(range(20)), "B": set(range(20, 40))})
    assert res["speakers"]["A"]["ids"] == ["1"] and res["heads"]["2"]["speaker"] is None


def test_the_crop_follows_whoever_talks():
    source = {"width": 1280, "height": 720, "fps": 25.0}
    plain = plan_segment(0.0, 8.0, source, head_track())
    assert plain.track_id == "2"  # larger and as centred: the plain rule stays on the right head
    follow = [{"t_in": 0.0, "t_out": 4.0, "speaker": "A", "ids": ["1"]},
              {"t_in": 4.0, "t_out": 8.0, "speaker": "B", "ids": ["2"]}]
    path = plan_segment(0.0, 8.0, source, head_track(), follow=follow)
    assert [(p["speaker"], p["track_id"]) for p in path.pieces] == [("A", "1"), ("B", "2")]
    assert len(path.xs) == 200
    crop_w = path.crop_w
    left_cx, right_cx = (LEFT[0] + LEFT[2]) / 2 * 4, (RIGHT[0] + RIGHT[2]) / 2 * 4
    assert path.xs[50] == pytest.approx(max(0, left_cx - crop_w / 2), abs=2)
    assert path.xs[150] == pytest.approx(right_cx - crop_w / 2, abs=2)
    assert id_at(path, 1.0) == "1" and id_at(path, 5.0) == "2"


def test_same_head_pieces_plan_as_one_window():
    follow = [{"t_in": 0.0, "t_out": 2.0, "speaker": "A", "ids": ["1"]},
              {"t_in": 2.0, "t_out": 4.0, "speaker": "C", "ids": None}]  # C off camera: the plain rule picks "2"
    path = plan_segment(0.0, 4.0, {"width": 1280, "height": 720, "fps": 25.0}, head_track(), follow=follow)
    assert [p["track_id"] for p in path.pieces] == ["1", "2"]
    follow[1]["ids"] = ["1"]
    path = plan_segment(0.0, 4.0, {"width": 1280, "height": 720, "fps": 25.0}, head_track(), follow=follow)
    assert path.pieces == [{"t_in": 0.0, "t_out": 4.0, "speaker": None, "track_id": "1"}]


def test_follow_pieces_from_the_spans_of_a_segment():
    spans = [{"id": "s1", "t_in": 0.0, "t_out": 3.9, "speaker": "A"}, {"id": "s2", "t_in": 3.9, "t_out": 6.0, "speaker": "B"},
             {"id": "s3", "t_in": 6.0, "t_out": 8.0, "speaker": "B"}]
    seg = Segment(index=0, t_in=0.0, t_out=8.0, span_ids=["s1", "s2", "s3"])
    assert follow_pieces(seg, spans, {}) is None
    assert follow_pieces(seg, spans, {"C": ["9"]}) is None
    assert follow_pieces(seg, spans, {"A": ["1"]}) == [
        {"t_in": 0.0, "t_out": 3.9, "speaker": "A", "ids": ["1"]}, {"t_in": 3.9, "t_out": 8.0, "speaker": "B", "ids": None}]


def test_an_edl_pin_replaces_the_mapped_head(tmp_path):
    (tmp_path / "speakers.json").write_text(json.dumps({"speakers": {"A": {"ids": ["1"]}, "B": {"ids": ["2"]}}}))
    assert load_speaker_ids(tmp_path, "head", {}) == {"A": ["1"], "B": ["2"]}
    pinned = load_speaker_ids(tmp_path, "head", {"reframe_speaker_ids": {"A": 2, "C": "1"}})
    assert pinned == {"A": ["2"], "B": ["2"], "C": ["1"]}
    assert load_speaker_ids(tmp_path, "person", {"reframe_speaker_ids": {"A": "2"}}) == {}
    (tmp_path / "speakers.json").unlink()  # a pin works without a mapping too
    assert load_speaker_ids(tmp_path, "head", {"reframe_speaker_ids": {"B": "1"}}) == {"B": ["1"]}
    follow = [{"t_in": 0.0, "t_out": 8.0, "speaker": "B", "ids": ["1"]}]
    path = plan_segment(0.0, 8.0, {"width": 1280, "height": 720, "fps": 25.0}, head_track(), follow=follow)
    assert path.track_id == "1"  # the pin wins over the larger head the plain rule would take


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_decode_path_end_to_end(tmp_path):
    clip = tmp_path / "track_source_5fps.mkv"
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", "5",
                            "-i", "-", "-c:v", "ffv1", str(clip)], stdin=subprocess.PIPE)
    for img in frames():
        enc.stdin.write(img.tobytes())
    enc.stdin.close()
    assert enc.wait() == 0
    res = map_speakers(words(), head_track(), video=clip)
    assert follow_ids(res) == {"A": ["1"], "B": ["2"]}
    assert res["frames_scored"]["1"] == N - 1


def test_the_mic_holder_wins_over_mouths_and_watch():
    # no mouth moves and watch has the sides the wrong way round (run 014): the mic decides
    smap = [{"speaker": "A", "on_camera": True, "position": "right"}, {"speaker": "B", "on_camera": True, "position": "left"}]
    res = map_speakers(words(), head_track(), video=None, speaker_map=smap, frames=frames(talking=False),
                       mic_track=mic_track())
    assert follow_ids(res) == {"A": ["1"], "B": ["2"]}
    assert res["speakers"]["A"]["method"] == "microphone" and res["speakers"]["A"]["mic_share"] == 1.0
    assert res["method"].startswith("microphone+") and res["mic_concept"] == "microphone"
    without = map_speakers(words(), head_track(), video=None, speaker_map=smap, frames=frames(talking=False))
    assert follow_ids(without) == {"A": ["2"], "B": ["1"]}  # the old path follows watch's wrong sides


def test_mic_votes_need_a_majority_and_one_head_each():
    objects = head_track()["objects"]
    speaking = {"A": set(range(0, 20)) | set(range(40, 50)), "B": set(range(20, 40)) | set(range(50, 60))}
    # the mic never leaves head 1: both voices claim it, one head per speaker, so only one keeps it
    always_left = mic_track(holder=lambda spk: LEFT)
    assert mic_votes(objects, always_left["objects"], speaking) == {"A": {"ids": ["1"], "share": 1.0, "frames": 30}}
    # held by A's head on half of A's frames and by B's on the other half: no majority, no vote
    split = {"0": [{"f": f, "box": [64, 115, 76, 135] if f % 2 else [224, 125, 236, 145]} for f in range(N)]}
    assert mic_votes(objects, split, speaking) == {}
    # a fixed object SAM also calls a microphone, right under B's head the whole clip: dropped, the held mic decides
    fixed = {"9": [{"f": f, "box": [224, 124, 236, 144]} for f in range(N)]}
    assert mic_votes(objects, {**mic_track()["objects"], **fixed}, speaking)["A"]["ids"] == ["1"]
    assert mic_votes(objects, fixed, speaking) == {}
    assert is_mic("microphone") and is_mic("handheld mic") and not is_mic("mission patch")


def test_the_mic_leaves_the_rest_to_mouth_motion():
    # only A's turns show the mic; B is mapped by mouth motion among the heads the mic left
    res = map_speakers(words(), head_track(), video=None, frames=frames(),
                       mic_track=mic_track(holder=lambda spk: LEFT if spk == "A" else None))
    assert res["speakers"]["A"]["method"] == "microphone" and res["speakers"]["B"] == {"ids": ["2"], "method": "mouth_motion"}


def test_upside_down_head_gets_flipped_bands():
    # B floats upside down: his mouth is at the top of the box, the person box runs up from his head
    persons = {"p1": [{"f": f, "box": [30, 20, 110, 175]} for f in range(N)],  # A upright: head at the top of the body
               "p2": [{"f": f, "box": [180, 0, 280, 125]} for f in range(N)]}  # B: head at the bottom of the body
    assert inverted_heads(head_track()["objects"], persons) == {"2"}
    assert inverted_heads(head_track()["objects"], None) == set()
    upside = frames(inverted=("B",))
    blind = map_speakers(words(), head_track(), video=None, frames=upside)
    assert blind["speakers"]["B"]["ids"] == [] and blind["heads"]["2"]["scores"]["B"] < 0  # backwards, as on run 014
    res = map_speakers(words(), head_track(), video=None, frames=upside,
                       person_track={"track_fps": FPS, "objects": persons})
    assert res["inverted_heads"] == ["2"] and follow_ids(res) == {"A": ["1"], "B": ["2"]}
