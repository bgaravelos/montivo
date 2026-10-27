"""Offline tests for the review patcher: cut map, fix resolution, EDL patches, facts and the loop guards."""
import json

from montivo.engine import review as R
from montivo.engine.captions import CWord, override_for, retext
from montivo.engine.reframe import SegmentPath
from montivo.engine.render import apply_bias

FPS = 30.0


def voice_words():
    """Turn 0: 10 words of 0.3 s from 10.0 s; turn 1: 10 words of 0.3 s from 30.0 s."""
    out = []
    for tid, t0 in ((0, 10.0), (1, 30.0)):
        for i in range(10):
            out.append({"w": f"w{tid}{i}", "t_start": round(t0 + 0.3 * i, 3), "t_end": round(t0 + 0.3 * (i + 1), 3),
                        "turn_id": tid, "i": i})
    return out


def span(sid, tid, a, b, role="body"):
    t0 = 10.0 if tid == 0 else 30.0
    return {"id": sid, "order": 0, "turn_id": tid, "speaker": "A", "role": role, "words": "", "t_in": round(t0 + 0.3 * a, 3),
            "t_out": round(t0 + 0.3 * (b + 1), 3), "duration": 0.0, "verbatim": True, "match_ratio": 1.0, "word_from": a,
            "word_to": b, "callout": "", "why": "", "contiguous_with_previous": False}


def make_edl(title=True):
    """s1 = turn 0 words 0-3 (1.2 s), s2 = turn 0 words 6-8, s3 = turn 1 words 2-6; title 1.5 s in front."""
    edl = {"version": 1, "brief": "b", "source": {"fps": FPS, "width": 1920, "height": 1080}, "runtime_s": 0,
           "spans": [span("s1", 0, 0, 3, "hook"), span("s2", 0, 6, 8), span("s3", 1, 2, 6)],
           "captions": [], "callouts": [], "caption_overrides": []}
    if title:
        edl["render"] = {"title": {"seconds": 1.5}, "duration_s": 6.3}
    words = R.Words(voice_words())
    for s in edl["spans"]:
        R.refresh_span(s, words)
    return edl, words


def issue(t, op, sev="major", **params):
    p = {"edge": "none", "seconds": 0, "target_t_s": -1, "text": "", "direction": "none"}
    p.update(params)
    return {"t_s": t, "t_end_s": t, "criterion": "pacing", "severity": sev, "what": "x", "fix_op": op, "params": p,
            "fix_args": ""}


def test_cut_map_puts_title_and_segments_in_cut_time():
    edl, _ = make_edl()
    cm = R.cut_map(edl)
    assert [c.span for c in cm] == ["s1", "s2", "s3"]
    assert cm[0].cut_in == 1.5 and cm[0].cut_out == 2.7
    assert cm[1].cut_in == 2.7 and abs(cm[1].cut_out - 3.6) < 1e-6
    assert cm[2].segment == 2 and abs(cm[2].cut_in - 3.6) < 1e-6
    points = [round(c[0], 2) for c in R.cuts(cm)]
    assert points == [1.5, 2.7, 3.6, 5.1]
    assert R.span_at(cm, 3.0).span == "s2" and R.span_at(cm, 99).span == "s3"


def test_move_cut_snaps_a_mid_word_edge_back_onto_its_word():
    edl, words = make_edl()
    edl["spans"][1]["t_out"] = 12.55  # inside w08 (12.4-12.7): a cut mid-word
    fix, why = R.resolve(issue(3.5, "move_cut"), 0, edl, words, R.cut_map(edl))
    assert why == "" and (fix.op, fix.target, fix.args["edge"], fix.args["mode"]) == ("move_cut", "s2", "out", "snap")
    new, log = R.apply_fixes(edl, [fix], words)
    assert new["spans"][1]["t_out"] == 12.7 and new["version"] == 2 and "snapped" in log[0]["note"]


