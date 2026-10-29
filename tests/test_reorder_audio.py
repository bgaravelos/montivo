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
