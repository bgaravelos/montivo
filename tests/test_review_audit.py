"""The review-loop findings of the 31/10 audit (docs/notes/full-audit_2026-10-31.md), each on the case that broke:
preflight fault times on a card-style EDL, the loop's edl_vN version, best_of, a word-level fix on one edge undoing
the other, the hook A/B's preflight and gate rank, the heard-words cache after a re-render, a stale shortened pause."""
import copy
import json
import os

from montivo.engine import hook as H
from montivo.engine import review as R
from tests.test_hook import folder, render_fn as hook_render_fn
from tests.test_review import _loop_folder, issue, make_edl


class QuietAudio:
    """A source whose quietest point is the middle of any window, the edges loud: a valley fix always applies."""
    def quietest(self, lo, hi, step=0.02):
        return round((lo + hi) / 2, 3), -60.0

    def level(self, a, b):
        return -20.0


def test_preflight_resolves_measured_faults_on_the_laid_out_cut_with_its_title_card(monkeypatch):
    # 014 v1: the gate measures on the laid EDL (a 1.5 s card in front), the fixes were resolved on the unlaid one
    from montivo.engine import preview
    edl, words = make_edl(title=False)  # unlaid: s1 0-1.2, s2 1.2-2.1, s3 2.1-3.6; laid: s1 1.5-2.7, s2 -3.6, s3 -5.1

    def laid(run_dir, e, laid=False):
        out = copy.deepcopy(e)
        out["render"] = {"title": {"seconds": 1.5}, "duration_s": 5.1}
        return {"_edl": out}
    monkeypatch.setattr(preview, "preview_data", laid)
    monkeypatch.setattr(R, "caption_face_facts", lambda e: {})
    monkeypatch.setattr(R, "head_frame_facts", lambda run_dir, e: None)
    edges = iter([[{"t": 2.7, "loud": True, "end_db": -20.0, "start_db": None, "loud_db": -34.0}], []])
    monkeypatch.setattr(R, "cut_edge_levels", lambda e, audio: next(edges))
    out, rec = R.preflight(None, edl, words, QuietAudio(), log=lambda m: None)
    assert rec["clear"] and len(rec["fixes"]) == 1
    f = rec["fixes"][0]
    assert (f["target"], f["args"]["edge"], f["args"]["mode"]) == ("s1", "out", "valley")  # the s1 | s2 cut, not s2's end
    a, b = out["spans"][1], edl["spans"][1]
    assert out["spans"][0]["t_out"] != edl["spans"][0]["t_out"] and (a["t_in"], a["t_out"]) == (b["t_in"], b["t_out"])


def test_the_loops_new_edl_carries_its_own_version_number(tmp_path):
    render_fn = _loop_folder(tmp_path)
    for n in range(2, 6):  # v2-v5 made by later edits: reviewing v1 makes v6
        (tmp_path / f"edl_v{n}.json").write_text(json.dumps({"later": n}))
        (tmp_path / f"cut_v{n}.mp4").write_bytes(b"later")

    def review_fn(e, cut, n):
        return {"name": f"review_v{n}", "cost_usd": 0.04, "review": {
            "pass": n > 1, "overall": 70 + n, "scores": {},
            "issues": [issue(R.cut_map(e)[0].cut_in + 0.1, "extend_span", edge="out", seconds=0.3)]}}
    R.review_loop(tmp_path, passes=2, budget_usd=1.0, log=lambda m: None, render_fn=render_fn, review_fn=review_fn,
                  ledger=R.Ledger(tmp_path / "ledger.json"))
    assert json.loads((tmp_path / "edl_v6.json").read_text())["version"] == 6  # was 2: finalize counted from v1


def test_best_of_walks_the_history_and_a_later_version_can_outrank_the_judges_pick():
    h = [{"version": 1, "pass": False, "checks": [], "overall": 70},
         {"version": 2, "pass": False, "checks": [], "overall": 65, "preferred_over": 1},
         {"version": 3, "pass": False, "checks": [], "overall": 68}]
    assert R.best_of(h)["version"] == 3  # v3 outranks v2, which the judge preferred over v1
    assert R.best_of(h[:2])["version"] == 2
    assert R.best_of(h[:1] + [{"version": 2, "pass": False, "checks": [], "overall": 60}])["version"] == 1
    assert R.best_of([{"version": 1, "pass": True, "checks": [], "overall": 60},
                      {"version": 2, "pass": False, "checks": [], "overall": 90}])["version"] == 1


def test_a_word_level_fix_on_one_edge_keeps_a_valley_fix_on_the_other():
    edl, words = make_edl()
    s3 = edl["spans"][2]  # turn 1 words 2-6, 30.6-32.1
    valley = R.Fix(op="move_cut", target="s3", issue=0, severity="major", t_s=5.0,
                   args={"edge": "out", "mode": "valley", "to": 32.06, "db": -60.0, "was_db": -30.0}, check="loud_edge")
    trim = R.Fix(op="trim_span", target="s3", issue=1, severity="major", t_s=3.7,
                 args={"edge": "in", "src_t": 31.22})
    new, _ = R.apply_fixes(edl, [valley, trim], words, None)
    n3 = new["spans"][2]
    assert n3["word_from"] > s3["word_from"] and n3["t_in"] == words.turn(1)[n3["word_from"]]["t_start"]
    assert n3["t_out"] == 32.06  # was reset to w16's end 32.1: "same fix twice" and a paid escalation
    # the same for shift_words and sentence_edge on their own
    span = dict(s3, t_out=32.06)
    assert R.shift_words(span, words, "in", 0.3, set()) == 1 and span["t_out"] == 32.06
    R.sentence_edge(span, words, "in")
    assert span["t_out"] == 32.06