def test_move_cut_by_words_trims_a_weak_opening():
    edl, words = make_edl(title=False)
    fix, _ = R.resolve(issue(0.1, "move_cut", edge="in", seconds=0.5), 0, edl, words, R.cut_map(edl))
    assert (fix.target, fix.args["mode"]) == ("s1", "words")
    new, _ = R.apply_fixes(edl, [fix], words)
    s1 = new["spans"][0]
    assert s1["word_from"] == 2 and s1["t_in"] == 10.6 and s1["words"] == "w02 w03"


class FakeAudio(R.Audio):
    """Loud everywhere except a quiet gap at 11.06-11.12 s of source time."""

    def __init__(self):
        import array
        import math
        self.rate = 8000
        self.pcm = array.array("h", [int(8000 * math.sin(k / 3)) for k in range(int(40 * self.rate))])
        for k in range(int(11.06 * self.rate), int(11.12 * self.rate)):
            self.pcm[k] = 0


def test_move_cut_on_a_boundary_moves_to_the_quietest_point_once():
    edl, words = make_edl()
    fix, why = R.resolve(issue(2.8, "move_cut", edge="out"), 0, edl, words, R.cut_map(edl))
    assert fix is None and "no audio.wav" in why
    audio = FakeAudio()
    fix, _ = R.resolve(issue(2.8, "move_cut", edge="out"), 0, edl, words, R.cut_map(edl), audio)
    assert (fix.target, fix.args["mode"]) == ("s1", "valley") and abs(fix.args["to"] - 11.08) < 0.021
    new, log = R.apply_fixes(edl, [fix], words)
    assert new["spans"][0]["t_out"] == fix.args["to"] and "quietest" in log[0]["note"]
    assert R.on_boundary(new["spans"][0], words, "out")  # a tuned edge is not snapped back
    again, why = R.resolve(issue(2.6, "move_cut", edge="out"), 0, new, words, R.cut_map(new), audio)
    assert again is None and "already at the quietest point" in why


def test_extend_span_stops_at_words_another_span_keeps():
    edl, words = make_edl()
    fix, _ = R.resolve(issue(2.0, "extend_span", edge="out", seconds=3.0), 0, edl, words, R.cut_map(edl))
    new, log = R.apply_fixes(edl, [fix], words)
    assert new["spans"][0]["word_to"] == 5  # w04 and w05 added, w06 belongs to s2
    assert new["spans"][1]["contiguous_with_previous"] is True


def test_title_issue_drops_the_card_and_render_fields_are_cleared():
    edl, words = make_edl()
    fix, _ = R.resolve(issue(0.4, "swap_hook", sev="blocker"), 0, edl, words, R.cut_map(edl))
    assert fix.op == "drop_title"
    fix2, _ = R.resolve(issue(0.0, "swap_hook", target_t_s=2.0), 0, edl, words, R.cut_map(edl))
    assert fix2.op == "drop_title"  # the hook already opens: the card in front is what delays it
    fix3, _ = R.resolve(issue(0.0, "swap_hook", target_t_s=4.0), 0, edl, words, R.cut_map(edl))
    assert fix3.op == "drop_title"  # another line wanted first: the card still goes first (run 013 kept it twice)
    new, _ = R.apply_fixes(edl, [fix], words)
    assert new["title_card"] is False and "render" not in new and new["captions"] == []


def test_swap_hook_reorder_and_drop():
    edl, words = make_edl()
    cm = R.cut_map(edl)
    swap, _ = R.resolve(issue(1.6, "swap_hook", target_t_s=4.0), 0, edl, words, cm)
    assert swap.target == "s3"
    new, _ = R.apply_fixes(edl, [swap], words)
    assert [s["id"] for s in new["spans"]] == ["s3", "s1", "s2"]
    assert new["spans"][0]["role"] == "hook" and new["spans"][1]["role"] == "body"
    reo, _ = R.resolve(issue(4.0, "reorder", target_t_s=2.0), 1, edl, words, cm)
    new, _ = R.apply_fixes(edl, [reo], words)
    assert [s["id"] for s in new["spans"]] == ["s3", "s1", "s2"]
    drop, _ = R.resolve(issue(3.0, "drop_span"), 2, edl, words, cm)
    new, _ = R.apply_fixes(edl, [drop], words)
    assert [s["id"] for s in new["spans"]] == ["s1", "s3"]
    assert new["runtime_s"] == round(1.2 + 1.5, 2)


