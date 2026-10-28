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


# ------------------------------------------------------------------- Home cards (runs.project_card)

def test_project_card_shows_the_version_the_editor_opens(tmp_path):
    import json

    from montivo.server.runs import list_runs
    d = _run_folder(tmp_path)  # v1 plan, v2 a human re-plan, v3 a hook A/B
    for n in (1, 2, 3):
        (d / f"cut_v{n}.mp4").write_bytes(b"")
    v1 = json.loads((d / "edl_v1.json").read_text())
    v1.update(title_card_text="Raw In, Reviewed Out", render={"duration_s": 41.5, "title": {"seconds": 1.5}})
    (d / "edl_v1.json").write_text(json.dumps(v1))
    for n, overall, ok in ((1, 76, False), (2, 88, True)):
        (d / f"review_v{n}.json").write_text(json.dumps({"cut": f"runs/r1/cut_v{n}.mp4", "review": {"overall": overall, "pass": ok}}))
    (d / "review_v3.json").write_text(json.dumps({"cut": "runs/r1/cut_v3_polish.mp4", "review": {"overall": 99, "pass": True}}))

    card = list_runs(tmp_path / "runs")[0]["card"]
    assert card["shown"] == 2  # the human re-plan; v3's hook A/B has no verdict yet
    assert [x["overall"] for x in card["scores"]] == [76, 88]  # review_v3 reviewed another file
    assert card["score"] == {"v": 2, "overall": 88, "pass": True} and card["score_is_shown"]

    v3 = json.loads((d / "edl_v3.json").read_text())
    v3["patches"][-1]["result"] = {"kept": 1}
    (d / "edl_v3.json").write_text(json.dumps(v3))
    card = list_runs(tmp_path / "runs")[0]["card"]
    assert card["shown"] == 2  # the A/B kept v1, older than the human edit: the newest of the two wins
    v2 = json.loads((d / "edl_v2.json").read_text())
    v2["patches"][-1]["source"] = "review"
    (d / "edl_v2.json").write_text(json.dumps(v2))
    card = list_runs(tmp_path / "runs")[0]["card"]
    assert card["shown"] == 1 and card["title_card_text"] == "Raw In, Reviewed Out"
    assert card["poster_t"] == pytest.approx(1.5 + 40 / 3, abs=0.05) and card["duration_s"] == 41.5
    assert card["score"]["v"] == 1 and not card["score"]["pass"]


def test_archive_moves_and_restores(tmp_path):
    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    d = _run_folder(tmp_path)
    (tmp_path / "runs" / "r1.stdout.log").write_text("x")
    (tmp_path / "runs" / "r10.stdout.log").write_text("y")  # another run's log stays put
    c = TestClient(create_app(tmp_path))
    assert c.post("/api/runs/r1/archive").json() == {"archived": "r1"}
    assert not d.exists() and (tmp_path / "runs" / "_archive" / "r1" / "edl_v2.json").is_file()
    assert (tmp_path / "runs" / "_archive" / "r1.stdout.log").is_file() and (tmp_path / "runs" / "r10.stdout.log").is_file()
    assert c.get("/api/runs").json()["runs"] == []  # _archive has no run.json: not a project
    assert [r["id"] for r in c.get("/api/archive").json()["runs"]] == ["r1"]
    assert c.get("/api/runs/r1").status_code == 404
    _run_folder(tmp_path)  # a new r1 in the way
    assert c.post("/api/archive/r1/restore").status_code == 409
    assert c.post("/api/runs/r1/archive").status_code == 409  # and archiving it would collide
    import shutil
    shutil.rmtree(d)
    assert c.post("/api/archive/r1/restore").json() == {"restored": "r1"}
    assert (d / "edl_v2.json").is_file() and (tmp_path / "runs" / "r1.stdout.log").is_file()
    assert c.get("/api/archive").json()["runs"] == []
    assert c.post("/api/archive/.r1/restore").status_code == 404  # not a run folder name


# ------------------------------------------------------------------- New project form, export presets

def _clip(path, size="1080x1920", seconds=1.0):
    import subprocess
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


