"""Offline: fold a recorded Voice stream (30 s of a public-domain NASA interview, recorded 26/10/2026) into turns and words."""
import json
from pathlib import Path

import pytest

from montivo.engine.voice import fold_events, plan_chunks

REC = Path(__file__).resolve().parent / "fixtures" / "voice_stream_nasa30.json"


@pytest.mark.skipif(not REC.is_file(), reason="recorded stream not present")
def test_fold_recorded_stream():
    events = json.loads(REC.read_text())["events"]
    turns, words = fold_events(events, offset_ms=0)
    assert [t.turnId for t in turns] == [0, 1, 2, 3, 4]
    assert [t.speaker for t in turns] == ["A", "B", "A", "B", "A"]
    assert turns[0].transcript == "Hi Robert can you hear me?"
    assert turns[0].startMs == 1340 and turns[0].endMs == 2700
    w0 = [w for w in words if w.turn_id == 0]
    assert [w.w for w in w0] == ["Hi", "Robert", "can", "you", "hear", "me?"]
    assert w0[0].t_start == 1.34  # snapped to speechStart
    assert w0[-1].t_end == 2.7  # last word ends at speechEnd
    assert all(a.t_end <= b.t_start + 1e-9 for a, b in zip(w0, w0[1:]))
    assert all(w.t_start <= w.t_end for w in words)
    # multi-word deltas were split: turn 1 had " can Terry" and " how are you?"
    assert [w.w for w in words if w.turn_id == 1] == ["I", "can", "Terry", "how", "are", "you?"]
    # every turn's words reproduce its transcript
    for t in turns:
        assert " ".join(w.w for w in words if w.turn_id == t.turnId) == t.transcript


def test_fold_offsets_and_renumbering():
    events = json.loads(REC.read_text())["events"] if REC.is_file() else []
    if not events:
        pytest.skip("recorded stream not present")
    turns, words = fold_events(events, offset_ms=480_000, turn_offset=7)
    assert turns[0].turnId == 7 and turns[0].startMs == 481_340
    assert words[0].t_start == 481.34


def test_plan_chunks_prefers_silence():
    silences = [(470.0, 471.0), (490.0, 492.0), (960.0, 961.0)]
    chunks = plan_chunks(1000.0, silences)
    assert chunks[0] == (0.0, 470.5)
    assert chunks[-1][1] == 1000.0
    assert all(e - s <= 600 for s, e in chunks)


def test_plan_chunks_short_file_untouched():
    assert plan_chunks(150.0, []) == [(0.0, 150.0)]


def test_turn_ids_stay_unique_when_a_chunk_drops_a_noise_turn():
    # Codex F07: chunk 1 = a turn with no words (music, breath) then speech; chunk 2 = speech
    def chunk(*turns):
        ev = []
        for tid, (ms, text) in enumerate(turns):
            ev += [{"type": "speechStart", "turnId": tid, "audioProcessedMs": ms}]
            ev += [{"type": "transcript", "audioProcessedMs": ms + 400, "transcript": text}] if text else []
            ev += [{"type": "speechEnd", "turnId": tid, "audioProcessedMs": ms + 800}]
        return ev

    t1, _ = fold_events(chunk((0, ""), (1000, "first")))
    t2, w2 = fold_events(chunk((0, "second")), offset_ms=480_000, turn_offset=len(t1))
    assert [(t.turnId, t.transcript) for t in t1 + t2] == [(0, "first"), (1, "second")]
    assert {w.turn_id for w in w2} == {1}