def caption(t_in, t_out, spans, words_cut, box, head):
    return {"id": "c1", "spans": spans, "text": " ".join(w for w, _, _ in words_cut), "t_in": t_in, "t_out": t_out,
            "words": [{"w": w, "t_in": a, "t_out": b} for w, a, b in words_cut], "box": box, "head_box": head}


def test_caption_fix_removes_a_forced_position_then_moves_or_retexts():
    edl, words = make_edl()
    head = [300, 100, 800, 900]
    edl["captions"] = [caption(4.0, 4.6, ["s3"], [("w13", 4.0, 4.3), ("w14", 4.3, 4.6)], [400, 500, 300, 100], head)]
    edl["caption_overrides"] = [{"span": "s3", "src_t": 31.01, "top": 500}]
    cm = R.cut_map(edl)
    fix, _ = R.resolve(issue(4.2, "caption_fix"), 0, edl, words, cm)
    assert fix.op == "caption_unforce"
    new, _ = R.apply_fixes(edl, [fix], words)
    assert new["caption_overrides"] == []
    edl["caption_overrides"] = []
    fix, _ = R.resolve(issue(4.2, "caption_fix"), 0, edl, words, cm)
    assert fix.op == "caption_move" and fix.args["top"] == 900 + R.HEAD_MARGIN + 1
    fix, _ = R.resolve(issue(4.2, "caption_fix", text="w13 w15"), 0, edl, words, cm)
    assert fix.op == "caption_text"
    fix, why = R.resolve(issue(4.2, "caption_fix", text="a whole other sentence nobody said here"), 0, edl, words, cm)
    assert fix is None and "does not look like" in why


def test_plan_fixes_skips_minor_and_takes_one_fix_per_target():
    edl, words = make_edl()
    rv = {"issues": [issue(3.0, "drop_span", sev="minor"), issue(3.1, "drop_span"), issue(3.2, "extend_span"),
                     issue(2.0, "reframe_bias", sev="blocker", direction="left"), issue(5.0, "none")]}
    fixes, skipped = R.plan_fixes(rv, edl, words)
    assert [(f.op, f.target) for f in fixes] == [("reframe_bias", "s1"), ("drop_span", "s2")]
    reasons = {s["issue"]: s["reason"] for s in skipped}
    assert reasons[0] == "severity minor" and "already fixed" in reasons[2] and reasons[4] == "fix_op none"
    new, _ = R.apply_fixes(edl, fixes, words)
    assert new["spans"][0]["reframe_bias_px"] == -R.BIAS_PX


def test_parse_silences_and_caption_facts():
    err = ("[silencedetect @ 0x1] silence_start: -0.002\n[silencedetect @ 0x1] silence_end: 1.52 | silence_duration: 1.52\n"
           "[silencedetect @ 0x1] silence_start: 20.1\n")
    assert R.parse_silences(err, duration=21.0) == [(0.0, 1.52), (20.1, 21.0)]
    edl = {"captions": [caption(1.0, 2.0, ["s1"], [("a", 1, 2)], [400, 1300, 200, 100], [300, 100, 800, 900]),
                        caption(3.0, 4.0, ["s1"], [("b", 3, 4)], [400, 600, 200, 100], [300, 100, 800, 900])]}
    f = R.caption_face_facts(edl)
    assert len(f["over_face"]) == 1 and f["over_face"][0]["overlap_px"] == 100 and f["min_clearance_px"] == 400
    text = R.facts_text([(0.0, 1.52)], f, 10.0)
    assert "0.00-1.52 s" in text and "1 overlap the head box" in text and '"b"' in text


