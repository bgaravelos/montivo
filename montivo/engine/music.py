"""Music bed: a bundled loop under the cut's speech, ducked while someone talks.

The EDL's `music` = {"track": id, "level_db": -18, "duck_db": -6, "voice_db": 0} (absent = no music; `voice_db`
only when not 0), and a person's choices (rail panels M4, M5, each kept only when set): `start_s`, where in the loop
the bed starts; `fade_out_s`, the fade at the end of the cut (default FADE_OUT_S); `duck_edits`, cut-second windows
[{"a", "b", "duck": true|false}] where the bed is held down although nobody speaks, or lifted although someone does
(edit_ducks; a structural edit carries each window with the source seconds it covers, follow_cut). The tracks are files in
montivo/music/ (CC0 tracks from OpenGameArt cut into loops by build.py; tracks.json holds each loop's measured
loudness, author and source page), so the bed is plain DSP: no model is called. `level_db` is the bed's loudness in LUFS where nobody speaks (the speech sits at the
render's -14 LUFS); under speech it drops by `duck_db`, so the defaults keep the bed about 9 dB under the voice. `voice_db`
moves the speech against the bed before the mix (the editor's Voice fader); the mix is normalised to -14 LUFS after
it, so +3 dB on the voice ends up as the bed 3 dB lower against it.

Ducking follows the Voice words in cut time (speech_intervals): every word of a segment is mapped through the
segment's place in the cut, pauses shorter than HOLD_GAP_S stay ducked, and the gain ramps down ATTACK_S before a
word (Voice word edges sit up to 0.3 s off) and back up over RELEASE_S after it. Without words the run's audio.wav
speech runs stand in. The envelope is one `volume` expression evaluated every 256 samples (about 5 ms steps).
render.render_audio mixes the bed into the loudness-normalised speech, then normalises the mix again
(-14 LUFS, true peak under -1 dBTP) and keeps the speech alone as a stem for the review's measured facts.
"""
from __future__ import annotations

import array
import json
import math
import subprocess
from pathlib import Path

MUSIC_DIR = Path(__file__).resolve().parent.parent / "music"
# about 9 dB of speech over the bed on a talking head (run 015: 9.1 dB measured; Billy chose it by ear, 30/10, over
# -20 / -12 = 17.1 dB, which a phone speaker all but loses, and 12 or 7 dB)
LEVEL_DB, DUCK_DB = -18.0, -6.0
LEVEL_RANGE = (-36.0, -10.0)
DUCK_RANGE = (-30.0, 0.0)
VOICE_RANGE = (-12.0, 6.0)
ATTACK_S, RELEASE_S = 0.15, 0.45
HOLD_GAP_S = 0.8  # a pause shorter than this keeps the bed down (>= ATTACK_S + RELEASE_S: the ramps never overlap)
FADE_IN_S, FADE_OUT_S = 0.5, 1.5
FADE_OUT_RANGE = (0.0, 8.0)
MAX_DUCK_EDITS = 40
ENV_SAMPLES = 256
STRIDE = 3  # rms_db reads every third sample: an odd stride still visits both channels


def tracks() -> list[dict]:
    """The bundled tracks (montivo/music/tracks.json), each with its file's absolute path in `path`."""
    data = json.loads((MUSIC_DIR / "tracks.json").read_text())
    return [{**t, "path": (MUSIC_DIR / t["file"]).as_posix()} for t in data["tracks"]]


def track(track_id: str) -> dict:
    for t in tracks():
        if t["id"] == track_id:
            return t
    raise ValueError(f"no music track {track_id!r} (tracks: {', '.join(t['id'] for t in tracks())})")


