"""Voice: one streaming DIARIZATION call per clip, turned into turns[] and lag-corrected words[].

Measured on 26/10/2026 (see docs/measurements.md): with partialMode DELTA and emitAudioProgress the endpoint emits
`transcript` deltas (one or a few words each) stamped with audioProcessedMs on an 80 ms grid, about 0.5 s after
the audio they describe, plus speechStart / speechEnd / speaker / speechComplete events carrying turn ids.
Word time = delta time minus the lag; a word ends where the next delta begins; the first and last word of a turn
snap to speechStart / speechEnd when they are within 0.4 s of them.

Files longer than 10 min (or 32 MB of wav) are split on silence into ~8 min chunks, transcribed one by one and
re-joined with offsets; turn ids are renumbered so they stay unique across the clip.
"""
from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import (VOICE_CHUNK_TARGET_S, VOICE_LAG_MS, VOICE_MAX_BYTES, VOICE_MAX_SECONDS, VOICE_MODEL, VOICE_SNAP_MS,
                      voice_cost)
from ..ledger import Entry, Ledger, Timer
from .api import dump_json, stream_transcribe
from .ingest import make_wav

WAV_BYTES_PER_S = 24000 * 2  # 24 kHz mono 16-bit


@dataclass
class Word:
    w: str
    t_start: float
    t_end: float
    turn_id: int
    speaker: str
    i: int  # index inside the turn


@dataclass
class Turn:
    turnId: int
    startMs: int
    endMs: int
    speaker: str
    transcript: str
    transcript_complete: str = ""
    n_words: int = 0


@dataclass
class VoiceResult:
    turns: list[Turn]
    words: list[Word]
    audio_s: float
    chunks: list[dict]
    speakers: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"model": VOICE_MODEL, "audio_s": self.audio_s, "speakers": self.speakers, "chunks": self.chunks,
                "turns": [asdict(t) for t in self.turns], "words": [asdict(w) for w in self.words]}

    @classmethod
    def from_json(cls, data: dict) -> "VoiceResult":
        turns = [Turn(**t) for t in data["turns"]]
        words = [Word(**w) for w in data["words"]]
        return cls(turns=turns, words=words, audio_s=data.get("audio_s", 0.0), chunks=data.get("chunks", []),
                   speakers=data.get("speakers") or sorted({t.speaker for t in turns}))

    def transcript_text(self) -> str:
        """The authoritative transcript handed to Spark: one line per turn with id, times and speaker."""
        return "\n".join(f"[turn {t.turnId} | {t.startMs / 1000:.2f}-{t.endMs / 1000:.2f}s | speaker {t.speaker}] {t.transcript}"
                         for t in self.turns)


# --------------------------------------------------------------------------- event folding

class _TurnBuilder:
    def __init__(self, turn_id: int, start_ms: int):
        self.turn_id = turn_id
        self.start_ms = start_ms
        self.end_ms: int | None = None
        self.speaker: str | None = None
        self.complete: str | None = None
        self.deltas: list[tuple[int, str]] = []  # (audioProcessedMs, text)