def test_new_project_form_reads_the_clip_and_passes_the_toggles(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import montivo.server.app as A
    (tmp_path / "footage").mkdir()
    _clip(tmp_path / "footage" / "c.mp4", size="320x240", seconds=2.0)
    c = TestClient(A.create_app(tmp_path))
    f = c.get("/api/footage").json()["files"][0]
    assert f["name"] == "c.mp4" and f["duration_s"] == pytest.approx(2.0, abs=0.1)
    assert c.get("/api/footage/c.mp4").status_code == 200 and c.get("/api/footage/x.mp4").status_code == 404
    assert c.get("/api/footage/..%2Frun.json").status_code == 404
    seen = {}
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None: seen.setdefault("args", args) and
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    monkeypatch.setenv("MODEL_API_KEY", "test")
    body = {"footage": "footage/c.mp4", "brief": "b", "id": "n1", "punch": True, "hook_ab": True}
    assert c.post("/api/runs", json=body).status_code == 200
    assert "--punch" in seen["args"] and "--review" in seen["args"] and "--hook-ab" in seen["args"]
    seen.clear()
    assert c.post("/api/runs", json={**body, "id": "n2", "review": False}).status_code == 200
    assert "--hook-ab" not in seen["args"] and "--review" not in seen["args"]  # the A/B needs reviewed cuts


def test_export_preset_720(tmp_path):
    import subprocess

    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    d = _run_folder(tmp_path)
    _clip(d / "cut_v1.mp4")
    c = TestClient(create_app(tmp_path))
    r = c.get("/api/runs/r1/export?v=1&preset=720")
    assert r.status_code == 200 and "720p" in r.headers["content-disposition"]
    out = d / "ui" / "cache" / "export_v1_720.mp4"
    dims = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert dims == "720,1280"
    assert c.get("/api/runs/r1/export?v=1&preset=4k").status_code == 400
    assert c.get("/api/runs/r1/export?v=1").headers["content-disposition"].endswith('-v1.mp4"')


def test_run_hook_ab_needs_review(tmp_path, capsys):
    from montivo.cli import main
    clip = tmp_path / "x.mp4"
    clip.write_bytes(b"")
    with pytest.raises(SystemExit):
        main(["run", str(clip), "--brief", "b", "--hook-ab"])
    assert "--hook-ab compares two reviewed cuts" in capsys.readouterr().err


# ------------------------------------------------------------------- batch B: text, reorder, pin (montivo.human)

def _human_run(tmp_path, monkeypatch):
    """A run folder with make_edl's v1, its Voice words, two speakers and a head track; renders are stubbed."""
    import json

    import montivo.human as H
    from tests.test_review import make_edl, voice_words
    d = tmp_path / "runs" / "h1"
    (d / "voice").mkdir(parents=True)
    (d / "track" / "head").mkdir(parents=True)
    edl, _ = make_edl()
    edl.update(title_card_text="Raw In", reframe_concept="head")
    edl["spans"][1]["callout"] = "Four Meta models"
    (d / "edl_v1.json").write_text(json.dumps(edl))
    (d / "voice" / "voice.json").write_text(json.dumps({"words": voice_words(), "speakers": ["A", "B"]}))
    (d / "track" / "head" / "tracks.json").write_text(json.dumps({"objects": {"0": [], "3": []}}))
    monkeypatch.setattr(H, "_finish", lambda run_dir, edl_path, n, *a: {"version": n, "edl": edl_path.name})
    return d


def test_human_text_edits_callouts_and_title(tmp_path, monkeypatch):
    import json

    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    out = H.text(d, {"s2": "", "s3": "Raw in, reviewed out"}, title_card=False)
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert out["version"] == 2 and v2["version"] == 2 and v2["title_card"] is False
    assert [s.get("callout") or "" for s in v2["spans"]] == ["", "", "Raw in, reviewed out"]
    ops = [f["op"] for f in v2["patches"][-1]["fixes"]]
    assert ops == ["callout_text", "callout_text", "title_off"] and v2["patches"][-1]["action"] == "text"
    H.text(d, title="Raw In, Reviewed Out", from_v=2)
    v3 = json.loads((d / "edl_v3.json").read_text())
    assert v3["title_card_text"] == "Raw In, Reviewed Out" and v3["title_card"] is True  # new words turn it back on
    with pytest.raises(SystemExit):
        H.text(d, {"s3": "Raw in, reviewed out"}, from_v=2)  # no change
    with pytest.raises(SystemExit):
        H.text(d, {"s9": "x"})


def test_human_reorder_moves_a_clip_and_keeps_roles_by_position(tmp_path, monkeypatch):
    import json

    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    H.reorder(d, "s1", None)  # the hook to the end
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert [s["id"] for s in v2["spans"]] == ["s2", "s3", "s1"]
    assert [s["role"] for s in v2["spans"]] == ["hook", "body", "payoff"] and v2["spans"][2]["planner_role"] == "hook"
    assert [s["order"] for s in v2["spans"]] == [0, 1, 2] and "moved to the end" in v2["patches"][-1]["fixes"][0]["note"]
    H.reorder(d, "s1", "s2", from_v=2)
    assert [s["id"] for s in json.loads((d / "edl_v3.json").read_text())["spans"]] == ["s1", "s2", "s3"]
    with pytest.raises(SystemExit):
        H.reorder(d, "s1", "s2", from_v=3)  # already there


def test_human_cut_splits_a_clip_around_deleted_words(tmp_path, monkeypatch):
    import json

    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["spans"][2]["callout"] = "Raw in"
    edl["caption_overrides"] = [{"span": "s3", "src_t": 31.81, "text": "W16 w17", "why": "human"},  # word 6
                                {"span": "s3", "src_t": 31.21, "text": "gone", "why": "human"}]  # word 4, deleted
    (d / "edl_v1.json").write_text(json.dumps(edl))
    out = H.cut(d, [("s3", 4, 4)])
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert out["version"] == 2 and v2["version"] == 2 and v2["patches"][-1]["action"] == "cut"
    sp = v2["spans"]
    assert [(s["id"], s["word_from"], s["word_to"]) for s in sp] == [("s1", 0, 3), ("s2", 6, 8), ("s3", 2, 3), ("s4", 5, 6)]
    assert [s["role"] for s in sp] == ["hook", "body", "body", "payoff"] and [s["order"] for s in sp] == [0, 1, 2, 3]
    assert sp[2]["t_out"] == 31.2 and sp[3]["t_in"] == 31.5 and sp[3]["words"] == "w15 w16"
    assert sp[2]["callout"] == "Raw in" and sp[3]["callout"] == "" and not sp[3]["contiguous_with_previous"]
    assert v2["caption_overrides"] == [{"span": "s4", "src_t": 31.81, "text": "W16 w17", "why": "human"}]
    fix = v2["patches"][-1]["fixes"][0]
    assert fix["op"] == "split_span" and fix["args"]["ids"] == ["s3", "s4"] and "'w14'" in fix["note"]
    assert abs(v2["runtime_s"] - round(1.2 + 0.9 + 0.6 + 0.6, 2)) < 1e-6


def test_human_cut_settles_a_split_edge_on_the_dip_not_back_in_the_deleted_word(tmp_path, monkeypatch):
    import json

    import montivo.human as H
    from tests.test_edges import SynthAudio
    d = _human_run(tmp_path, monkeypatch)
    # s3 = turn 1 words 2-6 (30.6-32.1); w14 (31.2-31.5) is deleted. Its sound really runs 31.3-31.44 (Voice late, as
    # on run 015), a 30 ms dip, then w15; the pause before it (31.18-31.3) would let the edge pass widen back over it
    audio = SynthAudio([(30.6, 31.18), (31.3, 31.44), (31.47, 32.1)])
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: audio))
    H.cut(d, [("s3", 4, 4)])
    s4 = json.loads((d / "edl_v2.json").read_text())["spans"][3]
    assert s4["id"] == "s4" and 31.43 < s4["t_in"] < 31.49
    assert "dip between the words" in json.loads((d / "edl_v2.json").read_text())["patches"][-1]["fixes"][-1]["note"]


