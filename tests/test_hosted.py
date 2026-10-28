"""Offline tests for the hosted demo (montivo/server/hosted.py): the sign-in, the spend cap read from the run
ledgers, one job at a time and the upload limits. Everything switches on from the environment only."""
import json

import pytest
from fastapi.testclient import TestClient

import montivo.server.app as A
from montivo.server import hosted as H
from tests.test_ui import _clip, _run_folder


def ledger(d, usd):
    (d / "ledger.json").write_text(json.dumps({"total_cost_usd": usd, "stages": {}}))


@pytest.fixture
def hosted_env(monkeypatch):
    for k in ("MONTIVO_PASSWORD", "MONTIVO_BUDGET_USD", "MONTIVO_ONE_JOB", "MONTIVO_MAX_FOOTAGE_S",
              "MONTIVO_MAX_UPLOAD_MB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "test")
    return monkeypatch


def fake_jobs(monkeypatch):
    seen = []
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None: seen.append(args) or
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    return seen


def test_without_the_environment_nothing_changes(tmp_path, hosted_env):
    _run_folder(tmp_path)
    c = TestClient(A.create_app(tmp_path))
    r = c.get("/api/runs").json()
    assert r["has_key"] is True and r["budget"] is None and r["key_note"] is None
    assert c.get("/api/runs/r1").status_code == 200 and not (tmp_path / "budget.json").exists()


def test_the_password_guards_every_page_but_health_and_the_sign_in(tmp_path, hosted_env):
    _run_folder(tmp_path)
    hosted_env.setenv("MONTIVO_PASSWORD", "open sesame")
    c = TestClient(A.create_app(tmp_path))
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/runs").status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "Sign in" in c.get("/login").text
    r = c.post("/api/login", data={"password": "nope"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=1" and H.COOKIE not in r.cookies
    r = c.post("/api/login", data={"password": "open sesame"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/" and r.cookies.get(H.COOKIE)
    assert c.get("/api/runs").status_code == 200  # the client keeps the cookie
    c.post("/api/logout")
    assert c.get("/api/runs").status_code == 401


def test_the_budget_counts_only_what_the_host_spent(tmp_path, hosted_env):
    d = _run_folder(tmp_path)
    ledger(d, 0.40)  # a seeded sample: spent on the Mac before the host saw it
    b = H.Budget(tmp_path, 1.0)
    assert b.spent() == 0 and b.baseline == {"r1": 0.40}
    ledger(d, 0.55)  # an edit with a review on the host
    new = tmp_path / "runs" / "n1"
    new.mkdir()
    ledger(new, 0.30)  # a judge's own run
    assert H.Budget(tmp_path, 1.0).spent() == pytest.approx(0.45)  # the baseline file survives a restart
    late = tmp_path / "runs" / "late"
    late.mkdir()
    ledger(late, 2.0)
    b = H.Budget(tmp_path, 1.0)
    assert b.left() < 0 and b.note() and "budget" in b.note()
    b.adopt(["late"])  # copied onto the host later: python -m montivo.server.hosted adopt
    assert b.spent() == pytest.approx(0.45)
    archived = tmp_path / "runs" / "_archive" / "n1"
    archived.parent.mkdir()
    new.rename(archived)  # an archive moves the folder, the spend stays counted
    assert H.Budget(tmp_path, 1.0).spent() == pytest.approx(0.45)


def test_paid_actions_stop_at_the_cap_and_free_edits_go_on(tmp_path, hosted_env):
    d = _run_folder(tmp_path)
    ledger(d, 0.0)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "0.10")
    seen = fake_jobs(hosted_env)
    c = TestClient(A.create_app(tmp_path))
    assert c.get("/api/runs/r1").json()["budget"] == {"cap": 0.1, "spent": 0.0, "left": 0.1}
    assert c.post("/api/runs/r1/hook", json={"from": 2}).status_code == 200  # $0.07 fits in $0.10
    r = c.post("/api/runs/r1/review", json={"from": 2, "passes": 3})  # three reviews, $0.105, do not
    assert r.status_code == 409 and "$0.10 of its $0.10 budget left" in r.json()["error"]
    ledger(d, 0.095)  # what the hook A/B spent
    s = c.get("/api/runs/r1").json()
    assert s["has_key"] is False and "budget" in s["key_note"] and s["budget"]["left"] == 0.01
    r = c.post("/api/runs/r1/replan", json={"instruction": "shorter"})
    assert r.status_code == 409 and "$0 edits" in r.json()["error"]
    n = len(seen)
    assert c.post("/api/runs/r1/drop", json={"span": "s1", "from": 2}).status_code == 200  # $0: still renders
    assert len(seen) == n + 1
    assert c.post("/api/runs/r1/drop", json={"span": "s1", "from": 2, "review": 1}).status_code == 409


def test_one_job_at_a_time_over_all_runs(tmp_path, hosted_env):
    _run_folder(tmp_path)
    other = tmp_path / "runs" / "r2"
    other.mkdir()
    (other / "run.json").write_text(json.dumps({"footage": "footage/x.mp4", "brief": "b"}))
    hosted_env.setenv("MONTIVO_ONE_JOB", "1")
    st = A.State(tmp_path)
    st.jobs["r1"] = A.Job(id="j", run="r1", kind="hook", label="Hook A/B", cmd=[])
    with pytest.raises(A.HTTPException) as e:
        A.start_job(st, "r2", "drop", "Delete clip s1", ["-c", "pass"])
    assert e.value.status_code == 409 and "one job at a time" in e.value.detail


def test_clip_length_and_upload_size_limits(tmp_path, hosted_env):
    (tmp_path / "footage").mkdir()
    _clip(tmp_path / "footage" / "c.mp4", size="320x240", seconds=2.0)
    hosted_env.setenv("MONTIVO_MAX_FOOTAGE_S", "1")
    hosted_env.setenv("MONTIVO_MAX_UPLOAD_MB", "0.001")
    fake_jobs(hosted_env)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/runs", json={"footage": "footage/c.mp4", "brief": "b", "id": "n1"})
    assert r.status_code == 400 and "up to 1 s" in r.json()["error"]
    r = c.put("/api/footage/big.mp4", content=b"x" * 5000)
    assert r.status_code == 413 and not (tmp_path / "footage" / "big.mp4").exists()
    assert c.put("/api/footage/small.mp4", content=b"x" * 500).status_code == 200


def test_the_samples_cannot_be_archived_on_the_host(tmp_path, hosted_env):
    d = _run_folder(tmp_path)
    ledger(d, 0.4)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/r1/archive").status_code == 403


def test_a_judges_first_edit_on_a_sample_goes_to_their_own_copy(tmp_path, hosted_env):
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    (d / "ui").mkdir()
    (d / "ui" / "chat.jsonl").write_text("")
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    seen = fake_jobs(hosted_env)
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, "aa11")
    r = c.post("/api/runs/r1/drop", json={"span": "s1", "from": 2})
    assert r.status_code == 200 and r.json()["job"]["run"] == "r1-aa11" and seen[-1][3] == "runs/r1-aa11"
    mine = tmp_path / "runs" / "r1-aa11"
    assert (mine / "ui" / "owner").read_text() == "aa11" and (mine / "edl_v3.json").is_file()
    assert "Your own copy" in (mine / "ui" / "chat.jsonl").read_text()
    assert not (tmp_path / ".copying" / "r1-aa11").exists()
    assert c.get("/api/runs").json()["budget"]["spent"] == 0  # the copy brings the sample's ledger, not new spend
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}).json()["job"]["run"] == "r1-aa11"  # one copy per judge
    assert c.post("/api/runs/r1-aa11/drop", json={"span": "s1"}).json()["job"]["run"] == "r1-aa11"
    assert [r["id"] for r in c.get("/api/runs").json()["runs"]] == ["r1-aa11"]  # their copy instead of the sample
    other = TestClient(A.create_app(tmp_path))
    other.cookies.set(H.JUDGE, "bb22")
    assert [r["id"] for r in other.get("/api/runs").json()["runs"]] == ["r1"]  # the sample, untouched
    # another judge can neither edit nor archive this judge's copy, nor see or restore it once archived
    assert other.post("/api/runs/r1-aa11/drop", json={"span": "s1"}).status_code == 404
    assert other.post("/api/runs/r1-aa11/archive").status_code == 404
    assert c.post("/api/runs/r1-aa11/archive").status_code == 200  # their own copy may go, the sample may not
    assert c.post("/api/runs/r1/archive").status_code == 403
    assert [r["id"] for r in c.get("/api/archive").json()["runs"]] == ["r1-aa11"]
    assert other.get("/api/archive").json()["runs"] == []
    assert other.post("/api/archive/r1-aa11/restore").status_code == 404
    assert c.post("/api/archive/r1-aa11/restore").status_code == 200


def test_sign_in_gives_each_browser_a_judge_id(tmp_path, hosted_env):
    _run_folder(tmp_path)
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/login", data={"password": "pw"}, follow_redirects=False)
    assert len(r.cookies.get(H.JUDGE) or "") == 8
    old = TestClient(A.create_app(tmp_path))  # signed in before the judge cookie existed
    old.cookies.set(H.COOKIE, H.token("pw"))
    assert old.get("/api/runs").cookies.get(H.JUDGE)


def test_an_upload_never_replaces_a_clip(tmp_path, hosted_env):
    (tmp_path / "footage").mkdir()
    (tmp_path / "footage" / "c.mp4").write_bytes(b"first")
    hosted_env.setenv("MONTIVO_MAX_UPLOAD_MB", "0.001")
    c = TestClient(A.create_app(tmp_path))
    r = c.put("/api/footage/c.mp4", content=b"second")
    assert r.status_code == 200 and r.json()["path"] == "footage/c-2.mp4"
    assert (tmp_path / "footage" / "c.mp4").read_bytes() == b"first"
    assert (tmp_path / "footage" / "c-2.mp4").read_bytes() == b"second"
    # too big: rejected without touching the clip of the same name and without a half file left behind
    assert c.put("/api/footage/c.mp4", content=b"x" * 5000).status_code == 413
    assert (tmp_path / "footage" / "c.mp4").read_bytes() == b"first"
    assert sorted(p.name for p in (tmp_path / "footage").iterdir()) == ["c-2.mp4", "c.mp4"]


def test_each_judge_sees_the_seeded_clips_and_their_own_uploads(tmp_path, hosted_env):
    (tmp_path / "footage").mkdir()
    _clip(tmp_path / "footage" / "seed.mp4", size="320x240", seconds=1.0)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    fake_jobs(hosted_env)
    a, b = TestClient(A.create_app(tmp_path)), TestClient(A.create_app(tmp_path))
    a.cookies.set(H.JUDGE, "aaaa")
    b.cookies.set(H.JUDGE, "bbbb")
    assert a.put("/api/footage/mine.mp4", content=(tmp_path / "footage" / "seed.mp4").read_bytes()).status_code == 200
    assert [f["name"] for f in a.get("/api/footage").json()["files"]] == ["mine.mp4", "seed.mp4"]
    assert [f["name"] for f in b.get("/api/footage").json()["files"]] == ["seed.mp4"]
    assert b.get("/api/footage/mine.mp4").status_code == 404 and a.get("/api/footage/mine.mp4").status_code == 200
    r = b.post("/api/runs", json={"footage": "footage/mine.mp4", "brief": "b", "id": "n1"})
    assert r.status_code == 400
    r1 = b.post("/api/runs", json={"footage": "footage/seed.mp4", "brief": "b"}).json()
    (tmp_path / "runs" / r1["id"] / "run.json").write_text("{}")  # the job would write it
    r2 = a.post("/api/runs", json={"footage": "footage/mine.mp4", "brief": "b"}).json()
    assert r2["id"] != r1["id"]  # two judges in the same minute get two run ids


def test_a_judge_never_reads_another_judges_project(tmp_path, hosted_env):
    d = _run_folder(tmp_path)  # r1: a seeded sample
    ledger(d, 0.36)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")
    for rid, judge in (("ra", "aaaa"), ("rb", "bbbb")):
        own = tmp_path / "runs" / rid
        A.shutil.copytree(d, own)
        (own / "ui").mkdir(exist_ok=True)
        (own / "ui" / "owner").write_text(judge)
    clients = {}
    for judge in ("aaaa", "bbbb"):
        c = TestClient(A.create_app(tmp_path))
        c.post("/api/login", data={"password": "pw"})
        c.cookies.set(H.JUDGE, judge)
        clients[judge] = c
    a, b = clients["aaaa"], clients["bbbb"]
    for path in ("", "/file/edl_v2.json", "/log", "/heads", "/frame?t=0.5", "/export", "/events"):
        r = b.get(f"/api/runs/ra{path}")
        assert r.status_code == 404 and r.json() == {"error": "no such run"}, path
    assert b.post("/api/runs/ra/drop", json={"span": "s1"}).status_code == 404
    assert a.get("/api/runs/ra").status_code == 200 and b.get("/api/runs/rb").status_code == 200
    assert a.get("/api/runs/r1").status_code == 200 and b.get("/api/runs/r1").status_code == 200  # the sample
    assert b.get("/api/runs/nope").json() == b.get("/api/runs/ra").json()  # absent and foreign look the same
    signed_out = TestClient(A.create_app(tmp_path))
    assert signed_out.get("/api/runs/r1").status_code == 401  # the sign-in gate still comes first
