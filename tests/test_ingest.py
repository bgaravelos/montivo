"""Offline tests for ingest.py: a phone clip's display rotation is read and baked once, so every later stage sees the
frame as it is shown (stored 320x240 with rotation 90 is shown 240x320)."""
import subprocess

from montivo.engine.ingest import ffprobe, make_upright
from tests.test_ui import _clip


def rotated(tmp_path, degrees):
    plain, rot = tmp_path / "plain.mp4", tmp_path / f"rot{degrees}.mp4"
    _clip(plain, size="320x240", seconds=0.5)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-display_rotation:v:0", str(degrees), "-i", str(plain),
                    "-c", "copy", str(rot)], check=True)
    return rot


def test_the_probe_reads_the_display_rotation(tmp_path):
    assert ffprobe(rotated(tmp_path, 90)).rotation in (90, 270)
    assert ffprobe(rotated(tmp_path, 180)).rotation == 180
    _clip(tmp_path / "up.mp4", size="320x240", seconds=0.5)
    assert ffprobe(tmp_path / "up.mp4").rotation == 0


def test_baking_gives_the_shown_size_and_no_rotation(tmp_path):
    for deg in (90, 270):
        p = make_upright(rotated(tmp_path, deg), tmp_path / f"upright{deg}.mp4")
        assert (p.width, p.height, p.rotation) == (240, 320, 0) and p.has_audio
    p = make_upright(rotated(tmp_path, 180), tmp_path / "upright180.mp4", hflip=True)
    assert (p.width, p.height, p.rotation) == (320, 240, 0)
