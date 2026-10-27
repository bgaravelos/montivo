"""Speakers: which SAM head object is which Voice speaker, so the 9:16 crop follows whoever is talking.

Voice diarizes the audio into speaker labels; SAM tracks every head in frame with an id. With more than one person on
camera (the NASA crew interview) the reframe used to pick the largest, most centred head per segment, which is not
the one talking half of the time. The mapping here costs no API call: a talking mouth moves, the forehead much less.

Per track frame (the 5 fps grid SAM saw) and per head box we take the mean absolute pixel difference to the previous
frame in two bands of the box, the mouth (lower 40 %, inner 60 % of the width) and the eyes (10 to 45 % from the top),
and keep mouth minus eyes, which cancels a nod, a lean or a camera move that moves the whole head. For each head and
each speaker the score is an effect size: the mean of that series on frames where only this speaker talks (Voice word
times) against the frames where this speaker is silent, over the pooled standard deviation. A head goes to the
speaker with the highest score when it clears D_MIN and beats the runner-up by D_MARGIN; two heads on screen together
never share a speaker (the stronger keeps it). A speaker left without a head falls back to the position Spark's watch
call gave in its speaker map ("left", "right", "centre") among the heads visible while they talk. Off-camera speakers
stay unmapped and the crop keeps the old rule for their spans.
"""
from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

MOUTH = (0.2, 0.6, 0.8, 1.0)  # x0, y0, x1, y1 as fractions of the head box
EYES = (0.2, 0.1, 0.8, 0.45)
MIN_HEAD_PX = 16  # boxes narrower than this (track pixels) carry no usable mouth
MIN_FRAMES = 8  # frames on each side of a comparison (1.6 s at 5 fps)
D_MIN = 0.4
D_MARGIN = 0.2
SHARE_OVERLAP = 0.25  # two heads visible together on this share of the smaller one's frames are different people


def decode_gray(path: Path, fps: float | None = None) -> Iterator[Image.Image]:
    """Grey frames of a video in decode order, resampled to `fps` when given (the same ffmpeg fps filter the track
    source was made with, so frame k here is SAM's frame k)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "json", str(path)], capture_output=True, text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    vf = (f"fps={fps}," if fps else "") + "format=gray"
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-vf", vf, "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)
    size = w * h
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield Image.frombytes("L", (w, h), buf)
    finally:
        proc.stdout.close()
        proc.wait()


def _band(box: list[float], frac: tuple[float, float, float, float], w: int, h: int) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    r = (max(0, int(x1 + frac[0] * bw)), max(0, int(y1 + frac[1] * bh)),
         min(w, int(x1 + frac[2] * bw)), min(h, int(y1 + frac[3] * bh)))
    return r if r[2] - r[0] >= 2 and r[3] - r[1] >= 2 else None


def _diff(cur: Image.Image, prev: Image.Image, r: tuple[int, int, int, int]) -> float:
    return ImageStat.Stat(ImageChops.difference(cur.crop(r), prev.crop(r))).mean[0]


def mouth_motion(frames: Iterable[Image.Image], objects: dict[str, list[dict]]) -> dict[str, dict[int, float]]:
    """Mouth-minus-eyes motion per head id and track frame index (only frames where the head has a box in this and
    the previous frame and is at least MIN_HEAD_PX wide). Both frames are cut with the current box."""
    by_frame: dict[int, list[tuple[str, list[float]]]] = {}
    for oid, recs in objects.items():
        for r in recs:
            by_frame.setdefault(int(r["f"]), []).append((oid, r["box"]))
    out: dict[str, dict[int, float]] = {oid: {} for oid in objects}
    prev = None
    prev_ids: set[str] = set()
    for f, img in enumerate(frames):
        here = by_frame.get(f, [])
        if prev is not None:
            for oid, box in here:
                if oid not in prev_ids or box[2] - box[0] < MIN_HEAD_PX:
                    continue
                m = _band(box, MOUTH, img.width, img.height)
                e = _band(box, EYES, img.width, img.height)
                if m and e:
                    out[oid][f] = _diff(img, prev, m) - _diff(img, prev, e)
        prev, prev_ids = img, {oid for oid, _ in here}
    return out


def speaking_frames(words: list[dict], fps: float, n_frames: int) -> dict[str, set[int]]:
    """Track frames (k / fps) that fall inside a word of each speaker; Voice word times are in source seconds."""
    out: dict[str, set[int]] = {}
    for w in words:
        a = int(math.ceil(float(w["t_start"]) * fps - 1e-6))
        b = int(math.floor(float(w["t_end"]) * fps + 1e-6))
        out.setdefault(str(w["speaker"]), set()).update(range(max(0, a), min(n_frames - 1, b) + 1))
    return out


def _effect(a: list[float], b: list[float]) -> float | None:
    if len(a) < MIN_FRAMES or len(b) < MIN_FRAMES:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    sd = math.sqrt((va + vb) / 2)
    return round((ma - mb) / sd, 3) if sd > 1e-9 else None


def score_heads(motion: dict[str, dict[int, float]], speaking: dict[str, set[int]]) -> dict[str, dict[str, float]]:
    """Effect size per head and speaker: motion while only this speaker talks against motion while they are silent."""
    others = {spk: set().union(*(fr for s, fr in speaking.items() if s != spk)) for spk in speaking}
    scores: dict[str, dict[str, float]] = {}
    for oid, series in motion.items():
        for spk, frames in speaking.items():
            solo = [v for f, v in series.items() if f in frames and f not in others[spk]]
            silent = [v for f, v in series.items() if f not in frames]
            d = _effect(solo, silent)
            if d is not None:
                scores.setdefault(oid, {})[spk] = d
    return scores


def position_band(text: str) -> tuple[float, float] | None:
    """Where in the frame (fraction of the width) Spark's free-text position puts a speaker."""
    t = (text or "").lower()
    if "left" in t:
        return 0.0, 0.45
    if "right" in t:
        return 0.55, 1.0
    if any(k in t for k in ("centre", "center", "middle")):
        return 0.3, 0.7
    return None


