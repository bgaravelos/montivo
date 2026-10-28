"""Offline: batch F, what a viewer sees and hears in every cut (28/10). E13 the equal-power crossfade at every cut
(no dip, the frames' length kept), E12 caption lines that do not end on a function word (behind the look's
`line_breaks`, old EDLs keep the greedy lines), E8 the eased reframe (zero-phase Gaussian, 1 px steps, exact crop),
E10 the person box and then the last x where the head track loses the speaker."""
import array
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from montivo.engine.captions import FUNCTION_WORDS, CWord, group_lines
from montivo.engine.edl import norm
from montivo.engine.graphics import CaptionStyle, normalize_look, restyle
from montivo.engine.look import with_style
from montivo.engine.reframe import ReframeParams, gaussian, plan_segment
from montivo.engine.render import AUDIO_RATE, Segment, cut_graph, handles, plan_paths, render_audio, segment_encode

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def pcm(path: Path) -> array.array:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    a = array.array("f")
    a.frombytes(raw)
    return a


def rms_db(x) -> float:
    return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(1, len(x))) + 1e-12)


def segs(*times, fps=30.0):
    return [Segment(index=i, t_in=a, t_out=b, span_ids=[f"s{i + 1}"], frames=round((b - a) * fps), fps=fps)
            for i, (a, b) in enumerate(times)]


# E13 ---------------------------------------------------------------------------------------------------------------

def test_handles_stay_inside_the_source_and_none_at_the_ends():
    s = segs((0.0, 1.0), (2.0, 3.0), (4.0, 4.995))
    assert handles(s, source_s=5.0) == [(0, 480), (480, 480), (480, 0)]
    assert handles(segs((0.004, 1.0), (1.5, 2.0)), source_s=2.0)[1] == (480, 0)
    assert handles(segs((0.0, 1.0), (0.004, 0.5)), source_s=1.002) == [(0, 96), (192, 0)]
    g = cut_graph(s, 5.0)
    assert sum("acrossfade=ns=960:c1=qsin:c2=qsin" in x for x in g) == 2 and g[-1].endswith("[cut]")
    assert cut_graph(segs((0.0, 1.0)), 1.0)[0].endswith("[cut]")


@needs_ffmpeg
def test_cuts_crossfade_without_a_dip_and_keep_the_frames_length(tmp_path):
    """Room tone (white noise) cut in three places: the joined audio is exactly as long as the frames, and no 5 ms
    window across a cut drops more than 2 dB under the tone (the old 20 ms fade out + fade in fell 20+ dB)."""
    src = tmp_path / "noise.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=6:c=white:r=48000:a=0.05:s=7",
                    "-ac", "2", str(src)], check=True)
    s = segs((0.5, 1.5), (2.0, 3.0), (3.5, 4.0), (5.0, 5.5))
    out, rec, _ = render_audio(src, s, tmp_path, loudnorm=False, source_s=6.0)
    x = pcm(out)
    total = sum(round(g.duration * AUDIO_RATE) for g in s)
    assert abs(len(x) - total) <= 1024  # AAC priming / padding only
    tone = rms_db(x[2400:40000])
    t = 0.0
    for g in s[:-1]:
        t += g.duration
        c = round(t * AUDIO_RATE)
        worst = min(rms_db(x[c + k:c + k + 240]) for k in range(-960, 960, 120))
        assert worst > tone - 2.0, (t, worst, tone)


# E12 ---------------------------------------------------------------------------------------------------------------

def timed(text: str, span: str = "s1", step: float = 0.28, pauses: dict[int, float] | None = None) -> list[CWord]:
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append(CWord(w=w, t_in=round(t, 3), t_out=round(t + step - 0.04, 3), span=span))
        t += step + (pauses or {}).get(i, 0.0)
    return out


def ends_on_function(lines) -> list[str]:
    return [" ".join(x.w for x in ln) for ln, nxt in zip(lines, lines[1:])
            if norm(ln[-1].w) in FUNCTION_WORDS and ln[-1].span == nxt[0].span]


def test_phrase_breaks_never_end_a_line_on_and_or_a():
    style = CaptionStyle()
    ws = timed("I run a small digital marketing agency in Athens and we edit short form videos every single day")
    greedy = group_lines(ws, style)
    assert "agency in Athens and" in [" ".join(x.w for x in ln) for ln in greedy]  # what 015 v5 shows
    phrase = group_lines(ws, style, breaks="phrase")
    assert not ends_on_function(phrase)
    assert [x.w for ln in phrase for x in ln] == [x.w for x in ws]
    assert all(1 <= len(ln) <= 4 for ln in phrase)
    raw = group_lines(timed("so raw footage in a reviewed cut out"), style, breaks="phrase")
    assert not ends_on_function(raw)


def test_phrase_breaks_take_the_pause_and_keep_names_whole():
    style = CaptionStyle()
    ws = timed("the reviewer scores the cut against the rubric", pauses={2: 0.3})  # a 0.3 s pause after "scores"
    lines = group_lines(ws, style, breaks="phrase")
    assert lines[0][-1].w == "scores" and not ends_on_function(lines)
    named = timed("then Muse Voice hears every word")
    named[1].src = named[2].src = "news voice"
    out = group_lines(named, style, max_words=2, breaks="phrase")
    assert ["Muse", "Voice"] in [[x.w for x in ln] for ln in out]


def test_old_looks_keep_greedy_and_a_style_edit_keeps_the_versions_breaks():
    assert normalize_look(None).line_breaks == "greedy"
    with pytest.raises(ValueError):
        normalize_look({"line_breaks": "smart"})
    new = {"line_breaks": "phrase"}
    assert restyle(new, {"preset": "bold_pop"}).line_breaks == "phrase"
    assert restyle(None, {"preset": "box", "line_breaks": "phrase"}).line_breaks == "greedy"
    edl = {"look": new, "spans": []}
    assert with_style(edl, normalize_look({"preset": "karaoke"}).to_json())["look"]["line_breaks"] == "phrase"


