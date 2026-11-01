"""Full audit, 31/10 (docs/notes/full-audit_2026-10-31.md): the human ops never bring a deleted word back, a shortened
pause's record goes stale with its edge, a draft refuses a rebase whose clip ids mean other clips, a kit keeps the bed's
own choices, line breaks and jump-cut skips follow their clip, and Ask Montivo's caption, pauses and restore checks."""
import json

import pytest

from tests.test_edges import SynthAudio
from tests.test_ui import _human_run

# make_edl: s1 = turn 0 words 0-3 (10.0-11.2), s2 = turn 0 words 6-8 (11.8-12.7), s3 = turn 1 words 2-6 (30.6-32.1);
# Voice words are 0.3 s each from 10.0 s (turn 0) and 30.0 s (turn 1)
RUN_ON = [(10.0, 11.15), (11.85, 12.65), (30.6, 31.65), (31.95, 32.1)]  # w15 (31.5-31.8) starts in s3's speech
TAILS = [(10.0, 11.35), (11.85, 12.65), (30.6, 32.05)]  # s1's speech runs 0.15 s on into the unused w04


def _audio(monkeypatch, speech):
    import montivo.human as H
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: SynthAudio(speech)))


def _v(d, n):
    return json.loads((d / f"edl_v{n}.json").read_text())


def test_a_delete_after_a_split_never_widens_into_the_deleted_words(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    _audio(monkeypatch, RUN_ON)
    H.split(d, "s3", 5)  # s3 = w12-w14 | s4 = w15-w16, one run of speech across the split
    H.drop(d, "s4", from_v=2)
    s3 = next(s for s in _v(d, 3)["spans"] if s["id"] == "s3")
    assert s3["word_to"] == 4 and s3["t_out"] <= 31.52  # the old edge pass widened to ~31.7, over the deleted w15


def test_a_move_never_widens_a_clip_into_a_word_no_clip_keeps(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    _audio(monkeypatch, TAILS)
    H.reorder(d, "s1", None)  # s1 to the end: its out edge meets nothing new and is tightened again
    s1 = next(s for s in _v(d, 2)["spans"] if s["id"] == "s1")
    assert s1["word_to"] == 3 and s1["t_out"] <= 11.22  # w04 (11.2-11.5) is in no clip


def test_a_trim_leaves_no_stale_pause_for_an_extend_to_give_back(tmp_path, monkeypatch):
    import montivo.human as H
    from montivo.engine import review as R
    from tests.test_batch_d import PAUSED
    d = _human_run(tmp_path, monkeypatch)
    _audio(monkeypatch, PAUSED)
    H.pauses(d)  # s3 | s4 apart at the pause after w13
    v2 = _v(d, 2)
    assert v2["spans"][2]["pause_out"] == v2["spans"][3]["pause_in"]
    H.trim(d, "s4", "in", 5, from_v=2)  # s4 starts a word later: the clips no longer meet across that pause
    v3 = _v(d, 3)
    a, b = v3["spans"][2], v3["spans"][3]
    assert "pause_out" not in a and "pause_in" not in b and b["word_from"] == 5
    fix = R.Fix(op="extend_span", target="s3", issue=0, severity="major", t_s=4.0, args={"edge": "out", "seconds": 0.3})
    _, log = R.apply_fixes(v3, [fix], H._words(d), SynthAudio(PAUSED))
    assert "given back whole" not in log[0]["note"]


def test_prune_pauses_keeps_a_pause_whose_clips_still_meet():
    from montivo.human import prune_pauses
    rec = {"from": 31.0, "to": 31.6, "join": 31.2}
    edl = {"spans": [{"id": "s1", "t_in": 30.0, "t_out": 31.1, "pause_out": dict(rec)},
                     {"id": "s2", "t_in": 31.5, "t_out": 32.0, "pause_in": dict(rec)}]}
    assert prune_pauses(edl) == [] and edl["spans"][0]["pause_out"] == rec
    edl["spans"][0]["t_out"] = 30.6  # the edge left the pause's silence
    assert prune_pauses(edl) == ["s1 pause_out", "s2 pause_in"]
    assert "pause_out" not in edl["spans"][0] and "pause_in" not in edl["spans"][1]


def test_a_draft_does_not_rebase_onto_ids_that_mean_other_clips(tmp_path, monkeypatch):
    import montivo.human as H
    from tests.test_draft import TALK, _client, _fake_jobs, _run
    d = _run(tmp_path, monkeypatch, TALK)
    c, st = _client(tmp_path)
    c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}, "from": 1})  # makes s4
    r = c.post("/api/runs/h1/draft", json={"op": "drop", "args": {"span": "s4"}})  # and deletes it
    assert r.status_code == 200, r.text
    H.split(d, "s1", 2)  # v2, made elsewhere: its own s4 is the end of s1
    (d / "cut_v2.mp4").write_bytes(b"")
    _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 409 and "name other clips there" in r.json()["error"]
    assert not (d / "edl_v3.json").exists()