def assign(scores: dict[str, dict[str, float]], objects: dict[str, list[dict]], speaking: dict[str, set[int]],
           speaker_map: list[dict] | None = None, frame_w: int = 0) -> dict:
    """Head ids per speaker from the scores, the same-screen rule and the watch positions as a fallback."""
    frames_of = {oid: {int(r["f"]) for r in recs} for oid, recs in objects.items()}
    heads: dict[str, dict] = {}
    for oid, by_spk in scores.items():
        ranked = sorted(by_spk.items(), key=lambda kv: -kv[1])
        best, d = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else None
        ok = d >= D_MIN and (runner is None or d - runner >= D_MARGIN)
        heads[oid] = {"speaker": best if ok else None, "d": d, "runner_up_d": runner, "scores": by_spk,
                      "frames": len(frames_of.get(oid, ()))}
    # two heads on screen together are two people: the weaker one gives the speaker up
    for a in list(heads):
        for b in list(heads):
            ha, hb = heads[a], heads[b]
            if a >= b or not ha["speaker"] or ha["speaker"] != hb["speaker"]:
                continue
            fa, fb = frames_of.get(a, set()), frames_of.get(b, set())
            if fa and fb and len(fa & fb) >= SHARE_OVERLAP * min(len(fa), len(fb)):
                (hb if ha["d"] >= hb["d"] else ha)["speaker"] = None

    speakers: dict[str, dict] = {}
    for spk in speaking:
        ids = sorted((oid for oid, h in heads.items() if h["speaker"] == spk), key=lambda o: -heads[o]["d"])
        speakers[spk] = {"ids": ids, "method": "mouth_motion" if ids else "none"}
    taken = {oid for s in speakers.values() for oid in s["ids"]}
    for m in speaker_map or []:
        spk = str(m.get("speaker"))
        band = position_band(m.get("position", ""))
        if spk not in speakers or speakers[spk]["ids"] or not m.get("on_camera") or band is None or not frame_w:
            continue
        talk = speaking.get(spk, set())
        best = None
        for oid, recs in objects.items():
            if oid in taken:
                continue
            during = [r for r in recs if int(r["f"]) in talk]
            if len(during) < MIN_FRAMES:
                continue
            cx = sum((r["box"][0] + r["box"][2]) / 2 for r in during) / len(during) / frame_w
            if band[0] <= cx <= band[1] and (best is None or len(during) > best[0]):
                best = (len(during), oid)
        if best:
            speakers[spk] = {"ids": [best[1]], "method": "position", "position": m.get("position")}
            taken.add(best[1])
    for m in speaker_map or []:
        if str(m.get("speaker")) in speakers:
            speakers[str(m["speaker"])]["who"] = m.get("who", "")
    return {"speakers": speakers, "heads": heads}