def normalize_music(m: dict | None) -> dict | None:
    """The EDL's `music` checked and filled in: a known track, level and duck in range (defaults -18 / -6), `voice_db`
    in range and kept only when not 0. None or {} = no music. Raises ValueError for anything else."""
    if not m:
        return None
    if not isinstance(m, dict):
        raise ValueError("music: an object {track, level_db, duck_db, voice_db}")
    extra = set(m) - {"track", "level_db", "duck_db", "voice_db", "start_s", "fade_out_s", "duck_edits"}
    if extra:
        raise ValueError(f"music: unknown keys {', '.join(sorted(extra))}")
    t = track(str(m.get("track") or ""))
    out = {"track": t["id"]}
    for key, default, (lo, hi) in (("level_db", LEVEL_DB, LEVEL_RANGE), ("duck_db", DUCK_DB, DUCK_RANGE),
                                   ("voice_db", 0.0, VOICE_RANGE)):
        v = m.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"music: {key} is a number")
        if not lo <= v <= hi:
            raise ValueError(f"music: {key} {v:g} is outside {lo:g}..{hi:g} dB")
        out[key] = round(float(v), 1)
    if not out["voice_db"]:
        del out["voice_db"]
    for key, default, (lo, hi) in (("start_s", 0.0, (0.0, float(t["loop_s"]))), ("fade_out_s", FADE_OUT_S, FADE_OUT_RANGE)):
        v = m.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"music: {key} is a number of seconds in {lo:g}..{hi:g}")
        if round(float(v), 1) != default:
            out[key] = round(float(v), 1)
    edits = m.get("duck_edits") or []
    if not isinstance(edits, list) or len(edits) > MAX_DUCK_EDITS:
        raise ValueError(f"music: duck_edits is a list of at most {MAX_DUCK_EDITS} windows")
    clean = []
    for e in edits:
        try:
            a, b, duck = float(e["a"]), float(e["b"]), e["duck"]
        except (KeyError, TypeError, ValueError):
            raise ValueError('music: a duck edit is {"a": s, "b": s, "duck": true|false}')
        if not isinstance(duck, bool) or not (math.isfinite(a) and math.isfinite(b)) or not 0 <= a < b <= 3600:
            raise ValueError("music: a duck edit's window is 0 <= a < b seconds, duck true or false")
        if not duck and b - a < HOLD_GAP_S:
            raise ValueError(f"music: a lifted window is at least {HOLD_GAP_S:g} s (the ramps need the room)")
        clean.append({"a": round(a, 2), "b": round(b, 2), "duck": duck})
    if clean:
        out["duck_edits"] = sorted(clean, key=lambda e: (e["a"], e["b"]))
    return out


def edit_ducks(intervals: list[tuple[float, float]], music: dict | None) -> list[tuple[float, float]]:
    """The ducked intervals with a person's duck_edits applied in order (M5): a "duck" window is added, a lifted one
    cut out of every interval; merged again (a lifted window is at least HOLD_GAP_S, so the ramps never overlap)."""
    cur = list(intervals)
    for e in (music or {}).get("duck_edits") or []:
        if e["duck"]:
            cur.append((e["a"], e["b"]))
        else:
            cur = [x for a, b in cur for x in ((a, min(b, e["a"])), (max(a, e["b"]), b)) if x[1] - x[0] > 1e-3]
        cur = merge(cur)
    return cur


FOLLOW_JOIN_S = 0.06  # pieces of one window closer than this in the new cut are one window (clips abut, a frame apart)


def _card(edl: dict) -> bool:
    return edl.get("title_card", True) is not False and bool(edl.get("title_card_text"))


def _lead(edl: dict) -> float:
    """The title card's seconds in front of the cut as follow_cut counts them: 0 with the card off, else what the
    EDL's render record says (an op's EDL carries the version's record, or none), else the length a render gives it."""
    from .render import TITLE_S
    from .review import title_seconds
    if not _card(edl):
        return 0.0
    if "title" in (edl.get("render") or {}):
        return title_seconds(edl)
    fps = float(edl["source"]["fps"])
    return round(TITLE_S * fps) / fps


