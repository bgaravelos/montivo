"""Offline: batch F, what a viewer sees and hears in every cut (28/10). E13 the equal-power crossfade at every cut
(no dip, the frames' length kept), E12 caption lines that do not end on a function word (behind the look's
`line_breaks`, old EDLs keep the greedy lines), E8 the eased reframe (zero-phase Gaussian, 1 px steps, exact crop),
E10 the person box and then the last x where the head track loses the speaker, E11 camera cuts found at ingest and the
reframe split there, E9 odd inputs made standard at ingest, E15 human.drop and an end-to-end render()."""
import array
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from montivo.engine.captions import FUNCTION_WORDS, CWord, group_lines
from montivo.engine.edl import norm
from montivo.engine.graphics import CaptionStyle, normalize_look, restyle
from montivo.engine.ingest import (Probe, bit_depth, detect_shots, ffprobe, hdr_kind, make_upright, standard_fps,
                                   standardise, to_sdr_chain)
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


# E11 ---------------------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_detect_shots_finds_the_camera_cuts_and_not_motion(tmp_path):
    clip = tmp_path / "two_cameras.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=480x270:r=30:d=2",
                    "-f", "lavfi", "-i", "mandelbrot=s=480x270:r=30", "-filter_complex",
                    "[1:v]trim=duration=1.5,format=yuv420p[m];[0:v]format=yuv420p,split[a][b];[b]trim=start=0,setpts=PTS-STARTPTS[c];"
                    "[a][m][c]concat=n=3:v=1:a=0", "-c:v", "libx264", "-preset", "ultrafast", str(clip)], check=True)
    assert detect_shots(clip) == [2.0, 3.5]
    one = tmp_path / "one_camera.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=480x270:r=30:d=3", "-c:v", "libx264",
                    "-preset", "ultrafast", str(one)], check=True)
    assert detect_shots(one) == []
    assert detect_shots(tmp_path / "missing.mp4") == []


def test_the_crop_cuts_with_the_camera_and_picks_each_shots_head():
    """Wide shot (head at proxy x 300), close-up from 4 s (head at 620, another SAM id, bigger): without shots the
    one window picks the close-up's head and frames its spot through the wide shot too; with them each shot gets its
    own head and the crop jumps with the picture at the cut, still on either side."""
    tr = trk({"0": boxes(lambda t: 300, 0.0, 3.8), "5": boxes(lambda t: 620, 4.0, 8.0, w=200)})
    old = plan_segment(1.0, 7.0, SRC, tr)
    new = plan_segment(1.0, 7.0, SRC, tr, shots=[4.0])
    k = 90  # the cut, 3 s into the segment
    assert set(old.xs) == {620 * 2 - 304}  # the wide shot framed on the close-up's head
    assert set(new.xs[:k]) == {300 * 2 - 304} and set(new.xs[k:]) == {620 * 2 - 304}
    assert [(p["t_in"], p["t_out"], p["track_id"]) for p in new.pieces] == [(1.0, 4.0, "0"), (4.0, 7.0, "5")]
    assert plan_segment(1.0, 7.0, SRC, tr, shots=[0.5, 9.0]).pieces == []  # cuts outside the segment


def test_no_hold_across_a_camera_cut():
    head = trk({"0": boxes(lambda t: 700, 0.0, 3.0)})
    edl = {"source": {**SRC, "shots": [4.0]}, "target": {"aspect": "9:16"},
           "spans": [{"id": "s1", "t_in": 1.0, "t_out": 2.0, "role": "hook"},
                     {"id": "s2", "t_in": 5.0, "t_out": 6.0, "role": "payoff"}]}
    _, paths = plan_paths(edl, head, {})
    assert paths[1].fallbacks == [{"t_in": 5.0, "t_out": 6.0, "by": "centre"}]


# E9 ----------------------------------------------------------------------------------------------------------------

def probe(**kw) -> Probe:
    base = dict(width=1920, height=1080, fps=30.0, duration=3.0, bytes=1, has_audio=True, codec="h264", r_fps=30.0,
                pix_fmt="yuv420p", transfer="bt709", primaries="bt709")
    return Probe(**{**base, **kw})


def test_odd_inputs_are_made_standard_and_plain_ones_left_alone():
    assert standardise(probe()) == ([], None, None)
    assert standardise(probe(fps=29.97, r_fps=29.97)) == ([], None, None)
    assert standard_fps(probe(fps=31.579, r_fps=31.579)) == 30.0  # the DJI's 600/19
    assert standard_fps(probe(fps=120.0, r_fps=120.0)) == 60.0
    assert standard_fps(probe(fps=119.88, r_fps=119.88)) == 59.94
    assert standard_fps(probe(fps=29.87, r_fps=30.0)) == 29.97  # a phone's variable rate
    assert standard_fps(probe(fps=21.0, r_fps=60.0)) == 23.976
    reasons, fps, sdr = standardise(probe(pix_fmt="yuv420p10le", transfer="arib-std-b67", primaries="bt2020"))
    assert fps is None and sdr == to_sdr_chain("HLG") and reasons[0].startswith("HDR HLG")
    assert standardise(probe(pix_fmt="yuv422p10le"))[0] == ["10-bit -> 8-bit BT.709"]
    assert standardise(probe(rotation=90), hflip=True)[0] == ["mirrored", "rotation 90 baked"]
    assert bit_depth(probe(pix_fmt="yuvj420p")) == 8 and bit_depth(probe(pix_fmt="p010le")) == 8  # p010: by name only


