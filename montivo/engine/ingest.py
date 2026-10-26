"""Ingest: ffprobe the source, make the 960 px proxy (source fps), the 24 kHz mono wav, and upload the proxy once.

The proxy's Files API id is the one every Spark call reuses. SAM gets its own upload (a resampled copy when the
source is long), see track.py.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import PROXY_MAX_DIM
from ..ledger import Entry, Ledger, Timer
from .api import dump_json, upload_file


@dataclass
class Probe:
    width: int
    height: int
    fps: float
    duration: float
    bytes: int
    has_audio: bool
    codec: str


@dataclass
class Ingested:
    source: Path
    probe: Probe
    proxy: Path
    proxy_probe: Probe
    wav: Path
    file_id: str
    upload_seconds: float

    def to_json(self) -> dict:
        d = asdict(self)
        d["source"], d["proxy"], d["wav"] = str(self.source), str(self.proxy), str(self.wav)
        return d


def ffprobe(path: Path) -> Probe:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate:format=duration,size",
         "-of", "json", str(path)], check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise SystemExit(f"{path}: no video stream")
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    if rate in ("0/0", "0"):
        rate = video.get("r_frame_rate") or "30/1"
    num, den = (rate.split("/") + ["1"])[:2]
    fps = float(num) / float(den) if float(den) else float(num)
    return Probe(width=int(video["width"]), height=int(video["height"]), fps=round(fps, 3),
                 duration=float(data["format"]["duration"]), bytes=int(data["format"]["size"]),
                 has_audio=has_audio, codec=video.get("codec_name", "?"))


def even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def proxy_size(p: Probe, max_dim: int = PROXY_MAX_DIM) -> tuple[int, int]:
    scale = min(1.0, max_dim / max(p.width, p.height))
    return even(p.width * scale), even(p.height * scale)


def make_proxy(src: Path, dst: Path, p: Probe, max_dim: int = PROXY_MAX_DIM, fps: float | None = None) -> Probe:
    """H.264 proxy at most max_dim px on the long side, source fps unless fps is given, AAC audio if present."""
    w, h = proxy_size(p, max_dim)
    vf = [f"scale={w}:{h}:flags=lanczos"]
    if fps:
        vf.insert(0, f"fps={fps}")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", "-map_metadata", "-1", str(dst)]
    subprocess.run(cmd, check=True)
    return ffprobe(dst)


def make_wav(src: Path, dst: Path, rate: int = 24000, start: float | None = None, seconds: float | None = None) -> Path:
    """24 kHz mono 16-bit PCM wav, the format the Voice endpoint measures best on."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", "-map_metadata", "-1", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def ingest(source: Path, out: Path, ledger: Ledger, log=print) -> Ingested:
    out.mkdir(parents=True, exist_ok=True)
    probe = ffprobe(source)
    log(f"ingest: {source.name} {probe.width}x{probe.height} {probe.fps:g} fps {probe.duration:.1f} s audio={probe.has_audio}")
    if not probe.has_audio:
        raise SystemExit("Montivo needs footage with an audio track (Voice is the clock).")

    proxy = out / "proxy.mp4"
    wav = out / "audio.wav"
    with Timer() as t_proxy:
        pp = make_proxy(source, proxy, probe)
        make_wav(source, wav)
    ledger.add(Entry(stage="ingest.ffmpeg", model="ffmpeg", seconds=t_proxy.seconds, cost_usd=0.0,
                     note=f"proxy {pp.width}x{pp.height} @ {pp.fps:g} fps, wav 24 kHz mono"))
    log(f"ingest: proxy {pp.width}x{pp.height} ({proxy.stat().st_size / 1e6:.1f} MB), wav {wav.stat().st_size / 1e6:.1f} MB in {t_proxy.seconds}s")

    up = upload_file(proxy, "video/mp4")
    ledger.add(Entry(stage="ingest.upload", model="files", seconds=up["upload_seconds"], cost_usd=0.0,
                     note=f"file_id {up['id']}"))
    log(f"ingest: uploaded proxy as {up['id']} in {up['upload_seconds']}s")

    ing = Ingested(source=source, probe=probe, proxy=proxy, proxy_probe=pp, wav=wav,
                   file_id=up["id"], upload_seconds=up["upload_seconds"])
    dump_json(out / "ingest.json", {**ing.to_json(), "upload": {k: v for k, v in up.items() if k != "upload_seconds"}})
    return ing
