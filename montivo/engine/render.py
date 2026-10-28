"""Render: EDL spans -> 9:16 segments from the SOURCE file -> concat -> cut_v1.mp4.

Spans flagged `contiguous_with_previous` (or adjacent in time inside one turn) are merged into one segment first,
so no cut lands inside continuous speech (Voice word edges inside a turn carry up to 0.3 s of slop). Each segment
is snapped to whole source frames: t_in to the frame grid, duration to a frame count, so video and audio of a
segment are exactly as long as each other and no drift builds up across cuts.

Video: per segment, accurate input seek on the source, crop x driven per frame by a sendcmd file from the reframe
path, full-height crop scaled to 1080x1920 (or the EDL's other aspect, frame.py), libx264 crf 18, no audio; then the concat demuxer joins the segments
with stream copy. Audio: atrim per segment over the source with 10 ms handles past each cut, an equal-power 20 ms
crossfade centred on every cut (the joined audio keeps the frames' length; the old 20 ms fade out + fade in dipped
40 ms at every cut), then loudness in two passes: loudnorm measures the joined audio first, the second pass applies one fixed gain to
-14 LUFS (loudnorm linear mode with the measured values), so room tone stays as far under speech as in the source
(single-pass loudnorm is a dynamic AGC and lifted the room tone at cut starts by up to 28 dB). When that gain would
push the true peak over -1.5 dBTP, a 4x oversampled lookahead limiter takes the peaks off after the gain and the
limited audio is measured again before the linear pass. AAC 192k 48 kHz. The two are muxed with stream copy.

Finishing (evening 3), still one video encode per segment: caption lines and callouts are transparent PNGs
(captions.py / graphics.py) burned in with `overlay` after the crop and scale, each gated by its segment-local time;
B-roll stills (broll.py, EDL `overlays`, a person's ask only) go in the same overlay graph under them; the title card
(image.py) is its own 1.5 s segment in front, with a short fade to black, and the audio gets the same 1.5 s of silence
in front before loudnorm.

Music (EDL `music`, engine/music.py): the normalised speech is kept as a float WAV, a bundled loop is ducked along the
Voice words in cut time, the two are mixed and the mix goes through the same two-pass loudness, then the AAC file's own
true peak is measured and the pass runs again with a lower TP while the encode overshoots -1 dBTP. The speech alone is
kept as render/<cut>.speech.m4a for the review's silences and heard words, the bed as render/<cut>.music.m4a for the
editor's live faders.

Re-renders (E7): a segment longer than CHUNK_S encodes as chunks of CHUNK_S counted from its first frame (a segment
with punch-ins stays whole: zoompan counts its own output frames), each chunk seeking the source at its first frame
with the segment's own clock (setpts adds the chunk's offset), so the crop's sendcmd, the overlays' enable times and
their fades are the segment's. Every encode (chunk or title clip) is one ffmpeg command whose arguments, with each input
file replaced by what identifies its content (the source by path + mtime + size, the chunk's sendcmd text, the bytes of
every overlay PNG on screen in it) and the ffmpeg build, hash to a cache key; an encode whose key is in
render/cache/ is linked from there instead of encoded, so a one-caption edit re-encodes only the chunk that shows that
line. The encodes that do run go side by side (ENCODE_WORKERS, each x264 on X264_THREADS threads: x264's bitstream
depends on its thread count, so that count is fixed per machine and part of the key). The audio runs at the same time in
its own thread and is cached the same way (its key: the source, the segment times, the title lead, loudness, the music
and its words, and this code), with its loudness records. The cache keeps about SEGCACHE_RENDERS renders' worth of
files, least recently used out first.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REFRAME_CONCEPT
from ..ledger import Entry, Ledger, Timer
from .api import dump_json
from . import broll
from .captions import Overlay, build_overlays
from .edges import Audio
from .frame import FRAME_916, Frame, frame_for
from .music import normalize_music
from .punch import plan_punches, zoom_filter
from .reframe import ReframeParams, SegmentPath, plan_segment
from .speakers import follow_ids

OUT_W, OUT_H = FRAME_916.w, FRAME_916.h
AUDIO_RATE = 48000
AUDIO_FADE_S = 0.02  # fade in at the cut's first in point, out at its last out point
AUDIO_XFADE_S = 0.02  # every cut: an equal-power crossfade this long, centred on the cut (10 ms of handle each side)
CONTIGUOUS_GAP_S = 0.05
TITLE_S = 1.5
TITLE_FADE_S = 0.3
# TP is asked for at -1.5: loudnorm holds -1.0 on the PCM, the AAC encode then measured -0.8 dBTP (run 011)
LOUDNORM = {"I": -14.0, "TP": -1.5, "LRA": 11.0}
LOUDNORM_LRA_MAX = 50.0  # loudnorm's own upper bound for the LRA target
LIMIT_MARGIN_DB = 0.5  # the limiter ceiling sits this far under TP, room for inter-sample peaks and the AAC encode
LIMIT_RATE = 192000  # the limiter runs 4x oversampled, so it catches true peaks, not only sample peaks
LIMIT_ATTACK_MS, LIMIT_RELEASE_MS = 5.0, 50.0
LIMIT_TRIES = 3
ENCODED_TP_MAX = -1.0  # the music mix's true peak, measured on the AAC file itself
ENCODE_TRIES, ENCODE_MARGIN_DB = 2, 0.3
# veryfast: 2.7x faster segment encodes than medium at SSIM 0.992 (015 seg_01: 7.24 s -> 2.67 s, 28/10)
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-map_metadata", "-1", "-movflags", "+faststart", "-movie_timescale", "90000"]
# movie_timescale: the edit list in 1/1000 s stored a 25-frame chunk as 0.833 s, and the concat demuxer moved every
# file after it that much early (a fraction of a millisecond per segment edge, frames paired wrong across chunk edges)
# every frame leaves the graph limited range BT.709 and says so: run 015's full-range source used to come out
# yuvj420p / pc, and the overlays went to YUV with BT.601 on untagged frames (marker #FFC629 came back #FFC122)
TAG_709 = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
TO_709 = "scale=out_color_matrix=bt709:out_range=tv"
# parallel encodes: a few at once, the CPUs split between them; the thread count goes into every x264 command (and so
# into the cache key), the number of workers changes nothing in the output
CPUS = os.cpu_count() or 2
ENCODE_WORKERS = max(1, min(4, CPUS // 2))
X264_THREADS = max(1, CPUS // ENCODE_WORKERS)
SEGCACHE = "cache"  # render/cache/: the hosted demo's judge copy of a sample skips folders named cache (app.py)
CHUNK_S = 4.0  # long segments encode in chunks of this many seconds: they run side by side and cache one by one
SEGCACHE_RENDERS = 4  # the cache keeps about this many renders' worth of segment bytes
SEGCACHE_PART_MAX_AGE_S = 3600
_MATRIX = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470", "fcc": "fcc", "smpte240m": "smpte240m",
           "bt2020nc": "bt2020", "bt2020c": "bt2020"}


@dataclass
class Segment:
    index: int
    t_in: float
    t_out: float
    span_ids: list[str]
    roles: list[str] = field(default_factory=list)
    frames: int = 0  # whole source frames, set by frame_align
    fps: float = 0.0

    @property
    def duration(self) -> float:
        return round(self.frames / self.fps if self.frames else self.t_out - self.t_in, 4)


def merge_segments(spans: list[dict]) -> list[Segment]:
    """Consecutive spans that continue the previous one (flagged contiguous_with_previous, or the same turn with
    no gap between them) join the previous segment; the order of spans is kept."""
    segments: list[Segment] = []
    prev: dict | None = None
    for s in spans:
        joins = bool(prev) and abs(prev["t_out"] - s["t_in"]) < CONTIGUOUS_GAP_S and (
            s.get("contiguous_with_previous") or s.get("turn_id") == prev.get("turn_id"))
        if joins:
            seg = segments[-1]
            seg.t_out = s["t_out"]
            seg.span_ids.append(s["id"])
            seg.roles.append(s["role"])
        else:
            segments.append(Segment(index=len(segments), t_in=s["t_in"], t_out=s["t_out"], span_ids=[s["id"]], roles=[s["role"]]))
        prev = s
    return segments


def frame_align(segments: list[Segment], fps: float) -> list[Segment]:
    """Snap each segment's in point to the source frame grid and its length to a whole number of frames."""
    for seg in segments:
        f_in = math.floor(seg.t_in * fps + 1e-6)
        n = max(1, int(round((seg.t_out - f_in / fps) * fps)))
        seg.t_in = round(f_in / fps, 6)
        seg.t_out = round((f_in + n) / fps, 6)
        seg.frames = n
        seg.fps = fps
    return segments


