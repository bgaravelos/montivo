"""Offline tests for the edge pass: cut edges tightened to the speech onset and offset in synthetic source audio."""
import array
import math

import pytest

from montivo.engine import edges as E
from montivo.engine import review as R
from montivo.engine.captions import span_words
from montivo.engine.edl import build_edl
from montivo.engine.voice import Turn, VoiceResult, Word

RATE = 8000
F = pytest.approx  # the 30 ms smoothing widens a run of speech by up to one 10 ms frame each side


def at(x):
    return F(x, abs=0.011)


class SynthAudio(E.Audio):
    """40 s of room tone at about -60 dB with loud "speech" (a -17 dB tone) over the given source intervals."""

    def __init__(self, speech, faint=()):
        self.rate = RATE
        self.pcm = array.array("h", [int(40 * math.sin(k * 1.7)) for k in range(40 * RATE)])
        for amp, spans in ((6000, speech), (100, faint)):  # faint: a breath, about 2 dB over the speech threshold
            for a, b in spans:
                for k in range(int(a * RATE), int(b * RATE)):
                    self.pcm[k] = int(amp * math.sin(k / 3))


def voice_words():
    """Turn 0: 12 words of 0.5 s from 10.0 s; turn 1: 12 words of 0.5 s from 30.0 s (Voice words always abut)."""
    return [{"w": f"w{tid}{i}", "t_start": round(t0 + 0.5 * i, 3), "t_end": round(t0 + 0.5 * (i + 1), 3), "turn_id": tid,
             "i": i, "speaker": "A"} for tid, t0 in ((0, 10.0), (1, 30.0)) for i in range(12)]


def span(sid, tid, a, b, contiguous=False):
    t0 = 10.0 if tid == 0 else 30.0
    return {"id": sid, "turn_id": tid, "word_from": a, "word_to": b, "t_in": round(t0 + 0.5 * a, 3),
            "t_out": round(t0 + 0.5 * (b + 1), 3), "duration": 0.5 * (b - a + 1), "contiguous_with_previous": contiguous}


def edl_of(*spans):
    return {"version": 1, "spans": list(spans), "runtime_s": sum(s["duration"] for s in spans),
            "source": {"fps": 30.0}}


WORDS = E.Words(voice_words())


def test_edges_move_to_the_speech_with_a_lead_and_a_tail():
    # s1 = words 0-3 (10.0-12.0): the first word holds 0.6 s of pause, the last one 0.5 s; s2 = turn 1 words 2-5
    # (31.0-33.0), the last span of the cut
    audio = SynthAudio([(10.6, 11.5), (31.4, 32.6)])
    edl = edl_of(span("s1", 0, 0, 3), span("s2", 1, 2, 5))
    out = E.tighten_edges(edl, WORDS, audio)
    s1, s2 = edl["spans"]
    assert (s1["t_in"], s1["t_out"]) == (at(10.5), at(11.65))  # 0.1 s before the onset, 0.15 s after the offset
    assert s2["t_in"] == at(31.3) and s2["t_out"] == at(32.9)  # the last span of the cut keeps END_TAIL_S
    assert s1["edge_tuned"] == {"in": s1["t_in"], "out": s1["t_out"]} and s1["edges"]["in"]["speech"] == at(10.6)
    assert out["moved"] == 4 and edl["edge_pass"]["moved"] == 4 and edl["runtime_s"] == round(s1["t_out"] - s1["t_in"] + s2["t_out"] - s2["t_in"], 2)
    assert out["floor_db"] < -55 and out["threshold_db"] == round(out["floor_db"] + E.SPEECH_OVER_FLOOR_DB, 1)
    assert all(m["db_after"] < -50 for m in out["moves"])  # every new edge sits in room tone


def test_a_second_pass_moves_nothing():
    audio = SynthAudio([(10.6, 11.5), (31.4, 32.6)])
    edl = edl_of(span("s1", 0, 0, 3), span("s2", 1, 2, 5))
    E.tighten_edges(edl, WORDS, audio)
    first = [(s["t_in"], s["t_out"]) for s in edl["spans"]]
    assert E.tighten_edges(edl, WORDS, audio)["moved"] == 0
    assert [(s["t_in"], s["t_out"]) for s in edl["spans"]] == first