def test_human_trim_holds_the_edge_out_of_the_words_it_removed(tmp_path, monkeypatch):
    import json

    import montivo.human as H
    from tests.test_edges import SynthAudio
    d = _human_run(tmp_path, monkeypatch)
    # s1 = turn 0 words 0-3 (10.0-11.2), trimmed to end on w01 (10.6): one run of speech into w02 w03 (a 30 ms dip
    # at 10.6), then a pause at 10.95. The edge pass would widen the clipped edge to that pause, keeping w02 w03
    audio = SynthAudio([(10.1, 10.59), (10.62, 10.95), (11.3, 12.0)])
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: audio))
    H.trim(d, "s1", "out", 1)
    v2 = json.loads((d / "edl_v2.json").read_text())
    s1 = v2["spans"][0]
    assert s1["word_to"] == 1 and 10.55 < s1["t_out"] < 10.66
    assert "held out of the removed words" in v2["patches"][-1]["fixes"][0]["note"]
    # the other edge keeps the place an earlier edit gave it (trim_at puts it back on the Voice boundary, 10.0)
    assert s1["t_in"] == 10.0 and "stays where it was" not in v2["patches"][-1]["fixes"][0]["note"]
    v2["spans"][2]["t_in"] = 30.65  # s3 = turn 1 words 2-6, its start tuned off the Voice boundary (30.6)
    (d / "edl_v2.json").write_text(json.dumps(v2))
    H.trim(d, "s3", "out", 4, from_v=2)
    v3 = json.loads((d / "edl_v3.json").read_text())
    assert v3["spans"][2]["t_in"] == 30.65 and "the in edge stays where it was" in v3["patches"][-1]["fixes"][0]["note"]


