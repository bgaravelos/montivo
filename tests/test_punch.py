"""Offline: punch-ins (engine/punch.py). Which words get a zoom (level over the median, highlight words, stop words,
budget and spacing), the jump-cut scale, the two safety rules (top of frame, captions), the zoom curve and the
zoompan filter on real frames."""
import shutil
import subprocess

import pytest
from PIL import Image, ImageChops, ImageStat

from montivo.engine.punch import JUMP_Z, PUNCH_Z, plan_punches, zoom_at, zoom_filter
from montivo.engine.reframe import SegmentPath
from montivo.engine.render import Segment

FPS = 25.0
HEAD = [540, 100, 740, 400]  # source px of a 1280x720 clip; in the 9:16 output about x 273-807, y 267-1067
LOUD = {"Montivo", "reviewed", "everything"}  # Montivo is a highlight word, the other two are spoken louder


class FakeAudio:
    """Source levels: -20 dB inside a loud word, -30 dB elsewhere."""

    def __init__(self, words):
        self.loud = [(w["t_start"], w["t_end"]) for w in words if w["w"] in LOUD and w["w"] != "Montivo"]

    def level(self, t0, t1):
        return -20.0 if any(a < t1 and b > t0 for a, b in self.loud) else -30.0


def words_for(text: str, t0: float, turn: int) -> list[dict]:
    out = []
    for i, w in enumerate(text.split()):
        out.append({"w": w, "t_start": round(t0 + 0.4 * i, 2), "t_end": round(t0 + 0.4 * i + 0.35, 2),
                    "turn_id": turn, "i": i, "speaker": "A"})
    return out


def fixture(head=HEAD, second_head=HEAD):
    w0 = words_for("so this is Montivo and it watches everything it cut", 0.2, 0)  # 10 words, 0.2-4.15 s
    w1 = words_for("then the agent reviewed what it made and fixed that", 8.2, 1)
    words = w0 + w1
    spans = [{"id": "s1", "turn_id": 0, "word_from": 0, "word_to": 9, "t_in": 0.0, "t_out": 6.0},
             {"id": "s2", "turn_id": 1, "word_from": 0, "word_to": 9, "t_in": 8.0, "t_out": 14.0}]
    segs = []
    for k, (a, b) in enumerate([(0.0, 6.0), (8.0, 14.0)]):
        seg = Segment(index=k, t_in=a, t_out=b, span_ids=[spans[k]["id"]], frames=int((b - a) * FPS), fps=FPS)
        segs.append(seg)
    paths = [SegmentPath(t_in=s.t_in, t_out=s.t_out, track_id="1", crop_w=404, crop_h=720, xs=[438] * s.frames, fps=FPS)
             for s in segs]
    recs = [{"t": round(f / 5, 3), "box": list(head if f / 5 < 7 else second_head)} for f in range(0, 75)]
    track = {"track_fps": 5.0, "source": {"width": 1280, "height": 720}, "objects": {"1": recs}}
    edl = {"source": {"width": 1280, "height": 720, "fps": FPS}, "spans": spans}
    return edl, segs, paths, words, track


def test_loud_and_highlight_words_get_a_punch_and_the_jump_cut_a_scale():
    edl, segs, paths, words, track = fixture()
    plan, entries = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[])
    emph = [e for e in entries if e["kind"] == "emphasis"]
    # budget 12 s // 6 = 2; "everything" (10 dB over the median) wins over the highlight word "Montivo" at the
    # median level, which is 1.2 s earlier and so inside the 4 s spacing
    assert sorted(e["word"] for e in emph) == ["everything", "reviewed"]
    assert all(e["zoom"] == PUNCH_Z for e in emph)
    assert [e for e in entries if e["kind"] == "jump_cut"] == [
        {"kind": "jump_cut", "segment": 1, "span": "s2", "key": "jump_cut:s2", "t_in": 6.0, "t_out": 12.0, "zoom": JUMP_Z,
         "anchor": [540, 667]}]
    assert plan[1]["base"] == JUMP_Z and plan[0]["base"] == 1.0
    ev = next(e for e in emph if e["word"] == "everything")
    # the zoom peaks at the word's onset (3.0 s) and is back to 1 after the hold and ramp out
    p0 = plan[0]
    assert zoom_at(p0, 3.0) == pytest.approx(PUNCH_Z) and zoom_at(p0, 0.5) == pytest.approx(1.0)
    assert zoom_at(p0, ev["t_out"] + 0.01) == pytest.approx(1.0)
    assert zoom_at(plan[1], 0.1) == pytest.approx(JUMP_Z)


