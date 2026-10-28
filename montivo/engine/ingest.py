"""Ingest: ffprobe the source, make the 960 px proxy (source fps), the 24 kHz mono wav, find the camera cuts in the
proxy (scdet, `shots`), and upload the proxy once.

The proxy's Files API id is the one every Spark call reuses. SAM gets its own upload (a resampled copy when the
source is long), see track.py.
"""
from __future__ import annotations

import functools
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
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
    rotation: int = 0  # display rotation in degrees (phone clips: frames stored landscape, shown portrait)
    r_fps: float = 0.0  # the stream's base rate (r_frame_rate); far from fps (the average) = variable frame rate
    pix_fmt: str = ""
    transfer: str = ""  # color_transfer: arib-std-b67 = HLG (the iPhone's HDR default), smpte2084 = PQ (HDR10)
    primaries: str = ""
    audio_codec: str = ""  # the first audio stream's codec (pcm_* from cameras: make_upright cannot copy it to .mp4)


@dataclass
class Ingested:
    source: Path
    probe: Probe
    proxy: Path
    proxy_probe: Probe
    wav: Path
    file_id: str
    upload_seconds: float
    shots: list[float] = field(default_factory=list)  # camera cuts in source seconds (detect_shots)

    def to_json(self) -> dict:
        d = asdict(self)
        d["source"], d["proxy"], d["wav"] = str(self.source), str(self.proxy), str(self.wav)
        return d


def ffprobe(path: Path) -> Probe:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,pix_fmt,color_transfer,"
         "color_primaries:stream_tags=rotate"
         ":stream_side_data=rotation:format=duration,size",
         "-of", "json", str(path)], check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise SystemExit(f"{path}: no video stream")
    audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    has_audio = audio is not None
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    if rate in ("0/0", "0"):
        rate = video.get("r_frame_rate") or "30/1"
    fps = _rate(rate)
    r_fps = _rate(video.get("r_frame_rate") or rate)
    rot = next((sd["rotation"] for sd in video.get("side_data_list") or [] if "rotation" in sd),
               (video.get("tags") or {}).get("rotate", 0))
    return Probe(width=int(video["width"]), height=int(video["height"]), fps=round(fps, 3),
                 duration=float(data["format"]["duration"]), bytes=int(data["format"]["size"]),
                 has_audio=has_audio, codec=video.get("codec_name", "?"), rotation=int(round(float(rot))) % 360,
                 r_fps=round(r_fps, 3), pix_fmt=video.get("pix_fmt", ""), transfer=video.get("color_transfer", ""),
                 primaries=video.get("color_primaries", ""), audio_codec=(audio or {}).get("codec_name", ""))


def _rate(rate: str) -> float:
    num, den = (rate.split("/") + ["1"])[:2]
    return float(num) / float(den) if float(den) else float(num)


# the frame rates a render keeps; anything else (the DJI's 600/19 = 31.58, a phone's variable rate, 120 / 240 fps slow
# motion) becomes the nearest of these at ingest, at most 60
STANDARD_FPS = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)
VFR_TOLERANCE = 0.003  # average vs base rate
HDR_TRANSFERS = {"arib-std-b67": "HLG", "smpte2084": "PQ"}


def standard_fps(p: Probe) -> float | None:
    """The frame rate the source should be re-encoded to, or None when it already is one of STANDARD_FPS at a
    constant rate."""
    base = p.r_fps or p.fps
    vfr = bool(p.r_fps) and abs(p.fps - p.r_fps) > VFR_TOLERANCE * p.r_fps
    f = p.fps if vfr else base
    while f > 60.5:
        f /= 2
    snap = min(STANDARD_FPS, key=lambda s: abs(s - f))
    if not vfr and abs(snap - base) <= 0.01:
        return None
    return snap


def hdr_kind(p: Probe) -> str | None:
    """"HLG" or "PQ" when the stream says it is HDR, else None."""
    return HDR_TRANSFERS.get(p.transfer)


def bit_depth(p: Probe) -> int:
    m = re.search(r"p(\d+)(le|be)?$", p.pix_fmt or "")
    return int(m.group(1)) if m and m.group(1) in ("9", "10", "12", "14", "16") else 8


@functools.lru_cache(maxsize=1)
def has_zscale() -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return any(line.split()[1:2] == ["zscale"] for line in out.splitlines() if line.strip())


def to_sdr_chain(kind: str | None, zscale: bool | None = None) -> str:
    """The filters that bring a frame to 8-bit limited-range BT.709: an HDR frame tone-mapped (zscale to linear
    light, hable, back to BT.709) where this ffmpeg has zscale (the Modal image's Debian build does); without it HLG,
    which is made to look right on an SDR BT.2020 screen, goes through colorspace from BT.2020; PQ cannot be read
    without zscale and raises ValueError (ingest refuses the clip with that message)."""
    zscale = has_zscale() if zscale is None else zscale
    if kind and zscale:
        return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    if kind == "HLG":
        return "colorspace=all=bt709:iall=bt2020:fast=0:format=yuv420p"
    if kind == "PQ":
        raise ValueError("this clip is HDR10 (PQ) and this ffmpeg has no zscale to tone-map it: export it as SDR "
                         "(H.264, Rec. 709) and upload that, or use an iPhone / HLG clip")
    return "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"


