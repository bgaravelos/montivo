"""Offline tests for the review's deterministic checks and pass gate: kept edge words heard, head in frame, the caption
diff for every reviewer, the gate's reasons and the patches it forces, the loop's ranking of gated versions."""
import json

from montivo.engine import review as R
from test_review import issue, make_edl, voice_words


def heard_from(kept, drop=(), swap=None):
    """The kept words as Voice would hear them in the cut: `drop` words missing, `swap` {word: heard instead}."""
    swap = swap or {}
    return [{"w": swap.get(k["w"], k["w"]), "t_start": k["t_start"], "t_end": k["t_end"]} for k in kept if k["w"] not in drop]


def test_kept_words_tag_the_words_on_both_sides_of_every_cut():
    edl, words = make_edl(title=False)  # s1 w00-w03 + s2 w06-w08 (one turn, a join), s3 w12-w16 in turn 1
    kept = R.kept_words(edl, words)
    assert [k["w"] for k in kept][:4] == ["w00", "w01", "w02", "w03"]
    edge = {k["w"]: (k["edge"], k["cut_t"]) for k in kept}
    assert edge["w00"] == ("in", 0.0) and edge["w01"] == ("in", 0.0)
    assert edge["w02"] == ("out", 1.2) and edge["w03"] == ("out", 1.2) and edge["w06"] == ("in", 1.2)
    assert edge["w07"] == ("out", 2.1)  # a 3-word span: the last two face the next cut, the first the one before
    assert edge["w15"] == ("out", 3.6) and edge["w16"] == ("out", 3.6) and edge["w14"][0] is None
    assert kept[4]["t_start"] == 1.2  # w06 starts where s2 starts in the cut


def test_a_clipped_edge_word_is_reported_only_where_speech_runs_at_the_edge():
    edl, words = make_edl(title=False)
    kept = R.kept_words(edl, words)
    speech = lambda cut_t, edge: (-42.7, True)  # noqa: E731  (015: the cut inside "Montivo", over the clip's -50.4 dB)
    silent = lambda cut_t, edge: (-52.5, False)  # noqa: E731
    out = R.edge_words_unheard(kept, heard_from(kept, drop={"w03"}), speech)
    assert [(m["w"], m["edge"], m["cut_t"], m["heard"]) for m in out["unheard"]] == [("w03", "out", 1.2, "")]
    assert out["cuts"] == 4 and out["edge_words"] == 11
    # the same word missing at an edge in silence is the speech model's slip, not a clipped word
    out = R.edge_words_unheard(kept, heard_from(kept, drop={"w03"}), silent)
    assert out["unheard"] == [] and [m["w"] for m in out["quiet"]] == ["w03"]
    # a word one further from the cut, missing while the word nearer the cut is heard, was not clipped (014 "smoked")
    assert R.edge_words_unheard(kept, heard_from(kept, drop={"w02"}), speech)["unheard"] == []
    # heard as a shorter, different word: clipped; heard longer and starting alike, or merged: the whole word is there
    out = R.edge_words_unheard(kept, heard_from(kept, swap={"w03": "x"}), speech)
    assert [(m["w"], m["heard"]) for m in out["unheard"]] == [("w03", "x")]
    assert R._same_word("montivo", "montevideo") and R._same_word("news", "newspark") and not R._same_word("montivo", "mont")


def test_head_frame_finds_where_the_followed_head_leaves_the_crop(tmp_path):
    edl, _ = make_edl(title=False)
    # one head, 160 px wide, standing still near x 960 of a 1920 source for the whole of both turns (track at 5 fps)
    recs = [{"t": round(t / 5, 2), "box": [440, 100, 520, 200]} for t in range(0, 5 * 40)]
    (tmp_path / "track" / "head").mkdir(parents=True)
    (tmp_path / "track" / "head" / "tracks.json").write_text(json.dumps(
        {"track_fps": 5.0, "source": {"width": 960, "height": 540}, "objects": {"0": recs}}))
    fr = R.head_frame_facts(tmp_path, edl)
    assert fr["concept"] == "head" and fr["max_out"] == 0.0 and fr["out_of_frame"] == []
    # the review's planted framing fault: s3's segment pushed 420 px right, the head leaves on the left
    edl["spans"][2]["reframe_bias_px"] = 420
    fr = R.head_frame_facts(tmp_path, edl)
    [r] = fr["out_of_frame"]
    assert r["side"] == "left" and r["max_out"] == 1.0 and r["t_in"] == 2.1 and r["t_out"] >= 3.5
    assert r["shift_px"] == -420  # head centre 960, crop centre 1076 + 304: the fix takes the bias back out
    gate = R.gate_check({"over_face": []}, None, None, fr)
    assert gate["blocked"] and gate["issues"][0]["fix_op"] == "reframe_bias"
    assert gate["issues"][0]["params"]["direction"] == "left" and gate["issues"][0]["params"]["px"] == abs(r["shift_px"])
    assert R.head_frame_facts(tmp_path / "none", edl) is None  # no head track, no fact