def load_track(run_dir: Path, edl: dict, concept: str) -> dict | None:
    """tracks.json for one concept: the path in the EDL when it resolves, else run_dir/track/<concept>/."""
    cands: list[Path] = []
    rec = (edl.get("tracks") or {}).get(concept) or {}
    if rec.get("tracks"):
        cands.append(Path(rec["tracks"]))
    cands.append(run_dir / "track" / concept / "tracks.json")
    for p in cands:
        if p.is_file():
            return json.loads(p.read_text())
    return None


def load_reframe_track(run_dir: Path, edl: dict) -> tuple[str | None, dict | None]:
    """The track the crop follows: the EDL's reframe concept (default "head"), then "person", else a centre crop."""
    order = [edl.get("reframe_concept") or REFRAME_CONCEPT]
    if "person" not in order:
        order.append("person")
    for concept in order:
        track = load_track(run_dir, edl, concept)
        if track and track.get("objects"):
            return concept, track
    return None, None


def load_fallback_track(run_dir: Path, edl: dict, concept: str | None) -> dict | None:
    """The person track when the crop follows another concept (heads): where the head track loses the speaker, the
    crop follows the person box instead of jumping to the centre (reframe.plan_segment)."""
    if concept in (None, "person"):
        return None
    track = load_track(run_dir, edl, "person")
    return track if track and track.get("objects") else None


def reframe_inputs(run_dir: Path, edl: dict) -> tuple[str | None, dict | None, dict[str, list[str]], dict | None]:
    """What plan_paths needs from a run folder: (concept, track, speaker ids, fallback track)."""
    concept, track = load_reframe_track(run_dir, edl)
    return concept, track, load_speaker_ids(run_dir, concept, edl), load_fallback_track(run_dir, edl, concept)


def load_speaker_ids(run_dir: Path, concept: str | None, edl: dict | None = None) -> dict[str, list[str]]:
    """speaker -> head ids from run_dir/speakers.json (speakers.py); only meaningful when the crop follows heads.
    A human pin in the EDL, `reframe_speaker_ids` {"A": "3"} (Voice label -> head id), replaces the mapped ids of its
    speaker: the pinned head is followed while it covers the piece, the plain rule when it leaves the frame."""
    if concept != "head":
        return {}
    p = run_dir / "speakers.json"
    ids = follow_ids(json.loads(p.read_text())) if p.is_file() else {}
    ids.update({str(spk): [str(oid)] for spk, oid in ((edl or {}).get("reframe_speaker_ids") or {}).items()})
    return ids


def follow_pieces(seg: Segment, spans: list[dict], speaker_ids: dict[str, list[str]]) -> list[dict] | None:
    """The segment cut into runs of spans by one speaker, each with that speaker's head ids; None when no span's
    speaker has a head (the crop keeps the plain rule)."""
    if not speaker_ids:
        return None
    by_id = {s["id"]: s for s in spans}
    ordered = [by_id[sid] for sid in seg.span_ids if sid in by_id]
    if not ordered or not any(str(s.get("speaker")) in speaker_ids for s in ordered):
        return None
    pieces: list[dict] = []
    for k, s in enumerate(ordered):
        a = seg.t_in if k == 0 else max(seg.t_in, min(seg.t_out, float(s["t_in"])))
        b = seg.t_out if k == len(ordered) - 1 else max(a, min(seg.t_out, float(ordered[k + 1]["t_in"])))
        spk = str(s.get("speaker"))
        if pieces and pieces[-1]["speaker"] == spk:
            pieces[-1]["t_out"] = b
        else:
            pieces.append({"t_in": a, "t_out": b, "speaker": spk, "ids": speaker_ids.get(spk)})
    return pieces


def segment_bias(spans: list[dict], span_ids: list[str]) -> int:
    """The review's reframe_bias for a segment: the `reframe_bias_px` of its spans (source px, + moves the crop right)."""
    vals = [int(s.get("reframe_bias_px") or 0) for s in spans if s["id"] in span_ids]
    return int(round(sum(vals) / len(vals))) if vals else 0


def apply_bias(path: SegmentPath, bias_px: int, source_w: int) -> None:
    """Shift a planned crop path by bias_px (kept inside the frame); the keyframes follow."""
    hi = source_w - path.crop_w
    path.xs = [min(hi, max(0, x + bias_px)) for x in path.xs]
    for kf in path.keyframes:
        kf["x"] = min(hi, max(0, kf["x"] + bias_px))
        kf["bias_px"] = bias_px


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr[-2000:]}")


def source_color(source: Path) -> dict:
    """The source's YUV range and matrix as the crop's scaler reads them: the stream's own tags, else what a player
    assumes for an untagged stream (limited range; BT.709 from 720 lines up, BT.601 under)."""
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=height,pix_fmt,color_range,color_space", "-of", "json", str(source)],
                          capture_output=True, text=True)
    st = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
    full = st.get("color_range") == "pc" or str(st.get("pix_fmt", "")).startswith("yuvj")
    matrix = _MATRIX.get(st.get("color_space") or "") or ("bt709" if int(st.get("height") or 0) >= 720 else "bt601")
    return {"range": "pc" if full else "tv", "matrix": matrix}


def _overlay_graph(base: str, overlays: list[Overlay], first_input: int, fps: float = 30.0) -> tuple[list[str], str]:
    """Input args and filter chain that burn `overlays` onto the stream labelled `base`; returns (args, graph) whose
    last output label is [v]. Times are segment-local; each overlay shows on frames with t_in <= t < t_out. A fading
    overlay loops at the segment's `fps`, so its fade steps once per output frame. Every PNG goes to YUV with BT.709
    limited range, as the base is (TO_709), so its colours land as drawn."""
    args: list[str] = []
    chain: list[str] = []
    cur = base
    for n, ov in enumerate(overlays):
        idx = first_input + n
        if ov.fade_s > 0:
            args += ["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{ov.t_out + 0.1:.4f}", "-i", str(ov.png)]
            fade = (f"fade=t=in:st={ov.t_in:.4f}:d={ov.fade_s}:alpha=1," if ov.fade_in else "") + \
                   (f"fade=t=out:st={max(ov.t_in, ov.t_out - ov.fade_s):.4f}:d={ov.fade_s}:alpha=1," if ov.fade_out else "")
        else:
            args += ["-i", str(ov.png)]
            fade = ""
        chain.append(f"[{idx}:v]format=rgba,{fade}{TO_709},format=yuva420p[o{n}]")
        nxt = f"[m{n}]"
        chain.append(f"{cur}[o{n}]overlay=x={ov.x}:y={ov.y}:format=yuv420:eof_action=repeat:"
                     f"enable='gte(t,{ov.t_in:.4f})*lt(t,{ov.t_out:.4f})'{nxt}")
        cur = nxt
    chain.append(f"{cur}format=yuv420p,{TAG_709}[v]")
    return args, ";".join(chain)