class FfmpegError(subprocess.CalledProcessError):
    """A failed ffmpeg step whose message carries the end of ffmpeg's stderr (CalledProcessError's does not)."""

    def __str__(self) -> str:
        return f"{super().__str__()}\n{(self.stderr or '').strip()[-1500:]}"


def run_ffmpeg(cmd: list[str], what: str, log=None) -> None:
    """Run one ffmpeg step with its stderr captured; on failure the end of it goes to `log` (run.log) and into the
    FfmpegError (it used to reach only the terminal of whoever started the run)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if log:
            log(f"ingest: {what} failed (ffmpeg exit {proc.returncode}):\n{proc.stderr.strip()[-1500:]}")
        raise FfmpegError(proc.returncode, cmd, proc.stdout, proc.stderr)


def even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def proxy_size(p: Probe, max_dim: int = PROXY_MAX_DIM) -> tuple[int, int]:
    scale = min(1.0, max_dim / max(p.width, p.height))
    return even(p.width * scale), even(p.height * scale)


def make_proxy(src: Path, dst: Path, p: Probe, max_dim: int = PROXY_MAX_DIM, fps: float | None = None, log=None) -> Probe:
    """H.264 proxy at most max_dim px on the long side, source fps unless fps is given, AAC audio if present."""
    w, h = proxy_size(p, max_dim)
    vf = [f"scale={w}:{h}:flags=lanczos"]
    if fps:
        vf.insert(0, f"fps={fps}")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", "-map_metadata", "-1", str(dst)]
    run_ffmpeg(cmd, "proxy", log)
    return ffprobe(dst)


SHOT_THRESHOLD = 10.0  # scdet score of a camera cut: single-camera talking heads peak at 3.3 (runs 012-015), cuts ~28
SHOT_MIN_S = 0.5  # a cut closer than this to the one before (a flash, a fast whip) is not a new shot
_SCD = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),\s*lavfi\.scd\.time:\s*([0-9.]+)")


def detect_shots(proxy: Path, threshold: float = SHOT_THRESHOLD, min_s: float = SHOT_MIN_S, log=None) -> list[float]:
    """The camera cuts of a clip (multi-camera footage like a TEDx talk: wide stage shots and close-ups), from
    ffmpeg's scdet on the proxy, $0 and a few seconds: the cut times in seconds, at least min_s apart. The reframe
    splits its windows there (reframe.plan_segment), so the crop never pans from one shot's head to the next's."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(proxy), "-map", "0:v:0", "-an",
                           "-vf", f"scdet=threshold={threshold:g}", "-f", "null", "-"], capture_output=True, text=True)
    if proc.returncode != 0:
        if log:
            log(f"ingest: shot detection failed (ffmpeg exit {proc.returncode}), the crop treats the clip as one shot")
        return []
    cuts: list[float] = []
    for score, t in _SCD.findall(proc.stderr):
        t = round(float(t), 3)
        if float(score) >= threshold and t > 0 and (not cuts or t - cuts[-1] >= min_s):
            cuts.append(t)
    return cuts