def test_gate_reasons_and_the_issues_it_hands_the_patcher():
    edges = [{"t": 1.2, "end_db": -25.8, "start_db": -50.0, "end": False, "loud": True, "loud_db": -34.0},
             {"t": 2.1, "end_db": -50.0, "start_db": -51.0, "end": False, "loud": False, "loud_db": -34.0}]
    caps = {"over_face": [{"t_in": 3.0, "t_out": 4.0, "text": "a b", "placement": "forced"},
                          {"t_in": 5.0, "t_out": 6.0, "text": "c d", "placement": "over_head"}]}
    ew = {"unheard": [{"w": "Montivo", "cut_t": 2.1, "edge": "out", "heard": "", "span": "s2"}]}
    head = {"out_of_frame": [{"t_in": 7.0, "t_out": 7.3, "seconds": 0.3, "side": "left", "max_out": 0.5, "shift_px": -80}]}
    g = R.gate_check(caps, edges, ew, head)
    assert g["blocked"] and len(g["reasons"]) == 3  # the 0.3 s head stretch and the unfixable over_head line do not count
    assert [(i["check"], i["fix_op"], i["params"]["edge"], i["severity"]) for i in g["issues"]] == [
        ("loud_edge", "move_cut", "out", "major"), ("edge_word", "move_cut", "out", "major"),
        ("caption_face", "caption_fix", "none", "major")]
    assert not R.gate_check({"over_face": []}, edges[1:], {"unheard": []}, {"out_of_frame": []})["blocked"]


def test_gate_issues_become_fixes_the_reviewer_missed_and_bias_moves_the_whole_segment():
    edl, words = make_edl(title=False)
    edl["spans"][0]["t_out"] = 10.95  # s1 cut halfway into w03 (ends 11.2)
    gate = {"issues": [dict(issue(1.2, "move_cut", edge="out"), check="edge_word", criterion="no_cut_mid_word"),
                       dict(issue(2.5, "reframe_bias", direction="right", px=300), check="head_frame")]}
    review = {"issues": [issue(1.15, "move_cut", sev="minor")]}  # the reviewer heard it, but only as minor
    fixes, skipped = R.plan_fixes(review, edl, words, gate=gate)
    assert [(f.op, f.target, f.check) for f in fixes] == [("move_cut", "s1", "edge_word"),
                                                          ("reframe_bias", "s3", "head_frame")]
    assert fixes[0].args["mode"] == "snap" and skipped == [{"issue": 0, "reason": "severity minor"}]
    assert fixes[0].issue == 1  # gate issues are numbered after the reviewer's
    new, _ = R.apply_fixes(edl, fixes, words)
    assert new["spans"][0]["t_out"] == 11.2 and new["spans"][2]["reframe_bias_px"] == 300
    edl, words = make_edl(title=False)
    edl["spans"][1]["word_from"], edl["spans"][1]["t_in"] = 4, 11.2  # s1 and s2 render as one segment ...
    fix, _ = R.resolve(issue(0.5, "reframe_bias", direction="right", px=300), 0, edl, words, R.cut_map(edl))
    new, log = R.apply_fixes(edl, [fix], words)
    assert [s.get("reframe_bias_px") for s in new["spans"]] == [300, 300, None]  # ... and both move, not a third
    assert "2 spans of the segment" in log[0]["note"]


def test_facts_text_gives_every_reviewer_the_checks_and_spark13_only_the_caption_diff():
    caps = {"lines": 1, "over_face": [], "min_clearance_px": 40, "min_clearance_at": 0.1}
    timeline = [{"t_in": 0.1, "t_out": 1.0, "text": "Hi. I'm Billy."}]
    heard = [{"w": "Hi.", "t_start": 0.1, "t_end": 0.4}, {"w": "I'm", "t_start": 0.45, "t_end": 0.6},
             {"w": "Bill.", "t_start": 0.6, "t_end": 1.0}]
    ew = {"edge_words": 12, "cuts": 4, "unheard": [{"w": "Montivo", "cut_t": 17.42, "edge": "out", "heard": ""}]}
    head = {"max_out": 1.0, "out_of_frame": [{"t_in": 22.57, "t_out": 27.97, "seconds": 5.4, "max_out": 1.0,
                                              "side": "right"}]}
    text = R.facts_text([], caps, 40.0, timeline=timeline, edge_words=ew, head=head, diff_heard=heard)
    lines = text.splitlines()
    assert lines[2].startswith("3. The words this cut keeps on both sides of every cut (12 words at 4 cuts)")
    assert '"Montivo" (end of the passage before the cut at 17.42 s) not heard' in lines[2]
    assert lines[3].startswith("4. The speaker's head") and "22.57-27.97 s (5.4 s, up to 100% outside, on the right)" in lines[3]
    assert lines[-1].startswith("6. Where the caption words and the words a speech model hears in the cut's own audio")
    assert '0.10 s caption "billy" / heard "bill"' in lines[-1] and "Words heard in this cut" not in text
    clean = R.facts_text([], caps, 40.0, edge_words=dict(ew, unheard=[]), head=dict(head, max_out=0.12, out_of_frame=[]))
    assert "every one of them is heard" in clean and "inside the frame throughout (at most 12% of its width outside)" in clean