@dataclass
class Encode:
    """One video encode: the ffmpeg args (no output) and, for the cache key, a stand-in for every file path in them
    that names its content (`stand_ins`, path text -> identity)."""
    args: list[str]
    out: Path
    stand_ins: dict[str, str] = field(default_factory=dict)

    def key(self) -> str:
        subs = sorted(self.stand_ins.items(), key=lambda kv: -len(kv[0]))
        args = []
        for a in self.args:
            for path, ident in subs:
                a = a.replace(path, ident)
            args.append(a)
        blob = json.dumps({"ffmpeg": _ffmpeg_build(), "args": args}, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:24]


@functools.lru_cache(maxsize=1)
def _ffmpeg_build() -> str:
    """The ffmpeg build in the cache key: an upgraded ffmpeg (or libraries) encodes differently."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True)
    return hashlib.sha256(proc.stdout.encode()).hexdigest()[:16]


def _file_id(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _source_id(p: Path) -> str:
    st = p.stat()
    return f"source:{p.resolve().as_posix()}:{st.st_mtime_ns}:{st.st_size}"


def _sendcmd(path: SegmentPath, k0: int, k1: int) -> str:
    """The crop's sendcmd lines for source frames k0..k1-1 of the segment, in the segment's own clock: the whole path's
    text (SegmentPath.sendcmd_text) when it is all of it. A chunk's first line fires on its first frame."""
    return "".join(f"{0.0 if k == 0 else (k - 0.5) / path.fps:.4f} crop x {path.xs[k]};\n" for k in range(k0, k1))


def chunk_bounds(seg: Segment, zoom: dict | None = None) -> list[tuple[int, int]]:
    """The segment's chunks as frame ranges: CHUNK_S each from its first frame, a short tail joined to the chunk before
    it (so a trim at the end leaves the earlier chunks as they were); one chunk with punch-ins (zoompan's `on`)."""
    size = max(1, int(round(CHUNK_S * seg.fps)))
    if zoom or seg.frames < 2 * size:
        return [(0, seg.frames)]
    starts = list(range(0, seg.frames, size))
    if seg.frames - starts[-1] < size // 2:
        starts.pop()
    return [(a, starts[k + 1] if k + 1 < len(starts) else seg.frames) for k, a in enumerate(starts)]


def segment_encode(source: Path, seg: Segment, path: SegmentPath, render_dir: Path,
                   overlays: list[Overlay] | None = None, zoom: dict | None = None, frame: Frame = FRAME_916,
                   color: dict | None = None, part: tuple[int, int] | None = None) -> Encode:
    """One segment's video, or its frames part = (k0, k1): crop, scale to the frame (from the source's range and
    matrix, `color` = source_color, to limited BT.709), the zoom, the overlays (a part takes the ones on screen in it).
    Writes the crop's sendcmd file; returns the encode."""
    k0, k1 = part or (0, seg.frames)
    whole = (k0, k1) == (0, seg.frames)
    name = f"seg_{seg.index + 1:02d}" + ("" if whole else f"_{k0:05d}")
    cmds = render_dir / f"{name}_crop.cmd"
    text = _sendcmd(path, k0, k1)
    cmds.write_text(text)
    out = render_dir / f"{name}.mp4"
    color = color or {"range": "tv", "matrix": "bt709"}
    c0, c1 = k0 / seg.fps, k1 / seg.fps
    if not whole:  # only the overlays this part shows, so an edit elsewhere in the segment leaves its key alone
        overlays = [ov for ov in overlays or [] if ov.t_in < c1 + 1e-3 and ov.t_out > c0 - 1e-3]
    # seek half a frame early so the frame on the grid is never lost to rounding, then rebase pts to 0 (a part: to its
    # offset in the segment, rounded to the stream's time base, and back to 0 after the overlays)
    seek = max(0.0, seg.t_in + c0 - 0.5 / seg.fps)
    rebase = "setpts=PTS-STARTPTS" + (f"+{c0:.9f}/TB+0.5" if k0 else "")
    vf = (f"{rebase},sendcmd=f={cmds.as_posix()},crop={path.crop_w}:{path.crop_h}:{path.xs[k0]}:{path.crop_y}:exact=1,"
          f"scale={frame.w}:{frame.h}:flags=lanczos:in_range={color['range']}:in_color_matrix={color['matrix']}:"
          f"out_range=tv:out_color_matrix=bt709,format=yuv420p,setsar=1"
          + (f",{zoom_filter(zoom, seg.fps, (frame.w, frame.h))}" if zoom else "") + f",{TAG_709}")
    ov_args, ov_graph = _overlay_graph("[base]", overlays or [], 1, seg.fps)
    if k0:
        ov_graph = ov_graph[:-len("[v]")] + ",setpts=PTS-STARTPTS[v]"
    graph = f"[0:v]{vf}[base];{ov_graph}"
    n = k1 - k0
    args = ["-ss", f"{seek:.6f}", "-t", f"{(n + 2) / seg.fps:.6f}", "-i", str(source), *ov_args,
            "-filter_complex", graph, "-map", "[v]", "-an", "-frames:v", str(n), "-r", f"{seg.fps:g}",
            "-fps_mode", "cfr", *X264, "-threads", str(X264_THREADS)]
    stand_ins = {str(source): _source_id(source), cmds.as_posix(): "sendcmd:" + hashlib.sha256(text.encode()).hexdigest()}
    stand_ins.update({str(ov.png): _file_id(Path(ov.png)) for ov in overlays or []})
    return Encode(args, out, stand_ins)


def render_segment_video(source: Path, seg: Segment, path: SegmentPath, render_dir: Path,
                         overlays: list[Overlay] | None = None, zoom: dict | None = None, frame: Frame = FRAME_916,
                         color: dict | None = None) -> Path:
    """segment_encode, encoded now without the cache."""
    enc = segment_encode(source, seg, path, render_dir, overlays, zoom, frame, color)
    _ffmpeg([*enc.args, str(enc.out)])
    return enc.out


def title_encode(png: Path, fps: float, render_dir: Path, seconds: float = TITLE_S, fade_s: float = TITLE_FADE_S,
                 frame: Frame = FRAME_916) -> tuple[Encode, int]:
    """The title card as a segment of whole frames: scaled to cover the frame (1080x1920; the 9:16 card's middle in a
    feed aspect), fade to black over the last fade_s."""
    frames = int(round(seconds * fps))
    out = render_dir / "seg_00_title.mp4"
    d = frames / fps
    vf = (f"scale={frame.w}:{frame.h}:force_original_aspect_ratio=increase:flags=lanczos,crop={frame.w}:{frame.h},setsar=1,"
          f"fade=t=out:st={d - fade_s:.4f}:d={fade_s},{TO_709},format=yuv420p,{TAG_709}")
    args = ["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{d + 1 / fps:.6f}", "-i", str(png), "-vf", vf,
            "-frames:v", str(frames), "-r", f"{fps:g}", "-fps_mode", "cfr", *X264, "-threads", str(X264_THREADS)]
    return Encode(args, out, {str(png): _file_id(png)}), frames


