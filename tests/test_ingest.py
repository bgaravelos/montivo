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


def test_the_upright_copy_is_made_fast_and_no_taller_than_the_cut(tmp_path, monkeypatch):
    # bug bash 28/10: a 90 s iPhone 4K HDR clip took 479 s to standardise (x264 medium at 2160x3840) while the hosted
    # demo's one job blocked every judge; the cut is 1080x1920, so the copy stops at the cut's height, encoded veryfast
    from montivo.engine import ingest as I
    seen = []
    real = I.run_ffmpeg
    monkeypatch.setattr(I, "run_ffmpeg", lambda cmd, what, log=None: seen.append(cmd) or real(cmd, what, log))
    monkeypatch.setattr(I, "UPRIGHT_MAX_H", 160)
    p = make_upright(rotated(tmp_path, 90), tmp_path / "upright.mp4")
    assert (p.width, p.height) == (120, 160)
    assert seen[-1][seen[-1].index("-preset") + 1] == "veryfast"
    monkeypatch.setattr(I, "UPRIGHT_MAX_H", 1920)
    p = make_upright(rotated(tmp_path, 180), tmp_path / "small.mp4")  # under the cap: the size is kept
    assert (p.width, p.height) == (320, 240)


def test_the_wav_is_the_first_audio_stream_as_the_render_reads_it(tmp_path):
    """A clip whose second audio stream is the default: ffmpeg's own pick is that one, the render's [i:a] the first;
    Voice must hear what the cut plays (audit 31/10)."""
    from array import array
    from montivo.engine.ingest import make_wav
    src = tmp_path / "two.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=1",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=1", "-f", "lavfi", "-i", "sine=f=1500:r=48000:d=1",
                    "-filter_complex", "[2:a]pan=stereo|c0=c0|c1=c0[st]", "-map", "0:v", "-map", "1:a", "-map", "[st]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-disposition:a:0", "0",
                    "-disposition:a:1", "default", str(src)], check=True)  # a phone's second track marked default
    wav = make_wav(src, tmp_path / "a.wav")
    pcm = array("h", wav.read_bytes()[44:])
    crossings = sum(1 for a, b in zip(pcm, pcm[1:]) if (a < 0) != (b < 0))  # two a cycle: 440 Hz ~880, 1500 Hz ~3000
    assert 800 < crossings < 960


needs_ffmpeg = __import__("pytest").mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")


def _late_audio_clip(tmp_path):
    """5 s of picture whose audio stream starts 0.7 s in (an -itsoffset mux), with a 1 kHz beep 1.0 s into that
    stream: at about 1.7 s of the clip, where the render's cut (timestamps) and the picture have it."""
    beep, video, clip = tmp_path / "beep.m4a", tmp_path / "v.mp4", tmp_path / "late_audio.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=1000:r=48000:d=0.2",
                    "-af", "adelay=1000:all=1,apad=whole_dur=4", "-c:a", "aac", str(beep)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video), "-itsoffset", "0.7", "-i", str(beep),
                    "-map", "0:v", "-map", "1:a", "-c", "copy", str(clip)], check=True)
    return clip


def _beep_at(media):
    from array import array
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    pcm = array("h", raw)
    return next(i for i, v in enumerate(pcm) if abs(v) > 1500) / 8000


@needs_ffmpeg
def test_the_wav_and_the_edit_audio_start_at_the_clips_time_zero(tmp_path):
    """An audio stream that starts after the picture: the wav and the live preview's FLAC used to begin at its first
    sample, so Voice timed every word 0.7 s early against the cut and Live played the speech 0.7 s ahead of the lips."""
    from montivo.engine.ingest import make_edit_proxy, make_wav
    clip = _late_audio_clip(tmp_path)
    assert abs(_beep_at(make_wav(clip, tmp_path / "a.wav")) - 1.7) < 0.05
    make_edit_proxy(clip, tmp_path / "edit.mp4", tmp_path / "edit.flac", ffprobe(clip))
    assert abs(_beep_at(tmp_path / "edit.flac") - 1.7) < 0.05
    plain = tmp_path / "plain.mp4"  # a stream from 0 keeps its times
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=1000:r=48000:d=0.2", "-af", "adelay=1200:all=1,apad=whole_dur=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(plain)], check=True)
    assert abs(_beep_at(make_wav(plain, tmp_path / "p.wav")) - 1.2) < 0.05