def fold_events(events: list[dict], offset_ms: int = 0, turn_offset: int = 0) -> tuple[list[Turn], list[Word]]:
    """Turn the raw event list of one chunk into turns and lag-corrected words (absolute times)."""
    builders: dict[int, _TurnBuilder] = {}
    order: list[int] = []
    open_id: int | None = None
    last_id: int | None = None
    for ev in events:
        et = ev.get("type")
        if et == "speechStart":
            tid = int(ev["turnId"])
            builders[tid] = _TurnBuilder(tid, int(ev["audioProcessedMs"]))
            order.append(tid)
            open_id = last_id = tid
        elif et == "transcript":
            target = open_id if open_id is not None else last_id
            if target is None:
                continue  # stray text before the first speechStart: nothing to attach it to
            builders[target].deltas.append((int(ev["audioProcessedMs"]), ev.get("transcript") or ""))
        elif et == "speaker":
            target = open_id if open_id is not None else last_id
            if target is not None and builders[target].speaker is None:
                builders[target].speaker = str(ev.get("label", "?"))
        elif et == "speechEnd":
            tid = int(ev["turnId"])
            if tid in builders:
                builders[tid].end_ms = int(ev["audioProcessedMs"])
                if open_id == tid:
                    open_id = None
        elif et == "speechComplete":
            tid = int(ev["turnId"])
            if tid in builders:
                builders[tid].complete = (ev.get("transcript") or "").strip()
                if builders[tid].end_ms is None:
                    builders[tid].end_ms = int(ev["audioProcessedMs"])
                if open_id == tid:
                    open_id = None

    turns: list[Turn] = []
    words: list[Word] = []
    for n, tid in enumerate(order):
        b = builders[tid]
        if b.end_ms is None:
            b.end_ms = max([b.start_ms] + [ms for ms, _ in b.deltas])
        new_id = turn_offset + n
        speaker = b.speaker or "?"
        tw = _words_from_deltas(b, new_id, speaker, offset_ms)
        if not tw:
            continue  # a turn with no words is noise (music, breath)
        transcript = " ".join(w.w for w in tw)
        turns.append(Turn(turnId=new_id, startMs=b.start_ms + offset_ms, endMs=b.end_ms + offset_ms, speaker=speaker,
                          transcript=transcript, transcript_complete=b.complete or "", n_words=len(tw)))
        words.extend(tw)
    return turns, words


def _words_from_deltas(b: _TurnBuilder, turn_id: int, speaker: str, offset_ms: int) -> list[Word]:
    # raw (delta-domain) boundaries: the turn starts LAG after speechStart in that domain
    prev = b.start_ms + VOICE_LAG_MS
    raw: list[tuple[str, float, float]] = []  # (word, raw_start, raw_end)
    for ms, text in b.deltas:
        toks = text.split()
        t = max(ms, prev)
        if toks:
            step = (t - prev) / len(toks)
            for i, tok in enumerate(toks):
                raw.append((tok, prev + step * i, prev + step * (i + 1)))
        else:
            # whitespace delta: it only closes the previous word
            if raw:
                w, s, _ = raw[-1]
                raw[-1] = (w, s, t)
        prev = t
    if not raw:
        return []
    # the last word runs to speechEnd (delta domain) when no later delta closed it
    w, s, e = raw[-1]
    raw[-1] = (w, s, max(e, b.end_ms + VOICE_LAG_MS))

    start_ms, end_ms = b.start_ms, b.end_ms
    out: list[Word] = []
    for i, (w, s, e) in enumerate(raw):
        s -= VOICE_LAG_MS
        e -= VOICE_LAG_MS
        s = min(max(s, start_ms), end_ms)
        e = min(max(e, s), end_ms)
        if i == 0 and abs(s - start_ms) <= VOICE_SNAP_MS:
            s = start_ms
        if i == len(raw) - 1 and abs(e - end_ms) <= VOICE_SNAP_MS:
            e = end_ms
        out.append(Word(w=w, t_start=round((s + offset_ms) / 1000, 3), t_end=round((e + offset_ms) / 1000, 3),
                        turn_id=turn_id, speaker=speaker, i=i))
    return out


# --------------------------------------------------------------------------- chunking

_SIL = re.compile(r"silence_(start|end): ([0-9.]+)")


def detect_silences(wav: Path, noise_db: float = -35.0, min_s: float = 0.5) -> list[tuple[float, float]]:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af",
                           f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                          capture_output=True, text=True)
    out: list[tuple[float, float]] = []
    start: float | None = None
    for kind, val in _SIL.findall(proc.stderr):
        if kind == "start":
            start = float(val)
        elif start is not None:
            out.append((start, float(val)))
            start = None
    return out