def render_title_video(png: Path, fps: float, render_dir: Path, seconds: float = TITLE_S, fade_s: float = TITLE_FADE_S,
                       frame: Frame = FRAME_916) -> tuple[Path, int]:
    """title_encode, encoded now without the cache."""
    enc, frames = title_encode(png, fps, render_dir, seconds, fade_s, frame)
    _ffmpeg([*enc.args, str(enc.out)])
    return enc.out, frames


def _place(src: Path, out: Path) -> None:
    """The cached file at `out`: a hard link (no copy), else a copy where links are not possible."""
    out.unlink(missing_ok=True)
    try:
        os.link(src, out)
    except OSError:
        if not src.is_file():
            raise
        shutil.copyfile(src, out)


def take_cached(enc: Encode, cache_dir: Path, key: str) -> bool:
    """Put the cached encode with this key at enc.out (and mark it used); False when there is none."""
    hit = cache_dir / f"{key}.mp4"
    try:
        _place(hit, enc.out)
        os.utime(hit)
    except FileNotFoundError:
        return False
    return True


def run_encode(enc: Encode, cache_dir: Path | None, key: str | None) -> None:
    """Encode to enc.out; with a cache, into the cache first (as a .part renamed when done), then linked out."""
    if cache_dir is None or key is None:
        enc.out.unlink(missing_ok=True)  # it may be a link to a cache file: ffmpeg -y would write into that
        _ffmpeg([*enc.args, str(enc.out)])
        return
    part = cache_dir / f"{key}.{os.getpid()}.{threading.get_ident()}.part"
    try:
        _ffmpeg([*enc.args, "-f", "mp4", str(part)])
        part.replace(cache_dir / f"{key}.mp4")
    finally:
        part.unlink(missing_ok=True)
    _place(cache_dir / f"{key}.mp4", enc.out)


def encode_all(encodes: list[Encode], cache_dir: Path | None, on_done, workers: int = ENCODE_WORKERS) -> list[str | None]:
    """Every encode, cache hits first, the rest ENCODE_WORKERS at a time; `on_done(i, cached)` is called on this
    thread as each one lands (so in finishing order, not in segment order). Returns the cache keys."""
    keys = [enc.key() if cache_dir is not None else None for enc in encodes]
    todo = []
    for i, enc in enumerate(encodes):
        if cache_dir is not None and take_cached(enc, cache_dir, keys[i]):
            on_done(i, True)
        else:
            todo.append(i)
    if not todo:
        return keys
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo))))
    try:
        futures = {pool.submit(run_encode, encodes[i], cache_dir, keys[i]): i for i in todo}
        for fut in as_completed(futures):
            fut.result()
            on_done(futures[fut], False)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return keys


