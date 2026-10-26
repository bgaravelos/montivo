"""Edges: tighten every cut to where speech really starts and ends, measured on the source audio.

Voice words always abut and Voice puts the pauses inside words: the first word of a passage often starts in the
silence before it ("Hi." ran 25.50 to 27.18 s in run 012, the speech starts at 27.15) and a turn's last word runs on to
the end of speech ("out." 121.90 to 124.14 s, spoken by 122.05). A cut on Voice word boundaries therefore opens up to
1.6 s before the first word and holds about 2 s after the last one. This pass moves each in edge to LEAD_S before the
speech onset and each out edge to TAIL_S after the speech offset (END_TAIL_S on the last span of the cut), using a
speech mask from the source audio (10 ms frames, 30 ms smoothing, threshold SPEECH_OVER_FLOOR_DB over the file's
noise floor). It is deterministic, costs nothing and only ever tightens: an edge moves into its own span, never past
its first or last word by more than Voice's slop, never past the middle of the span, so it cannot reach a word another
span keeps. Spans the render joins into one continuous segment are never split.

One case moves an edge past sound: a short fragment of the neighbouring word that is still going at the edge and is
followed (in edge) or preceded (out edge) by a real pause. Run 012's second take opened on the end of "again" (93.98 to
94.25 s), then 1.05 s of silence before "this is the part that matters"; the fragment goes with the pause.

Moved edges are recorded per span (`edges`) and in `edge_tuned`, so the review patcher treats them as on a boundary.
"""
from __future__ import annotations

import array
import math
import wave
from pathlib import Path

FRAME_S = 0.01
SPEECH_OVER_FLOOR_DB = 6.0  # run 012: floor -51 dB (10th percentile), word onsets cross -45 within 10 to 20 ms
FLOOR_PERCENTILE = 0.10
MIN_SPEECH_S = 0.04  # a louder run shorter than this is a click or a breath, not speech
MERGE_GAP_S = 0.06  # dips shorter than this (stop consonants) stay inside one run of speech
LEAD_S = 0.10
TAIL_S = 0.15
END_TAIL_S = 0.30  # the last word of the cut gets a beat before the player loops
SLOP_S = 0.6  # Voice word edges were measured up to 0.4 s off on run 012 ("this" heard 0.4 s after its Voice end)
FRAGMENT_MAX_S = 0.35  # a neighbour's sound still going at the edge, at most this long inside the span ...
FRAGMENT_GAP_S = 0.30  # ... and separated from the span's own speech by a pause at least this long
MIN_MOVE_S = 0.03  # smaller moves are left alone (a frame at 30 fps)
JOIN_GAP_S = 0.05  # render.CONTIGUOUS_GAP_S: spans of one turn closer than this render as one segment


def level_db(pcm: array.array, rate: int, t0: float, t1: float) -> float:
    seg = pcm[max(0, int(t0 * rate)):max(0, int(t1 * rate))]
    if not seg:
        return -120.0
    rms = math.sqrt(sum(x * x for x in seg) / len(seg))
    return round(20 * math.log10(rms / 32768 + 1e-9), 1)


class Words:
    """Voice words by turn (voice.json `words`), indexed like the EDL's word_from / word_to."""

    def __init__(self, words: list[dict]):
        self.by_turn: dict[int, list[dict]] = {}
        for w in words:
            self.by_turn.setdefault(int(w["turn_id"]), []).append(w)
        for tw in self.by_turn.values():
            tw.sort(key=lambda w: w["i"])

    def turn(self, tid: int) -> list[dict]:
        return self.by_turn.get(int(tid), [])


