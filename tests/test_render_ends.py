"""Render audit of 31/10: segments past the last video frame keep their length (the picture holds), abutting spans
of two turns play no frame twice, a late fading still loops only from its own start, ingest reads the first audio
stream as the render does."""
import copy
import shutil
import subprocess

import pytest

from montivo.engine import render as R
from montivo.engine.render import Segment, frame_align, merge_segments
from montivo.engine.reframe import SegmentPath
from montivo.ledger import Ledger

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def frames_of(media) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(media)], capture_output=True, text=True).stdout
    return int(out.strip())


def stream_s(media, kind: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"{kind}:0", "-show_entries", "stream=duration",
                          "-of", "csv=p=0", str(media)], capture_output=True, text=True).stdout
    return float(out.strip())


@pytest.fixture
def short_video(tmp_path):
    """2.0 s of picture, 2.5 s of sound (a camera clip whose audio runs on, like 015's source: 68.50 / 68.96 s)."""
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=2.5", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    return src


def path_for(seg):
    return SegmentPath(t_in=seg.t_in, t_out=seg.t_out, track_id=None, crop_w=608, crop_h=1080, xs=[656] * seg.frames,
                       fps=seg.fps)


@needs_ffmpeg
def test_video_end_is_the_picture_not_the_container(short_video):
    assert R.video_end(short_video) == pytest.approx(2.0, abs=1e-3)
    assert R.video_end(short_video.with_name("missing.mp4")) is None


@needs_ffmpeg
@pytest.mark.parametrize("t_in,t_out", [(1.5, 2.5), (2.1, 2.4)])
def test_a_segment_past_the_last_frame_keeps_all_its_frames(tmp_path, short_video, t_in, t_out):
    seg = frame_align([Segment(0, t_in, t_out, ["s1"], ["body"])], 30.0)[0]
    enc = R.segment_encode(short_video, seg, path_for(seg), tmp_path, video_end=R.video_end(short_video))
    R._ffmpeg([*enc.args, str(enc.out)])
    assert frames_of(enc.out) == seg.frames  # the old encode stopped at the picture's end: 15 of 30, then none


@needs_ffmpeg
def test_a_moved_end_of_clip_span_keeps_picture_and_sound_together(tmp_path, short_video):
    """The last words of the clip moved first (a reorder, the hook A/B): the cut's picture is as long as its sound,
    so the clip after it plays against its own audio."""
    spans = [{"id": "s1", "turn_id": 1, "role": "hook", "t_in": 1.5, "t_out": 2.5, "speaker": "A"},
             {"id": "s2", "turn_id": 0, "role": "body", "t_in": 0.2, "t_out": 1.0, "speaker": "A"}]
    edl = {"source": {"path": str(short_video), "width": 1920, "height": 1080, "fps": 30.0, "duration_s": 2.5},
           "spans": spans, "glossary": {}, "highlight": []}
    run = tmp_path / "run"
    run.mkdir()
    rec = R.render(run, copy.deepcopy(edl), short_video, Ledger(run / "ledger.json"), words=[], callouts=False,
                   loudnorm=False, cache=False, log=lambda m: None)
    cut = run / "cut_v1.mp4"
    assert frames_of(cut) == sum(s["frames"] for s in rec["segments"]) == 54
    assert abs(stream_s(cut, "v") - stream_s(cut, "a")) < 1.5 / 30


@needs_ffmpeg
def test_a_part_inside_the_picture_is_encoded_as_before(tmp_path, short_video):
    seg = frame_align([Segment(0, 0.2, 1.0, ["s1"], ["body"])], 30.0)[0]
    plain = R.segment_encode(short_video, seg, path_for(seg), tmp_path)
    ended = R.segment_encode(short_video, seg, path_for(seg), tmp_path, video_end=R.video_end(short_video))
    assert plain.args == ended.args and "tpad" not in " ".join(ended.args)


def span(i, turn, t_in, t_out):
    return {"id": f"s{i}", "turn_id": turn, "role": "body", "t_in": t_in, "t_out": t_out}


def test_abutting_spans_of_two_turns_play_no_frame_twice():
    segs = frame_align(merge_segments([span(1, 1, 9.0, 10.02), span(2, 2, 10.02, 11.0)]), 30.0)
    assert len(segs) == 2
    assert segs[1].t_in == pytest.approx(segs[0].t_out) == pytest.approx(301 / 30)  # the floor said frame 300 again
    assert segs[1].frames == 29 and segs[0].frames + segs[1].frames == 60


def test_a_gap_or_an_earlier_start_keeps_its_own_in_point():
    gap = frame_align(merge_segments([span(1, 1, 9.0, 10.02), span(2, 2, 10.2, 11.0)]), 30.0)
    assert gap[1].t_in == pytest.approx(306 / 30)
    moved = frame_align(merge_segments([span(1, 1, 9.0, 10.02), span(2, 2, 4.0, 5.0)]), 30.0)
    assert moved[1].t_in == pytest.approx(4.0) and moved[1].frames == 30


@needs_ffmpeg
def test_a_late_fading_still_loops_from_its_own_start_and_fades_as_before(tmp_path):
    """ov_perf.py: a still fading in 9 s into a segment used to loop (and fade) every frame from 0."""
    from PIL import Image
    from montivo.engine.captions import Overlay
    src = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30:d=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)], check=True)
    png = tmp_path / "still.png"
    Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(png)
    seg = frame_align([Segment(0, 0.0, 11.0, ["s1"], ["body"])], 30.0)[0]
    ov = Overlay(png=png, x=0, y=0, t_in=9.0, t_out=10.5, fade_s=0.3)
    enc = R.segment_encode(src, seg, path_for(seg), tmp_path, [ov])
    loop_t = float(enc.args[enc.args.index(str(png)) - 2])
    assert loop_t <= ov.t_out - ov.t_in + 0.2  # was t_out + 0.1 = 10.6 s of looped frames
    R._ffmpeg([*enc.args, str(enc.out)])
    assert frames_of(enc.out) == seg.frames

    def luma(t):  # the still's corner of the 1080x1920 frame, mean luma at cut second t
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(enc.out), "-frames:v", "1", "-vf",
                              "crop=60:60:10:10,scale=1:1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        return out[0]
    assert luma(8.5) < 30 and luma(9.15) not in range(0, 30) and luma(9.15) < 220 and luma(9.8) > 220
    assert luma(10.6) < 30