def prune_cache(cache_dir: Path, keep: set[str], renders: int = SEGCACHE_RENDERS) -> int:
    """Drop the least recently used cache files past `renders` times the bytes of the keys in `keep` (the render just
    made, never dropped), and .part files left by a crash; returns how many went."""
    files = sorted(cache_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    budget = renders * sum(p.stat().st_size for p in files if p.stem in keep)
    total, dropped = 0, 0
    for p in files:
        total += p.stat().st_size
        if p.stem not in keep and total > budget:
            p.unlink(missing_ok=True)
            dropped += 1
    metas = sorted(cache_dir.glob("audio_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for k, meta in enumerate(metas):
        key = meta.stem[len("audio_"):]
        if key not in keep and k >= renders:
            for f in cache_dir.glob(f"audio_{key}.*"):
                f.unlink(missing_ok=True)
            dropped += 1
    now = time.time()
    for p in cache_dir.glob("*.part"):
        if now - p.stat().st_mtime > SEGCACHE_PART_MAX_AGE_S:
            p.unlink(missing_ok=True)
    return dropped


_LOUD_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)
_LOUD_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def parse_loudnorm(stderr: str) -> dict | None:
    """The last loudnorm JSON block in an ffmpeg log (print_format=json), numbers as floats."""
    m = _LOUD_JSON.findall(stderr)
    if not m:
        return None
    return {k: (float(v) if _num(v) else v) for k, v in json.loads(m[-1]).items()}


def loudnorm_filter(lra: float, measured: dict | None = None, targets: dict | None = None) -> str:
    """loudnorm with the targets; with `measured` (a first pass's input_* values) the second, linear pass."""
    tg = targets or LOUDNORM
    f = f"loudnorm=I={tg['I']}:TP={tg['TP']}:LRA={lra:g}"
    if measured:
        f += (f":measured_I={measured['input_i']}:measured_LRA={measured['input_lra']}:measured_TP={measured['input_tp']}"
              f":measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true")
    return f + ":print_format=json"


def target_lra(measured: dict, targets: dict | None = None) -> float:
    """loudnorm leaves linear mode when the measured LRA is over the target: the target is raised to clear it."""
    tg = targets or LOUDNORM
    return float(min(LOUDNORM_LRA_MAX, max(tg["LRA"], math.ceil(measured["input_lra"]) + 1.0)))


def linear_ok(measured: dict, targets: dict | None = None) -> bool:
    """Whether loudnorm can stay linear on audio with these measured values: the gain to the target must not lift
    the true peak over TP (and the LRA target is raised by target_lra)."""
    tg = targets or LOUDNORM
    return measured["input_tp"] + (tg["I"] - measured["input_i"]) <= tg["TP"] + 1e-6


def limiter_chain(gain_db: float, targets: dict | None = None) -> str:
    """Fixed gain then a lookahead limiter at TP - LIMIT_MARGIN_DB, 4x oversampled, delay compensated (no A/V shift)."""
    tg = targets or LOUDNORM
    ceiling = 10 ** ((tg["TP"] - LIMIT_MARGIN_DB) / 20)
    return (f"aresample={LIMIT_RATE},volume={gain_db:.2f}dB,alimiter=limit={ceiling:.4f}:attack={LIMIT_ATTACK_MS:g}:"
            f"release={LIMIT_RELEASE_MS:g}:level=disabled:latency=1")


def _audio_graph(source_graph: str, chain: str) -> str:
    return source_graph + (f",{chain}" if chain else "") + "[a]"


def _run_audio(source: Path | list[Path], graph: str, out: Path | None) -> str:
    """One ffmpeg pass over the input(s) with an audio graph ending in [a]; to `out` (AAC, or float PCM for a .wav),
    or measure only (None)."""
    if out is None:
        tail = ["-f", "null", "-"]
    elif out.suffix == ".wav":
        tail = ["-c:a", "pcm_f32le", "-ar", str(AUDIO_RATE), "-ac", "2", "-map_metadata", "-1", str(out)]
    else:
        tail = ["-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE), "-ac", "2", "-map_metadata", "-1", str(out)]
    inputs = [x for p in (source if isinstance(source, list) else [source]) for x in ("-i", str(p))]
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "info", *inputs,
                           "-filter_complex", graph, "-map", "[a]", *tail], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio failed ({proc.returncode})\n{proc.stderr[-2000:]}")
    return proc.stderr


def _loudness(source: Path | list[Path], joined: str, out: Path, targets: dict | None = None) -> dict:
    """The two-pass loudness (normalize_plan) of the audio graph `joined` over source, written to out; the record."""
    tg = targets or LOUDNORM
    first = parse_loudnorm(_run_audio(source, _audio_graph(joined, loudnorm_filter(tg["LRA"], targets=tg)), None))
    if first is None:
        raise RuntimeError("loudnorm measuring pass printed no JSON")
    rec, chain = normalize_plan(first, lambda pre: parse_loudnorm(_run_audio(
        source, _audio_graph(joined, ",".join(x for x in (pre, loudnorm_filter(tg["LRA"], targets=tg)) if x)), None)), tg)
    final = parse_loudnorm(_run_audio(source, _audio_graph(joined, chain), out)) or {}
    rec["normalization_type"] = final.get("normalization_type")
    rec["output"] = {k: final.get(k) for k in ("output_i", "output_tp", "output_lra", "output_thresh")}
    if rec["normalization_type"] != "linear":
        rec["mode"] = "dynamic"
    return rec


def _loudness_encoded(source: Path | list[Path], joined: str, out: Path) -> dict:
    """_loudness, then the true peak of the encoded AAC itself: the encode overshoots on hard transients (the music
    mix of run 015 measured -1.95 dBTP as PCM and -0.5 as AAC), so while the file is over ENCODED_TP_MAX the pass runs
    again with TP lowered by the overshoot (ENCODE_TRIES times at most). `encoded_tp` and `tp_retries` record it."""
    tg, tries = dict(LOUDNORM), 0
    while True:
        rec = _loudness(source, joined, out, tg)
        tp = (measure_loudness(out) or {}).get("true_peak_dbtp")
        if tp is None or tp <= ENCODED_TP_MAX or tries >= ENCODE_TRIES:
            break
        tries += 1
        tg["TP"] = round(tg["TP"] - (tp - ENCODED_TP_MAX) - ENCODE_MARGIN_DB, 2)
    rec["encoded_tp"], rec["tp_retries"] = tp, tries
    return rec


def handles(segments: list[Segment], source_s: float | None = None, xfade_s: float = AUDIO_XFADE_S) -> list[tuple[int, int]]:
    """(samples before t_in, samples after t_out) each segment's audio reads past its edges for the crossfades: half the
    crossfade on each side of every cut, none at the cut's first in point and last out point, never outside the
    source (0 .. source_s)."""
    half = int(xfade_s * AUDIO_RATE / 2)
    out = []
    for i, seg in enumerate(segments):
        before = 0 if i == 0 else min(half, int(seg.t_in * AUDIO_RATE))
        after = 0 if i == len(segments) - 1 else half
        if after and source_s is not None:
            after = max(0, min(after, int((source_s - seg.t_out) * AUDIO_RATE)))
        out.append((before, after))
    return out


def cut_graph(segments: list[Segment], source_s: float | None = None, xfade_s: float = AUDIO_XFADE_S) -> list[str]:
    """The filter lines that cut the source audio into the segments and join them as [cut]: each segment read with
    its handles (handles()), every cut an equal-power crossfade (quarter-sine both ways, sin^2 + cos^2 = 1) centred on
    the cut over both handles, so the joined audio is exactly as long as the segments' frames and room tone runs
    through the cut instead of dipping; a short fade in at the first in point and out at the last out point. The old
    join (a 20 ms fade out, then a 20 ms fade in) dropped the level for 40 ms at every cut."""
    fmt = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo"
    hs = handles(segments, source_s, xfade_s)
    lines = []
    for i, (seg, (before, after)) in enumerate(zip(segments, hs)):
        start = round(seg.t_in * AUDIO_RATE) - before
        n = round(seg.duration * AUDIO_RATE) + before + after
        fades = []
        if i == 0:
            fades.append(f"afade=t=in:st=0:d={AUDIO_FADE_S}")
        if i == len(segments) - 1:
            fades.append(f"afade=t=out:st={max(0.0, n / AUDIO_RATE - AUDIO_FADE_S):.6f}:d={AUDIO_FADE_S}")
        # apad: exactly n samples even where the audio stream ends before the container (a phone clip's video runs on),
        # else the crossfade eats the short segment's end and every later segment plays early against its video
        lines.append(f"[0:a]aresample={AUDIO_RATE},atrim=start={start / AUDIO_RATE:.9f}:duration={n / AUDIO_RATE:.9f},"
                     f"asetpts=PTS-STARTPTS,apad=whole_len={n},{','.join(fades + [fmt])}[s{i}]")
    cur = "[s0]"
    for i in range(1, len(segments)):
        ns = hs[i - 1][1] + hs[i][0]
        nxt = "[cut]" if i == len(segments) - 1 else f"[x{i}]"
        if ns >= 2:
            lines.append(f"{cur}[s{i}]acrossfade=ns={ns}:c1=qsin:c2=qsin{nxt}")
        else:  # both edges at the source's ends: nothing to overlap
            lines.append(f"{cur}[s{i}]concat=n=2:v=0:a=1{nxt}")
        cur = nxt
    if len(segments) == 1:
        lines[-1] = lines[-1][:-len("[s0]")] + "[cut]"
    return lines


def render_audio(source: Path, segments: list[Segment], render_dir: Path, lead_s: float = 0.0,
                 loudnorm: bool = True, music: dict | None = None, words: list[dict] | None = None, audio=None,
                 stem_name: str = "cut", source_s: float | None = None) -> tuple[Path, dict | None, dict | None]:
    """All segments' audio: cut with equal-power crossfades at every cut (cut_graph; `source_s` keeps the handles
    inside the source), `lead_s` of silence in front (title card), concat, then
    two-pass loudness (see the module docstring), AAC. Returns the file, a record of the loudness passes and, with a
    `music` bed (engine/music.py), the bed's record: the normalised speech is kept as a float WAV, the ducked bed is
    rendered against the speech intervals (Voice `words`, else `audio`'s speech runs), the two are mixed and the mix
    goes through the same two-pass loudness (the speech moved by the music's `voice_db` first); the speech alone is
    kept as render/<stem_name>.speech.m4a (the review measures silences and heard words on it), the ducked bed as
    render/<stem_name>.music.m4a (the editor's live faders) and the record carries the measured speech-vs-bed levels."""
    out = render_dir / "audio.m4a"
    fmt = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo"
    parts = []
    labels = []
    if lead_s > 0:
        parts.append(f"anullsrc=r={AUDIO_RATE}:cl=stereo,atrim=duration={lead_s:.6f},{fmt}[lead]")
        labels.append("[lead]")
    parts += cut_graph(segments, source_s)
    labels.append("[cut]")
    joined = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1"
    speech = render_dir / "speech.wav" if music else out
    rec = None
    if loudnorm:
        rec = _loudness(source, joined, speech)
    else:
        _run_audio(source, _audio_graph(joined, ""), speech)
    if not music:
        return out, rec, None
    from . import music as bed_mod
    duration = round(lead_s + sum(s.duration for s in segments), 6)
    intervals, src = bed_mod.speech_intervals(segments, words, lead_s, audio)
    bed = render_dir / "music_bed.wav"
    mrec = bed_mod.render_bed(music, duration, intervals, bed, AUDIO_RATE, fmt)
    mrec["duck_source"] = src
    voice_db = float(music.get("voice_db") or 0.0)
    gain = f",volume={voice_db:.2f}dB" if voice_db else ""
    mix = f"[0:a]{fmt}{gain}[s];[1:a]{fmt}[m];[s][m]amix=inputs=2:normalize=0:duration=first:dropout_transition=0"
    if loudnorm:
        mixed = _loudness_encoded([speech, bed], mix, out)
        mixed["speech"] = rec
        rec = mixed
    else:
        _run_audio([speech, bed], _audio_graph(mix, ""), out)
    stem = render_dir / f"{stem_name}.speech.m4a"
    _run_audio(speech, "[0:a]anull[a]", stem)
    mrec["speech_stem"] = stem.as_posix()
    bed_stem = render_dir / f"{stem_name}.music.m4a"  # the editor plays both stems to move the faders live
    _run_audio(bed, "[0:a]anull[a]", bed_stem)
    mrec["music_stem"] = bed_stem.as_posix()
    mrec["voice_db"] = voice_db
    mrec["measured"] = bed_mod.measure_windows(speech, bed, intervals, duration, voice_db)
    speech.unlink(missing_ok=True)
    bed.unlink(missing_ok=True)
    return out, rec, mrec


@functools.lru_cache(maxsize=1)
def _code_id() -> str:
    """This module, music.py and edges.py (the speech runs the bed ducks along without words): the audio's cache key cannot see the ffmpeg graphs it would run, so a change to the
    code that builds them starts the audio cache over."""
    here = Path(__file__).resolve().parent
    return hashlib.sha256(b"".join((here / f).read_bytes() for f in ("render.py", "music.py", "edges.py"))).hexdigest()[:16]


def audio_key(source: Path, segments: list[Segment], lead_s: float, loudnorm: bool, music: dict | None,
              words: list[dict] | None, run_dir: Path) -> str:
    """What render_audio's output depends on: the source, each segment's in point and length, the title lead, the
    loudness switch and, with music, the bed, the words it ducks along (else the run's audio.wav) and the track file."""
    rec: dict = {"ffmpeg": _ffmpeg_build(), "code": _code_id(), "source": _source_id(source),
                 "segments": [[seg.t_in, seg.duration] for seg in segments], "lead": round(lead_s, 6),
                 "loudnorm": bool(loudnorm), "music": music}
    if music:
        from . import music as bed_mod
        rec["words"] = hashlib.sha256(json.dumps(words, sort_keys=True).encode()).hexdigest() if words else None
        wav = run_dir / "audio.wav"
        rec["audio_wav"] = _source_id(wav) if not words and wav.is_file() else None
        rec["track"] = _source_id(Path(bed_mod.track(music["track"])["path"]))
        rec["track_meta"] = {k: v for k, v in bed_mod.track(music["track"]).items() if k != "path"}  # its lufs sets the gain
    return hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:24]


def cached_audio(cache_dir: Path | None, key: str | None, render_dir: Path, stem_name: str,
                 make) -> tuple[Path, dict | None, dict | None, bool]:
    """render_audio's (file, loudness record, music record) from the cache when the key is there (the files copied
    out: ffmpeg -y on a later render writes into the file it finds), else `make()` and keep a copy. The last item says
    whether it came from the cache."""
    files = {"audio": render_dir / "audio.m4a", "speech": render_dir / f"{stem_name}.speech.m4a",
             "music": render_dir / f"{stem_name}.music.m4a"}
    meta = cache_dir / f"audio_{key}.json" if cache_dir is not None and key else None
    if meta is not None and meta.is_file():
        try:
            m = json.loads(meta.read_text())
            for kind in m["files"]:
                shutil.copyfile(cache_dir / f"audio_{key}.{kind}.m4a", files[kind])
            os.utime(meta)
            bed = m["music"]
            if bed:
                bed["speech_stem"], bed["music_stem"] = files["speech"].as_posix(), files["music"].as_posix()
            return files["audio"], m["loudnorm"], bed, True
        except (OSError, ValueError, KeyError):
            pass
    out, rec, bed = make()
    if meta is not None:
        kinds = ["audio"] + (["speech", "music"] if bed else [])
        for kind in kinds:
            part = cache_dir / f"audio_{key}.{kind}.{os.getpid()}.part"
            shutil.copyfile(files[kind], part)
            part.replace(cache_dir / f"audio_{key}.{kind}.m4a")
        dump_json(meta, {"files": kinds, "loudnorm": rec, "music": bed})
    return out, rec, bed, False


def music_stem(run_dir: Path, edl: dict, out: Path) -> Path:
    """The ducked bed of a rendered version as AAC, rebuilt from its render block (segments, title seconds) and the
    Voice words, as render_audio made it: for versions rendered before the bed was kept as render/<cut>.music.m4a."""
    music = normalize_music(edl.get("music"))
    rec = edl.get("render") or {}
    if not music or not rec.get("segments"):
        raise ValueError("this version has no rendered music bed")
    from . import music as bed_mod
    segments = [Segment(index=s["index"], t_in=s["t_in"], t_out=s["t_out"], span_ids=s.get("spans") or [],
                        frames=1, fps=1 / s["duration"]) for s in rec["segments"]]  # duration = frames / fps
    lead = float((rec.get("title") or {}).get("seconds") or 0.0)
    vpath = run_dir / "voice" / "voice.json"
    words = json.loads(vpath.read_text()).get("words") if vpath.is_file() else None
    intervals, _ = bed_mod.speech_intervals(segments, words, lead, None if words else Audio.load(run_dir))
    fmt = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo"
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".tmp.wav")
    bed_mod.render_bed(music, round(lead + sum(s.duration for s in segments), 6), intervals, wav, AUDIO_RATE, fmt)
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    _run_audio(wav, "[0:a]anull[a]", tmp)
    wav.unlink(missing_ok=True)
    tmp.replace(out)
    return out