def test_no_head_track_no_punches():
    edl, segs, paths, words, _ = fixture()
    assert plan_punches(edl, segs, paths, words, FakeAudio(words), None, captions=[]) == ({}, [])


def test_a_head_that_moved_is_not_a_jump_cut():
    edl, segs, paths, words, track = fixture(second_head=[840, 100, 1040, 400])
    paths[1].xs = [738] * segs[1].frames  # the crop followed the head to the right: same place on screen
    _, entries = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[])
    assert any(e["kind"] == "jump_cut" for e in entries)  # on screen it did not move: still a jump cut
    paths[1].xs = [438] * segs[1].frames  # the crop stayed: the head jumps 800 px on screen, a real change of shot
    _, entries = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[])
    assert not any(e["kind"] == "jump_cut" for e in entries)


def test_a_zoom_never_pushes_the_head_out_of_the_top_or_into_a_caption():
    edl, segs, paths, words, track = fixture(head=[540, 0, 740, 400], second_head=[540, 0, 740, 400])
    _, entries = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[])
    assert entries == []  # the head touches the top: any zoom about its centre would cut the forehead off
    edl, segs, paths, words, track = fixture()
    # a caption right under the chin while "everything" is said (cut 2.7-3.9 s): that zoom would run into it
    cap = {"segment": 0, "t_in": 2.7, "t_out": 3.9, "box": [200, 1090, 680, 120]}
    _, entries = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[cap])
    assert "everything" not in [e.get("word") for e in entries]
    assert "Montivo" in [e.get("word") for e in entries]  # the highlight word takes the slot


def test_zoom_filter_text():
    p = {"base": 1.0, "anchor": (540.0, 667.0), "events": [{"a": 1.28, "b": 1.4, "c": 2.1, "d": 2.35, "z": PUNCH_Z}]}
    f = zoom_filter(p, 30000 / 1001)
    assert f.startswith("zoompan=z='1.0000*(1+0.1200*clip((on-38)/4,0,1)*clip((70-on)/7,0,1))'")
    assert f.endswith(":x='540.0-540.0/zoom':y='667.0-667.0/zoom':d=1:s=1080x1920:fps=30000/1001")
    assert zoom_filter({"base": JUMP_Z, "anchor": (540, 600), "events": []}, 25).startswith("zoompan=z='1.0600'")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_zoompan_zooms_on_the_planned_frames(tmp_path):
    p = {"base": 1.0, "anchor": (540.0, 700.0), "events": [{"a": 0.4, "b": 0.4, "c": 0.8, "d": 0.8, "z": 1.2}]}
    out = tmp_path / "z.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=1.6",
                    "-vf", f"crop=202:360:219:0,scale=1080:1920,setsar=1,{zoom_filter(p, 25)}", "-frames:v", "40",
                    "-c:v", "libx264", "-qp", "0", str(out)], check=True, timeout=120)
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    n = 1080 * 1920
    frames = [Image.frombytes("L", (1080, 1920), raw[k * n:(k + 1) * n]) for k in range(len(raw) // n)]
    assert len(frames) == 40
    jumps = [k for k in range(1, 40) if ImageStat.Stat(ImageChops.difference(frames[k], frames[k - 1])).mean[0] > 8]
    assert jumps == [10, 20]  # in at frame 10 (0.4 s), out at frame 20 (0.8 s)
