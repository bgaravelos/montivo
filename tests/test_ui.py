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


def _run_folder(tmp_path, planner=None):
    """A run folder with v1 (the plan) and v2 (a re-plan on Muse Spark Open) and v2's planner A/B record."""
    import json
    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"footage": "footage/x.mp4", "brief": "b", "model": "muse-spark-1.3",
                                            **({"planner_model": planner} if planner else {})}))
    (d / "edl_v1.json").write_text(json.dumps({"spans": [], "runtime_s": 40}))
    (d / "edl_v2.json").write_text(json.dumps({"spans": [], "runtime_s": 41, "patches": [
        {"from": 1, "source": "human", "action": "replan", "instruction": "x", "model": "meta-models/muse-spark-open-1026",
         "fixes": [{"op": "replan", "target": "plan", "issue": -1}]}]}))
    (d / "edl_v3.json").write_text(json.dumps({"spans": [], "runtime_s": 41, "patches": [
        {"from": 2, "source": "hook_ab", "action": "hook_ab", "challenger": "s4", "why": "the planner's payoff, as a cold open",
         "fixes": [{"op": "swap_hook", "target": "s4", "issue": -1}]}]}))
    (d / "plan_compare_v1_v2.json").write_text(json.dumps({"v1": {"planner": "muse-spark-1.3"}, "v2": {"planner": "o"}}))
    return d


def test_versions_name_their_planner(tmp_path):
    from montivo.server.runs import stage_summaries, versions
    d = _run_folder(tmp_path)
    v1, v2, v3 = versions(d)
    assert v1["planner"] == {"model": "muse-spark-1.3", "name": "Muse Spark 1.3"}
    assert v2["planner"]["name"] == "Muse Spark Open" and v2["plan_compare"]["v2"]["planner"] == "o"
    assert v3["planner"] is None and v3["origin"]["challenger"] == "s4"
    d2 = _run_folder(tmp_path / "open", planner="meta-models/muse-spark-open-1026")
    assert versions(d2)[0]["planner"]["name"] == "Muse Spark Open"
    assert next(s for s in stage_summaries(d2, []) if s["id"] == "plan")["model"] == "Muse Spark Open"


def test_hook_and_planner_actions_need_their_keys(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    _run_folder(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    c = TestClient(create_app(tmp_path))
    assert c.get("/api/runs").json()["has_open_key"] is False
    r = c.post("/api/runs/r1/hook", json={"from": 2})
    assert r.status_code == 409 and "MODEL_API_KEY" in r.json()["error"]
    monkeypatch.setenv("MODEL_API_KEY", "test")
    assert c.post("/api/runs/r1/hook", json={"from": 9}).status_code == 400  # no edl_v9.json
    r = c.post("/api/runs/r1/replan", json={"instruction": "shorter", "planner": "open"})
    assert r.status_code == 409 and "BASETEN_API_KEY" in r.json()["error"]
    assert c.post("/api/runs/r1/replan", json={"instruction": "shorter", "planner": "gpt"}).status_code == 400


def test_hook_job_result_reads_the_record(tmp_path):
    import json

    from montivo.server.app import Job, State, result_message
    d = _run_folder(tmp_path)
    out = d / "job.out"
    rec = {"kept": 2, "reason": "higher overall", "decisive": False,
           "a": {"version": 2, "opener": "s1", "overall": 88, "hook": 10}, "b": {"version": 3, "opener": "s4", "overall": 81, "hook": 3}}
    out.write_text("[ 0.1s] hook a/b: rendering\n" + json.dumps(rec, indent=2) + "\n")
    job = Job(id="j", run="r1", kind="hook", label="Hook A/B", cmd=[], out=str(out), rc=0)
    msg = result_message(State(tmp_path), job)
    assert msg["version"] == 2 and "Keeping v2" in msg["text"] and "noise" in msg["text"]
    out.write_text(json.dumps({"stop": "no challenger: one span only"}, indent=2))
    assert "did not run: no challenger" in result_message(State(tmp_path), job)["text"]
