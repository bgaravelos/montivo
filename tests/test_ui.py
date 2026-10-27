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


def test_replay_keeps_the_runs_own_loop():
    """A run with --review logs its loop's renders under `montivo render:` headers on the same clock (run 015): the
    replay keeps all of it, not the part before the first header plus a later loop."""
    from montivo.server.runs import parse_log, replay_script
    log = "\n".join([
        "[   0.0s] montivo run: clip.mp4 -> runs/x", "[ 130.4s] render: runs/x/cut_v1.mp4 (40 s)",
        "[ 162.8s] review: review_v1 overall 68 pass False (6 issues), $0.03", "[ 162.8s] patch v1 -> v2: drop_title",
        "[ 162.8s] montivo render: runs/x/edl_v2.json -> runs/x/cut_v2.mp4", "[ 182.6s] render: runs/x/cut_v2.mp4 (38 s)",
        "[ 217.3s] review: review_v2 overall 81 pass True (2 issues), $0.04", "[ 217.4s] review loop: 2 reviews, stop: pass",
        "[ 217.4s] done: 38.69 s cut", "[   0.0s] montivo render: runs/x/edl_v3.json -> runs/x/cut_v3.mp4",
        "[  20.0s] render: runs/x/cut_v3.mp4 (38 s)", "[   7.4s] review: review_v3 overall 87 pass True (1 issues), $0.03",
        "[  35.7s] review loop: 1 reviews, stop: pass",
    ])
    msgs = [ln["msg"] for ln in replay_script(parse_log(log))]
    assert msgs[-1].startswith("done:") and any(m.startswith("review: review_v2") for m in msgs)
    assert not any("v3" in m for m in msgs)


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


# ------------------------------------------------------------------- live progress (runs.progress)

COSTS = {"plan_s": 35, "plan_open_s": 7, "review_s": 26, "review_prep_s": 8, "image_s": 10, "render_s_per_frame": 0.02}


def _progress(log: str, **kw):
    from montivo.server.runs import parse_log, progress
    return progress(parse_log(log), costs=COSTS, **kw)


def _log(*lines):
    return "\n".join(f"[{t:7.1f}s] {m}" for t, m in lines)


def test_progress_render_segments_by_frames():
    head = [(0.0, "montivo drop: s3 from edl_v2 -> edl_v3"), (0.0, "patch v2 -> v3: drop_span s3: dropped (human)"),
            (0.0, "montivo render: runs/x/edl_v3.json -> runs/x/cut_v3.mp4"),
            (0.1, "render: 8 spans -> 3 segments (1000 frames), source a.mp4, track head")]
    p = _progress(_log(*head))
    assert (p["label"], p["stage"], p["title"], p["detail"]) == ("Delete clip", "render", "Render v3", "segment 1 of 3")
    assert p["floor"] == 0 and p["ceil"] == pytest.approx(0.8)  # the next segment's size is not known yet
    assert p["expected_s"] == 20 and p["expected_from"] == "hint"  # 1000 frames x 0.02 s
    seg1 = (9.0, "render: seg 1 1.0-20.0s (600 frames, s1+s2) track 0 crop 608x1080 x 1..2, 50 overlays")
    p = _progress(_log(*head, seg1))
    assert p["detail"] == "segment 2 of 3" and p["floor"] == pytest.approx(0.05 + 0.75 * 0.6)
    segs = [seg1, (12.0, "render: seg 2 ... (300 frames, s4) track 0"), (14.0, "render: seg 3 ... (100 frames, s5) track 0")]
    p = _progress(_log(*head, *segs))
    assert p["detail"] == "audio and mux" and p["floor"] == pytest.approx(0.8) and p["ceil"] == 0.98
    p = _progress(_log(*head, *segs, (18.0, "render: runs/x/cut_v3.mp4 (38.7 s, 26 MB) in 17.9s"),
                       (18.0, "human: v3 ready (edl_v3.json, cut_v3.mp4)")))
    assert p["finished"] and p["floor"] == p["ceil"] == 1 and p["detail"] == "cut_v3.mp4 ready"
    assert p["job_elapsed"] == 18.0


def test_progress_old_log_counts_segments_and_reads_the_ledger():
    log = _log((0.0, "montivo render: runs/x/edl_v2.json -> runs/x/cut_v2.mp4"),
               (0.1, "render: 8 spans -> 4 segments, source a.mp4, track head"),
               (5.0, "render: seg 1 ... (100 frames, s1) track 0"))
    ledger = {"entries": [{"stage": "render", "seconds": 18.0}, {"stage": "render", "seconds": 22.0}]}
    p = _progress(log, ledger=ledger)
    assert p["floor"] == pytest.approx(0.05 + 0.75 / 4) and p["ceil"] == pytest.approx(0.05 + 0.75 / 2)
    assert (p["expected_s"], p["expected_from"]) == (20.0, "ledger")
    assert p["stage_elapsed"] == pytest.approx(4.9)


