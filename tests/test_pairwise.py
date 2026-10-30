"""Offline: the pairwise judge (C2). One Spark call sees two cuts, asked twice with the two swapped; the second
version wins only when both calls pick it. Used by the hook A/B (instead of the two absolute scores) and by the review
loop's regression guard (a version that scores lower only by its score is put to the judge)."""
import json
from types import SimpleNamespace

import pytest

from montivo.engine import hook as H
from montivo.engine import review as R
from montivo.engine import schemas
from tests.test_hook import folder, render_fn, review_fn
from tests.test_review import _loop_folder, issue


class FakeSpark:
    """compare() answers by position: `picks` = what it says (A, B or tie) in the first and in the swapped call."""
    model = "muse-spark-1.3"

    def __init__(self, picks):
        self.picks, self.calls = list(picks), []

    def compare(self, file_a, file_b, brief, facts_a, facts_b, dur_a, dur_b, name="compare"):
        self.calls.append((file_a, file_b, facts_a, facts_b, name))  # the two orders run at once: answer by name
        w = self.picks[0 if name.endswith("_ab") else 1]
        return SimpleNamespace(parsed={"winner": w, "why": f"{w} is better",
                                       "criteria": {c: w for c in schemas.CRITERIA}}, cost_usd=0.037, seconds=20.0)


def rv(n, overall=70):
    return {"name": f"review_v{n}", "version": n, "file_id": f"file-{n}", "facts_text": f"facts of v{n}",
            "duration_s": 39.5, "cut": f"cut_v{n}.mp4", "review": {"overall": overall, "pass": False}}


@pytest.mark.parametrize("picks, winner, agree, verdict", [
    (["B", "A"], "b", True, "v2 in both orders"),  # "A" in the swapped call is v2 again
    (["A", "B"], "a", True, "v1 in both orders"),
    (["A", "A"], "a", False, "split (v1 / v2): v1 stays"),  # position bias: each call picks the first cut
    (["B", "B"], "a", False, "split (v2 / v1): v1 stays"),
    (["tie", "tie"], "a", True, "a tie in both orders: v1 stays"),
    (["B", "tie"], "a", False, "split (v2 / tie): v1 stays"),
])
def test_pairwise_reads_the_swap_back(tmp_path, picks, winner, agree, verdict):
    sp = FakeSpark(picks)
    out = R.pairwise(tmp_path, rv(1), rv(2), sp, "brief", log=lambda m: None)
    assert (out["winner"], out["agree"], out["verdict"]) == (winner, agree, verdict)
    assert out["kept"] == (2 if winner == "b" else 1) and out["mode"] == "two_files"
    # the first call shows v1 then v2, the second v2 then v1, each cut with its own facts
    calls = sorted(sp.calls, key=lambda c: c[4])
    assert calls[0][:5] == ("file-1", "file-2", "facts of v1", "facts of v2", "compare_v1_v2_ab")
    assert calls[1][:5] == ("file-2", "file-1", "facts of v2", "facts of v1", "compare_v1_v2_ba")
    assert [c["order"] for c in out["calls"]] == ["AB", "BA"]
    assert out["cost_usd"] == pytest.approx(0.074)
    assert json.loads((tmp_path / "review" / "compare_v1_v2.json").read_text()) == out
    if picks == ["B", "A"]:
        assert set(out["calls"][1]["criteria"].values()) == {"b"}  # "A" in the swapped call is v2