def test_caption_timeline_fact():
    # runs/012-open cut_v3: "Hi." up 0.03-0.43 s fell between the reviewer's one-per-second frames
    edl = {"captions": [caption(0.03, 0.43, ["s1"], [("Hi.", 0.03, 0.18)], [470, 1300, 140, 100], None),
                        caption(0.43, 1.14, ["s1"], [("I'm", 0.43, 0.9), ("Billy.", 0.9, 1.14)], [400, 1300, 280, 100], None)]}
    tl = R.caption_timeline(edl)
    assert tl == [{"t_in": 0.03, "t_out": 0.43, "text": "Hi."}, {"t_in": 0.43, "t_out": 1.14, "text": "I'm Billy."}]
    text = R.facts_text([], R.caption_face_facts(edl), 45.4, [], tl)
    assert '4. Caption lines exactly as they are on screen, cut seconds: 0.03-0.43 s "Hi."; 0.43-1.14 s "I\'m Billy."' in text
    assert "fall between your frames" in text
    assert "4. Caption" not in R.facts_text([], {"lines": 0}, 3.0, [])  # no captions: no timeline line
    assert "3. Caption lines" in R.facts_text([], R.caption_face_facts(edl), 45.4, None, tl)  # numbered in order


def test_caption_override_and_retext():
    ws = [CWord("its", 30.0, 30.3, "s3"), CWord("a", 30.3, 30.4, "s3"), CWord("lop", 30.4, 30.8, "s3")]
    assert override_for(ws, [{"span": "s3", "src_t": 30.31, "top": 500}])["top"] == 500
    assert override_for(ws, [{"span": "s2", "src_t": 30.31}]) is None
    new = retext(ws, "it's a loop")
    assert [w.w for w in new] == ["it's", "a", "loop"] and new[0].t_in == 30.0 and new[-1].t_out == 30.8


def test_apply_bias_stays_in_frame():
    p = SegmentPath(t_in=0, t_out=1, track_id="0", crop_w=608, crop_h=1080, xs=[0, 600, 1300], fps=30,
                    keyframes=[{"t": 0, "x": 0}])
    apply_bias(p, 60, 1920)
    assert p.xs == [60, 660, 1312] and p.keyframes[0]["x"] == 60


def test_loop_stops_when_the_same_fix_comes_back(tmp_path):
    edl, _ = make_edl()
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "voice.json").write_text(json.dumps({"words": voice_words()}))
    (tmp_path / "edl_v1.json").write_text(json.dumps(edl))
    renders = []

    def render_fn(ep, name):
        e = json.loads(ep.read_text())
        e["render"] = {"title": None, "duration_s": e["runtime_s"], "output": f"{tmp_path}/{name}"}
        ep.write_text(json.dumps(e))
        (tmp_path / name).write_bytes(b"")
        renders.append(name)

    def review_fn(e, cut, n):
        rv = {"pass": False, "overall": 60 + n, "scores": {}, "issues": [issue(R.cut_map(e)[0].cut_in + 0.1, "extend_span",
                                                                                  edge="out", seconds=0.3)]}
        return {"name": f"review_v{n}", "review": rv, "cost_usd": 0.04}

    out = R.review_loop(tmp_path, passes=3, budget_usd=1.0, log=lambda m: None, render_fn=render_fn, review_fn=review_fn,
                        ledger=R.Ledger(tmp_path / "ledger.json"))
    assert out["stop"].startswith("same fix twice") and out["reviews"] == 2
    assert renders == ["cut_v1.mp4", "cut_v2.mp4"]
    v2 = json.loads((tmp_path / "edl_v2.json").read_text())
    assert v2["spans"][0]["word_to"] == 4 and v2["patches"][0]["from"] == 1
    assert json.loads((tmp_path / "review" / "loop.json").read_text())["best"]["version"] == 2
    # a later review of one version (the editor's "Review this version") keeps the agent loop's history beside it
    R.review_loop(tmp_path, tmp_path / "edl_v2.json", passes=1, budget_usd=1.0, log=lambda m: None, render_fn=render_fn,
                  review_fn=review_fn, ledger=R.Ledger(tmp_path / "ledger.json"))
    kept = list((tmp_path / "review").glob("loop_*.json"))
    assert len(kept) == 1 and json.loads(kept[0].read_text())["reviews"] == 2
    assert json.loads((tmp_path / "review" / "loop.json").read_text())["stop"] == "max passes (1)"