def plan_chunks(duration_s: float, silences: list[tuple[float, float]], target_s: float = VOICE_CHUNK_TARGET_S,
                max_s: float = VOICE_MAX_SECONDS, window: tuple[float, float] = (120.0, 60.0)) -> list[tuple[float, float]]:
    """(start, end) pairs covering the file; every cut sits in the silence closest to the 8 min target."""
    chunks: list[tuple[float, float]] = []
    cursor = 0.0
    while duration_s - cursor > max_s - 5:
        target = cursor + target_s
        candidates = [(s + e) / 2 for s, e in silences if target - window[0] <= (s + e) / 2 <= target + window[1]]
        cut = min(candidates, key=lambda c: abs(c - target)) if candidates else target
        chunks.append((cursor, cut))
        cursor = cut
    chunks.append((cursor, duration_s))
    return chunks


def wav_duration_s(wav: Path) -> float:
    return max(0.0, (wav.stat().st_size - 44) / WAV_BYTES_PER_S)


# --------------------------------------------------------------------------- the stage

def transcribe(wav: Path, out_dir: Path, ledger: Ledger, language: str | None = "English", log=print,
               workers: int = 2) -> VoiceResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    request = {"mode": "DIARIZATION", "model": VOICE_MODEL, "audioEncoding": "WAV",
               "partialMode": "DELTA", "emitAudioProgress": True}
    if language:
        request["languageBias"] = [language]
    dump_json(out_dir / "request.json", request)

    duration = wav_duration_s(wav)
    if duration > VOICE_MAX_SECONDS or wav.stat().st_size > VOICE_MAX_BYTES:
        silences = detect_silences(wav)
        spans = plan_chunks(duration, silences)
        log(f"voice: {duration:.0f} s wav is over the 10 min limit, splitting into {len(spans)} chunks on silence")
    else:
        spans = [(0.0, duration)]

    chunk_files: list[tuple[int, Path, float, float]] = []
    for i, (s, e) in enumerate(spans):
        if len(spans) == 1:
            chunk_files.append((i, wav, s, e))
        else:
            cw = out_dir / f"chunk_{i:02d}.wav"
            make_wav(wav, cw, start=s, seconds=e - s)
            chunk_files.append((i, cw, s, e))

    def run_chunk(item: tuple[int, Path, float, float]) -> tuple[int, list[dict], float]:
        i, path, s, e = item
        events: list[dict] = []
        with Timer() as t:
            for ev in stream_transcribe(path, request):
                ev["chunk"] = i
                events.append(ev)
        log(f"voice: chunk {i} ({e - s:.0f} s) streamed {len(events)} events in {t.seconds}s")
        return i, events, t.seconds

    with Timer() as t_all, ThreadPoolExecutor(max_workers=min(workers, len(chunk_files))) as pool:
        results = sorted(pool.map(run_chunk, chunk_files), key=lambda r: r[0])

    with (out_dir / "events.jsonl").open("w") as fh:
        for _, events, _ in results:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")

    turns: list[Turn] = []
    words: list[Word] = []
    chunks_meta: list[dict] = []
    for (i, _path, s, e), (_, events, secs) in zip(chunk_files, results):
        ct, cw = fold_events(events, offset_ms=int(round(s * 1000)), turn_offset=len(turns))
        turns.extend(ct)
        words.extend(cw)
        chunks_meta.append({"chunk": i, "start_s": s, "end_s": e, "events": len(events), "turns": len(ct), "words": len(cw), "seconds": secs})

    res = VoiceResult(turns=turns, words=words, audio_s=round(duration, 2), chunks=chunks_meta,
                      speakers=sorted({t.speaker for t in turns}))
    dump_json(out_dir / "voice.json", res.to_json())
    cost = voice_cost(duration)
    ledger.add(Entry(stage="voice", model=VOICE_MODEL, seconds=t_all.seconds, cost_usd=round(cost, 5), audio_s=round(duration, 2),
                     note=f"{len(turns)} turns, {len(words)} words, speakers {res.speakers}, {len(spans)} chunk(s)"))
    log(f"voice: {len(turns)} turns, {len(words)} words, speakers {res.speakers}, ${cost:.4f}, {t_all.seconds}s")
    return res