def map_speakers(words: list[dict], head_track: dict, video: Path, speaker_map: list[dict] | None = None,
                 resample: bool = False, frames: Iterable[Image.Image] | None = None) -> dict:
    """The whole mapping for one run: `head_track` is the head concept's tracks.json, `video` the clip SAM saw (the
    resampled track source) or, with resample=True, the proxy at its own rate. `frames` replaces the decode in tests."""
    fps = float(head_track.get("track_fps") or 5.0)
    objects = head_track.get("objects") or {}
    frames = frames if frames is not None else decode_gray(video, fps if resample else None)
    motion = mouth_motion(frames, objects)
    n = max((f for s in motion.values() for f in s), default=0) + 1
    n = max(n, max((int(r["f"]) for recs in objects.values() for r in recs), default=0) + 1)
    speaking = speaking_frames(words, fps, n)
    result = assign(score_heads(motion, speaking), objects, speaking, speaker_map,
                    frame_w=int((head_track.get("source") or {}).get("width") or 0))
    result.update({"method": "mouth_motion+watch_position", "track_fps": fps, "video": str(video),
                   "frames_scored": {oid: len(s) for oid, s in motion.items()}})
    return result


def follow_ids(mapping: dict | None) -> dict[str, list[str]]:
    """speaker -> head ids to follow, only for speakers that got one."""
    return {spk: s["ids"] for spk, s in ((mapping or {}).get("speakers") or {}).items() if s.get("ids")}


def track_video(run_dir: Path, head_track: dict, proxy: Path) -> tuple[Path, bool]:
    """The clip SAM saw for this track: the resampled track source when it is in the run folder, else the proxy
    (resampled on decode when the track rate is below the proxy's)."""
    fps = float(head_track.get("track_fps") or 5.0)
    src = run_dir / "track" / f"track_source_{fps:g}fps.mp4"
    if src.is_file():
        return src, False
    return proxy, bool((head_track.get("source") or {}).get("resampled", True))


def speakers_run(run_dir: Path, log=print) -> dict:
    """`montivo speakers runs/001` and the run's own step: speakers.json from voice.json, the head track, the clip
    SAM saw and watch.json's speaker map. $0, ffmpeg only."""
    head_path = run_dir / "track" / "head" / "tracks.json"
    voice_path = run_dir / "voice" / "voice.json"
    if not head_path.is_file() or not voice_path.is_file():
        raise FileNotFoundError(f"{run_dir}: needs track/head/tracks.json and voice/voice.json")
    head = json.loads(head_path.read_text())
    words = json.loads(voice_path.read_text())["words"]
    watch_path = run_dir / "watch.json"
    smap = json.loads(watch_path.read_text()).get("speaker_map", []) if watch_path.is_file() else []
    proxy = run_dir / "proxy.mp4"
    if not proxy.is_file() and (run_dir / "ingest.json").is_file():
        proxy = Path(json.loads((run_dir / "ingest.json").read_text())["proxy"])
    video, resample = track_video(run_dir, head, proxy)
    result = map_speakers(words, head, video, smap, resample=resample)
    (run_dir / "speakers.json").write_text(json.dumps(result, indent=2))
    for spk, s in result["speakers"].items():
        ev = ", ".join(f"{oid} d={result['heads'][oid]['d']}" for oid in s["ids"])
        log(f"speakers: {spk} ({s.get('who') or '?'}) -> {s['method']}{': ' + ev if ev else ''}")
    return result
