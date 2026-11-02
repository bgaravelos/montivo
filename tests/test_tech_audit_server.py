"""Offline: the server findings of the 2/11 tech audit. A job the server never saw end (a container stop mid-run) comes
back as stopped after a restart, so a new project killed before its first cut keeps its Home tile and Try again."""
import json
import os
import signal
import time
from pathlib import Path

from fastapi.testclient import TestClient

import montivo.server.app as A
from tests.test_hosted import hosted_env  # noqa: F401 (the fixture)


def _root(tmp_path):
    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"brief": "b", "footage": "footage/x.mp4"}))
    (d / "run.log").write_text("montivo run\n")
    return d


def test_a_job_cut_off_by_a_restart_comes_back_stopped(tmp_path):
    d = _root(tmp_path)
    st = A.State(tmp_path)
    job = A.start_job(st, "r1", "run", "The first cut", ["-c", "import time; time.sleep(30)"])
    try:
        rec = json.loads((d / A.JOB_FILE).read_text())
        assert rec["ended"] is None and rec["args"][-1].startswith("import time")
        # the container goes away: a new server on the same Volume, the old process never reports back
        c = TestClient(A.create_app(tmp_path))
        runs = c.get("/api/runs").json()["runs"]
        r1 = next(r for r in runs if r["id"] == "r1")
        assert not r1["has_cut"]
        assert r1["job"] and r1["job"]["rc"] == -1 and not r1["job"]["running"]  # Home's Stopped tile
        assert c.get("/api/runs/r1").json()["job"]["label"] == "The first cut"
        assert json.loads((d / A.JOB_FILE).read_text())["rc"] == -1  # a second restart does not bring it back again
    finally:
        os.killpg(job.proc.pid, signal.SIGKILL)


def test_a_finished_job_is_not_brought_back(tmp_path):
    d = _root(tmp_path)
    st = A.State(tmp_path)
    job = A.start_job(st, "r1", "run", "The first cut", ["-c", "pass"])
    for _ in range(100):  # the record is written right after job.ended is set
        if job.ended is not None and json.loads((d / A.JOB_FILE).read_text())["rc"] is not None:
            break
        time.sleep(0.05)
    assert json.loads((d / A.JOB_FILE).read_text())["rc"] == 0
    c = TestClient(A.create_app(tmp_path))
    r1 = next(r for r in c.get("/api/runs").json()["runs"] if r["id"] == "r1")
    assert r1["job"] is None


def test_live_finds_the_footage_from_another_working_directory(tmp_path, monkeypatch):
    """`montivo serve --root DIR` started elsewhere: the EDL's source path is relative to the root, not the cwd."""
    from montivo.engine import preview as P
    from tests.test_preview import _clip, _edl
    (tmp_path / "footage").mkdir()
    _clip(tmp_path / "footage" / "x.mp4", seconds=1.0)
    d = _root(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rec = P.edit_media(d, _edl("footage/x.mp4"))
    assert (d / rec["video"]).is_file()


def test_a_hung_export_command_is_stopped(tmp_path):
    import sys
    import pytest
    from fastapi import HTTPException
    t0 = time.time()
    with pytest.raises(HTTPException) as e:
        A.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0.5)
    assert e.value.status_code == 504 and time.time() - t0 < 5


def test_exports_on_a_sample_are_kept_per_judge(tmp_path, hosted_env):
    """Four exports by other judges on the same sample no longer delete the first judge's zip before the click."""
    from montivo.server import hosted as H
    from tests.test_hosted_samples import sample_client

    def fake(args, cwd=None, timeout=A.EXPORT_MAX_S):
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "a.mp4").write_bytes(b"x")
        return A.subprocess.CompletedProcess(args, 0, json.dumps({"files": [{"name": "a.mp4"}], "zip": None}), "")
    hosted_env.setattr(A, "run_bounded", fake)
    d, c = sample_client(tmp_path, hosted_env, "aa11")
    first = c.post("/api/runs/r1/export/bundle", json={"v": 2, "include": ["video"], "name": "mine"}).json()
    url = first["files"][0]["url"]
    assert "/judges/aa11/" in url and c.get(url).status_code == 200
    c.cookies.set(H.JUDGE, "bb22")
    for n in range(4):
        r = c.post("/api/runs/r1/export/bundle", json={"v": 2, "include": ["video"], "name": f"other{n}"})
        assert r.status_code == 200 and "/judges/bb22/" in r.json()["files"][0]["url"]
    c.cookies.set(H.JUDGE, "aa11")
    assert c.get(url).status_code == 200


def test_the_review_copy_leaves_no_clock_frames(tmp_path):
    from montivo.engine.review import review_copy
    from tests.test_preview import _clip
    cut = tmp_path / "cut_v1.mp4"
    _clip(cut, seconds=1.0)
    out = review_copy(cut, tmp_path / "review")
    assert out.is_file() and not (tmp_path / "review" / "clock").exists()
