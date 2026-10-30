"""Timeline media for the UI: a filmstrip sprite and waveform peaks per cut, one ffmpeg pass each, cached under
<run>/ui/cache/ (never next to the engine's outputs) and rebuilt when the cut is newer than the cache."""
from __future__ import annotations

import json
import subprocess
import threading
from array import array
from pathlib import Path

THUMB_W, THUMB_H = 54, 96  # one 9:16 frame of the sprite
THUMB_STEP_S = 0.5
PEAKS_RATE = 100  # peaks per second of cut
PEAKS_SR = 8000

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock(p: Path) -> threading.Lock:
    with _guard:
        return _locks.setdefault(str(p), threading.Lock())


def fresh(out: Path, src: Path) -> bool:
    return out.is_file() and out.stat().st_mtime >= src.stat().st_mtime


def filmstrip(src: Path, out: Path) -> Path:
    """Every THUMB_STEP_S of the cut as one row of THUMB_W x THUMB_H frames: frame k shows time k * THUMB_STEP_S."""
    with _lock(out):
        if fresh(out, src):
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        frames = max(1, min(1200, int(probe_duration(src) / THUMB_STEP_S) + 1))  # JPEG stays under 65535 px wide
        vf = (f"fps={1 / THUMB_STEP_S:g}:round=down,scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase,"
              f"crop={THUMB_W}:{THUMB_H},tile={frames}x1")
        tmp = out.with_name(out.stem + ".tmp.jpg")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", vf, "-frames:v", "1",
                        "-q:v", "5", str(tmp)], capture_output=True, check=True)
        tmp.replace(out)
        return out


def head_thumb(src: Path, t: float, box: list[float], out: Path, size: int = 120) -> Path:
    """A square crop around a head box (x0, y0, x1, y1 on the tracked video), with room around it, at time t."""
    with _lock(out):
        if fresh(out, src):
            return out
        x0, y0, x1, y1 = box
        side = max(x1 - x0, y1 - y0) * 1.6
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        out.parent.mkdir(parents=True, exist_ok=True)
        crop = (f"crop='min({side:.0f},iw)':'min({side:.0f},ih)':'max(0,min(iw-ow,{cx - side / 2:.0f}))'"
                f":'max(0,min(ih-oh,{cy - side / 2:.0f}))',scale={size}:{size}:force_original_aspect_ratio=increase,"
                f"crop={size}:{size}")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(src),
                        "-frames:v", "1", "-vf", crop, "-q:v", "4", str(out)], capture_output=True, check=True)
        return out


def small_copy(src: Path, out: Path) -> Path:
    """A 720x1280 copy of a cut (H.264 CRF 26, AAC 128k, faststart), redone only when the cut is newer."""
    with _lock(out):
        if fresh(out, src):
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.mp4")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", "scale=720:1280",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(tmp)], capture_output=True, check=True)
        tmp.replace(out)
        return out


def probe_duration(src: Path, timeout: float | None = None) -> float:
    """ffprobe's duration, 0.0 when it has none; past `timeout` seconds ffprobe is killed and TimeoutExpired raised."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(src)],
                       capture_output=True, text=True, timeout=timeout)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def peaks_from_pcm(pcm: bytes, sr: int = PEAKS_SR, rate: int = PEAKS_RATE) -> list[float]:
    """Signed 16-bit mono PCM -> the absolute peak of every 1/rate s, 0..1."""
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    hop = max(1, sr // rate)
    out = []
    for i in range(0, len(a), hop):
        chunk = a[i:i + hop]
        out.append(round(max(-min(chunk), max(chunk)) / 32768, 3))
    return out


def peaks(src: Path, out: Path) -> dict:
    with _lock(out):
        if fresh(out, src):
            return json.loads(out.read_text())
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:a:0", "-ac", "1",
                            "-ar", str(PEAKS_SR), "-f", "s16le", "-"], capture_output=True, check=True)
        data = {"rate": PEAKS_RATE, "peaks": peaks_from_pcm(r.stdout)}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, separators=(",", ":")))
        return data


def draft_bed_peaks(track_peaks: dict, music: dict, duration: float) -> dict:
    """A draft's music bed waveform: the track's loop from the cut's start, each 10 ms at the gain the live player gives
    it (Live.ts bedGain: level over the track's loudness, the duck along the speech with its ramps, the fades)."""
    rate, tp = track_peaks["rate"], track_peaks["peaks"]
    off = int(round(float(music.get("start_s") or 0.0) * rate))  # M4: the bed starts this far into the loop
    att, rel = music["attack_s"], music["release_s"]
    fin, fout = max(1e-3, music["fade_in_s"]), max(1e-3, music["fade_out_s"])
    out = []
    for i in range(int(round(duration * rate))):
        c = i / rate
        duck = sum(min(1.0, max(0.0, (c - (a - att)) / att)) * min(1.0, max(0.0, (b + rel - c) / rel)) for a, b in music["ducked"])
        fade = max(0.0, min(1.0, c / fin, (duration - c) / fout))
        g = 10 ** ((music["gain_db"] + music["duck_db"] * duck) / 20) * fade
        out.append(round(tp[(i + off) % len(tp)] * g, 3) if tp else 0.0)
    return {"rate": rate, "peaks": out}


def draft_filmstrip(source_strip: Path, segments: list[dict], duration: float, title_s: float, out: Path) -> Path:
    """A draft's filmstrip without a render: the cut's sprite (one tile every THUMB_STEP_S of cut time) put together
    from a filmstrip of the source (`filmstrip` run on the edit proxy, tile k = source time k * THUMB_STEP_S) through
    the draft's segments (engine/preview.py: t_in, cut_t0, frames at fps); the title card's tiles stay black."""
    from PIL import Image
    src = Image.open(source_strip)
    n_src = max(1, src.width // THUMB_W)
    n = max(1, min(1200, int(duration / THUMB_STEP_S) + 1))
    img = Image.new("RGB", (n * THUMB_W, THUMB_H))
    for k in range(n):
        c = k * THUMB_STEP_S
        seg = next((s for s in reversed(segments) if c >= s["cut_t0"] - 1e-6), None)
        if seg is None or c < title_s:
            continue
        j = min(n_src - 1, max(0, round((seg["t_in"] + c - seg["cut_t0"]) / THUMB_STEP_S)))
        img.paste(src.crop((j * THUMB_W, 0, (j + 1) * THUMB_W, THUMB_H)), (k * THUMB_W, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=80)
    return out


def draft_peaks(source_peaks: dict, segments: list[dict], fps: float, title_s: float) -> dict:
    """A draft's waveform from the source's peaks (`peaks` of the source audio) through the draft's segments."""
    rate, sp = source_peaks["rate"], source_peaks["peaks"]
    out = [0.0] * int(round(title_s * rate))
    for s in segments:
        a = int(round(s["t_in"] * rate))
        out += sp[a:a + int(round(s["frames"] / fps * rate))]
    return {"rate": rate, "peaks": out}