def test_human_cut_drops_and_trims_in_one_version(tmp_path, monkeypatch):
    import json

    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    H.cut(d, [("s1", 3, 3), ("s2", 6, 8), ("s3", 2, 3)])  # the end of s1, all of s2, the start of s3
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert [(s["id"], s["word_from"], s["word_to"]) for s in v2["spans"]] == [("s1", 0, 2), ("s3", 4, 6)]
    assert sorted(f["op"] for f in v2["patches"][-1]["fixes"]) == ["drop_span", "trim_span", "trim_span"]
    assert [s["role"] for s in v2["spans"]] == ["hook", "payoff"]
    for bad in ([("s1", 1, 3)],  # keeps one word
                [("s1", 0, 3), ("s2", 6, 8), ("s3", 2, 6)],  # every clip
                [("s1", 3, 5)], [("s9", 0, 1)]):  # words outside the clip, no such clip
        with pytest.raises(SystemExit):
            H.cut(d, bad)
    assert not (d / "edl_v3.json").exists()


def test_cut_endpoint_builds_its_command(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import montivo.server.app as A
    _run_folder(tmp_path)
    seen = []
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None: seen.append((kind, label, args, chat)) or
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/runs/r1/cut", json={"words": [{"span": "s2", "from": 16, "to": 16}, {"span": "s3", "from": 0, "to": 2}],
                                         "from": 2, "text": "and"})
    assert r.status_code == 200
    kind, label, args, chat = seen[-1]
    assert kind == "cut" and label == "Delete words"
    assert args[2:4] == ["cut", "runs/r1"] and args[4:8] == ["--words", "s2:16-16", "--words", "s3:0-2"]
    assert args[-4:] == ["--from", "2", "--review", "0"] and chat["text"] == "Delete 4 words from v2: “and”."
    for body in ({}, {"words": []}, {"words": [{"span": "s2; rm", "from": 1, "to": 2}]}, {"words": [{"span": "s2", "from": 3, "to": 1}]},
                 {"words": [{"span": "s2"}]}):
        assert c.post("/api/runs/r1/cut", json=body).status_code == 400
    assert c.post("/api/runs/r1/cut", json={"words": [{"span": "s2", "from": 1, "to": 2}], "review": 1}).status_code == 409