def normalize_plan(first: dict, measure, targets: dict | None = None) -> tuple[dict, str]:
    """The second pass from the measuring one: loudnorm linear when the gain to I keeps the true peak under TP; else
    gain + limiter (limiter_chain), measured again by `measure(pre_chain) -> loudnorm JSON`, with the gain corrected
    for the loudness the limiter took (up to LIMIT_TRIES times). Returns the record for the EDL and the filter chain."""
    tg = targets or LOUDNORM
    rec: dict = {"target_i": tg["I"], "target_tp": tg["TP"], "target_lra": tg["LRA"],
                 "first_pass": {k: first.get(k) for k in _LOUD_KEYS}, "limiter": None}
    m, pre, gain, tries = first, "", 0.0, 0
    while not linear_ok(m, tg) and tries < LIMIT_TRIES:
        gain = round(gain + tg["I"] - m["input_i"], 2)
        pre = limiter_chain(gain, tg)
        m = measure(pre)
        if m is None:
            raise RuntimeError("loudnorm measuring pass after the limiter printed no JSON")
        tries += 1
    lra = target_lra(m, tg)
    if pre:
        rec["limiter"] = {"gain_db": gain, "ceiling_dbtp": tg["TP"] - LIMIT_MARGIN_DB, "rate": LIMIT_RATE,
                          "attack_ms": LIMIT_ATTACK_MS, "release_ms": LIMIT_RELEASE_MS, "tries": tries,
                          "limited_pass": {k: m.get(k) for k in _LOUD_KEYS}}
    rec["lra_used"] = lra
    rec["lra_raised"] = lra > tg["LRA"]
    ok = linear_ok(m, tg)
    rec["mode"] = ("limiter+linear" if pre else "linear") if ok else "dynamic"
    rec["passes"] = 2 + tries
    chain = ",".join(x for x in (pre, loudnorm_filter(lra, m, tg), f"aresample={AUDIO_RATE}") if x)
    return rec, chain


def _num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


_EBU = re.compile(r"Integrated loudness:\s+I:\s+(-?[0-9.]+) LUFS.*?Loudness range:\s+LRA:\s+(-?[0-9.]+) LU.*?"
                  r"True peak:\s+Peak:\s+(-?[0-9.]+|-inf) dBFS", re.S)


