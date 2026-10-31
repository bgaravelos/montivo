"""Offline: a cut whose clips are out of source order (a reorder, the hook A/B, insert --before) keeps all its audio.
ffmpeg before 8.0 (6.1.1 on Ubuntu 24.04, 7.0.2 measured, 29/10) gave no samples from the first segment that starts
earlier in the source than the one before it when every segment was an atrim of one input: the cut's audio ended
there (9 s of audio in a 22 s video) and some reorders hung the render. Each segment now reads its own input."""
import array
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from montivo.engine.render import AUDIO_RATE, Segment, render_audio

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def pcm(path: Path) -> array.array:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    a = array.array("f")
    a.frombytes(raw)
    return a


def rms_db(x) -> float:
    return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(1, len(x))) + 1e-12)


def test_segments_out_of_source_order_keep_their_audio(tmp_path):
    src = tmp_path / "noise.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=6:c=white:r=48000:a=0.05:s=5",
                    "-ac", "2", str(src)], check=True)
    times = [(4.0, 5.0), (0.5, 1.5), (2.5, 3.5)]  # the last clip of the source first, then two earlier ones
    s = [Segment(index=i, t_in=a, t_out=b, span_ids=[f"s{i + 1}"], frames=round((b - a) * 30), fps=30.0)
         for i, (a, b) in enumerate(times)]
    out, _, _ = render_audio(src, s, tmp_path, loudnorm=False, source_s=6.0)
    x = pcm(out)
    total = sum(round(g.duration * AUDIO_RATE) for g in s)
    assert abs(len(x) - total) <= 1024, (len(x), total)  # AAC priming / padding only; 6.1 gave 1.0 s of 3.0 s
    for k in range(len(s)):  # every segment sounds (the noise), none is the silence of a dropped segment
        a = round((k + 0.2) * AUDIO_RATE)
        assert rms_db(x[a:a + round(0.6 * AUDIO_RATE)]) > -40, k


def test_a_clip_past_the_end_of_the_audio_moved_first_keeps_the_cut_audio(tmp_path):
    """The case CLAUDE.md lists as left on ffmpeg 7.1 (cloud review 29/10): the video runs past the audio stream (a
    phone clip, or a trim that cut the audio short) and a clip from that tail is moved first. On this Mac's ffmpeg
    9.0.2 and the static 8.1 build the cut keeps its full length and the later clip its sound; no 7.1 build is on the
    Mac to reproduce the silence (31/10), so this guards the behaviour that works rather than a fix."""
    src = tmp_path / "short_audio.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=4", "-f", "lavfi",
                    "-i", "anoisesrc=d=3:c=white:r=48000:a=0.05:s=5", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "4", str(src)], check=True)
    times = [(3.2, 3.9), (0.5, 1.5)]  # the first clip lies wholly past the audio's end (3.0 s)
    s = [Segment(index=i, t_in=a, t_out=b, span_ids=[f"s{i + 1}"], frames=round((b - a) * 30), fps=30.0)
         for i, (a, b) in enumerate(times)]
    out, _, _ = render_audio(src, s, tmp_path, loudnorm=False, source_s=4.0)
    x = pcm(out)
    total = sum(round(g.duration * AUDIO_RATE) for g in s)
    assert abs(len(x) - total) <= 1024, (len(x), total)
    assert rms_db(x[round(0.1 * AUDIO_RATE):round(0.6 * AUDIO_RATE)]) < -60  # the tail has no sound to play
    assert rms_db(x[round(0.9 * AUDIO_RATE):round(1.6 * AUDIO_RATE)]) > -40  # the later clip does