def make_wav(src: Path, dst: Path, rate: int = 24000, start: float | None = None, seconds: float | None = None,
             log=None) -> Path:
    """24 kHz mono 16-bit PCM wav, the format the Voice endpoint measures best on."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", "-map_metadata", "-1", str(dst)]
    run_ffmpeg(cmd, "wav", log)
    return dst


def make_upright(src: Path, dst: Path, hflip: bool = False, log=None, fps: float | None = None,
                 sdr: str | None = None) -> Probe:
    """Re-encode the footage once, upright (the decoder applies the display rotation) and mirrored when hflip
    (selfie / mirror-mode clips where on-screen text reads backwards): libx264 crf 18 at the source fps, audio copied
    untouched so the Voice clock is unchanged. Everything downstream (proxy, wav, SAM, render) reads this file, so
    boxes, crops and the probed size all describe the frame as it is shown. `fps` makes it that constant frame rate
    (a variable or odd rate, see standard_fps), `sdr` is to_sdr_chain's filters for an HDR or 10-bit source (the copy
    is then tagged BT.709)."""
    vf = [x for x in (sdr, f"fps={fps:g}" if fps else "", "hflip" if hflip else "") if x]
    tags = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"] if sdr else []
    # camera PCM (10-bit XAVC S, .MOV) cannot be copied into .mp4 by older ffmpeg builds: lossless ALAC instead
    audio = "alac" if ffprobe(src).audio_codec.startswith("pcm_") else "copy"
    run_ffmpeg(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
                *(["-vf", ",".join(vf)] if vf else []), *(["-fps_mode", "cfr"] if fps else []),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", *tags, "-c:a", audio,
                "-movflags", "+faststart", str(dst)], "upright copy", log)
    return ffprobe(dst)


def standardise(p: Probe, hflip: bool = False) -> tuple[list[str], float | None, str | None]:
    """What the source needs before anything reads it: (reasons, the frame rate to make it, the SDR filters), all
    empty when it can be read as it is. Raises ValueError for footage this ffmpeg cannot bring to SDR."""
    reasons, sdr = [], None
    if hflip:
        reasons.append("mirrored")
    if p.rotation:
        reasons.append(f"rotation {p.rotation} baked")
    fps = standard_fps(p)
    if fps:
        vfr = bool(p.r_fps) and abs(p.fps - p.r_fps) > VFR_TOLERANCE * p.r_fps
        reasons.append(f"{'variable frame rate' if vfr else f'{p.fps:g} fps'} -> {fps:g} fps")
    kind, depth = hdr_kind(p), bit_depth(p)
    if kind or depth > 8:
        sdr = to_sdr_chain(kind)
        reasons.append(f"HDR {kind} tone-mapped to SDR BT.709" + ("" if has_zscale() else " (colorspace, no zscale)")
                       if kind else f"{depth}-bit -> 8-bit BT.709")
    return reasons, fps, sdr


def ingest(source: Path, out: Path, ledger: Ledger, log=print, hflip: bool = False) -> Ingested:
    out.mkdir(parents=True, exist_ok=True)
    original = source
    probe = ffprobe(source)
    log(f"ingest: {source.name} {probe.width}x{probe.height} {probe.fps:g} fps {probe.duration:.1f} s audio={probe.has_audio}")
    if not probe.has_audio:
        raise SystemExit("Montivo needs footage with an audio track (Voice is the clock).")
    rotation = probe.rotation
    try:
        reasons, std_fps, sdr = standardise(probe, hflip)
    except ValueError as e:
        log(f"ingest: {e}")
        raise SystemExit(f"Montivo cannot read this clip: {e}.")
    # a rotated clip's stored size is not the shown size the crop and the boxes need; an odd or variable frame rate,
    # slow motion over 60 fps and HDR / 10-bit (an iPhone's default) are made standard once, here
    if reasons:
        upright = out / ("source_hflip.mp4" if hflip else "source_upright.mp4")
        what = ", ".join(reasons)
        with Timer() as t_flip:
            probe = make_upright(source, upright, hflip, log, fps=std_fps, sdr=sdr)
        ledger.add(Entry(stage="ingest.hflip" if hflip else "ingest.upright", model="ffmpeg", seconds=t_flip.seconds,
                         cost_usd=0.0, note=f"{what}: {source.name} -> {upright.name} ({upright.stat().st_size / 1e6:.1f} MB)"))
        log(f"ingest: {what} to {upright.name} {probe.width}x{probe.height} ({upright.stat().st_size / 1e6:.1f} MB) "
            f"in {t_flip.seconds}s; it is the source from here on")
        source = upright

    proxy = out / "proxy.mp4"
    wav = out / "audio.wav"
    with Timer() as t_proxy:
        pp = make_proxy(source, proxy, probe, log=log)
        make_wav(source, wav, log=log)
    ledger.add(Entry(stage="ingest.ffmpeg", model="ffmpeg", seconds=t_proxy.seconds, cost_usd=0.0,
                     note=f"proxy {pp.width}x{pp.height} @ {pp.fps:g} fps, wav 24 kHz mono"))
    log(f"ingest: proxy {pp.width}x{pp.height} ({proxy.stat().st_size / 1e6:.1f} MB), wav {wav.stat().st_size / 1e6:.1f} MB in {t_proxy.seconds}s")
    with Timer() as t_shots:
        shots = detect_shots(proxy, log=log)
    ledger.add(Entry(stage="ingest.shots", model="ffmpeg", seconds=t_shots.seconds, cost_usd=0.0,
                     note=f"{len(shots)} camera cut(s) (scdet {SHOT_THRESHOLD:g})"))
    log(f"ingest: {len(shots)} camera cut(s){': ' + ', '.join(f'{c:g}' for c in shots[:12]) if shots else ''} in {t_shots.seconds}s")

    up = upload_file(proxy, "video/mp4")
    ledger.add(Entry(stage="ingest.upload", model="files", seconds=up["upload_seconds"], cost_usd=0.0,
                     note=f"file_id {up['id']}"))
    log(f"ingest: uploaded proxy as {up['id']} in {up['upload_seconds']}s")

    ing = Ingested(source=source, probe=probe, proxy=proxy, proxy_probe=pp, wav=wav,
                   file_id=up["id"], upload_seconds=up["upload_seconds"], shots=shots)
    dump_json(out / "ingest.json", {**ing.to_json(), "hflip": hflip, "rotation": rotation, "standardised": reasons,
                                    "original_source": str(original),
                                    "upload": {k: v for k, v in up.items() if k != "upload_seconds"}})
    return ing