def test_speech_at_the_edge_stays_and_nothing_moves_outward():
    # speech runs through both edges (a clipped start and end): the pass never moves an edge into or out of speech
    audio = SynthAudio([(9.0, 12.5)])
    edl = edl_of(span("s1", 0, 0, 3))
    assert E.tighten_edges(edl, WORDS, audio)["moved"] == 0
    assert (edl["spans"][0]["t_in"], edl["spans"][0]["t_out"]) == (10.0, 12.0)
    # speech starting right at the edge: the lead would reach before t_in, so it stays (only ever tightens)
    audio = SynthAudio([(10.04, 11.96)])
    assert E.tighten_edges(edl, WORDS, audio)["moved"] == 0


def test_a_fragment_of_the_neighbour_goes_with_the_pause():
    # the previous word's end runs 0.2 s into the span, then 0.6 s of pause, then the span's own speech
    audio = SynthAudio([(9.6, 10.2), (10.8, 11.8)])
    edl = edl_of(span("s1", 0, 0, 3))
    E.tighten_edges(edl, WORDS, audio)
    s = edl["spans"][0]
    assert s["t_in"] == at(10.7) and s["edges"]["in"]["fragment"] is True
    # the next word starts 0.1 s before the out edge after a pause: same on the other side (END_TAIL_S: the only span)
    audio = SynthAudio([(10.6, 11.4), (11.9, 12.4)])
    edl = edl_of(span("s1", 0, 0, 3))
    E.tighten_edges(edl, WORDS, audio)
    assert edl["spans"][0]["t_out"] == at(11.7) and edl["spans"][0]["edges"]["out"]["fragment"] is True
    # a short dip (0.15 s) is not a pause: the sound at the edge may be the span's own word, nothing moves
    audio = SynthAudio([(9.6, 10.2), (10.35, 11.8)])
    edl = edl_of(span("s1", 0, 0, 3))
    E.tighten_edges(edl, WORDS, audio)
    assert edl["spans"][0]["t_in"] == 10.0


def test_a_dropped_word_at_the_edge_goes_with_the_pause():
    # run 015: Voice placed the dropped "Okay." 0.8 s early, so the cut's first word window opened on it; here word 1
    # is dropped and its sound runs 0.38 s into s1 (longer than a fragment), a breath, then s1's own speech at 11.85
    audio = SynthAudio([(10.98, 11.38), (11.85, 12.8)], faint=[(11.5, 11.6)])
    assert E.Audio.level(audio, 11.5, 11.6) > audio.threshold_db()  # the breath is a run of its own
    edl = edl_of(span("s1", 0, 2, 5))
    E.tighten_edges(edl, WORDS, audio)
    s = edl["spans"][0]
    assert s["t_in"] == at(11.75) and s["edges"]["in"]["fragment"] is True
    # the same sound when another clip keeps word 1: it may be speech that clip needs, the edge stays
    audio = SynthAudio([(10.98, 11.38), (11.85, 12.8)], faint=[(11.5, 11.6)])
    s0, s1 = span("s0", 0, 0, 1), span("s1", 0, 2, 5)
    edl = edl_of(s1, s0)  # reordered, so the two are not joined
    E.tighten_edges(edl, WORDS, audio)
    assert s1["t_in"] == 11.0
    # a sound longer than a short word is the span's own speech, dropped neighbour or not: the clipped edge widens
    audio = SynthAudio([(10.98, 11.7), (12.1, 12.8)])
    edl = edl_of(span("s1", 0, 2, 5))
    E.tighten_edges(edl, WORDS, audio)
    s = edl["spans"][0]
    assert s["t_in"] < 10.98 and s["edges"]["in"]["widened"] is True


def test_joined_spans_are_not_split():
    # s1 words 0-3 and s2 words 4-7 of one turn render as one segment: only its outer edges are cuts
    audio = SynthAudio([(10.6, 11.6), (12.3, 13.4)])
    edl = edl_of(span("s1", 0, 0, 3), span("s2", 0, 4, 7, contiguous=True))
    assert [(k, e) for k, e, _ in E.cut_edges(edl["spans"])] == [(0, "in"), (1, "out")]
    E.tighten_edges(edl, WORDS, audio)
    s1, s2 = edl["spans"]
    assert s1["t_out"] == 12.0 and s2["t_in"] == 12.0  # the pause inside the segment stays
    assert s1["t_in"] == at(10.5) and s2["t_out"] == at(13.7)