def test_rebase_conflict_names_a_clip_the_newer_version_changed():
    from montivo.server.draft import rebase_conflict, span_ids
    sp = lambda i, a, b: {"id": i, "turn_id": 0, "word_from": a, "word_to": b}  # noqa: E731
    base = {"spans": [sp("s1", 0, 3), sp("s2", 6, 8)]}
    assert span_ids({"ranges": [["s2", 6, 6]], "clips": {"s1": None}}) == {"s1", "s2"}
    assert rebase_conflict(base, {"spans": [sp("s1", 0, 3), sp("s2", 6, 8)]}, [{"op": "drop", "args": {"span": "s2"}}]) is None
    why = rebase_conflict(base, {"spans": [sp("s1", 0, 3), sp("s2", 7, 8)]}, [{"op": "drop", "args": {"span": "s2"}}])
    assert why == "s2 is not the same clip there"
    assert rebase_conflict(base, {"spans": [sp("s1", 0, 1), sp("s2", 6, 8)]}, [{"op": "drop", "args": {"span": "s2"}}]) is None


def test_a_kit_keeps_the_beds_start_fade_and_duck_edits():
    from montivo.engine.kit import with_kit
    music = {"track": "chill_lofi", "level_db": -18.0, "duck_db": -6.0, "start_s": 20.0, "fade_out_s": 4.0,
             "duck_edits": [{"a": 9.5, "b": 13.3, "duck": False}]}
    edl = {"spans": [], "music": dict(music)}
    _, out = with_kit(edl, {"music": {"track": "chill_lofi", "level_db": -18, "duck_db": -6}})
    assert out == [] and edl["music"] == music  # the same bed: nothing changes, nothing is lost
    _, out = with_kit(edl, {"music": {"track": "ukulele", "level_db": -18, "duck_db": -6}})
    m = edl["music"]
    assert m["track"] == "ukulele" and m["fade_out_s"] == 4.0 and len(m["duck_edits"]) == 1 and out[0]["op"] == "music"