def test_hook_b_is_preflighted_before_its_render(tmp_path, monkeypatch):
    folder(tmp_path)
    seen = []

    def fake_preflight(run_dir, edl, words, audio, log=print):
        seen.append([s["id"] for s in edl["spans"]])
        return dict(edl, preflighted=True), {"fixes": [{"op": "move_cut", "target": "s1", "fingerprint": "move_cut:s1:",
                                                         "note": "valley"}], "clear": True, "left": [], "stop": "clear"}
    monkeypatch.setattr(H, "preflight", fake_preflight)
    reviews = []
    out = H.hook_ab(tmp_path, budget_usd=0.2, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=hook_render_fn(tmp_path, []),
                    review_fn=lambda e, c, n: reviews.append(e.get("preflighted")) or {
                        "name": f"review_v{n}", "cut": str(c), "cost_usd": 0.03,
                        "review": {"overall": 70, "pass": False, "scores": {}, "issues": [], "summary": ""}})
    assert seen == [["s2", "s1", "s3"]] and reviews == [None, True]  # B, never A, and B reviewed after it
    v2 = json.loads((tmp_path / "edl_v2.json").read_text())
    p = v2["patches"][-1]
    assert v2["preflighted"] and v2["version"] == 2 and p["preflight"]["clear"]
    assert [f["op"] for f in p["fixes"]] == ["swap_hook", "move_cut"] and p["fixes"][1]["preflight"]
    assert out["kept"] == 1


def test_hook_a_gate_blocked_b_never_beats_a_clear_a_whatever_the_judge_says(tmp_path):
    folder(tmp_path)
    judged = []

    def review_fn(e, cut, n):
        gate = {"checks": ["cut at 2.00 s inside speech"]} if n == 2 else {"checks": []}
        return {"name": f"review_v{n}", "cut": str(cut), "cost_usd": 0.03, "pass_gate": gate,
                "review": {"overall": 70 if n == 1 else 85, "pass": False, "scores": {}, "issues": [], "summary": ""}}
    out = H.hook_ab(tmp_path, budget_usd=0.5, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=hook_render_fn(tmp_path, []), review_fn=review_fn,
                    compare_fn=lambda a, b: judged.append(1) or {"winner": "b"})
    assert (out["winner"], out["kept"], out["decisive"]) == ("a", 1, True) and judged == []
    assert out["reason"] == "no measured check blocks A, one blocks B" and out["by_score"]["winner"] == "b"


def test_heard_words_are_heard_again_when_the_cut_is_rendered_again(tmp_path, monkeypatch):
    from montivo.engine import voice
    calls = []

    def transcribe(wav, out_dir, ledger, log=print):
        calls.append(wav)
        (out_dir / "voice.json").write_text(json.dumps({"turns": [], "words": []}))
        return voice.VoiceResult(turns=[], words=[], audio_s=0.0, chunks=[])
    monkeypatch.setattr(voice, "transcribe", transcribe)
    monkeypatch.setattr(R, "make_wav", lambda cut, out: out)
    cut, heard = tmp_path / "cut_v2.mp4", tmp_path / "heard"
    cut.write_bytes(b"first render")
    R.heard_words(cut, heard, None, log=lambda m: None)
    R.heard_words(cut, heard, None, log=lambda m: None)
    assert len(calls) == 1  # the same cut: cached
    cut.write_bytes(b"the cut rendered again")
    R.heard_words(cut, heard, None, log=lambda m: None)
    assert len(calls) == 2
    # a cache from before the key: kept while it is newer than the cut, heard again once the cut is newer
    (heard / "heard.json").unlink()
    R.heard_words(cut, heard, None, log=lambda m: None)
    assert len(calls) == 2
    st = cut.stat()
    os.utime(heard / "voice.json", ns=(st.st_atime_ns, st.st_mtime_ns - 10**9))
    R.heard_words(cut, heard, None, log=lambda m: None)
    assert len(calls) == 3 and (heard / "heard.json").is_file()


def test_a_shortened_pause_is_given_back_only_while_its_words_still_meet_there():
    edl, words = make_edl()
    s1, s2 = edl["spans"][:2]  # s1 turn 0 w0-3 (ends 11.2), s2 w6-8 (starts 11.8): w4, w5 deleted between them
    rec = {"from": 11.2, "to": 11.8, "join": 11.65}
    s1["pause_out"], s2["pause_in"] = dict(rec), dict(rec)
    fix = R.Fix(op="extend_span", target="s1", issue=0, severity="major", t_s=2.5, args={"edge": "out", "seconds": 0.3})
    new, log = R.apply_fixes(edl, [fix], words, None)
    assert "given back whole" not in log[0]["note"] and new["spans"][1]["t_in"] == 11.8  # a stale record: no rejoin
    assert new["spans"][0]["word_to"] == 4  # the extend adds a word, as without a pause
    # the clips still meet word after word, but the pause recorded is far from where they meet now
    edl, words = make_edl()
    s1, s2 = edl["spans"][:2]
    s2.update(word_from=4, t_in=11.2)
    far = {"from": 12.5, "to": 13.0, "join": 12.7}
    s1["pause_out"], s2["pause_in"] = dict(far), dict(far)
    assert R.rejoin_pause(edl, s1, "out", words) == ""
    near = {"from": 11.1, "to": 11.5, "join": 11.2}
    s1["pause_out"], s2["pause_in"] = dict(near), dict(near)
    assert "given back whole" in R.rejoin_pause(edl, s1, "out", words)