def test_human_pin_sets_and_drops_a_speaker_head(tmp_path, monkeypatch):
    import json

    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    H.pin(d, "A", "3")
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert v2["reframe_speaker_ids"] == {"A": "3"} and v2["patches"][-1]["fixes"][0]["op"] == "pin_speaker"
    H.pin(d, "A", None, from_v=2)
    assert "reframe_speaker_ids" not in json.loads((d / "edl_v3.json").read_text())
    for spk, head in (("C", "3"), ("A", "7")):
        with pytest.raises(SystemExit):
            H.pin(d, spk, head)


def test_text_reorder_pin_endpoints_build_their_commands(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import montivo.server.app as A
    _run_folder(tmp_path)
    seen = []
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None: seen.append((kind, label, args)) or
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/r1/text", json={"callouts": {"s2": "", "s3": "Raw in"}, "title_card": False}).status_code == 200
    kind, label, args = seen[-1]
    assert kind == "text" and "--callout=s2=" in args and "--callout=s3=Raw in" in args and "--title-off" in args
    r = c.post("/api/runs/r1/text", json={"title": "New words"})
    assert r.status_code == 409 and "Muse Image" in r.json()["error"]  # new title words call Muse Image
    assert c.post("/api/runs/r1/text", json={}).status_code == 400
    assert c.post("/api/runs/r1/text", json={"callouts": {"x; rm": "a"}}).status_code == 400
    assert c.post("/api/runs/r1/reorder", json={"span": "s3", "before": None}).status_code == 200
    assert "--end" in seen[-1][2] and "--before" not in seen[-1][2]
    assert c.post("/api/runs/r1/reorder", json={"span": "s3", "before": "s1"}).status_code == 200 and "--before" in seen[-1][2]
    assert c.post("/api/runs/r1/pin", json={"speaker": "A", "head": "3"}).status_code == 200
    assert seen[-1][2][4:8] == ["--speaker", "A", "--head", "3"]
    assert c.post("/api/runs/r1/pin", json={"speaker": "A", "head": None}).status_code == 200 and "none" in seen[-1][2]
    assert c.post("/api/runs/r1/pin", json={"speaker": "A; x", "head": "3"}).status_code == 400


def test_heads_lists_speakers_and_thumbnails_a_head(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    d = _run_folder(tmp_path)
    (d / "voice").mkdir()
    (d / "voice" / "voice.json").write_text(json.dumps({"speakers": ["A", "B"], "words": [{"speaker": "A"}, {"speaker": "B"}, {"speaker": "B"}]}))
    (d / "speakers.json").write_text(json.dumps({"speakers": {"A": {"ids": ["1"], "method": "microphone", "who": "left"}},
                                                 "heads": {"1": {"speaker": "A"}}}))
    (d / "track" / "head").mkdir(parents=True)
    (d / "track" / "head" / "tracks.json").write_text(json.dumps({"source": {"width": 320, "height": 240}, "objects": {
        "1": [{"f": 0, "t": 0.2, "box": [10, 10, 40, 50]}, {"f": 1, "t": 0.4, "box": [10, 10, 80, 100]}], "2": []}}))
    _clip(d / "track" / "track_source_5fps.mp4", size="320x240", seconds=1.0)
    c = TestClient(create_app(tmp_path))
    h = c.get("/api/runs/r1/heads").json()
    assert [(s["id"], s["ids"], s["words"]) for s in h["speakers"]] == [("A", ["1"], 1), ("B", [], 2)]
    assert [(x["id"], x["t"], x["speaker"]) for x in h["heads"]] == [("1", 0.4, "A")]  # 2 has no box: not offered
    r = c.get("/api/runs/r1/head/1.jpg")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert c.get("/api/runs/r1/head/2.jpg").status_code == 404


def test_a_gated_review_serves_its_reasons_and_measured_issues(tmp_path):
    import json

    from montivo.server.runs import versions
    d = _run_folder(tmp_path)
    issue = {"t_s": 3.0, "t_end_s": 3.0, "criterion": "pacing", "severity": "major", "what": "x", "fix_op": "drop_span"}
    gi = dict(issue, t_s=17.42, criterion="no_cut_mid_word", fix_op="move_cut", check="edge_word")
    gate = {"blocked": True, "reasons": ['"Montivo" at the cut at 17.42 s not heard', "reviewer's major still open: pacing at 3.00 s"],
            "checks": ['"Montivo" at the cut at 17.42 s not heard'], "issues": [gi],
            "open_majors": [{"t_s": 3.0, "criterion": "pacing", "severity": "major"}]}
    (d / "review_v1.json").write_text(json.dumps({"name": "review_v1", "pass_gate": gate,
                                                   "review": {"overall": 88, "pass": False, "reviewer_pass": True, "issues": [issue]},
                                                   "fixes": [{"op": "move_cut", "target": "s4", "issue": 1, "check": "edge_word"}]}))
    (d / "review_v2.json").write_text(json.dumps({"name": "review_v2", "review": {"overall": 90, "pass": True, "issues": []}}))
    e = json.loads((d / "edl_v2.json").read_text())
    e["patches"][-1]["fixes"][0]["check"] = "edge_word"
    (d / "edl_v2.json").write_text(json.dumps(e))
    v1, v2, _ = versions(d)
    r = v1["review"]
    assert not r["pass"] and r["reviewer_pass"] and r["gate"]["blocked"]
    assert r["gate"]["checks"] == gate["checks"] and r["gate"]["open_majors"][0]["criterion"] == "pacing"
    assert r["gate"]["issues"][0]["k"] == 1 and r["gate"]["issues"][0]["check"] == "edge_word"  # numbered after the reviewer's
    assert r["fixes"][0]["check"] == "edge_word" and v2["origin"]["fixes"][0]["check"] == "edge_word"
    assert v2["review"]["gate"] is None and v2["review"]["reviewer_pass"] is True  # written before the gate
    gate = {"blocked": True, "reasons": gate.pop("checks"), "issues": [gi]}  # a round-3 gate: only measured reasons
    (d / "review_v1.json").write_text(json.dumps({"name": "review_v1", "pass_gate": gate, "review": {"overall": 88, "pass": False, "issues": [issue]}}))
    g = versions(d)[0]["review"]["gate"]
    assert g["checks"] == g["reasons"] and g["open_majors"] == []


def test_the_ui_page_is_never_cached_and_its_hashed_assets_are(tmp_path):
    # after a deploy a cached index.html asked for the old asset names, so the old UI stayed on screen (28/10)
    import re

    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    c = TestClient(create_app(tmp_path))
    page = c.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    js = re.search(r'assets/[^"]+\.js', page.text).group(0)
    assert "immutable" in c.get(f"/{js}").headers["cache-control"]
    # Modal stamps every file 1970-01-01 and the page keeps its size, so an ETag never changed across deploys: no
    # validators on the page, and a conditional request still gets the whole page
    assert "etag" not in page.headers and "last-modified" not in page.headers
    again = c.get("/", headers={"If-None-Match": '"anything"', "If-Modified-Since": "Thu, 01 Jan 1970 00:00:00 GMT"})
    assert again.status_code == 200 and js in again.text


def test_a_refused_edit_says_why_in_the_chat(tmp_path):
    # bug bash 28/10: "Remove pauses failed (exit 1). The log has the details." where the edit only said "no pause over
    # 0.6 s in edl_v9" (and "punch-ins are already on", "nothing to change"); the chat now carries that line
    from montivo.server.app import Job, State, result_message
    _run_folder(tmp_path)
    out = tmp_path / "job.out"
    out.write_text("[ 0.2s] montivo pauses: reading edl_v9\nno pause over 0.6 s in edl_v9\n")
    job = Job(id="j", run="r1", kind="pauses", label="Remove pauses", cmd=[], out=str(out), rc=1)
    assert result_message(State(tmp_path), job)["text"] == \
        "Remove pauses: no pause over 0.6 s in edl_v9. Nothing was charged."
    out.write_text("Traceback (most recent call last):\n  File \"x.py\", line 1\nKeyError: 'spans'\n")
    assert "failed (exit 1). The log has the details" in result_message(State(tmp_path), job)["text"]