def follow_cut(prev: dict, new: dict, severity: str = "human") -> list[dict]:
    """M5's duck edits through a structural edit (prev -> new): each window goes with the source seconds it covers
    (prev's cut map back to the recording, new's forward), so a trim, move, drop, insert, split, a re-plan or the title
    card on or off keep it on its words. The pieces that stay in the cut and meet there are one window; pieces apart
    become windows of their own; a window whose words left the cut goes (a lifted piece under HOLD_GAP_S too). Writes
    new's `music.duck_edits` and returns a patch fix per window that moved or went. Nothing to do when the op set the
    duck edits itself, or the spans and the card are as they were."""
    from .review import cut_map
    edits = ((prev.get("music") or {}).get("duck_edits")) or []
    m = new.get("music")
    if not edits or not m or m.get("duck_edits") != edits:
        return []
    lp = _lead(prev)
    ln = lp if _card(new) == _card(prev) else _lead(new)  # the same card: the same seconds, whatever new's record
    shape = lambda e: [(s["t_in"], s["t_out"]) for s in e["spans"]]  # noqa: E731
    if shape(prev) == shape(new) and abs(lp - ln) < 1e-6:
        return []
    cp = cut_map({**prev, "render": {"title": {"seconds": lp} if lp else None}})
    cn = cut_map({**new, "render": {"title": {"seconds": ln} if ln else None}})
    out, fixes = [], []
    for e in edits:
        a, b = float(e["a"]), float(e["b"])
        got: list[tuple[float, float]] = []
        if a < lp and min(b, lp, ln) > a:  # the part over the title card stays over the card
            got.append((a, min(b, lp, ln)))
        for c in cp:
            x, y = max(a, c.cut_in), min(b, c.cut_out)
            if y <= x:
                continue
            s = prev["spans"][c.index]
            sa, sb = s["t_in"] + x - c.cut_in, s["t_in"] + y - c.cut_in
            for d in cn:
                t = new["spans"][d.index]
                u, v = max(sa, t["t_in"]), min(sb, t["t_out"])
                if v > u:
                    got.append((max(d.cut_in, d.cut_in + u - t["t_in"]), min(d.cut_out, d.cut_in + v - t["t_in"])))
        runs: list[list[float]] = []
        for x, y in sorted(got):
            if runs and x - runs[-1][1] < FOLLOW_JOIN_S:
                runs[-1][1] = max(runs[-1][1], y)
            else:
                runs.append([x, y])
        keep = [(round(x, 2), round(y, 2)) for x, y in runs if y - x >= (0.05 if e["duck"] else HOLD_GAP_S)]
        what = "held down" if e["duck"] else "lifted"
        if keep == [(round(a, 2), round(b, 2))]:
            out.append(dict(e))
            continue
        out.extend({"a": x, "b": y, "duck": e["duck"]} for x, y in keep)
        to = ", ".join(f"{x:.2f}-{y:.2f} s" for x, y in keep)
        note = (f"music {what} {a:.2f}-{b:.2f} s follows its words to {to}" if keep else
                f"music {what} {a:.2f}-{b:.2f} s removed: its words left the cut" if not runs else
                f"music {what} {a:.2f}-{b:.2f} s removed: under {HOLD_GAP_S:g} s of it is left in the cut")
        fixes.append({"op": "duck_follow", "target": "music", "issue": -1, "severity": severity,
                      "args": {"from": [a, b], "to": [list(k) for k in keep], "duck": e["duck"]}, "note": note})
    if len(out) > MAX_DUCK_EDITS:
        fixes.append({"op": "duck_follow", "target": "music", "issue": -1, "severity": severity, "args": {},
                      "note": f"music: {len(out) - MAX_DUCK_EDITS} duck edit(s) past the {MAX_DUCK_EDITS} kept removed"})
        out = out[:MAX_DUCK_EDITS]
    new["music"] = {**m, "duck_edits": sorted(out, key=lambda e: (e["a"], e["b"]))} if out else \
        {k: v for k, v in m.items() if k != "duck_edits"}
    return fixes