def test_loop_budget_guard(tmp_path):
    edl, _ = make_edl()
    edl["render"]["output"] = str(tmp_path / "cut_v1.mp4")
    (tmp_path / "cut_v1.mp4").write_bytes(b"")
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "voice.json").write_text(json.dumps({"words": voice_words()}))
    (tmp_path / "edl_v1.json").write_text(json.dumps(edl))
    out = R.review_loop(tmp_path, passes=3, budget_usd=0.001, log=lambda m: None, render_fn=None,
                        review_fn=lambda *a: None, ledger=R.Ledger(tmp_path / "ledger.json"))
    assert out["stop"].startswith("budget") and out["reviews"] == 0


def test_drop_span_window_trims_the_end_and_never_drops_the_hook():
    edl, words = make_edl(title=False)  # s1 hook at cut 0-1.2 (w00-w03), s2 1.2-2.1, s3 2.1-3.6
    cm = R.cut_map(edl)
    iss = issue(0.6, "drop_span")
    iss["t_end_s"] = 1.15
    fix, _ = R.resolve(iss, 0, edl, words, cm)
    assert (fix.op, fix.args["edge"]) == ("trim_span", "out")
    new, _ = R.apply_fixes(edl, [fix], words)
    assert new["spans"][0]["words"] == "w00 w01" and new["spans"][0]["t_out"] == 10.6
    fix, why = R.resolve(issue(0.6, "drop_span"), 0, edl, words, cm)
    assert fix is None and "hook" in why
    iss = issue(2.75, "drop_span")
    iss["t_end_s"] = 2.95
    fix, why = R.resolve(iss, 0, edl, words, cm)
    assert fix is None and "split" in why


def test_loop_stops_on_a_regression(tmp_path):
    edl, _ = make_edl()
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "voice.json").write_text(json.dumps({"words": voice_words()}))
    (tmp_path / "edl_v1.json").write_text(json.dumps(edl))

    def render_fn(ep, name):
        e = json.loads(ep.read_text())
        e["render"] = {"title": None, "duration_s": e["runtime_s"], "output": f"{tmp_path}/{name}"}
        ep.write_text(json.dumps(e))
        (tmp_path / name).write_bytes(b"")

    scores = {1: 70, 2: 65}

    def review_fn(e, cut, n):
        t = R.cut_map(e)[-1].cut_in + 0.1
        return {"name": f"review_v{n}", "cost_usd": 0.03, "review": {"pass": False, "overall": scores[n], "scores": {},
                "issues": [issue(t, "reframe_bias", direction="left" if n == 1 else "right")]}}

    out = R.review_loop(tmp_path, passes=3, log=lambda m: None, render_fn=render_fn, review_fn=review_fn,
                        ledger=R.Ledger(tmp_path / "ledger.json"))
    assert out["stop"].startswith("regression") and out["best"]["version"] == 1 and out["reviews"] == 2


