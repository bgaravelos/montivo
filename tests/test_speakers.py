"""Offline: speaker -> head mapping from mouth motion (synthetic frames, two heads, two voices), the watch-position
fallback, the same-screen rule, and the reframe / render steering that follows whoever talks."""
import random
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from montivo.engine.reframe import id_at, plan_segment
from montivo.engine.render import Segment, follow_pieces
from montivo.engine.speakers import assign, follow_ids, map_speakers

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


def frames(talking: bool = True, seed: int = 7) -> list[Image.Image]:
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
