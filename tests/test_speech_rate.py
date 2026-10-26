"""Offline: the planner's speech rate comes from Voice (words over the spoken seconds of the turns), never a constant."""
import json
from pathlib import Path

import pytest

from montivo.engine.voice import Turn, VoiceResult, Word, fold_events

REC = Path(__file__).resolve().parent / "fixtures" / "voice_stream_nasa30.json"


@pytest.mark.skipif(not REC.is_file(), reason="recorded stream not present")
def test_speech_rate_from_recorded_stream():
    events = json.loads(REC.read_text())["events"]
    turns, words = fold_events(events, offset_ms=0)
    res = VoiceResult(turns=turns, words=words, audio_s=30.0, chunks=[])
    spoken_s = sum(t.endMs - t.startMs for t in turns) / 1000
    assert spoken_s > 0
    assert res.speech_rate() == round(len(words) / spoken_s, 2)
    assert 1.2 <= res.speech_rate() <= 4.0
    assert res.speech_rate() != 2.5  # a measured figure, not the old constant


def test_speech_rate_falls_back_when_unknown_or_absurd():
    assert VoiceResult(turns=[], words=[], audio_s=0.0, chunks=[]).speech_rate() == 2.5
    # 1 word stretched over 10 s is not a speech rate the planner should trust
    t = Turn(turnId=0, startMs=0, endMs=10_000, speaker="A", transcript="hi", n_words=1)
    w = Word(w="hi", t_start=0.0, t_end=10.0, turn_id=0, speaker="A", i=0)
    assert VoiceResult(turns=[t], words=[w], audio_s=10.0, chunks=[]).speech_rate() == 2.5
    assert VoiceResult(turns=[t], words=[w], audio_s=10.0, chunks=[]).speech_rate(default=2.0) == 2.0


def test_speech_rate_counts_spoken_seconds_not_gaps():
    # two 5 s turns with a 20 s gap between them: 20 words / 10 s = 2.0, not 20 / 30
    turns = [Turn(turnId=0, startMs=0, endMs=5_000, speaker="A", transcript="", n_words=10),
             Turn(turnId=1, startMs=25_000, endMs=30_000, speaker="A", transcript="", n_words=10)]
    words = [Word(w="w", t_start=0, t_end=0, turn_id=0, speaker="A", i=i) for i in range(10)] + \
            [Word(w="w", t_start=0, t_end=0, turn_id=1, speaker="A", i=i) for i in range(10)]
    assert VoiceResult(turns=turns, words=words, audio_s=30.0, chunks=[]).speech_rate() == 2.0