def test_progress_plan_expects_its_planner():
    p = _progress(_log((0.0, 'montivo replan: edl_v3 + "shorter" -> edl_v4 (target 30 s, meta-models/muse-spark-open-1026)'),
                       (0.1, "spark: plan -> meta-models/muse-spark-open-1026 ...")), label="Ask Montivo (Muse Spark Open)")
    assert (p["stage"], p["detail"], p["expected_s"], p["version"]) == ("plan", "Muse Spark Open", 7, 4)
    ledger = {"entries": [{"stage": "spark.plan", "model": "muse-spark-1.3", "seconds": 39.6},
                          {"stage": "spark.plan", "model": "meta-models/muse-spark-open-1026", "seconds": 6.8}]}
    p = _progress(_log((0.0, "plan: speech rate 2.39 words/s"), (0.0, "spark: plan -> muse-spark-1.3 ...")), ledger=ledger)
    assert (p["detail"], p["expected_s"], p["expected_from"]) == ("Muse Spark 1.3", 39.6, "ledger")
    p = _progress(_log((0.0, "spark: plan -> muse-spark-1.3 ..."), (35.3, "spark: plan: completed, 1 in / 2 out, $0.05, 35.29s")))
    assert p["floor"] == 1 and p["detail"] == "Muse Spark 1.3, 35.29s"


def test_progress_review_prep_then_call():
    head = [(0.0, "review: review_v2: facts, review copy and upload ...")]
    p = _progress(_log(*head))
    assert (p["label"], p["title"], p["expected_s"]) == ("Review loop", "Review v2", 34)
    assert p["ceil"] == pytest.approx(8 / 34, abs=1e-3)  # the bar waits at the end of the prep until the call starts
    call = [(8.0, "review: review_v2 facts:\n1. Silence gaps: none."), (8.0, "spark: review_v2 -> muse-spark-1.3 + video ...")]
    p = _progress(_log(*head, *call))
    assert p["floor"] == pytest.approx(8 / 34, abs=1e-3) and p["ceil"] == 0.97 and p["detail"] == "Muse Spark 1.3, blind"
    p = _progress(_log(*head, *call, (33.0, "review: review_v2 overall 81 pass True (2 issues), $0.0383"),
                       (33.0, "review loop: 1 reviews, stop: pass; scores v2 81; $0.0383")))
    assert p["finished"] and p["detail"] == "scored 81, pass"
    assert p["steps"] == []  # one stage so far: it is the current one, not a finished step


def test_progress_takes_the_last_process_and_lists_steps():
    log = _log((0.0, "montivo run: a.mp4 -> runs/x"), (20.0, "ingest: uploaded proxy as f in 9s"),
               (30.0, "voice: 2 turns, 117 words, speakers ['A'], $0.0034, 15.4s"), (40.0, "done: 38 s cut"),
               (0.0, "hook a/b: rendering edl_v4.json -> cut_v4.mp4"),
               (1.0, 'hook a/b: v4 opens on s1 "Hi."; v5 opens on s2 "for the Meta" (the planner\'s payoff)'))
    p = _progress(log)
    assert (p["label"], p["title"], p["detail"], p["version"]) == ("Hook A/B", "Hook A/B", "v5 opens on s2", 5)
    assert p["job_elapsed"] == 1.0 and not p["finished"]
    p = _progress(log.split("\n[    0.0s] hook")[0])
    assert p["finished"] and [s["title"] for s in p["steps"]] == ["Ingest"] and p["steps"][0]["seconds"] == 20.0
    assert p["title"] == "Voice · SAM · Watch" and p["detail"] == "Voice ✓ · SAM · Watch" and p["floor"] == 1  # done: ends it
    p = _progress(log.split("\n[   40.0s] done")[0])
    assert not p["finished"] and p["floor"] == pytest.approx(1 / 3) and p["expected_s"] is None  # no estimate, one of three


def test_job_progress_reads_only_the_jobs_lines(tmp_path):
    import time

    from montivo.server.app import Job, State, job_progress
    d = _run_folder(tmp_path)
    old = _log((0.0, "montivo run: a.mp4 -> runs/r1"), (99.0, "done: 38 s cut"))
    (d / "run.log").write_text(old + "\n" + _log((0.0, "montivo caption: 1 line(s) in edl_v3 -> edl_v4"),
                                                  (0.1, "render: 8 spans -> 2 segments (500 frames), source a.mp4, track head")) + "\n")
    job = Job(id="j", run="r1", kind="caption", label="Edit 1 caption line", cmd=[], started=time.time() - 3,
              log_pos=len(old.encode()) + 1)
    p = job_progress(State(tmp_path), job)
    assert p["active"] and p["label"] == "Edit 1 caption line" and p["title"] == "Render v4" and p["rate"] == 1.0
    assert p["job_elapsed"] == pytest.approx(3, abs=0.5)
    job.ended, job.rc = time.time(), 0
    assert not job_progress(State(tmp_path), job)["active"]
