"""Offline tests for the editor UI: the human trim op (a timeline drag lands on its word through apply_fixes), the
waveform peaks and the replay script."""
import pytest

from montivo.engine import review as R
from montivo.human import trim_fix
from tests.test_review import make_edl


@pytest.mark.parametrize("edge,word,want", [
    ("out", 4, (2, 4)),  # s3 = turn 1 words 2-6: trim the end to word 4
    ("out", 3, (2, 3)),  # the shortest a clip may get: two words
    ("in", 5, (5, 6)),
    ("out", 8, (2, 8)),  # extend the end by two words
    ("in", 0, (0, 6)),  # extend the start to the first word of the turn
])
def test_trim_lands_on_the_word(edge, word, want):
    edl, words = make_edl()
    fix = trim_fix(edl, words, "s3", edge, word)
    assert fix.op == ("extend_span" if (edge == "out" and word > 6) or (edge == "in" and word < 2) else "trim_span")
    new, log = R.apply_fixes(edl, [fix], words)
    s3 = next(s for s in new["spans"] if s["id"] == "s3")
    assert (s3["word_from"], s3["word_to"]) == want
    assert s3["duration"] == pytest.approx(0.3 * (want[1] - want[0] + 1))


def test_trim_guards():
    edl, words = make_edl()
    with pytest.raises(SystemExit):
        trim_fix(edl, words, "s3", "out", 2)  # one word left
    with pytest.raises(SystemExit):
        trim_fix(edl, words, "s3", "out", 6)  # already there
    with pytest.raises(SystemExit):
        trim_fix(edl, words, "s2", "in", 3)  # words 3 belongs to s1
    with pytest.raises(SystemExit):
        trim_fix(edl, words, "s9", "in", 3)


def test_peaks_from_pcm():
    from array import array

    from montivo.server.media import peaks_from_pcm
    pcm = array("h", [0, 1000, -16384, 0] * 2 + [0] * 8 + [32767, 0, 0, 0] * 2).tobytes()
    assert peaks_from_pcm(pcm, sr=8, rate=1) == [0.5, 0.0, 1.0]  # 8 samples per peak


def test_replay_keeps_a_loop_split_by_its_renders():
    from montivo.server.runs import parse_log, replay_script
    log = "\n".join([
        "[   0.0s] montivo run: clip.mp4 -> runs/x", "[   5.0s] render: runs/x/cut_v1.mp4 (40 s)", "[   6.0s] done: $0.40",
        "[   0.0s] edges: 9 edges moved, 7 s removed", "[   0.1s] edges: 2 edges moved, 1 s removed",
        "[   0.0s] montivo render: runs/x/edl_v1.json -> runs/x/cut_v1.mp4", "[  20.0s] render: runs/x/cut_v1.mp4 (38 s)",
        "[   9.8s] review: review_v1 overall 76 pass False (3 issues), $0.03", "[  10.0s] patch v1 -> v2: drop_title",
        "[  10.0s] montivo render: runs/x/edl_v2.json -> runs/x/cut_v2.mp4", "[  30.0s] render: runs/x/cut_v2.mp4 (37 s)",
        "[  40.0s] review: review_v2 overall 100 pass True (0 issues), $0.03", "[  40.0s] review loop: 2 reviews, stop: pass",
        "[   0.0s] montivo render: runs/x/edl_v2_polish.json -> runs/x/cut_v2_polish.mp4",
    ])
    msgs = [ln["msg"] for ln in replay_script(parse_log(log))]
    assert any(m.startswith("review: review_v1 overall") for m in msgs)
    assert msgs[-1].startswith("review loop:")
    assert not any("polish" in m for m in msgs)