def test_caption_at_prefers_the_line_that_contains_t_and_swap_hook_trims_into_the_opener():
    edl, words = make_edl(title=False)
    head = [300, 100, 800, 900]
    edl["captions"] = [caption(1.0, 2.1, ["s2"], [("w06", 1.2, 2.1)], [400, 1300, 200, 100], head),
                       caption(2.1, 2.7, ["s3"], [("w12", 2.1, 2.4), ("w13", 2.4, 2.7)], [400, 500, 200, 100], head)]
    fix, _ = R.resolve(issue(2.1, "caption_fix"), 0, edl, words, R.cut_map(edl))
    assert fix.op == "caption_move" and fix.args["span"] == "s3"
    fix, _ = R.resolve(issue(0.0, "swap_hook", target_t_s=0.65), 0, edl, words, R.cut_map(edl))
    assert (fix.op, fix.target, fix.args["edge"]) == ("trim_span", "s1", "in")
    new, _ = R.apply_fixes(edl, [fix], words)
    assert new["spans"][0]["words"] == "w02 w03"


def test_cut_edge_levels_flag_an_edge_inside_speech():
    """FakeAudio is loud everywhere except 11.06-11.12 s: s1 (ends 11.2) and s2 (starts 11.8) are cut inside speech."""
    edl, _ = make_edl(title=False)
    edges = R.cut_edge_levels(edl, FakeAudio())
    assert [e["t"] for e in edges] == [1.2, 2.1, 3.6] and edges[2]["end"] and edges[2]["start_db"] is None
    assert all(e["loud"] for e in edges)
    edl["spans"][0]["t_out"] = 11.09
    edges = R.cut_edge_levels(edl, FakeAudio())
    assert edges[0]["end_db"] < -100 and edges[0]["loud"]  # the next start (11.8) is still inside speech
    text = R.facts_text([], {"lines": 0}, 3.6, edges)
    assert "so a word is cut off there: 1.10 s" in text
    quiet = [{**e, "loud": False} for e in edges]
    assert "every cut here is below it" in R.facts_text([], {"lines": 0}, 3.6, quiet)


def test_two_edges_of_one_span_can_be_fixed_in_one_pass_but_not_with_a_drop():
    edl, words = make_edl(title=False)
    rv = {"issues": [issue(0.0, "move_cut", sev="blocker", edge="in", seconds=0.5),
                     issue(1.2, "move_cut", sev="blocker", edge="out", seconds=-0.6),
                     issue(0.5, "reframe_bias", direction="left")]}
    fixes, skipped = R.plan_fixes(rv, edl, words)
    assert [(f.op, f.target, f.args["edge"]) for f in fixes if f.op == "move_cut"] == [("move_cut", "s1", "in"), ("move_cut", "s1", "out")]
    assert skipped == [{"issue": 2, "reason": "s1 already fixed in this pass"}]


def test_trim_ends_on_a_sentence_when_one_is_close():
    ws = voice_words()
    ws[2]["w"] = "w02."  # turn 0: "w00 w01 w02. w03 ..."
    words = R.Words(ws)
    s = span("s1", 0, 0, 6)
    n = R.trim_at(s, words, "out", 11.15)  # middles before 11.15: w00..w03 -> would keep "w00 w01 w02. w03"
    assert s["word_to"] == 2 and s["t_out"] == 10.9 and n == 4
    s = span("s1", 0, 0, 6)
    R.trim_at(s, words, "in", 10.75)  # middles after 10.75: w02..w06 -> starts after "w02." instead
    assert s["word_from"] == 3


