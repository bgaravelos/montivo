"""C4: when the review loop stops without a pass on a stop no fix of the cut gets past (no applicable fix, the same fix
twice, a regression, a measured fault the fixes cannot clear) and a blocker or major is still open, it re-plans once
from the best version with the reviewer's open notes, preflights, renders and reviews that version, and keeps it only
when its gated rank beats the best. Runs that never passed: d1-nasa, d1-tedx, d1-usgs, 013."""
import json

import pytest

from montivo.engine import review as R
from tests.test_review import _loop_folder, issue


def reviews(scores, majors=True):
    """scores: version -> (overall, pass). v1 asks for a fix that cannot apply (a move_cut far from any cut), so the
    loop stops on "no applicable fix" with its major open; later versions report the same kind of note."""
    def go(e, cut, n):
        overall, ok = scores[n]
        its = [] if ok else [dict(issue(0.2, "none", "major" if majors else "minor"), what="two voices over the cut",
                                  criterion="pacing", fix_args="cannot be fixed by moving the cut")]
        return {"name": f"review_v{n}", "version": n, "cost_usd": 0.04, "duration_s": 4.5,
                "review": {"pass": ok, "overall": overall, "scores": {}, "issues": its}}
    go.calls = []
    return go


def escalator(tmp_path, made):
    """escalate_fn: writes the next EDL (a copy of the version it starts from, one span fewer) unrendered."""
    def go(from_v, instruction):
        e = json.loads((tmp_path / f"edl_v{from_v}.json").read_text())
        n = max(int(p.stem.split("_v")[1]) for p in tmp_path.glob("edl_v*.json")) + 1
        e["version"], e["spans"] = n, e["spans"][:-1]
        e.pop("render", None)
        e.setdefault("patches", []).append({"from": from_v, "source": "review", "action": "escalate", "fixes": [
            {"op": "replan", "target": "plan", "issue": -1, "severity": "human", "note": "replanned"}]})
        (tmp_path / f"edl_v{n}.json").write_text(json.dumps(e))
        made.append((from_v, instruction))
        return tmp_path / f"edl_v{n}.json", 0.012, "meta-models/muse-spark-open-1026"
    return go


RENDER = {}


def loop(tmp_path, rv, esc, budget=1.0, passes=3):
    render = RENDER.get(tmp_path) or RENDER.setdefault(tmp_path, _loop_folder(tmp_path))
    return R.review_loop(tmp_path, passes=passes, budget_usd=budget, log=lambda m: None, render_fn=render,
                         review_fn=rv, escalate_fn=esc, ledger=R.Ledger(tmp_path / "ledger.json"))


def test_a_stuck_loop_replans_once_and_keeps_the_better_version(tmp_path):
    made = []
    out = loop(tmp_path, reviews({1: (62, False), 2: (70, False)}), escalator(tmp_path, made))
    assert len(made) == 1 and made[0][0] == 1
    assert "0.2 s, major pacing: two voices over the cut (the reviewer's fix: cannot be fixed" in made[0][1]
    assert "v1, 62/100, no pass" in made[0][1] and "Keep everything the brief asks for" in made[0][1]
    assert out["stop"] == "no applicable fix; escalation v2 70, kept"
    assert [h["version"] for h in out["passes"]] == [1, 2] and out["passes"][1]["escalation"] is True
    assert out["best"]["version"] == 2 and out["last_edl"] == "edl_v2.json"
    esc = out["escalation"]
    assert (esc["from"], esc["version"], esc["overall"], esc["pass"], esc["kept"]) == (1, 2, 70, False, True)
    assert esc["model"].startswith("meta-models/") and esc["plan_cost_usd"] == 0.012 and esc["stop_before"] == "no applicable fix"
    assert out["review_cost_usd"] == pytest.approx(0.04 + 0.012 + 0.04)
    assert (tmp_path / "cut_v2.mp4").is_file() and json.loads((tmp_path / "review" / "loop.json").read_text()) == out


def test_an_escalation_that_passes_ends_the_loop_on_a_pass(tmp_path):
    out = loop(tmp_path, reviews({1: (62, False), 2: (84, True)}), escalator(tmp_path, []))
    assert out["stop"] == "pass" and out["escalation"]["pass"] and out["best"]["version"] == 2