def test_the_loop_does_not_stop_on_a_gated_pass_and_prefers_a_version_the_gate_lets_through(tmp_path):
    edl, _ = make_edl()
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "voice.json").write_text(json.dumps({"words": voice_words()}))
    (tmp_path / "edl_v1.json").write_text(json.dumps(edl))

    def render_fn(ep, name):
        e = json.loads(ep.read_text())
        e["render"] = {"title": None, "duration_s": e["runtime_s"], "output": f"{tmp_path}/{name}"}
        ep.write_text(json.dumps(e))
        (tmp_path / name).write_bytes(b"")

    def review_fn(e, cut, n):
        t = R.cut_map(e)[-1].cut_in + 0.1
        gated = n == 1
        gate = {"blocked": gated, "reasons": ["head out of frame"] if gated else [],
                "issues": [dict(issue(t, "reframe_bias", direction="left", px=200), check="head_frame")] if gated else []}
        return {"name": f"review_v{n}", "cost_usd": 0.03, "pass_gate": gate,
                "review": {"pass": not gated, "reviewer_pass": True, "overall": 92 if gated else 85, "scores": {},
                           "issues": []}}

    out = R.review_loop(tmp_path, passes=3, log=lambda m: None, render_fn=render_fn, review_fn=review_fn,
                        ledger=R.Ledger(tmp_path / "ledger.json"))
    assert out["stop"] == "pass" and out["reviews"] == 2 and out["best"]["version"] == 2
    p1 = out["passes"][0]
    assert p1["reviewer_pass"] and not p1["pass"] and p1["gate"] == ["head out of frame"]
    assert p1["fixes"][0]["check"] == "head_frame" and p1["fixes"][0]["args"]["px"] == 200
    v2 = json.loads((tmp_path / "edl_v2.json").read_text())
    assert v2["spans"][2]["reframe_bias_px"] == -200


def test_an_open_reviewer_major_holds_the_pass_but_brief_covered_does_not():
    rv = {"pass": True, "reviewer_pass": True, "overall": 88,
          "issues": [dict(issue(3.6, "none"), criterion="brief_covered"),
                     dict(issue(22.1, "caption_fix"), criterion="captions_match_speech"),
                     dict(issue(9.0, "move_cut", sev="minor"), criterion="no_cut_mid_word")]}
    g = R.close_gate(rv, {"blocked": False, "reasons": [], "issues": []})
    assert not rv["pass"] and rv["reviewer_pass"] and g["blocked"] and g["checks"] == []
    assert g["reasons"] == ["reviewer's major still open: captions_match_speech at 22.10 s"]
    assert g["open_majors"] == [{"t_s": 22.1, "criterion": "captions_match_speech", "severity": "major"}]
    assert R.close_gate(rv, g) == g  # idempotent: the bench re-scores stored reviews with it
    only_brief = {"pass": True, "overall": 96, "issues": [dict(issue(33.8, "none"), criterion="brief_covered")]}
    g = R.close_gate(only_brief, {"blocked": False, "reasons": [], "issues": []})
    assert only_brief["pass"] and only_brief["reviewer_pass"] and not g["blocked"]
    measured = {"pass": True, "overall": 90, "issues": []}  # a gate written before the rule: its reasons are checks
    g = R.close_gate(measured, {"blocked": True, "reasons": ["head out of frame 1.00-2.00 s"], "issues": []})
    assert not measured["pass"] and g["checks"] == g["reasons"] == ["head out of frame 1.00-2.00 s"]


def test_a_caption_major_on_a_line_a_person_checked_does_not_hold_the_pass():
    # run 015 v5 (28/10): 82, the reviewer's pass held by "Four" at 17.15 s, a word Billy confirmed
    rv = {"pass": True, "overall": 82, "issues": [dict(issue(17.15, "caption_fix"), criterion="captions_match_speech"),
                                                  dict(issue(33.0, "none"), criterion="brief_covered")]}
    g = R.close_gate(rv, {"blocked": False, "reasons": [], "issues": [], "checked_captions": [[17.153, 18.833]]})
    assert rv["pass"] and not g["blocked"] and g["open_majors"] == []
    rv = {"pass": True, "overall": 82, "issues": [dict(issue(22.2, "caption_fix"), criterion="captions_match_speech")]}
    g = R.close_gate(rv, {"blocked": False, "reasons": [], "issues": [], "checked_captions": [[17.153, 18.833]]})
    assert not rv["pass"] and g["reasons"] == ["reviewer's major still open: captions_match_speech at 22.20 s"]


def test_the_loop_ranks_by_measured_checks_not_by_open_reviewer_majors():
    a = {"overall": 90, "gate": ["reviewer's major still open: pacing at 3.00 s"], "checks": []}
    b = {"overall": 84, "gate": [], "checks": []}
    c = {"overall": 95, "gate": ["head out of frame"]}  # old loop.json: gate reasons were all checks
    assert max([a, b, c], key=R.rank) is a and R.rank(c) < R.rank(b)
    passed = {"overall": 80, "pass": True, "gate": [], "checks": []}  # a pass outranks every version that did not pass
    assert max([a, b, c, passed], key=R.rank) is passed
