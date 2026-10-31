"""Offline: letterboxed sources (heli-02: a 1920x1012 picture inside 1080 rows, the bars stayed in every 9:16 frame).
Ingest finds the picture box with cropdetect on the proxy, the reframe keeps every crop inside it, and a source
without bars plans exactly as before."""
import shutil
import subprocess

import pytest
from PIL import Image

from montivo.engine.ingest import detect_picture, picture_from_crop
from montivo.engine.reframe import SegmentPath, picture_box, plan_segment
from montivo.engine.render import Segment, apply_bias, render_segment_video

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

SOURCE = {"width": 1920, "height": 1080, "fps": 30.0}
BOX = {"x": 0, "y": 34, "w": 1920, "h": 1008}


def test_picture_from_cropdetect_on_the_proxy():
    # heli-02's proxy: 960x506 at y 16 inside 960x540 (bars 16 and 18 px); inward by a proxy pixel, even numbers
    assert picture_from_crop((960, 506, 0, 16), 960, 540, 1920, 1080) == BOX
    assert picture_from_crop((960, 540, 0, 0), 960, 540, 1920, 1080) is None  # no bars
    assert picture_from_crop((960, 530, 0, 0), 960, 540, 1920, 1080) is None  # one dark edge only
    assert picture_from_crop((960, 534, 0, 3), 960, 540, 1920, 1080) is None  # bars under 1.5 % of the height
    pillar = picture_from_crop((720, 540, 120, 0), 960, 540, 1920, 1080)  # a 4:3 picture inside 16:9
    assert pillar == {"x": 242, "y": 0, "w": 1436, "h": 1080}


def test_the_crop_stays_inside_the_picture():
    path = plan_segment(10.0, 12.0, {**SOURCE, "picture": BOX}, None)
    assert (path.crop_h, path.crop_y) == (1008, 34) and path.crop_w == 568  # 1008 * 9 / 16, even
    assert all(0 <= x <= 1920 - 568 for x in path.xs)
    feed = plan_segment(10.0, 12.0, {**SOURCE, "picture": BOX}, None, aspect=(4, 5))
    assert (feed.crop_h, feed.crop_y, feed.crop_w) == (1008, 34, 806)
    pillar = {"x": 242, "y": 0, "w": 1436, "h": 1080}
    p = plan_segment(10.0, 12.0, {**SOURCE, "picture": pillar}, None, aspect=(1, 1))
    assert p.crop_w == 1080 and all(242 <= x <= 242 + 1436 - 1080 for x in p.xs)
    apply_bias(p, -9999, pillar["w"], pillar["x"])
    assert min(p.xs) == 242
    # no picture box: the whole frame, as before
    assert picture_box(SOURCE) == (0, 0, 1920, 1080)
    plain = plan_segment(10.0, 12.0, SOURCE, None)
    assert (plain.crop_w, plain.crop_h, plain.crop_y) == (608, 1080, 0)


@pytest.fixture
def letterboxed(tmp_path):
    """A 640x360 clip whose picture is 640x300 between two black bars of 30 rows."""
    src = tmp_path / "lb.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x300:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=2", "-vf", "pad=640:360:0:30:black",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
                   check=True)
    return src


@needs_ffmpeg
def test_ingest_finds_the_bars_and_the_render_drops_them(tmp_path, letterboxed):
    box = detect_picture(letterboxed, 640, 360, 1280, 720)  # as if the proxy were half the source
    assert box == {"x": 0, "y": 62, "w": 1280, "h": 596}
    box = detect_picture(letterboxed, 640, 360, 640, 360)
    assert box["y"] >= 30 and box["y"] + box["h"] <= 330
    path = plan_segment(0.0, 1.0, {"width": 640, "height": 360, "fps": 30.0, "picture": box}, None)
    rd = tmp_path / "render"
    rd.mkdir()
    seg = Segment(index=0, t_in=0.0, t_out=1.0, span_ids=["s1"], frames=30, fps=30.0)
    out = render_segment_video(letterboxed, seg, SegmentPath(**{**path.__dict__, "xs": path.xs[:30]}), rd, [])
    png = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(out), "-frames:v", "1", str(png)], check=True)
    im = Image.open(png).convert("L")
    w, h = im.size
    for row in (0, 1, h - 2, h - 1):  # no black row at the top or the bottom of the 9:16 frame
        assert sum(im.getpixel((x, row)) for x in range(w)) / w > 40, row
    assert detect_picture(tmp_path / "missing.mp4", 640, 360, 640, 360) is None
