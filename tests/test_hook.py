"""Offline: the hook A/B (engine/hook.py). Which line challenges the opener, the A/B on fake renders and reviews
(B is the next version through the swap_hook patch, the winner and the reason are recorded, A is never touched), the
reuse of an existing review, the budget guard and the no-repeat rule."""
import json

import pytest

from montivo.engine import hook as H
from montivo.engine import review as R
from tests.test_review import issue, make_edl, voice_words


def folder(tmp_path, roles=None):
    edl, _ = make_edl()  # s1 hook (turn 0 w0-3), s2 (turn 0 w6-8), s3 (turn 1 w2-6), a 1.5 s title card in front
    for s, r in zip(edl["spans"], roles or []):
        s["role"] = r
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "voice.json").write_text(json.dumps({"words": voice_words()}))
    (tmp_path / "edl_v1.json").write_text(json.dumps(edl))
    return edl


def render_fn(tmp_path, calls):
    def go(ep, name):
        e = json.loads(ep.read_text())
        e["render"] = {"title": {"seconds": 1.5}, "duration_s": e["runtime_s"] + 1.5, "output": f"{tmp_path}/{name}"}
        ep.write_text(json.dumps(e))
        (tmp_path / name).write_bytes(b"")
        calls.append(name)
    return go


def review_fn(scores, calls, issues=None):
    """scores: version -> (overall, pass); issues: version -> the review's issues."""
    def go(e, cut, n):
        calls.append(n)
        overall, ok = scores[n]
        return {"name": f"review_v{n}", "cut": str(cut), "cost_usd": 0.03,
                "review": {"overall": overall, "pass": ok, "scores": {"hook_in_1_5s": overall // 10},
                           "issues": (issues or {}).get(n, []), "summary": ""}}
    return go


def test_challenger_order(tmp_path):
    edl = folder(tmp_path)
    assert H.challengers(edl, tmp_path)[0] == ("s2", "the first new passage after the opener")
    edl["spans"][2]["role"] = "payoff"
    assert [c[0] for c in H.challengers(edl, tmp_path)] == ["s3", "s2"]
    edl["spans"][1]["role"] = "hook"
    assert [c[0] for c in H.challengers(edl, tmp_path)] == ["s2", "s3"]
    # the reviewer asked for the line at 4.0 s of cut_v1 (s3): that comes first
    (tmp_path / "review_v1.json").write_text(json.dumps({"review": {"issues": [issue(1.6, "swap_hook", target_t_s=4.0)]}}))
    first = H.challengers(edl, tmp_path)[0]
    assert first[0] == "s3" and "reviewer asked" in first[1] and "cut_v1" in first[1]
    edl["spans"][2]["contiguous_with_previous"] = True  # a reviewer target is taken even mid-passage
    assert H.challengers(edl, tmp_path)[0][0] == "s3"
    assert H.challengers({"spans": edl["spans"][:1]}, tmp_path) == []


def test_b_wins_and_everything_is_recorded(tmp_path):
    folder(tmp_path)
    renders, reviews = [], []
    out = H.hook_ab(tmp_path, budget_usd=0.2, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=render_fn(tmp_path, renders), review_fn=review_fn({1: (68, False), 2: (84, True)}, reviews))
    assert renders == ["cut_v1.mp4", "cut_v2.mp4"] and reviews == [1, 2]  # A had no cut and no review yet
    assert (out["a"]["version"], out["b"]["version"], out["challenger"], out["kept"]) == (1, 2, "s2", 2)
    assert out["winner"] == "b" and out["reason"] == "B passes and A does not" and out["decisive"]
    assert out["cost_usd"] == pytest.approx(0.06) and out["a"]["reused"] is False
    v1, v2 = (json.loads((tmp_path / f"edl_v{n}.json").read_text()) for n in (1, 2))
    assert [s["id"] for s in v1["spans"]] == ["s1", "s2", "s3"] and "patches" not in v1  # A untouched
    assert [s["id"] for s in v2["spans"]] == ["s2", "s1", "s3"]
    assert [s["role"] for s in v2["spans"]] == ["hook", "body", "body"] and v2["version"] == 2
    p = v2["patches"][-1]
    assert (p["from"], p["source"], p["action"], p["challenger"]) == (1, "hook_ab", "hook_ab", "s2")
    assert p["fixes"][0]["op"] == "swap_hook" and p["result"]["kept"] == 2
    rec = json.loads((tmp_path / "review" / "hook_ab_v1_v2.json").read_text())
    assert rec == out and rec["a"]["hook"] == 6 and rec["b"]["hook"] == 8
    for n in (1, 2):  # both reviews on disk with the fixes the loop would plan, for the editor
        rv = json.loads((tmp_path / f"review_v{n}.json").read_text())
        assert "fixes" in rv and "skipped" in rv


def test_reuses_a_review_a_tie_keeps_a_and_no_repeat(tmp_path):
    folder(tmp_path, roles=["hook", "body", "payoff"])
    renders, reviews = [], []
    rf = render_fn(tmp_path, renders)
    rf(tmp_path / "edl_v1.json", "cut_v1.mp4")
    (tmp_path / "review_v1.json").write_text(json.dumps({"name": "review_v1", "cut": str(tmp_path / "cut_v1.mp4"),
                                                          "cost_usd": 0.03, "review": {"overall": 80, "pass": True,
                                                                                       "scores": {}, "issues": []}}))
    kw = dict(log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"), render_fn=rf)
    out = H.hook_ab(tmp_path, tmp_path / "edl_v1.json", review_fn=review_fn({2: (80, True)}, reviews), **kw)
    assert reviews == [2] and out["a"]["reused"] and out["cost_usd"] == pytest.approx(0.03)
    assert (out["challenger"], out["winner"], out["kept"], out["decisive"]) == ("s3", "a", 1, False)
    assert out["reason"].startswith("a tie at 80")
    # the payoff was compared against v1 already: the next candidate, s2, becomes v3
    out = H.hook_ab(tmp_path, tmp_path / "edl_v1.json", review_fn=review_fn({3: (90, True)}, reviews), **kw)
    assert (out["challenger"], out["b"]["version"], out["kept"]) == ("s2", 3, 3)
    out = H.hook_ab(tmp_path, tmp_path / "edl_v1.json", review_fn=review_fn({}, reviews), **kw)
    assert out["stop"].startswith("no challenger: every candidate") and not (tmp_path / "edl_v4.json").exists()


def test_a_review_that_names_a_new_opener_picks_the_challenger(tmp_path):
    folder(tmp_path)
    reviews = []
    rv = review_fn({1: (60, False), 2: (70, False)}, reviews, issues={1: [issue(1.6, "swap_hook", "blocker", target_t_s=4.0)]})
    out = H.hook_ab(tmp_path, budget_usd=0.2, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=render_fn(tmp_path, []), review_fn=rv)
    assert out["challenger"] == "s3" and "reviewer asked" in out["why"]
    assert (out["winner"], out["reason"]) == ("b", "B scores 70 against A's 60")


def test_budget_guard_spends_nothing(tmp_path):
    folder(tmp_path)
    reviews = []
    out = H.hook_ab(tmp_path, budget_usd=0.01, log=lambda m: None, ledger=R.Ledger(tmp_path / "ledger.json"),
                    render_fn=render_fn(tmp_path, []), review_fn=review_fn({}, reviews))
    assert out["stop"].startswith("budget: two reviews") and reviews == []
    assert not (tmp_path / "edl_v2.json").exists()


def test_cli_hook_and_review_hook_ab(tmp_path, monkeypatch):
    from montivo import cli
    from montivo.engine import hook
    folder(tmp_path)
    got = []
    monkeypatch.setattr(hook, "hook_ab", lambda run_dir, edl_path=None, **kw: got.append((edl_path, kw["budget_usd"])) or {})
    monkeypatch.setattr(R, "review_loop", lambda *a, **kw: {"best": {"version": 2}})
    assert cli.main(["hook", str(tmp_path), "--budget", "0.07"]) == 0
    assert cli.main(["review", str(tmp_path), "--hook-ab"]) == 0
    assert got == [(None, 0.07), (tmp_path / "edl_v2.json", 0.10)]


def test_old_edl_with_many_payoffs_offers_the_last_one(tmp_path):
    # edl_v3 of run 012-open was built before normalize_roles: every body span a payoff. The ending is the cold open
    # to try, not the first middle line; a hook the guard turned into body is still offered
    edl = folder(tmp_path, roles=["hook", "payoff", "payoff"])
    edl["spans"][1]["contiguous_with_previous"] = True
    assert [c[0] for c in H.challengers(edl, tmp_path)] == ["s3"]
    edl["spans"][1].update(role="body", planner_role="hook")
    assert H.challengers(edl, tmp_path)[0] == ("s2", "the planner marked it a hook too")