def test_hdr_goes_through_zscale_when_there_is_one_and_pq_without_it_is_refused():
    assert to_sdr_chain("PQ", zscale=True).startswith("zscale=t=linear") and "tonemap=hable" in to_sdr_chain("HLG", True)
    assert to_sdr_chain("HLG", zscale=False).startswith("colorspace=all=bt709:iall=bt2020")
    with pytest.raises(ValueError, match="HDR10"):
        to_sdr_chain("PQ", zscale=False)
    assert "out_color_matrix=bt709" in to_sdr_chain(None, zscale=False)


@needs_ffmpeg
def test_an_hlg_120_fps_clip_becomes_8_bit_bt709_60_fps_with_its_audio_untouched(tmp_path):
    src = tmp_path / "hlg120.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=120:d=1", "-f", "lavfi",
                    "-i", "sine=d=1", "-vf", "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=arib-std-b67:"
                    "colorspace=bt2020nc:range=tv", "-c:v", "libx264", "-profile:v", "high10", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    p = ffprobe(src)
    assert hdr_kind(p) == "HLG" and bit_depth(p) == 10
    _, fps, sdr = standardise(p)
    out = make_upright(src, tmp_path / "source_upright.mp4", fps=fps, sdr=sdr)
    assert (out.fps, out.pix_fmt, out.transfer) == (60.0, "yuv420p", "bt709") and standardise(out) == ([], None, None)
    assert abs(out.duration - p.duration) < 0.05


# E15 ---------------------------------------------------------------------------------------------------------------

def test_human_drop_removes_the_clip_and_refuses_one_it_would_not_drop(tmp_path, monkeypatch):
    import json as _json

    import montivo.human as H
    from tests.test_ui import _human_run
    d = _human_run(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="s1 was not dropped: the hook span"):
        H.drop(d, "s1")  # used to write an edl_v2 identical to v1
    assert not (d / "edl_v2.json").exists()
    out = H.drop(d, "s2")
    v1, v2 = (_json.loads((d / f"edl_v{n}.json").read_text()) for n in (1, 2))
    assert out["version"] == 2 and [s["id"] for s in v2["spans"]] == ["s1", "s3"]
    assert v2["spans"][0]["role"] == "hook" and v2["patches"][-1]["action"] == "drop"
    assert v2["runtime_s"] == round(sum(x["duration"] for x in v1["spans"] if x["id"] != "s2"), 2)
    with pytest.raises(SystemExit, match="not a span"):
        H.drop(d, "s9")


def frame_tags(media: Path) -> set[tuple]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "frame=color_range,color_space,color_primaries,color_transfer", "-of", "csv=p=0", str(media)],
                         capture_output=True, text=True, check=True).stdout
    return {tuple(line.strip(",").split(",")) for line in out.splitlines() if line}


@needs_ffmpeg
def test_render_end_to_end_title_three_spans_captions(tmp_path):
    """A lavfi clip (testsrc2 + a tone, full range like Billy's DJI footage) through render(): a title card and three
    segments in one file of the frames' length, audio as long as the video, every frame limited-range BT.709 (one
    set of colour tags across the title and the segments), -14 LUFS."""
    from PIL import Image

    from montivo.ledger import Ledger
    from montivo.engine import render as R
    from tests.test_segcache import edl_for, words
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=3", "-f", "lavfi",
                    "-i", "sine=f=220:r=48000:d=3", "-vf", "scale=out_range=pc", "-c:v", "libx264", "-preset",
                    "ultrafast", "-pix_fmt", "yuvj420p", "-color_range", "pc", "-c:a", "aac", str(src)], check=True)
    run = tmp_path / "run"
    run.mkdir()
    title = run / "title.png"
    Image.new("RGB", (1080, 1920), (20, 20, 20)).save(title)
    edl = edl_for(src)
    rec = R.render(run, edl, src, Ledger(run / "ledger.json"), words=words(), title_png=title, log=lambda m: None)
    out = run / "cut_v1.mp4"
    frames = rec["title"]["frames"] + sum(s["frames"] for s in rec["segments"])
    streams = {s["codec_type"]: s for s in __import__("json").loads(subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_entries",
         "stream=codec_type,nb_read_frames,duration,color_range,color_space,width,height", "-of", "json", str(out)],
        capture_output=True, text=True, check=True).stdout)["streams"]}
    v, a = streams["video"], streams["audio"]
    assert int(v["nb_read_frames"]) == frames and (v["width"], v["height"]) == (1080, 1920)
    assert abs(float(a["duration"]) - frames / 30.0) < 0.03 and abs(float(v["duration"]) - frames / 30.0) < 1e-3
    assert (v["color_range"], v["color_space"]) == ("tv", "bt709")
    assert frame_tags(out) == {("tv", "bt709", "bt709", "bt709")}
    assert rec["loudnorm"]["output"]["output_i"] == pytest.approx(-14.0, abs=0.5)
    assert len(rec["segments"]) == 3 and edl["captions"]