class Audio:
    """The run's source audio (audio.wav, 24 kHz mono from ingest) for level checks in source seconds."""

    def __init__(self, wav: Path):
        with wave.open(str(wav)) as w:
            self.rate = w.getframerate()
            self.pcm = array.array("h", w.readframes(w.getnframes()))

    @classmethod
    def load(cls, run_dir: Path) -> "Audio | None":
        p = run_dir / "audio.wav"
        return cls(p) if p.is_file() else None

    def level(self, t0: float, t1: float) -> float:
        return level_db(self.pcm, self.rate, t0, t1)

    def quietest(self, lo: float, hi: float, half: float = 0.02, step: float = 0.02) -> tuple[float, float]:
        """(time, dB) of the quietest 2*half window centred between lo and hi."""
        best = (lo, 1e9)
        k = 0
        while lo + k * step <= hi + 1e-9:
            x = round(lo + k * step, 3)
            db = self.level(x - half, x + half)
            if db < best[1]:
                best = (x, db)
            k += 1
        return best

    def frame_db(self) -> list[float]:
        """dB of every 10 ms frame of the file, smoothed over 30 ms (computed once)."""
        cached = getattr(self, "_frame_db", None)
        if cached is not None:
            return cached
        n = int(round(FRAME_S * self.rate))
        ms = [math.sumprod(seg, seg) / n for seg in (self.pcm[k:k + n] for k in range(0, len(self.pcm) - n + 1, n))]
        out = []
        for k in range(len(ms)):
            m = sum(ms[max(0, k - 1):k + 2]) / len(ms[max(0, k - 1):k + 2])
            out.append(10 * math.log10(m / 32768 ** 2 + 1e-12))
        self._frame_db = out
        return out

    def floor_db(self) -> float:
        cached = getattr(self, "_floor_db", None)
        if cached is None:
            db = sorted(self.frame_db())
            cached = self._floor_db = round(db[int(len(db) * FLOOR_PERCENTILE)], 1) if db else -120.0
        return cached

    def threshold_db(self) -> float:
        return round(self.floor_db() + SPEECH_OVER_FLOOR_DB, 1)

    def speech_runs(self, t0: float, t1: float) -> list[tuple[float, float]]:
        """Runs of speech inside [t0, t1] (source seconds, on the 10 ms frame grid): frames over the threshold, dips
        under MERGE_GAP_S bridged, runs under MIN_SPEECH_S dropped."""
        db, thr = self.frame_db(), self.threshold_db()
        k0, k1 = max(0, int(math.floor(t0 / FRAME_S))), min(len(db), int(math.ceil(t1 / FRAME_S)))
        runs: list[list[int]] = []
        for k in range(k0, k1):
            if db[k] <= thr:
                continue
            if runs and (k - runs[-1][1]) * FRAME_S <= MERGE_GAP_S:
                runs[-1][1] = k + 1
            else:
                runs.append([k, k + 1])
        return [(round(a * FRAME_S, 3), round(b * FRAME_S, 3)) for a, b in runs if (b - a) * FRAME_S >= MIN_SPEECH_S]


def joins(prev: dict, cur: dict) -> bool:
    """The render merges cur into prev's segment (render.merge_segments): no cut between them."""
    return abs(prev["t_out"] - cur["t_in"]) < JOIN_GAP_S and (
        bool(cur.get("contiguous_with_previous")) or cur.get("turn_id") == prev.get("turn_id"))


def onset(span: dict, words: Words, audio: Audio, bound: float | None = None) -> tuple[float | None, bool]:
    """Where the span's own speech starts, searched up to `bound` (default the middle of the span): (time or None,
    a neighbour's fragment skipped)."""
    tw = words.turn(span["turn_id"])
    t = span["t_in"]
    hi = min(tw[span["word_from"]]["t_end"] + SLOP_S, bound if bound is not None else (t + span["t_out"]) / 2)
    runs = [r for r in audio.speech_runs(t - 0.1, hi) if r[1] > t]
    skipped = False
    if runs and runs[0][0] <= t + FRAME_S:  # sound already going at the edge
        nxt = runs[1][0] if len(runs) > 1 else hi
        if runs[0][1] - t > FRAGMENT_MAX_S or nxt - runs[0][1] < FRAGMENT_GAP_S:
            return None, False  # speech at the edge
        runs, skipped = runs[1:], True
    if not runs:
        return None, skipped
    return runs[0][0], skipped


