"""Speakers: which SAM head object is which Voice speaker, so the 9:16 crop follows whoever is talking.

Voice diarizes the audio into speaker labels; SAM tracks every head in frame with an id. With more than one person on
camera (the NASA crew interview) the reframe used to pick the largest, most centred head per segment, which is not
the one talking half of the time. The mapping takes three signals, strongest first.

1. The microphone. When watch suggests a microphone (a handheld mic passed around a crew), run.py tracks it with SAM on
the same frames as the heads (one more request, about $0.12 per 120 s). Per track frame the mic is held by the head
whose box is nearest the mic's box (the gap between the two boxes over the head's height, within MIC_REACH); a speaker
gets the head that holds the mic on at least MIC_SHARE of the frames where only that speaker talks. A mic object that
never moves is dropped: nobody holds it (SAM also called a fixed fitting on the station wall a microphone). The gap,
not the distance between centres, because a mic held low at chest height sits nearer the centre of the next head down
(run 014: centres gave A's turns to the wrong astronaut 91 % of the time, gaps to the right one 86 %). Run 014 (four
astronauts, one mic) is why the mic comes first: watch's speaker map named the wrong person for three of four voices,
and mouth motion could not tell them apart at 54 to 122 px.

2. Mouth motion ($0) for the speakers and heads the mic left. Per track frame (the 5 fps grid SAM saw) and per head box we take the mean absolute pixel difference to the previous
frame in two bands of the box, the mouth (lower 40 %, inner 60 % of the width) and the eyes (10 to 45 % from the top),
and keep mouth minus eyes, which cancels a nod, a lean or a camera move that moves the whole head. For each head and
each speaker the score is an effect size: the mean of that series on frames where only this speaker talks (Voice word
times) against the frames where this speaker is silent, over the pooled standard deviation. A head goes to the
speaker with the highest score when it clears D_MIN and beats the runner-up by D_MARGIN; two heads on screen together
never share a speaker (the stronger keeps it). A speaker left without a head falls back to the position Spark's watch
call gave in its speaker map ("left", "right", "centre") among the heads visible while they talk (3.). Off-camera
speakers stay unmapped and the crop keeps the old rule for their spans.

A head upside down (an astronaut floating on the ceiling) has its mouth at the top of the box, so mouth minus eyes ran
backwards (run 014: -0.36 on the speaker's own head). With a person track, a head that sits at the bottom edge of its
body's box on most frames is inverted and its bands are flipped.
"""
from __future__ import annotations

import json
import math
import re
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
MOUTH_INV = (0.2, 0.0, 0.8, 0.4)  # the bands of an upside-down head
EYES_INV = (0.2, 0.55, 0.8, 0.9)
MIC_REACH = 1.5  # head heights between the head box and the mic box within which a head can hold it
MIC_STILL = 0.25  # a mic object whose centre never moves more than this share of its size is not held
MIC_SHARE = 0.6
MIC_RE = re.compile(r"\bmic(rophone)?s?\b", re.I)


def is_mic(concept: str) -> bool:
    return bool(MIC_RE.search(concept or ""))


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


def inverted_heads(objects: dict[str, list[dict]], persons: dict[str, list[dict]] | None) -> set[str]:
    """Head ids upside down on most of their frames: the head's own body is the person box holding the head centre
    whose top or bottom edge the head sits closest to, and the head is inverted when that is the bottom edge."""
    if not persons:
        return set()
    by_frame: dict[int, list[list[float]]] = {}
    for recs in persons.values():
        for r in recs:
            by_frame.setdefault(int(r["f"]), []).append(r["box"])
    out: set[str] = set()
    for oid, recs in objects.items():
        inv = up = 0
        for r in recs:
            x1, y1, x2, y2 = r["box"]
            cx, cy, hh = (x1 + x2) / 2, (y1 + y2) / 2, max(1.0, y2 - y1)
            bodies = [b for b in by_frame.get(int(r["f"]), []) if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]]
            if not bodies:
                continue
            b = min(bodies, key=lambda b: min(abs(y1 - b[1]), abs(b[3] - y2)) / hh)
            if abs(b[3] - y2) < abs(y1 - b[1]):
                inv += 1
            else:
                up += 1
        if inv > up:
            out.add(oid)
    return out