def merge(intervals: list[tuple[float, float]], gap_s: float = HOLD_GAP_S) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a - out[-1][1] < gap_s:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def speech_intervals(segments, words: list[dict] | None, lead_s: float = 0.0, audio=None) -> tuple[list[tuple[float, float]], str]:
    """Where someone speaks, in cut seconds, merged over pauses under HOLD_GAP_S; and what it came from ("voice
    words", "audio" for audio.wav's speech runs, "none"). `segments` are the render's frame-aligned segments in order."""
    raw: list[tuple[float, float]] = []
    source = "none"
    off = lead_s
    for seg in segments:
        if words:
            source = "voice words"
            for w in words:
                a, b = max(seg.t_in, float(w["t_start"])), min(seg.t_out, float(w["t_end"]))
                if b > a:
                    raw.append((off + a - seg.t_in, off + b - seg.t_in))
        elif audio is not None:
            source = "audio"
            raw.extend((off + a - seg.t_in, off + b - seg.t_in) for a, b in audio.speech_runs(seg.t_in, seg.t_out))
        off += seg.duration
    return merge(raw), source


def duck_expr(intervals: list[tuple[float, float]], base_db: float, duck_db: float) -> str:
    """The gain over time as one ffmpeg expression (linear factor): base_db, plus duck_db inside every interval with
    ramps of ATTACK_S before and RELEASE_S after. The ramps never overlap (merge keeps the gaps >= HOLD_GAP_S), so
    the sum of the trapezoids is the duck amount, 0..1."""
    terms = [f"clip((t-{a - ATTACK_S:.3f})/{ATTACK_S},0,1)*clip(({b + RELEASE_S:.3f}-t)/{RELEASE_S},0,1)"
             for a, b in intervals]
    duck = "+".join(terms) if terms and duck_db else "0"
    return f"pow(10,({base_db:.2f}+({duck_db:.2f})*({duck}))/20)"


def duck_at(intervals: list[tuple[float, float]], t: float) -> float:
    """The duck amount (0..1) at cut second t, as duck_expr computes it (for tests and the measured windows)."""
    return sum(min(1.0, max(0.0, (t - (a - ATTACK_S)) / ATTACK_S)) * min(1.0, max(0.0, (b + RELEASE_S - t) / RELEASE_S))
               for a, b in intervals)


def fade_out_for(music: dict, duration_s: float) -> float:
    """The fade at the end of the cut: the person's fade_out_s (M4), else FADE_OUT_S; at most a third of the cut."""
    return min(float(music.get("fade_out_s", FADE_OUT_S)), duration_s / 3)


def bed_graph(duration_s: float, gain_db: float, duck_db: float, intervals: list[tuple[float, float]], fmt: str,
              start_s: float = 0.0, fade_out_s: float = FADE_OUT_S) -> str:
    """The filter chain for the looped track (input 0, looped by -stream_loop) -> the bed: from `start_s` into the
    loop, cut to the cut's length, the static gain to level_db, the duck envelope, a fade in and out."""
    fade_out = min(fade_out_s, duration_s / 3)
    trim = f"atrim=start={start_s:.3f}:duration={duration_s:.6f}" if start_s else f"atrim=duration={duration_s:.6f}"
    fade = f",afade=t=out:st={max(0.0, duration_s - fade_out):.6f}:d={fade_out:.3f}" if fade_out > 0 else ""
    return (f"[0:a]{fmt},{trim},asetpts=PTS-STARTPTS,asetnsamples=n={ENV_SAMPLES},"
            f"volume='{duck_expr(intervals, gain_db, duck_db)}':eval=frame,"
            f"afade=t=in:st=0:d={min(FADE_IN_S, duration_s / 3):.3f}{fade}[a]")