def test_an_edge_never_moves_past_the_middle_of_its_segment():
    # the span is silent until 11.6 s: the onset is past the middle (11.0) and past the first word + slop (11.1)
    audio = SynthAudio([(11.6, 11.9)])
    edl = edl_of(span("s1", 0, 0, 3))
    E.tighten_edges(edl, WORDS, audio)
    assert edl["spans"][0]["t_in"] == 10.0


def test_build_edl_tightens_v1_when_the_audio_is_given():
    words = [Word(w=w["w"], t_start=w["t_start"], t_end=w["t_end"], turn_id=w["turn_id"], speaker="A", i=w["i"])
             for w in voice_words()]
    voice = VoiceResult(turns=[Turn(turnId=0, startMs=10000, endMs=16000, speaker="A", transcript=""),
                               Turn(turnId=1, startMs=30000, endMs=36000, speaker="A", transcript="")],
                        words=words, audio_s=40.0, chunks=[])
    plan = {"paper_edit": [{"turn_id": 0, "words": "w00 w01 w02 w03", "role": "hook"}]}
    loose = build_edl(plan, voice, {}, {"fps": 30.0}, "b", {})
    assert loose["spans"][0]["t_in"] == 10.0 and "edge_pass" not in loose
    tight = build_edl(plan, voice, {}, {"fps": 30.0}, "b", {}, audio=SynthAudio([(10.6, 11.5)]))
    s = tight["spans"][0]
    assert (s["t_in"], s["t_out"]) == (at(10.5), at(11.8))  # the only span is the last: END_TAIL_S
    assert tight["runtime_s"] == round(s["t_out"] - s["t_in"], 2) and s["duration"] == round(s["t_out"] - s["t_in"], 3)


def test_caption_words_follow_the_edges():
    s = span("s1", 0, 0, 3)
    s["t_in"], s["t_out"] = 10.5, 11.65
    ws = span_words([s], voice_words())["s1"]
    assert [(w.t_in, w.t_out) for w in ws] == [(10.5, 10.5), (10.5, 11.0), (11.0, 11.5), (11.5, 11.65)]


def test_a_review_trim_is_tightened_and_not_snapped_back():
    # the trim puts the out edge on a Voice boundary (12.0, inside the pause), settle moves it to the quietest point
    # nearby; the pass pulls it back to the speech offset + END_TAIL_S (the only span is the last)
    audio = SynthAudio([(10.6, 11.4), (12.6, 13.4)])
    edl = edl_of(span("s1", 0, 0, 5))
    for s in edl["spans"]:
        s.update(order=0, speaker="A", role="hook", words="", verbatim=True, match_ratio=1.0, callout="", why="")
    fix = R.Fix(op="trim_span", target="s1", issue=0, severity="major", t_s=1.9, args={"edge": "out", "src_t": 12.0})
    new, log = R.apply_fixes(edl, [fix], WORDS, audio)
    s = new["spans"][0]
    assert s["word_to"] == 3 and s["t_out"] == at(11.7) and "tightened to the speech" in log[0]["note"]
    assert s["t_in"] == at(10.5)  # the trim re-snapped the in edge too: tightened again
    assert s["duration"] == round(s["t_out"] - s["t_in"], 3) and R.on_boundary(s, WORDS, "out")
    edges = R.cut_edge_levels(new, audio)
    assert edges[-1]["end"] and not edges[-1]["loud"]