def measure_loudness(media: Path) -> dict | None:
    """Integrated loudness, LRA and true peak of a file's audio with ffmpeg's ebur128 scanner (peak=true)."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(media), "-map", "0:a:0", "-af",
                           "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    m = _EBU.search(proc.stderr[proc.stderr.rfind("Summary:"):])
    if not m:
        return None
    return {"integrated_lufs": float(m.group(1)), "lra_lu": float(m.group(2)),
            "true_peak_dbtp": float(m.group(3)) if m.group(3) != "-inf" else None}


def mux(segment_files: list[Path], audio: Path, out: Path, render_dir: Path) -> Path:
    """Join the segments and the audio by stream copy into `out`, written as <out>.part and renamed over it when done:
    a version rendered again under the same name is never half a file to the player or the Volume's commit."""
    lst = render_dir / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segment_files))
    part = out.with_name(out.name + ".part")
    try:
        _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                 "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(part)])
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return out


def plan_paths(edl: dict, track: dict | None, speaker_ids: dict[str, list[str]],
               params: ReframeParams | None = None, fallback: dict | None = None) -> tuple[list[Segment], list[SegmentPath]]:
    """The frame-aligned segments of the EDL and the crop path of each (one crop x per output frame), as the render
    burns them; the editor's preview still (look.py) and the review's head checks plan the same way. The crop's width
    follows the EDL's aspect (frame.frame_for). `fallback` is the person track for windows the head track misses;
    a window neither covers holds the previous segment's last centre unless a camera cut lies between the two (the
    source's `shots`, ingest.detect_shots), which also splits a segment's crop path into pieces."""
    segments = frame_align(merge_segments(edl["spans"]), float(edl["source"]["fps"]))
    aspect = frame_for(edl).ratio
    shots = [float(c) for c in edl["source"].get("shots") or []]
    paths: list[SegmentPath] = []
    hold, prev = None, None
    for seg in segments:
        if prev is not None and any(min(prev.t_out, seg.t_in) < c <= max(prev.t_out, seg.t_in) for c in shots):
            hold = None
        path = plan_segment(seg.t_in, seg.t_out, edl["source"], track, params, span_ids=seg.span_ids,
                            follow=follow_pieces(seg, edl["spans"], speaker_ids), aspect=aspect, fallback=fallback,
                            hold=hold, shots=shots)
        prev = seg
        path.xs = path.xs[:seg.frames] + [path.xs[-1]] * max(0, seg.frames - len(path.xs))
        hold = path.xs[-1] + path.crop_w / 2
        bias = segment_bias(edl["spans"], seg.span_ids)
        if bias:
            apply_bias(path, bias, int(edl["source"]["width"]))
        paths.append(path)
    return segments, paths