def test_spark_call_sends_two_labelled_files(tmp_path, monkeypatch):
    from montivo.engine import spark as S
    sent = {}

    def fake(body):
        sent.update(body)
        return {"status": "completed", "usage": {}, "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"winner": "A", "why": "", "criteria": {}})}]}]}
    monkeypatch.setattr(S, "responses_create", fake)
    sp = S.Spark(tmp_path, R.Ledger(tmp_path / "ledger.json"), log=lambda m: None)
    sp.compare("file-a", "file-b", "the brief", "fa", "fb", 39.5, 38.0)
    parts = sent["input"][0]["content"]
    assert [p["type"] for p in parts] == ["input_text", "input_text", "input_file", "input_text", "input_file"]
    assert (parts[1]["text"], parts[2]["file_id"], parts[3]["text"], parts[4]["file_id"]) == \
        ("Cut A:", "file-a", "Cut B:", "file-b")
    assert sent["text"]["format"]["schema"] == schemas.COMPARE and sent["reasoning"] == {"effort": "low"}
    assert "Cut A:\nfa" in parts[0]["text"] and "the brief" in parts[0]["text"]


def judge(winner, agree=True, calls=None):
    def go(ra, rb):
        go.asked.append((ra["name"], rb["name"]))
        a, b = ra.get("version", 1), rb.get("version", 2)
        return {"a": a, "b": b, "winner": winner, "kept": b if winner == "b" else a, "agree": agree,
                "verdict": f"{'v' + str(b) if winner == 'b' else 'v' + str(a)} in both orders" if agree else "split",
                "mode": "two_files", "cost_usd": 0.07,
                "calls": calls or [{"order": "AB", "winner": winner, "why": "w", "criteria": {}},
                                   {"order": "BA", "winner": winner, "why": "w", "criteria": {}}]}
    go.asked = []
    return go


def test_hook_ab_decides_by_the_pairwise_judge_not_the_scores(tmp_path):
    folder(tmp_path)
    reviews = []
    j = judge("a", agree=False, calls=[{"order": "AB", "winner": "b", "why": "", "criteria": {}},
                                       {"order": "BA", "winner": "a", "why": "", "criteria": {}}])
    out = H.hook_ab(tmp_path, budget_usd=0.5, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=render_fn(tmp_path, []), review_fn=review_fn({1: (60, False), 2: (75, False)}, reviews),
                    compare_fn=j)
    # B scores 15 more, but the judge split: A stays
    assert j.asked == [("review_v1", "review_v2")]
    assert (out["winner"], out["kept"], out["decisive"]) == ("a", 1, False)
    assert out["reason"] == "the pairwise judge: split" and out["by_score"]["winner"] == "b"
    assert out["judge"]["agree"] is False and len(out["judge"]["calls"]) == 2
    assert out["cost_usd"] == pytest.approx(0.03 * 2 + 0.07)


def test_hook_ab_keeps_b_when_the_judge_picks_it_twice_against_the_scores(tmp_path):
    folder(tmp_path)
    out = H.hook_ab(tmp_path, budget_usd=0.5, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=render_fn(tmp_path, []), review_fn=review_fn({1: (80, False), 2: (76, False)}, []),
                    compare_fn=judge("b"))
    assert (out["winner"], out["kept"], out["decisive"]) == ("b", 2, True)
    assert out["by_score"] == {"winner": "a", "reason": "A scores 80 against B's 76"}
    v2 = json.loads((tmp_path / "edl_v2.json").read_text())
    assert v2["patches"][-1]["result"]["judge"]["verdict"] == "v2 in both orders"


def test_hook_ab_without_budget_for_the_judge_decides_on_the_scores(tmp_path):
    folder(tmp_path)
    j = judge("a")
    # two reviews of the 6 s cut fit in $0.06 (about $0.025 each), the judge (about $0.04 more) does not
    out = H.hook_ab(tmp_path, budget_usd=0.06, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=render_fn(tmp_path, []), review_fn=review_fn({1: (60, False), 2: (75, False)}, []),
                    compare_fn=j)
    assert j.asked == [] and out["judge"] is None and out["judge_note"].startswith("no budget for the pairwise judge")
    assert (out["winner"], out["kept"]) == ("b", 2)


def loop_review_fn(scores):
    def go(e, cut, n):
        return {"name": f"review_v{n}", "version": n, "cost_usd": 0.04, "duration_s": 4.5, "review": {
            "pass": False, "overall": scores[n], "scores": {},
            "issues": [issue(R.cut_map(e)[n - 1].cut_in + 0.1, "extend_span", edge="out", seconds=0.3)]}}
    return go


def test_loop_regression_on_the_score_alone_asks_the_judge(tmp_path):
    render = _loop_folder(tmp_path)
    j = judge("b")
    out = R.review_loop(tmp_path, passes=3, budget_usd=1.0, log=lambda m: None, render_fn=render,
                        review_fn=loop_review_fn({1: 70, 2: 66, 3: 64}), compare_fn=j,
                        ledger=R.Ledger(tmp_path / "ledger.json"))
    # v2 66 < v1 70: the judge prefers v2 in both orders, the loop goes on; v3 64 < v2: asked again, v3 wins too
    assert j.asked == [("review_v1", "review_v2"), ("review_v2", "review_v3")]
    assert [h["version"] for h in out["passes"]] == [1, 2, 3] and out["stop"] == "max passes (3)"
    assert out["passes"][1]["preferred_over"] == 1 and out["passes"][2]["preferred_over"] == 2
    assert out["best"]["version"] == 3 and out["review_cost_usd"] == pytest.approx(0.12 + 0.14)


def test_loop_regression_stands_when_the_judge_keeps_the_best(tmp_path):
    render = _loop_folder(tmp_path)
    j = judge("a", agree=False)
    out = R.review_loop(tmp_path, passes=3, budget_usd=1.0, log=lambda m: None, render_fn=render,
                        review_fn=loop_review_fn({1: 70, 2: 66}), compare_fn=j, ledger=R.Ledger(tmp_path / "ledger.json"))
    assert out["stop"] == "regression: v2 scored 66 < v1 70, the best stays v1; the pairwise judge: split"
    assert out["best"]["version"] == 1 and out["passes"][1]["pairwise"]["winner"] == "a"


def test_loop_without_a_judge_or_its_budget_stops_on_the_score(tmp_path):
    render = _loop_folder(tmp_path)
    out = R.review_loop(tmp_path, passes=3, budget_usd=1.0, log=lambda m: None, render_fn=render,
                        review_fn=loop_review_fn({1: 70, 2: 66}), ledger=R.Ledger(tmp_path / "ledger.json"))
    assert out["stop"] == "regression: v2 scored 66 < v1 70, the best stays v1"
    j = judge("b")
    for f in list(tmp_path.glob("*_v2.*")) + [tmp_path / "review_v1.json"]:
        f.unlink(missing_ok=True)
    out = R.review_loop(tmp_path, passes=3, budget_usd=0.09, log=lambda m: None, render_fn=render,
                        review_fn=loop_review_fn({1: 70, 2: 66}), compare_fn=j, ledger=R.Ledger(tmp_path / "ledger.json"))
    assert j.asked == [] and "no budget for the pairwise judge" in out["stop"]


def test_best_of_follows_the_judges_wins():
    h = [{"version": 1, "overall": 70, "pass": False}, {"version": 2, "overall": 66, "pass": False, "preferred_over": 1},
         {"version": 3, "overall": 60, "pass": False, "preferred_over": 2}, {"version": 4, "overall": 50, "pass": False}]
    assert R.best_of(h)["version"] == 3
    assert R.best_of(h[:1] + h[3:])["version"] == 1