def test_a_split_moves_a_line_break_to_the_piece_that_keeps_its_word(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    _audio(monkeypatch, RUN_ON)
    edl = _v(d, 1)
    edl["caption_breaks"] = [{"span": "s3", "src_t": 31.82}]  # a break at w16, in the second piece after the split
    (d / "edl_v1.json").write_text(json.dumps(edl))
    H.split(d, "s3", 5)
    assert _v(d, 2)["caption_breaks"] == [{"span": "s4", "src_t": 31.82}]


def test_a_replan_maps_jump_cut_skips_and_line_breaks_by_source_time():
    from montivo.human import carry_by_source
    prev = {"spans": [{"id": "s4", "turn_id": 0, "t_in": 10.0, "t_out": 12.0}],
            "punch_skip": ["jump_cut:s4", "emphasis:four@18.200"], "caption_breaks": [{"span": "s4", "src_t": 11.0}]}
    new = {"spans": [{"id": "s1", "turn_id": 0, "t_in": 3.0, "t_out": 5.0}, {"id": "s2", "turn_id": 0, "t_in": 10.2, "t_out": 12.4}]}
    skips, breaks = carry_by_source(prev, new)
    assert skips == ["jump_cut:s2", "emphasis:four@18.200"] and breaks == [{"span": "s2", "src_t": 11.0}]
    new["spans"].pop()  # the new plan no longer keeps those words: the clip skip and the break go
    assert carry_by_source(prev, new) == (["emphasis:four@18.200"], [])


def test_ask_refuses_pauses_with_a_selection_and_a_restore_of_no_version(tmp_path):
    from montivo import agent
    from tests.test_agent_preview import SPANS, op
    edl = {"version": 3, "spans": SPANS, "runtime_s": 7.0}
    (tmp_path / "edl_v2.json").write_text("{}")
    kept, skipped = agent.check(edl, [op("pauses")], {"clips": ["s1"]}, tmp_path)
    assert kept == [] and "not your selection" in skipped[0]
    kept, skipped = agent.check(edl, [op("restore", version=7)], None, tmp_path)
    assert kept == [] and "there is no v7" in skipped[0]
    kept, _ = agent.check(edl, [op("restore", version=2)], None, tmp_path)
    assert [o["op"] for o in kept] == ["restore"]


@pytest.mark.parametrize("made", [False, True])
def test_a_failed_apply_never_leaves_a_proposal_running(tmp_path, monkeypatch, made):
    from montivo import agent, human
    d = tmp_path / "r"
    (d / "ui" / "pending").mkdir(parents=True)
    (d / "edl_v3.json").write_text(json.dumps({"version": 3, "spans": [], "runtime_s": 1.0}))
    (d / "ui" / "pending" / "prop1.json").write_text(json.dumps({"id": "prop1", "status": "open", "from": 3, "message": "m",
                                                              "ops": [], "full_ops": [], "passes": 1}))

    def boom(*a, **k):
        if made:  # the version rendered, then its review failed
            (d / "edl_v4.json").write_text("{}")
        raise RuntimeError("review call failed")
    monkeypatch.setattr(agent, "execute", boom)
    with pytest.raises(RuntimeError):
        agent.apply(d, "prop1")
    assert json.loads((d / "ui" / "pending" / "prop1.json").read_text())["status"] == ("applied" if made else "open")
    assert human.latest_edl(d) == (4 if made else 3)


def test_a_replan_keeps_the_title_style():
    """'Card before' (one paid Muse Image card) survives a re-plan; an EDL from before the field keeps its card instead
    of the header pill build_edl writes into every new plan."""
    from montivo import human
    from montivo.engine.edl import title_style
    new = {"title_card_text": "New words", "title_style": "header"}
    human.carry_title({"title_card_text": "Raw In", "title_style": "card"}, new)
    assert new == {"title_card_text": "Raw In", "title_style": "card"}
    new = {"title_card_text": "New words", "title_style": "header"}
    human.carry_title({"title_card_text": "Raw In", "title_card": False}, new)
    assert title_style(new) == "card" and new["title_card"] is False and new["title_card_text"] == "Raw In"
    new = {"title_card_text": "New words", "title_style": "header"}
    human.carry_title({"title_style": "header"}, new)
    assert new == {"title_card_text": "New words", "title_style": "header"}


def test_dropping_the_last_clip_makes_the_one_before_it_the_payoff(tmp_path, monkeypatch):
    from montivo import human
    d = _human_run(tmp_path, monkeypatch)
    human.drop(d, "s3")
    sp = json.loads((d / "edl_v2.json").read_text())["spans"]
    assert [(s["id"], s["role"]) for s in sp] == [("s1", "hook"), ("s2", "payoff")]


def test_a_callout_keeps_its_source_second_when_its_clip_start_moves():
    """017 v13: s5 'Score 87 Pass' at 4.1 s into the clip, just before 'passes at 87'. A delete of the clip's first five
    words moves its start 3.2 s later: the shift follows so the callout still fires at the same second of the
    recording; a split that leaves that second in the second piece gives the callout to that piece."""
    from montivo import human
    prev = {"spans": [{"id": "s5", "turn_id": 5, "t_in": 97.98, "t_out": 104.38, "callout": "Score 87 Pass",
                       "callout_shift_s": 4.1, "callout_xy": [500, 300]}]}
    new = {"spans": [{**prev["spans"][0], "t_in": 101.19}]}
    fixes = human.follow_callouts(prev, new)
    assert new["spans"][0]["callout_shift_s"] == 0.89 and fixes[0]["op"] == "callout_follow"
    new = {"spans": [{**prev["spans"][0], "t_out": 101.0},
                     {"id": "s9", "turn_id": 5, "t_in": 101.0, "t_out": 104.38, "callout": ""}]}
    human.follow_callouts(prev, new)
    a, b = new["spans"]
    assert a["callout"] == "" and "callout_shift_s" not in a and "callout_xy" not in a
    assert b["callout"] == "Score 87 Pass" and b["callout_xy"] == [500, 300] and b["callout_shift_s"] == 1.08
    # nothing moved: nothing changes
    same = {"spans": [dict(prev["spans"][0])]}
    assert human.follow_callouts(prev, same) == [] and same == prev