def offset(span: dict, words: Words, audio: Audio, bound: float | None = None) -> tuple[float | None, bool]:
    """Where the span's own speech ends, searched back to `bound` (default the middle of the span): (time or None,
    a neighbour's fragment skipped)."""
    tw = words.turn(span["turn_id"])
    t = span["t_out"]
    lo = max(tw[span["word_to"]]["t_start"] - SLOP_S, bound if bound is not None else (span["t_in"] + t) / 2)
    runs = [r for r in audio.speech_runs(lo, t + 0.1) if r[0] < t]
    skipped = False
    if runs and runs[-1][1] >= t - FRAME_S:
        prv = runs[-2][1] if len(runs) > 1 else lo
        if t - runs[-1][0] > FRAGMENT_MAX_S or runs[-1][0] - prv < FRAGMENT_GAP_S:
            return None, False  # speech at the edge
        runs, skipped = runs[:-1], True
    if not runs:
        return None, skipped
    return runs[-1][1], skipped


def tighten_edge(span: dict, words: Words, edge: str, audio: Audio, tail: float = TAIL_S,
                 bound: float | None = None) -> dict | None:
    """Move one edge of the span to the speech onset - LEAD_S (in) or the speech offset + tail (out), only inward and
    never past `bound` (the middle of the segment the span opens or closes). Returns the record, None when it stays."""
    key = "t_in" if edge == "in" else "t_out"
    before = span[key]
    if edge == "in":
        at, frag = onset(span, words, audio, bound)
        new = None if at is None else round(max(before, at - LEAD_S), 3)
        moved = new is not None and new - before >= MIN_MOVE_S
    else:
        at, frag = offset(span, words, audio, bound)
        new = None if at is None else round(min(before, at + tail), 3)
        moved = new is not None and before - new >= MIN_MOVE_S
    if not moved:
        return None
    span[key] = new
    span["duration"] = round(span["t_out"] - span["t_in"], 3)
    span.setdefault("edge_tuned", {})[edge] = new
    rec = {"from": round(before, 3), "to": new, "speech": at, "fragment": frag,
           "db_before": audio.level(before - 0.02, before + 0.02), "db_after": audio.level(new - 0.02, new + 0.02)}
    span.setdefault("edges", {})[edge] = rec
    return rec


def cut_edges(spans: list[dict]) -> list[tuple[int, str, float]]:
    """(span index, edge, bound) of every edge that is a cut, not inside a segment the render joins; bound is the
    middle of that segment, the furthest the edge may move."""
    groups: list[list[int]] = []
    for k, s in enumerate(spans):
        if k and joins(spans[k - 1], s):
            groups[-1].append(k)
        else:
            groups.append([k])
    out: list[tuple[int, str, float]] = []
    for g in groups:
        mid = (spans[g[0]]["t_in"] + spans[g[-1]]["t_out"]) / 2
        out += [(g[0], "in", mid), (g[-1], "out", mid)]
    return out


def tighten_edges(edl: dict, words: Words, audio: Audio, only: set[tuple[str, str]] | None = None) -> dict:
    """Tighten every cut edge of the EDL in place (or only the (span id, edge) pairs in `only`); updates runtime_s and
    writes the summary to edl["edge_pass"]. Running it again moves nothing."""
    spans = edl["spans"]
    moves: list[dict] = []
    for k, edge, bound in cut_edges(spans):
        s = spans[k]
        if only is not None and (s["id"], edge) not in only:
            continue
        tail = END_TAIL_S if edge == "out" and k == len(spans) - 1 else TAIL_S
        rec = tighten_edge(s, words, edge, audio, tail=tail, bound=bound)
        if rec:
            moves.append({"span": s["id"], "edge": edge, **rec})
    edl["runtime_s"] = round(sum(s["t_out"] - s["t_in"] for s in spans), 2)
    removed = round(sum(abs(m["to"] - m["from"]) for m in moves), 2)
    summary = {"floor_db": audio.floor_db(), "threshold_db": audio.threshold_db(), "lead_s": LEAD_S, "tail_s": TAIL_S,
               "end_tail_s": END_TAIL_S, "moved": len(moves), "removed_s": removed, "moves": moves}
    if only is None:
        edl["edge_pass"] = summary
    return summary