def test_a_trimmed_edge_settles_in_the_gap_and_a_clipped_word_is_never_fixed_by_removing_words():
    edl, words = make_edl(title=False)
    edl["spans"][0]["word_to"], edl["spans"][0]["t_out"] = 6, 12.1  # s1 = w00..w06, s2 starts at w06 -> make s2 later
    edl["spans"][1].update(word_from=8, word_to=9, t_in=12.4, t_out=13.0)
    iss = issue(1.0, "drop_span")
    iss["t_end_s"] = 2.1
    fix, _ = R.resolve(iss, 0, edl, words, R.cut_map(edl))
    assert fix.op == "trim_span"
    new, log = R.apply_fixes(edl, [fix], words, FakeAudio())
    assert new["spans"][0]["word_to"] == 2 and abs(new["spans"][0]["t_out"] - 11.08) < 0.021 and "settled" in log[0]["note"]
    edl, words = make_edl(title=False)  # s1 ends 11.2, the gap at 11.06-11.12 is in reach
    mid = issue(1.2, "move_cut", sev="blocker", edge="out", seconds=-0.3)
    mid["criterion"] = "no_cut_mid_word"
    fix, _ = R.resolve(mid, 0, edl, words, R.cut_map(edl), FakeAudio())
    assert fix.args["mode"] == "valley"
    mid["criterion"] = "pacing"
    fix, _ = R.resolve(mid, 0, edl, words, R.cut_map(edl), FakeAudio())
    assert fix.args["mode"] == "words"


def test_heard_phrases_break_on_pauses_and_length():
    from montivo.engine.review import heard_phrases
    w = lambda x, a, b: {"w": x, "t_start": a, "t_end": b}  # noqa: E731
    words = [w("Hi.", 0.1, 0.4), w("I'm", 0.45, 0.6), w("Billy.", 0.6, 1.0), w("Four", 1.6, 1.9), w("Meta", 1.9, 2.1)]
    assert heard_phrases(words) == [{"t_in": 0.1, "t_out": 1.0, "text": "Hi. I'm Billy."},
                                    {"t_in": 1.6, "t_out": 2.1, "text": "Four Meta"}]
    assert [p["text"] for p in heard_phrases(words, max_words=2)] == ["Hi. I'm", "Billy.", "Four Meta"]


def test_facts_text_lists_the_heard_words_last():
    from montivo.engine.review import facts_text
    caps = {"lines": 0, "over_face": [], "min_clearance_px": None}
    heard = [{"w": "Hi.", "t_start": 0.1, "t_end": 0.4}, {"w": "I'm", "t_start": 0.45, "t_end": 0.6},
             {"w": "Bill.", "t_start": 0.6, "t_end": 1.0}]
    text = facts_text([], caps, 10.0, heard=heard)
    assert text.splitlines()[-1].startswith("3. Words heard in this cut's own audio")
    assert '0.10-1.00 s "Hi. I\'m Bill."' in text
    assert "Words heard" not in facts_text([], caps, 10.0)
    timeline = [{"t_in": 0.1, "t_out": 1.0, "text": "Hi. I'm Billy."}]
    lines = facts_text([], caps, 10.0, timeline=timeline, heard=heard).splitlines()
    assert lines[-1].startswith("5. Where the caption words and the heard words differ")
    assert '0.10 s caption "billy" / heard "bill"' in lines[-1]


def test_caption_heard_diff_aligns_words_not_times():
    from montivo.engine.review import caption_heard_diff
    timeline = [{"t_in": 19.6, "text": "the work Muse Google"}, {"t_in": 21.4, "text": "hears every word."},
                {"t_in": 22.9, "text": "Four"}]
    heard = [{"w": x, "t_start": 19.0 + k * 0.3, "t_end": 19.2 + k * 0.3}
             for k, x in enumerate("all the work news voice hears every word".split())]
    assert caption_heard_diff(timeline, heard) == [{"t": 19.6, "caption": "muse", "heard": "news"},
                                                   {"t": 19.6, "caption": "google", "heard": "voice"},
                                                   {"t": 22.9, "caption": "four", "heard": ""}]


def test_dedupe_heard_drops_voice_doubles_only():
    from montivo.engine.review import dedupe_heard
    w = lambda x, a, b: {"w": x, "t_start": a, "t_end": b}  # noqa: E731
    words = [w("I", 0.0, 0.2), w("run", 0.2, 0.5), w("run", 0.55, 0.8), w("a", 0.8, 0.9), w("a", 1.5, 1.6)]
    assert [x["w"] for x in dedupe_heard(words)] == ["I", "run", "a", "a"]  # a real repeat after a pause stays