def test_a_worse_escalation_is_not_kept(tmp_path):
    out = loop(tmp_path, reviews({1: (62, False), 2: (55, False)}), escalator(tmp_path, []))
    assert out["stop"] == "no applicable fix; escalation v2 55, not kept"
    assert out["best"]["version"] == 1 and out["escalation"]["kept"] is False


def test_no_escalation_without_a_major_open_or_on_another_stop(tmp_path):
    made = []
    out = loop(tmp_path, reviews({1: (62, False)}, majors=False), escalator(tmp_path, made))
    assert made == [] and out["escalation"]["skipped"] == "no blocker or major open"
    out = loop(tmp_path, reviews({1: (62, False)}), escalator(tmp_path, made), passes=1)
    assert made == [] and out["stop"] == "max passes (1)" and "escalation" not in out


def test_the_budget_covers_the_escalation(tmp_path):
    made = []
    # one review of the 6 s cut (~$0.025) fits in $0.06, a re-plan and its review (~$0.055 more) does not
    out = loop(tmp_path, reviews({1: (62, False)}), escalator(tmp_path, made), budget=0.06)
    assert made == [] and out["escalation"]["skipped"].startswith("budget:")


def test_a_failed_escalation_keeps_the_history_and_leaves_no_edl_without_its_cut(tmp_path):
    render = _loop_folder(tmp_path)
    made = []
    esc = escalator(tmp_path, made)

    def render_fails(ep, name):
        if name == "cut_v2.mp4":
            raise RuntimeError("ffmpeg died")
        return render(ep, name)
    out = R.review_loop(tmp_path, passes=3, budget_usd=1.0, log=lambda m: None, render_fn=render_fails,
                        review_fn=reviews({1: (62, False)}), escalate_fn=esc, ledger=R.Ledger(tmp_path / "ledger.json"))
    assert out["escalation"]["error"].startswith("RuntimeError: ffmpeg died") and out["stop"] == "no applicable fix"
    assert [h["version"] for h in out["passes"]] == [1] and not (tmp_path / "edl_v2.json").exists()


def test_the_default_escalation_replans_in_the_loops_own_ledger(tmp_path, monkeypatch):
    from montivo import human
    (tmp_path / "run.json").write_text(json.dumps({"brief": "b", "target_s": 40}))
    led = R.Ledger(tmp_path / "ledger.json")
    seen = {}

    def fake(run_dir, instruction, **kw):
        seen.update(kw, instruction=instruction, defer=human.DEFER)
        return {"version": 7, "edl": "edl_v7.json", "deferred": True}
    monkeypatch.setattr(human, "replan", fake)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    path, cost, model = R.replan_escalation(tmp_path, led)(3, "the notes")
    assert path == tmp_path / "edl_v7.json" and model == "muse-spark-1.3" and cost == 0.0
    assert (seen["from_v"], seen["target_s"], seen["ledger"], seen["source"], seen["action"], seen["review"]) == \
        (3, 40.0, led, "review", "escalate", "review_v3")
    assert seen["defer"] == [] and human.DEFER is None  # unrendered, and the global put back
    monkeypatch.setenv("BASETEN_API_KEY", "k")
    assert R.replan_escalation(tmp_path, led)(3, "x")[2] == "meta-models/muse-spark-open-1026"


def test_the_notes_are_the_open_majors_worst_first_and_the_measured_faults():
    rv = {"review": {"overall": 58, "issues": [
        {"t_s": 9.0, "severity": "major", "criterion": "pacing", "what": "a repeated take", "fix_args": ""},
        {"t_s": 3.0, "severity": "blocker", "criterion": "hook_in_1_5s", "what": "slow open", "fix_args": "open on s3"},
        {"t_s": 1.0, "severity": "minor", "criterion": "ending", "what": "abrupt", "fix_args": ""},
        {"t_s": 2.0, "severity": "major", "criterion": "brief_covered", "what": "a line never said", "fix_args": ""}]},
        "pass_gate": {"checks": ["cut at 12.30 s inside speech (out edge over -34.0 dB)"]}}
    assert R.escalation_notes(rv) == ["3.0 s, blocker hook_in_1_5s: slow open (the reviewer's fix: open on s3)",
                                      "9.0 s, major pacing: a repeated take",
                                      "measured: cut at 12.30 s inside speech (out edge over -34.0 dB)"]