# E8 ----------------------------------------------------------------------------------------------------------------

SRC = {"width": 1920, "height": 1080, "fps": 30.0}


def boxes(cx_of_t, t0: float, t1: float, fps: float = 5.0, w: int = 70):
    """A head box per track sample (proxy 960x540) with its centre at cx_of_t(t) in proxy px."""
    n = int(round((t1 - t0) * fps)) + 1
    return [{"f": k, "t": round(t0 + k / fps, 3), "box": [cx_of_t(t0 + k / fps) - w / 2, 30,
                                                           cx_of_t(t0 + k / fps) + w / 2, 250]} for k in range(n)]


def trk(objects: dict, fps: float = 5.0) -> dict:
    return {"track_fps": fps, "source": {"width": 960, "height": 540}, "objects": objects}


def accel(xs: list[int], fps: float) -> float:
    v = [(b - a) * fps for a, b in zip(xs, xs[1:])]
    return max(abs(b - a) * fps for a, b in zip(v, v[1:]))


def test_gaussian_keeps_a_still_camera_still_and_eases_a_step():
    assert gaussian([500.0] * 40, 3.9) == [500.0] * 40
    step = gaussian([100.0] * 30 + [400.0] * 30, 3.9)
    assert step[0] == 100.0 and step[-1] == 400.0 and all(100 <= v <= 400 for v in step)
    assert all(b >= a for a, b in zip(step, step[1:]))
    assert max(b - a for a, b in zip(step, step[1:])) < 40  # was 300 in one sample
    assert gaussian([1.0, 2.0], 0) == [1.0, 2.0]


def test_a_lean_eases_in_and_out_in_1px_steps():
    """The 015 v5 lean (head 283 -> 480 proxy px): the EMA's pan started and stopped in one frame (7200 px/s^2); with
    the Gaussian no frame changes the pan speed by more than a few px per frame."""
    lean = lambda t: 283 if t < 1.5 else 480 if t > 2.5 else 283 + (t - 1.5) * 197  # noqa: E731
    tr = trk({"0": boxes(lean, 0.0, 5.0)})
    old = plan_segment(0.0, 5.0, SRC, tr, ReframeParams(smooth_sigma_s=0.0))
    new = plan_segment(0.0, 5.0, SRC, tr)
    assert accel(old.xs, 30.0) >= 5000 and accel(new.xs, 30.0) <= 2400
    assert any(x % 2 for x in new.xs) and abs(new.xs[-1] - old.xs[-1]) <= 2


def test_the_crop_is_exact_on_odd_x(tmp_path):
    from montivo.engine.reframe import SegmentPath
    seg = Segment(index=0, t_in=0.0, t_out=1.0, span_ids=["s1"], frames=30, fps=30.0)
    path = SegmentPath(t_in=0.0, t_out=1.0, track_id=None, crop_w=608, crop_h=1080, xs=[301 + k for k in range(30)], fps=30.0)
    (tmp_path / "src.mp4").write_bytes(b"x")
    enc = segment_encode(tmp_path / "src.mp4", seg, path, tmp_path)
    graph = enc.args[enc.args.index("-filter_complex") + 1]
    assert "crop=608:1080:301:0:exact=1" in graph
    assert (tmp_path / "seg_01_crop.cmd").read_text().splitlines()[1] == "0.0167 crop x 302;"


# E10 ---------------------------------------------------------------------------------------------------------------

def test_a_lost_head_follows_the_person_box_then_the_last_x():
    head = trk({"0": boxes(lambda t: 700, 0.0, 2.0)})  # the head leaves the track after 2 s
    person = trk({"7": boxes(lambda t: 640, 0.0, 10.0, w=300)})
    seg = plan_segment(4.0, 6.0, SRC, head, fallback=person)
    assert seg.track_id is None and seg.fallbacks == [{"t_in": 4.0, "t_out": 6.0, "by": "person"}]
    assert set(seg.xs) == {640 * 2 - 304}  # person centre 1280 source px, not the frame's centre (656)
    held = plan_segment(4.0, 6.0, SRC, head, hold=1400.0)
    assert held.fallbacks[0]["by"] == "hold" and set(held.xs) == {1400 - 304}
    assert plan_segment(4.0, 6.0, SRC, head).fallbacks[0]["by"] == "centre"
    assert plan_segment(0.5, 1.5, SRC, head, fallback=person).fallbacks == []


def test_plan_paths_holds_the_previous_segments_x_and_pieces_hold_the_piece_before():
    head = trk({"0": boxes(lambda t: 700, 0.0, 3.0)})
    edl = {"source": SRC, "target": {"aspect": "9:16"},
           "spans": [{"id": "s1", "t_in": 1.0, "t_out": 2.0, "role": "hook"}, {"id": "s2", "t_in": 5.0, "t_out": 6.0, "role": "payoff"}]}
    _, paths = plan_paths(edl, head, {})
    assert paths[0].fallbacks == [] and set(paths[1].xs) == {paths[0].xs[-1]}
    # a speaker piece without a head after one with it keeps that piece's x
    follow = [{"t_in": 1.0, "t_out": 2.0, "speaker": "A", "ids": ["0"]},
              {"t_in": 2.0, "t_out": 5.0, "speaker": "B", "ids": ["9"]}]
    seg = plan_segment(1.0, 5.0, SRC, head, follow=follow)
    assert seg.xs[-1] == seg.xs[0]