def test_a_trim_at_one_end_keeps_the_other_end_tight():
    # trim_at re-snaps both edges to Voice boundaries; the end tightened out of the next word must stay tight
    audio = SynthAudio([(10.1, 10.4), (11.1, 12.3), (12.75, 13.4)])
    edl = edl_of(span("s1", 0, 0, 5), span("s2", 1, 2, 5))
    for s in edl["spans"]:
        s.update(order=0, speaker="A", role="body", words="", verbatim=True, match_ratio=1.0, callout="", why="")
    E.tighten_edges(edl, WORDS, audio)
    tight_out = edl["spans"][0]["t_out"]
    assert tight_out == at(12.45)
    fix = R.Fix(op="trim_span", target="s1", issue=0, severity="major", t_s=0.5, args={"edge": "in", "src_t": 11.0})
    new, log = R.apply_fixes(edl, [fix], WORDS, audio)
    assert new["spans"][0]["word_from"] == 2 and new["spans"][0]["t_out"] == tight_out


def test_a_clipped_edge_moves_outward_to_the_next_gap():
    # speech runs through the out edge (12.0) to 12.25, then 0.15 s of room tone, then the next word
    audio = SynthAudio([(10.6, 12.25), (12.4, 13.4)])
    edl = edl_of(span("s1", 0, 0, 3), span("s2", 1, 2, 5))
    E.tighten_edges(edl, WORDS, audio)
    s = edl["spans"][0]
    assert 12.25 < s["t_out"] < 12.4 and s["edges"]["out"]["widened"] is True
    assert s["edges"]["out"]["db_after"] < -50 and edl["edge_pass"]["widened"] == 1
    assert E.tighten_edges(edl, WORDS, audio)["moved"] == 0  # the widened edge stays
    # a clipped start moves earlier the same way
    audio = SynthAudio([(9.4, 9.6), (9.75, 11.5)])
    edl = edl_of(span("s1", 0, 0, 3), span("s2", 1, 2, 5))
    E.tighten_edges(edl, WORDS, audio)
    assert 9.6 < edl["spans"][0]["t_in"] < 9.75 and edl["spans"][0]["edges"]["in"]["widened"] is True


def test_a_clipped_edge_never_widens_into_a_word_another_span_keeps():
    # s2 keeps w04 (12.0-12.5) and starts after a cut elsewhere in the cut: s1's clipped end may not reach into it
    audio = SynthAudio([(10.6, 12.25), (12.4, 13.4)])
    s1, s2 = span("s1", 0, 0, 3), span("s2", 0, 4, 6)
    edl = edl_of(s2, s1)  # reordered, so the two are not joined
    E.tighten_edges(edl, WORDS, audio)
    assert s1["t_out"] == 12.0 and "out" not in s1.get("edges", {})
    # nor into the words a transcript delete removed (keep_out): w04 is gone, not kept by another clip
    audio = SynthAudio([(10.6, 12.25), (12.4, 13.4)])
    edl = edl_of(span("s1", 0, 0, 3), span("s2", 1, 2, 5))
    E.tighten_edges(edl, WORDS, audio, keep_out={0: {4}})
    assert edl["spans"][0]["t_out"] == 12.0
    # no quiet stretch within reach: the edge stays
    audio = SynthAudio([(10.6, 13.0)])
    edl = edl_of(span("s1", 0, 0, 3), span("s2", 1, 2, 5))
    E.tighten_edges(edl, WORDS, audio)
    assert edl["spans"][0]["t_out"] == 12.0


def test_reset_edges_lets_today_s_rules_redo_an_old_pass():
    # an older pass widened s1's start to 10.6 (in front of the dropped word's sound); reset puts it back on the Voice
    # boundary and the pass again skips the dropped word
    audio = SynthAudio([(10.98, 11.38), (11.85, 12.8)], faint=[(11.5, 11.6)])
    s1 = span("s1", 0, 2, 5)
    s1.update(t_in=10.6, edges={"in": {"from": 11.0, "to": 10.6, "widened": True}}, edge_tuned={"in": 10.6})
    edl = edl_of(s1)
    assert E.reset_edges(edl, WORDS) == {("s1", "in")} and s1["t_in"] == 11.0
    E.tighten_edges(edl, WORDS, audio, only={("s1", "in")})
    assert s1["t_in"] == at(11.75)
    # an edge something moved after the pass (a trim, a review patch) is left alone
    s1 = span("s1", 0, 2, 5)
    s1.update(t_in=10.7, edges={"in": {"from": 11.0, "to": 10.6, "widened": True}})
    assert E.reset_edges(edl_of(s1), WORDS) == set() and s1["t_in"] == 10.7
