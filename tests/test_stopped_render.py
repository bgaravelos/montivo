"""A job stopped or failed in its render must not leave a version without a cut (debugging day 1, 29/10: the Stop
button during a human edit's render left edl_vN, which the next edit then took as its base and its number)."""
import json
import time

import pytest

from montivo import human
from montivo.server.app import State, set_aside_unrendered, start_job


def _run(tmp_path, n_cut: int, n_edl: int):
    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"footage": "footage/x.mp4", "brief": "b"}))
    for n in range(1, n_edl + 1):
        (d / f"edl_v{n}.json").write_text(json.dumps({"spans": [], "version": n}))
    for n in range(1, n_cut + 1):
        (d / f"cut_v{n}.mp4").write_bytes(b"x")
    return d


def test_set_aside_unrendered_keeps_the_plan_and_earlier_versions(tmp_path):
    d = _run(tmp_path, n_cut=2, n_edl=4)
    assert set_aside_unrendered(d, after=2, job_id="j1") == [3, 4]
    assert sorted(p.name for p in d.glob("edl_v*.json")) == ["edl_v1.json", "edl_v2.json"]
    assert (d / "stopped" / "edl_v3-j1.json").is_file()
    assert human.next_version(d) == 3
    d2 = _run(tmp_path / "new", n_cut=0, n_edl=1)  # a new run stopped before its first render keeps its plan
    assert set_aside_unrendered(d2, after=0, job_id="j") == [] and (d2 / "edl_v1.json").is_file()


def test_a_stopped_job_leaves_no_version_without_a_cut(tmp_path):
    d = _run(tmp_path, n_cut=2, n_edl=2)
    st = State(tmp_path)
    # the job writes edl_v3 and dies in its "render" (exit 143, what SIGTERM's handler gives)
    code = f"import pathlib, sys; pathlib.Path({str(d / 'edl_v3.json')!r}).write_text('{{}}'); sys.exit(143)"
    job = start_job(st, "r1", "drop", "Drop s2", ["-c", code])
    for _ in range(100):
        if job.ended is not None:
            break
        time.sleep(0.05)
    assert job.rc == 143
    assert not (d / "edl_v3.json").exists() and (d / "stopped" / f"edl_v3-{job.id}.json").is_file()


def test_a_render_that_fails_removes_its_edl(tmp_path, monkeypatch):
    d = _run(tmp_path, n_cut=1, n_edl=1)
    edl = d / "edl_v2.json"
    edl.write_text("{}")

    def boom(*a, **kw):
        raise RuntimeError("ffmpeg failed")
    monkeypatch.setattr(human, "render_run", boom)
    with pytest.raises(RuntimeError):
        human._finish(d, edl, 2, 0, 0.15, None, print)
    assert not edl.exists()


def test_no_job_starts_while_an_export_renders_in_the_run(tmp_path):
    # code review, debugging day 1: a "clean" or feed-aspect export renders into the run's render/ folder, which a
    # job's render empties first; start_job did not know an export was running
    from fastapi import HTTPException
    d = _run(tmp_path, n_cut=1, n_edl=1)
    st = State(tmp_path)
    st.exporting["r1"] = 1
    with pytest.raises(HTTPException) as e:
        start_job(st, "r1", "drop", "Drop s2", ["-c", "pass"])
    assert e.value.status_code == 409 and "export" in e.value.detail
    assert not (d / "ui").exists() or not list((d / "ui").glob("job-*"))