def render_bed(music: dict, duration_s: float, intervals: list[tuple[float, float]], out: Path, rate: int, fmt: str) -> dict:
    """The ducked bed as a float WAV of exactly duration_s; returns the record for the EDL's render block."""
    t = track(music["track"])
    gain = round(music["level_db"] - float(t["lufs"]), 2)
    graph = bed_graph(duration_s, gain, music["duck_db"], intervals, fmt, float(music.get("start_s") or 0.0),
                      float(music.get("fade_out_s", FADE_OUT_S)))
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", t["path"],
                           "-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_f32le", "-ar", str(rate), str(out)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg music bed failed ({proc.returncode})\n{proc.stderr[-2000:]}")
    return {"track": t["id"], "name": t["name"], "level_db": music["level_db"], "duck_db": music["duck_db"],
            "track_lufs": t["lufs"], "gain_db": gain, "loop_s": t["loop_s"], "attack_s": ATTACK_S,
            "release_s": RELEASE_S, "hold_gap_s": HOLD_GAP_S, "fade_in_s": FADE_IN_S,
            "fade_out_s": float(music.get("fade_out_s", FADE_OUT_S)), "start_s": float(music.get("start_s") or 0.0),
            "duck_edits": music.get("duck_edits") or [], "ducked": [list(x) for x in intervals]}


def read_f32(path: Path) -> tuple[array.array, int, int]:
    """A float WAV (as ffmpeg writes pcm_f32le) -> (interleaved samples, rate, channels), parsed by hand: the
    wave module reads PCM integers only."""
    raw = path.read_bytes()
    pos, rate, ch, data = 12, 0, 0, b""
    while pos + 8 <= len(raw):
        cid, size = raw[pos:pos + 4], int.from_bytes(raw[pos + 4:pos + 8], "little")
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            ch, rate = int.from_bytes(body[2:4], "little"), int.from_bytes(body[4:8], "little")
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    a = array.array("f")
    a.frombytes(data[:len(data) // 4 * 4])
    return a, rate, ch


def rms_db(samples: array.array, rate: int, ch: int, spans: list[tuple[float, float]]) -> float | None:
    """RMS level in dBFS over the given cut-second spans together (all channels); None when they hold no samples."""
    acc, n = 0.0, 0
    for a, b in spans:
        k0, k1 = max(0, int(a * rate)) * ch, min(len(samples) // ch, int(b * rate)) * ch
        for k in range(k0, k1, STRIDE):
            acc += samples[k] * samples[k]
        n += len(range(k0, k1, STRIDE))
    if not n or acc <= 0:
        return None
    return round(10 * math.log10(acc / n), 1)


def measure_windows(speech: Path, bed: Path, intervals: list[tuple[float, float]], duration_s: float,
                    voice_db: float = 0.0, fade_out_s: float = FADE_OUT_S) -> dict:
    """The listen check, measured: speech and bed levels under speech (inside the ducked intervals, ramps left out)
    and the bed where nobody speaks (between them, ramps and fades left out). Both stems go through the same final
    gain in the mix, so `margin_db` (speech minus bed under it) is what a listener gets; `voice_db` is the gain the
    mix gives the speech (the stem on disk is without it)."""
    s, rate, ch = read_f32(speech)
    m, rate_m, ch_m = read_f32(bed)
    under = [(a, b) for a, b in intervals if b > a]
    gaps, cur, end = [], FADE_IN_S, duration_s - min(max(FADE_OUT_S, fade_out_s), duration_s / 3)
    for a, b in intervals:
        hi = min(a - ATTACK_S, end)
        if hi - cur > 0.2:
            gaps.append((cur, hi))
        cur = max(cur, b + RELEASE_S)
    if end - cur > 0.2:
        gaps.append((cur, end))
    speech_db, bed_under = rms_db(s, rate, ch, under), rms_db(m, rate_m, ch_m, under)
    if speech_db is not None and voice_db:
        speech_db = round(speech_db + voice_db, 1)
    return {"speech_rms_db": speech_db, "bed_under_speech_rms_db": bed_under,
            "bed_open_rms_db": rms_db(m, rate_m, ch_m, gaps),
            "margin_db": round(speech_db - bed_under, 1) if speech_db is not None and bed_under is not None else None,
            "under_s": round(sum(b - a for a, b in under), 2), "open_s": round(sum(b - a for a, b in gaps), 2)}
