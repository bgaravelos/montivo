"""Bug audit, 1/11 (docs/notes/bug-audit_2026-11-01.md): a split or a word delete inside a retexted caption line
divides its text between the pieces (no word twice, a deleted word never on screen), a punch-in removal on an EDL
not laid out since its last edit, a clip moved away and back joins its take again, and drop / reorder keep
runtime_s in step with the edges they move."""
import pytest

from montivo import human
from montivo.engine.render import merge_segments
from tests.test_edges import SynthAudio
from tests.test_review import make_edl

# make_edl: s1 = turn 0 words 0-3 (10.0-11.2), s2 = turn 0 words 6-8 (11.8-12.7), s3 = turn 1 words 2-6 (30.6-32.1);
# Voice words are 0.3 s each from 10.0 s (turn 0) and 30.0 s (turn 1), named w<turn><index>


def _with_override(**o):
    edl, words = make_edl()
    edl["caption_overrides"] = [{"span": "s3", "src_t": 30.61, "why": "human", **o}]
    return edl, words


def _texts(edl):
    return [(o["span"], o["text"]) for o in edl["caption_overrides"]]


def test_a_split_inside_a_retexted_line_divides_its_text_between_the_pieces():
    edl, words = _with_override(text="W12 W13 W14 W15", until=31.8)
    new, _ = human.step(edl, words, None, {"op": "split", "args": {"span": "s3", "word": 4}})
    s4 = new["spans"][3]["id"]
    assert _texts(new) == [("s3", "W12 W13"), (s4, "W14 W15")]  # was s3 "W12 W13 W14 W15" and s4 captioned w14 again
    a, b = new["caption_overrides"]
    assert (a["src_t"], a["until"], b["src_t"], b["until"]) == (30.61, 31.2, 31.21, 31.8)


def test_a_split_divides_an_override_from_before_until_by_its_text():
    # 015 v5's "Four Meta models do all" (Voice heard "meta meta"): no until, its extent is where the text still aligns
    edl, words = _with_override(text="Four W13 W14 W15")
    new, _ = human.step(edl, words, None, {"op": "split", "args": {"span": "s3", "word": 4}})
    assert _texts(new) == [("s3", "Four W13"), (new["spans"][3]["id"], "W14 W15")]


def test_a_word_deleted_inside_a_retexted_line_leaves_its_text():
    # 017 v15: "EDITOR I TRIED STOPS", the "I" deleted, used to stay on screen with "tried stops" captioned again
    edl, words = _with_override(text="W12 W13 W14 W15", until=31.8)
    new, _ = human.step(edl, words, None, {"op": "cut", "args": {"ranges": [["s3", 4, 4]]}})
    assert _texts(new) == [("s3", "W12 W13"), (new["spans"][3]["id"], "W15")]
    assert not any("W14" in t for _, t in _texts(new))


def test_a_trim_through_a_retexted_line_keeps_the_text_of_the_words_left():
    edl, words = _with_override(text="W12 W13 W14 W15", until=31.8)
    new, _ = human.step(edl, words, None, {"op": "cut", "args": {"ranges": [["s3", 2, 2]]}})
    assert new["spans"][2]["word_from"] == 3 and _texts(new) == [("s3", "W13 W14 W15")]


def test_a_retext_that_cannot_be_divided_word_by_word_is_dropped_and_one_inside_a_piece_stays():
    edl, words = _with_override(text="W12 merged W15", until=31.8)  # "merged" stands for w13 w14 across the split
    keep = {"span": "s1", "src_t": 10.01, "text": "Hello W01", "why": "human", "until": 10.6}
    edl["caption_overrides"].append(keep)
    new, _ = human.step(edl, words, None, {"op": "split", "args": {"span": "s3", "word": 4}})
    assert new["caption_overrides"] == [keep]


def test_a_second_punch_in_removal_on_an_unlaid_edl_is_checked_against_its_clips():
    edl, words = make_edl()
    edl.update(punch=True, punch_ins=[{"key": "jump_cut:s2", "span": "s2", "t_in": 1.0, "zoom": 1.06}])
    first, _ = human.step(edl, words, None, {"op": "punch", "args": {"skip": "jump_cut:s2"}})
    assert first["punch_ins"] == []  # cleared: the render plans them again
    second, rec = human.step(first, words, None, {"op": "punch", "args": {"skip": "emphasis:w14@31.200"}})
    assert second["punch_skip"] == ["jump_cut:s2", "emphasis:w14@31.200"] and "w14" in rec.fixes[0]["note"]
    third, _ = human.step(second, words, None, {"op": "punch", "args": {"skip": "jump_cut:s3"}})
    assert third["punch_skip"][-1] == "jump_cut:s3"
    for bad in ("jump_cut:s9", "jump_cut:s1", "emphasis:w05@11.500", "jump_cut:s2"):  # no clip / the first / a gap / done
        with pytest.raises(SystemExit):
            human.step(second, words, None, {"op": "punch", "args": {"skip": bad}})


def _take():
    """s1 = turn 0 words 0-3 and s2 = words 4-7, one take met on 11.2 s; the speech pauses 11.05-11.35 between them."""
    edl, words = make_edl()
    s1, s2, s3 = edl["spans"]
    s2.update(word_from=4, word_to=7, t_in=11.2, t_out=12.4, contiguous_with_previous=True)
    for s in edl["spans"]:
        human.refresh_span(s, words)
    return edl, words, SynthAudio([(10.0, 11.05), (11.35, 12.4), (30.6, 32.1)])


def test_a_clip_moved_away_and_back_joins_its_take_again():
    edl, words, audio = _take()
    away, _ = human.step(edl, words, audio, {"op": "reorder", "args": {"span": "s2", "before": None}})
    back, rec = human.step(away, words, audio, {"op": "reorder", "args": {"span": "s2", "before": "s3"}})
    s1, s2 = back["spans"][:2]
    assert [s["id"] for s in back["spans"]] == ["s1", "s2", "s3"]
    assert s1["t_out"] == s2["t_in"] and 11.0 < s2["t_in"] < 11.4  # met on the pause, not two tightened edges
    assert merge_segments(back["spans"])[0].span_ids == ["s1", "s2"]
    assert "joined to the clip it continues" in rec.fixes[0]["note"]
    assert back["runtime_s"] == round(sum(s["duration"] for s in back["spans"]), 2)


def test_drop_and_reorder_keep_runtime_in_step_with_the_edges_they_move():
    edl, words = make_edl()
    audio = SynthAudio([(10.0, 10.8), (11.8, 12.7), (30.6, 32.1)])  # s1's last word is silence: its out edge moves
    dropped, _ = human.step(edl, words, audio, {"op": "drop", "args": {"span": "s2"}})
    assert dropped["spans"][0]["t_out"] < 11.1
    assert dropped["runtime_s"] == round(sum(s["duration"] for s in dropped["spans"]), 2)
    moved, _ = human.step(edl, words, audio, {"op": "reorder", "args": {"span": "s1", "before": None}})
    assert moved["runtime_s"] == round(sum(s["duration"] for s in moved["spans"]), 2)