def render(run_dir: Path, edl: dict, source: Path, ledger: Ledger, out_name: str = "cut_v1.mp4",
           params: ReframeParams | None = None, log=print, edl_path: Path | None = None, words: list[dict] | None = None,
           title_png: Path | None = None, captions: bool = True, callouts: bool = True, loudnorm: bool = True,
           punch: bool = False, cache: bool | None = None) -> dict:
    """Render the EDL to run_dir/<out_name>; writes segments, cmd files, overlay PNGs and the audio under run_dir/render/
    and fills the EDL's `reframe` keyframes, `captions` / `callouts` / `caption_corrections` and a `render` block
    (written back to edl_path when given). `words` are the Voice words (voice.json) the captions come from; without
    them, or with captions=False, the cut has no captions. `title_png` puts the title card in front. `punch` adds the
    punch-in zooms (punch.py) and records them in the EDL's `punch_ins`. `cache` (default on, MONTIVO_SEGCACHE=0 turns
    it off) reuses encoded segments from render/cache/ (see the module docstring)."""
    if not source.is_file():
        raise FileNotFoundError(f"source footage not found: {source}")
    if not edl.get("spans"):
        raise RuntimeError("EDL has no spans to render")
    render_dir = run_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    for stale in render_dir.glob("seg_*"):
        stale.unlink()
    ov_dir = render_dir / "overlays"
    shutil.rmtree(ov_dir, ignore_errors=True)
    ov_dir.mkdir()
    fps = float(edl["source"]["fps"])
    frame = frame_for(edl)
    concept, track, speaker_ids, fallback = reframe_inputs(run_dir, edl)
    segments, paths = plan_paths(edl, track, speaker_ids, params, fallback)
    color = source_color(source)
    if cache is None:
        cache = os.environ.get("MONTIVO_SEGCACHE", "1") != "0"
    cache_dir = render_dir / SEGCACHE if cache else None
    if cache_dir is not None:
        cache_dir.mkdir(exist_ok=True)
    log(f"render: {len(edl['spans'])} spans -> {len(segments)} segments ({sum(s.frames for s in segments)} frames), "
        f"source {source.name}, {frame.w}x{frame.h} ({frame.aspect}), track {concept or 'none (centre crop)'}"
        + (f", following speakers {', '.join(f'{k}->{'/'.join(v)}' for k, v in speaker_ids.items())}" if speaker_ids else ""))

    keyframes: list[dict] = []
    seg_info: list[dict] = []
    with Timer() as t:
        title_frames = 0
        title_s = 0.0
        if title_png is not None:
            title_frames = int(round(TITLE_S * fps))
            title_s = title_frames / fps
        overlays: dict[int, list[Overlay]] = {}
        if (captions and words) or callouts:
            fields, overlays = build_overlays(edl, words or [], segments, paths, load_track(run_dir, edl, "head"),
                                              load_track(run_dir, edl, "person"), ov_dir, title_s=title_s,
                                              captions=bool(captions and words), callouts=callouts,
                                              reframe_concept=concept)
            edl.update(fields)
            log(f"render: {len(fields['captions'])} caption lines "
                f"({sum(1 for c in fields['captions'] if c['placement'] in ('below_head', 'above_head'))} moved off the head, "
                f"{sum(1 for c in fields['captions'] if c['over_face'])} over it), {len(fields['callouts'])} callouts, "
                f"{len(fields['caption_corrections'])} corrections: "
                + "; ".join(f"{f['from']} -> {f['to']}" for f in fields["caption_corrections"]))
        else:
            edl.update({"captions": [], "callouts": [], "caption_corrections": []})
        broll_records: list[dict] = []
        if edl.get("overlays"):  # B-roll stills go under the captions and callouts (drawn first)
            stills, broll_records = broll.place(edl, segments, title_s, run_dir, ov_dir, frame)
            full = {r["png"] for r in broll_records if r["shown"] and r["placement"] == "full"}
            hidden: list[str] = []
            for k, ovs in stills.items():  # a callout pill does not sit on a full-frame still
                rest, cut = broll.clear_callouts(overlays.get(k, []), [(o.t_in, o.t_out) for o in ovs
                                                                       if o.png.as_posix() in full])
                overlays[k] = ovs + rest
                hidden += cut
            log(f"render: {sum(r['shown'] for r in broll_records)} of {len(broll_records)} B-roll stills"
                + "".join(f"; {r['id']} {r['placement']} {r['t_in']:.2f}-{r['t_out']:.2f}s" if r["shown"]
                          else f"; {r['id']} not shown: {r['why']}" for r in broll_records)
                + (f"; kept off the full-frame stills: {', '.join(dict.fromkeys(hidden))}" if hidden else ""))
        zooms: dict[int, dict] = {}
        edl["punch_ins"] = []
        if punch:
            zooms, edl["punch_ins"] = plan_punches(edl, segments, paths, words or [], Audio.load(run_dir),
                                                   load_track(run_dir, edl, "head") if concept == "head" else None,
                                                   edl.get("captions") or [], title_s=title_s)
            log(f"render: {len(edl['punch_ins'])} punch-ins" + "".join(
                f"{'; ' if k else ': '}{e['t_in']:.1f}s {e.get('word') or 'jump cut'} x{e['zoom']}"
                for k, e in enumerate(edl["punch_ins"])))
        encodes: list[Encode] = []
        owner: list[int] = []  # the segment index of each encode, -1 for the title clip
        if title_png is not None:
            encodes.append(title_encode(title_png, fps, render_dir, frame=frame)[0])
            owner.append(-1)
        lines: list[str] = []
        for seg, path in zip(segments, paths):
            ovs = overlays.get(seg.index, [])
            parts = chunk_bounds(seg, zooms.get(seg.index))
            for part in parts:
                encodes.append(segment_encode(source, seg, path, render_dir, ovs, zooms.get(seg.index), frame, color, part))
                owner.append(seg.index)
            keyframes.extend(path.keyframes)
            moving = sum(1 for a, b in zip(path.xs, path.xs[1:]) if a != b)
            seg_info.append({"index": seg.index, "t_in": seg.t_in, "t_out": seg.t_out, "duration": seg.duration, "frames": seg.frames,
                             "spans": seg.span_ids, "track_concept": concept, "track_id": path.track_id, "crop": [path.crop_w, path.crop_h],
                             **({"crop_y": path.crop_y} if path.crop_y else {}),
                             "x_first": path.xs[0], "x_min": min(path.xs), "x_max": max(path.xs),
                             "frames_moving": moving, "overlays": len(ovs),
                             "files": [e.out.as_posix() for e, o in zip(encodes, owner) if o == seg.index],
                             "chunks": [list(p) for p in parts],
                             **({"pieces": path.pieces} if path.pieces else {}),
                             **({"zoom": {"base": zooms[seg.index]["base"], "events": len(zooms[seg.index]["events"])}}
                                if seg.index in zooms else {})})
            lines.append(f"render: seg {seg.index + 1} {seg.t_in:.3f}-{seg.t_out:.3f}s ({seg.frames} frames, {'+'.join(seg.span_ids)}) "
                         f"track {'/'.join(f'{p['speaker']}:{p['track_id']}' for p in path.pieces) if path.pieces else path.track_id} crop {path.crop_w}x{path.crop_h} x {min(path.xs)}..{max(path.xs)}, {len(ovs)} overlays")
        files = [e.out for e in encodes]
        cached: list[bool] = [False] * len(encodes)
        left = {seg.index: owner.count(seg.index) for seg in segments}

        def landed(i: int, hit: bool) -> None:
            """The log line of a segment once its last chunk is in (the progress bar counts these lines)."""
            cached[i] = hit
            k = owner[i]
            if k < 0:
                log(f"render: title card {title_png.name} {title_s:.2f}s ({title_frames} frames, fade {TITLE_FADE_S}s)"
                    + (", cached" if hit else ""))
                return
            left[k] -= 1
            if left[k]:
                return
            n = owner.count(k)
            hits = sum(c for c, o in zip(cached, owner) if o == k)
            note = ", cached" if hits == n else f", {hits} of {n} chunks cached" if hits else f", {n} chunks" if n > 1 else ""
            log(lines[k] + note)

        music = normalize_music(edl.get("music"))
        if music and words is None:
            vpath = run_dir / "voice" / "voice.json"
            words = json.loads(vpath.read_text())["words"] if vpath.is_file() else None
        stem_name = Path(out_name).stem
        source_s = (edl.get("source") or {}).get("duration_s")
        akey = audio_key(source, segments, title_s, loudnorm, music, words, run_dir) if cache_dir is not None else None

        def make_audio():
            return render_audio(source, segments, render_dir, lead_s=title_s, loudnorm=loudnorm, music=music,
                                words=words, audio=Audio.load(run_dir) if music else None, stem_name=stem_name,
                                source_s=source_s)
        # the audio needs only the segment times: it runs beside the video encodes
        with ThreadPoolExecutor(max_workers=1) as audio_pool:
            audio_job = audio_pool.submit(cached_audio, cache_dir, akey, render_dir, stem_name, make_audio)
            keys = encode_all(encodes, cache_dir, landed)
            audio, loud, bed, audio_hit = audio_job.result()
        for info in seg_info:
            mine = [i for i, o in enumerate(owner) if o == info["index"]]
            info["cached"] = all(cached[i] for i in mine)
            info["chunks_cached"] = sum(cached[i] for i in mine)
            if cache_dir is not None:
                info["cache_keys"] = [keys[i] for i in mine]
        if bed:
            m = bed["measured"]
            log(f"render: music {bed['name']} at {bed['level_db']} LUFS, ducked {bed['duck_db']} dB over "
                f"{len(bed['ducked'])} speech stretches ({bed['duck_source']}); measured speech {m['speech_rms_db']} dB, "
                f"bed under it {m['bed_under_speech_rms_db']} dB (margin {m['margin_db']} dB), bed in the open "
                f"{m['bed_open_rms_db']} dB")
        out = mux(files, audio, run_dir / out_name, render_dir)
        if cache_dir is not None:
            prune_cache(cache_dir, {k for k in [*keys, akey] if k})
    measured = measure_loudness(out)
    size_mb = out.stat().st_size / 1e6
    total = round(title_s + sum(s.duration for s in segments), 3)
    edl["reframe"] = keyframes
    edl["render"] = {"output": out.as_posix(), "aspect": frame.aspect, "width": frame.w, "height": frame.h, "duration_s": total, "segments": seg_info,
                     "title": {"png": title_png.as_posix(), "seconds": round(title_s, 4), "frames": title_frames,
                               "fade_out_s": TITLE_FADE_S, "text": edl.get("title_card_text", "")} if title_png else None,
                     "color": {"source": color, "output": {"range": "tv", "matrix": "bt709"}},
                     "loudnorm": loud, "loudness_ebur128": measured, "music": bed,
                     "captions": len(edl.get("captions") or []), "callouts": len(edl.get("callouts") or []),
                     **({"overlays": broll_records} if broll_records else {}),
                     "audio": audio.as_posix(), "seconds": t.seconds, "bytes": out.stat().st_size,
                     "segcache": {"on": cache_dir is not None, "cached": sum(cached), "encoded": len(cached) - sum(cached),
                                  "audio_cached": audio_hit, "workers": ENCODE_WORKERS, "x264_threads": X264_THREADS}}
    if edl_path:
        dump_json(edl_path, edl)
    loud_note = f", {measured['integrated_lufs']} LUFS / {measured['true_peak_dbtp']} dBTP" if measured else ""
    if loud:
        loud_note += f" ({loud['mode']}" + (f", limiter +{loud['limiter']['gain_db']} dB" if loud["limiter"] else "") + ")"
    if bed:
        loud_note += f", music {bed['track']}"
    ledger.add(Entry(stage="render", model="ffmpeg", seconds=t.seconds, cost_usd=0.0,
                     note=f"{len(segments)} segments{' + title' if title_png else ''} ({sum(cached)} of {len(cached)} encodes{' + audio' if audio_hit else ''} cached), {total} s, "
                          f"{len(edl.get('captions') or [])} captions, {out.name} {size_mb:.1f} MB{loud_note}"))
    log(f"render: {out} ({total} s, {size_mb:.1f} MB{loud_note}) in {t.seconds}s")
    return edl["render"]