def held_mic(recs: list[dict]) -> bool:
    """A mic object that moves: its centre travels more than MIC_STILL of its median size over its frames."""
    if not recs:
        return False
    xs = [(r["box"][0] + r["box"][2]) / 2 for r in recs]
    ys = [(r["box"][1] + r["box"][3]) / 2 for r in recs]
    sizes = sorted(max(r["box"][2] - r["box"][0], r["box"][3] - r["box"][1]) for r in recs)
    return max(max(xs) - min(xs), max(ys) - min(ys)) > MIC_STILL * sizes[len(sizes) // 2]


def mic_votes(objects: dict[str, list[dict]], mics: dict[str, list[dict]], speaking: dict[str, set[int]]) -> dict:
    """Speaker -> {ids, share, frames}: the head holding the mic while only that speaker talks, when it does on at
    least MIC_SHARE of those frames (and MIN_FRAMES of them). A head claimed by two speakers goes to the higher share."""
    heads_at: dict[int, list[tuple[str, list[float]]]] = {}
    for oid, recs in objects.items():
        for r in recs:
            heads_at.setdefault(int(r["f"]), []).append((oid, r["box"]))
    mics_at: dict[int, list[list[float]]] = {}
    for recs in mics.values():
        if not held_mic(recs):
            continue
        for r in recs:
            mics_at.setdefault(int(r["f"]), []).append(r["box"])

    def holder(f: int) -> str | None:
        best = None
        for m in mics_at.get(f, []):
            for oid, b in heads_at.get(f, []):
                dx = max(0.0, max(m[0], b[0]) - min(m[2], b[2]))
                dy = max(0.0, max(m[1], b[1]) - min(m[3], b[3]))
                d = math.hypot(dx, dy) / max(1.0, b[3] - b[1])
                if d <= MIC_REACH and (best is None or d < best[0]):
                    best = (d, oid)
        return best[1] if best else None

    others = {spk: set().union(*(fr for s, fr in speaking.items() if s != spk)) for spk in speaking}
    found: dict[str, dict] = {}
    for spk, frames in speaking.items():
        held = [h for h in (holder(f) for f in sorted(frames - others[spk])) if h is not None]
        if len(held) < MIN_FRAMES:
            continue
        top = max(set(held), key=held.count)
        share = held.count(top) / len(held)
        if share >= MIC_SHARE:
            found[spk] = {"ids": [top], "share": round(share, 3), "frames": len(held)}
    kept: dict[str, dict] = {}
    for spk in sorted(found, key=lambda k: -found[k]["share"]):
        if all(k["ids"] != found[spk]["ids"] for k in kept.values()):
            kept[spk] = found[spk]
    return kept


def mouth_motion(frames: Iterable[Image.Image], objects: dict[str, list[dict]],
                 inverted: set[str] | None = None) -> dict[str, dict[int, float]]:
    """Mouth-minus-eyes motion per head id and track frame index (only frames where the head has a box in this and
    the previous frame and is at least MIN_HEAD_PX wide). Both frames are cut with the current box; the bands of an
    `inverted` head are flipped."""
    inverted = inverted or set()
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
                flip = oid in inverted
                m = _band(box, MOUTH_INV if flip else MOUTH, img.width, img.height)
                e = _band(box, EYES_INV if flip else EYES, img.width, img.height)
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
           speaker_map: list[dict] | None = None, frame_w: int = 0, mic: dict | None = None) -> dict:
    """Head ids per speaker: the mic holders first (mic_votes), then the mouth-motion scores among the speakers and
    heads the mic left, with the same-screen rule, then the watch positions as a fallback."""
    mic = mic or {}
    mic_head = {s["ids"][0]: spk for spk, s in mic.items()}
    frames_of = {oid: {int(r["f"]) for r in recs} for oid, recs in objects.items()}
    heads: dict[str, dict] = {}
    for oid, by_spk in scores.items():
        free = {spk: d for spk, d in by_spk.items() if spk not in mic}
        ranked = sorted(free.items(), key=lambda kv: -kv[1])
        best, d = ranked[0] if ranked else (None, None)
        runner = ranked[1][1] if len(ranked) > 1 else None
        ok = oid not in mic_head and d is not None and d >= D_MIN and (runner is None or d - runner >= D_MARGIN)
        heads[oid] = {"speaker": mic_head.get(oid) or (best if ok else None), "d": d, "runner_up_d": runner,
                      "scores": by_spk, "frames": len(frames_of.get(oid, ())),
                      **({"by": "microphone"} if oid in mic_head else {})}
    for oid, spk in mic_head.items():
        heads.setdefault(oid, {"speaker": spk, "d": None, "runner_up_d": None, "scores": {},
                               "frames": len(frames_of.get(oid, ())), "by": "microphone"})
    # two heads on screen together are two people: the weaker one gives the speaker up
    for a in list(heads):
        for b in list(heads):
            ha, hb = heads[a], heads[b]
            if a >= b or not ha["speaker"] or ha["speaker"] != hb["speaker"] or "by" in ha or "by" in hb:
                continue
            fa, fb = frames_of.get(a, set()), frames_of.get(b, set())
            if fa and fb and len(fa & fb) >= SHARE_OVERLAP * min(len(fa), len(fb)):
                (hb if ha["d"] >= hb["d"] else ha)["speaker"] = None

    speakers: dict[str, dict] = {}
    for spk in speaking:
        if spk in mic:
            speakers[spk] = {"ids": list(mic[spk]["ids"]), "method": "microphone", "mic_share": mic[spk]["share"]}
            continue
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
                 resample: bool = False, frames: Iterable[Image.Image] | None = None, mic_track: dict | None = None,
                 person_track: dict | None = None) -> dict:
    """The whole mapping for one run: `head_track` is the head concept's tracks.json, `video` the clip SAM saw (the
    resampled track source) or, with resample=True, the proxy at its own rate. `mic_track` and `person_track` are
    tracks.json contents on the same frames (or None). `frames` replaces the decode in tests."""
    fps = float(head_track.get("track_fps") or 5.0)
    objects = head_track.get("objects") or {}
    same_grid = lambda t: t if t and abs(float(t.get("track_fps") or 0) - fps) < 0.01 else None  # noqa: E731
    mic_track, person_track = same_grid(mic_track), same_grid(person_track)
    inverted = inverted_heads(objects, (person_track or {}).get("objects"))
    frames = frames if frames is not None else decode_gray(video, fps if resample else None)
    motion = mouth_motion(frames, objects, inverted)
    n = max((f for s in motion.values() for f in s), default=0) + 1
    n = max(n, max((int(r["f"]) for recs in objects.values() for r in recs), default=0) + 1)
    speaking = speaking_frames(words, fps, n)
    mic = mic_votes(objects, mic_track.get("objects") or {}, speaking) if mic_track else {}
    result = assign(score_heads(motion, speaking), objects, speaking, speaker_map,
                    frame_w=int((head_track.get("source") or {}).get("width") or 0), mic=mic)
    result.update({"method": ("microphone+" if mic_track else "") + "mouth_motion+watch_position", "track_fps": fps,
                   "video": str(video), "inverted_heads": sorted(inverted), "mic_concept": (mic_track or {}).get("concept"),
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
    SAM saw, watch.json's speaker map and, when the run has them, a microphone track and the person track. $0, ffmpeg
    only (the mic track itself is tracked by run.py)."""
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
    others = [json.loads(p.read_text()) for p in sorted((run_dir / "track").glob("*/tracks.json")) if p != head_path]
    mic = next((t for t in others if is_mic(t.get("concept", ""))), None)
    person = next((t for t in others if t.get("concept") == "person"), None)
    result = map_speakers(words, head, video, smap, resample=resample, mic_track=mic, person_track=person)
    (run_dir / "speakers.json").write_text(json.dumps(result, indent=2))
    if result["inverted_heads"]:
        log(f"speakers: upside-down heads {', '.join(result['inverted_heads'])} (mouth and eyes bands flipped)")
    for spk, s in result["speakers"].items():
        ev = ", ".join(f"{oid} mic {s['mic_share']:.0%}" if s["method"] == "microphone" else f"{oid} d={result['heads'].get(oid, {}).get('d', '?')}"
                       for oid in s["ids"])
        log(f"speakers: {spk} ({s.get('who') or '?'}) -> {s['method']}{': ' + ev if ev else ''}")
    return result
