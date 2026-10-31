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
              "MONTIVO_MAX_UPLOAD_MB", "MONTIVO_MAX_UPLOADS", "MONTIVO_JUDGE_SECRET"):
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
    hosted_env.setenv("MONTIVO_BUDGET_USD", "0.20")
    seen = fake_jobs(hosted_env)
    c = TestClient(A.create_app(tmp_path))
    assert c.get("/api/runs/r1").json()["budget"] == {"cap": 0.2, "spent": 0.0, "left": 0.2}
    assert c.post("/api/runs/r1/hook", json={"from": 2}).status_code == 200  # $0.145 with the judge fits in $0.20
    ledger(d, 0.10)  # what the hook A/B spent
    r = c.post("/api/runs/r1/review", json={"from": 2, "passes": 3})  # three reviews, $0.105, do not
    assert r.status_code == 409 and "$0.10 of its $0.20 budget left" in r.json()["error"]
    ledger(d, 0.195)
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
    # under the size limit but no readable length (junk, a truncated file, a live recording): refused, no quota slot
    r = c.put("/api/footage/small.mp4", content=b"x" * 500)
    assert r.status_code == 400 and "could not read" in r.json()["error"]
    assert not (tmp_path / "footage" / "small.mp4").exists()


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
    assert [(r["id"], r["kind"]) for r in c.get("/api/runs").json()["runs"]] == [("r1-aa11", "copy")]  # their copy instead of the sample
    other = TestClient(A.create_app(tmp_path))
    other.cookies.set(H.JUDGE, "bb22")
    assert [(r["id"], r["kind"]) for r in other.get("/api/runs").json()["runs"]] == [("r1", "sample")]  # the sample, untouched
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
    jid, _, sig = (r.cookies.get(H.JUDGE) or "").partition(".")
    assert len(jid) == 8 and r.cookies.get(H.JUDGE) == H.judge_cookie(H.judge_key(tmp_path), jid)
    old = TestClient(A.create_app(tmp_path))  # signed in, but no judge cookie: the id comes with a sign-in only
    old.cookies.set(H.COOKIE, H.token("pw"))
    r = old.get("/api/runs")
    assert r.status_code == 401 and not r.cookies.get(H.JUDGE)  # (a new id per request stepped past every limit)
    assert old.post("/api/login", data={"password": "pw"}, follow_redirects=False).cookies.get(H.JUDGE)
    assert old.get("/api/runs").status_code == 200


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


def test_each_judge_has_an_upload_quota(tmp_path, hosted_env):
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    hosted_env.setenv("MONTIVO_MAX_UPLOADS", "2")
    a, b = TestClient(A.create_app(tmp_path)), TestClient(A.create_app(tmp_path))
    a.cookies.set(H.JUDGE, "aaaa")
    b.cookies.set(H.JUDGE, "bbbb")
    assert [a.put(f"/api/footage/c{i}.mp4", content=b"x" * 100).status_code for i in range(3)] == [200, 200, 429]
    assert not (tmp_path / "footage" / "c2.mp4").exists()
    assert "2 uploads per judge" in a.put("/api/footage/c3.mp4", content=b"x").json()["error"]
    assert b.put("/api/footage/c0.mp4", content=b"x" * 100).status_code == 200  # another judge has their own


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
        c.cookies.set(H.JUDGE, H.judge_cookie(H.judge_key(tmp_path), judge))
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


def test_a_slow_sample_copy_runs_off_the_event_loop_after_the_body_is_read(tmp_path, hosted_env):
    # 015 took 13 s to copy on the Modal Volume: copying before the body was read lost the edit (408) and held the loop
    import asyncio
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    seen = fake_jobs(hosted_env)
    real, threads = A.shutil.copytree, []

    def slow_copy(*a, **k):
        try:
            asyncio.get_running_loop()
            threads.append("event loop")
        except RuntimeError:
            threads.append("worker")
        return real(*a, **k)

    hosted_env.setattr(A.shutil, "copytree", slow_copy)
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, "aa11")
    r = c.post("/api/runs/r1/music", json={"track": "chill_lofi", "level_db": -22, "from": 2})
    assert r.status_code == 200 and r.json()["job"]["run"] == "r1-aa11"
    assert seen[-1][3:7] == ["runs/r1-aa11", "--track", "chill_lofi", "--level"] and seen[-1][7] == "-22"
    assert threads == ["worker"]  # copied in a worker thread, not on the loop


def test_a_judge_cookie_the_server_did_not_sign_is_another_judge(tmp_path, hosted_env):
    # code review, debugging day 1: the id is in every copy's name (runs/<sample>-<id>, and the one-job 409 named the
    # running run), so a bare id cookie let one judge act as another
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")
    fake_jobs(hosted_env)
    victim = tmp_path / "runs" / "r1-6fa922fb"
    A.shutil.copytree(d, victim)
    (victim / "ui").mkdir(exist_ok=True)
    (victim / "ui" / "owner").write_text("6fa922fb")
    owner = TestClient(A.create_app(tmp_path))
    owner.post("/api/login", data={"password": "pw"})
    owner.cookies.set(H.JUDGE, H.judge_cookie(H.judge_key(tmp_path), "6fa922fb"))
    assert owner.get("/api/runs/r1-6fa922fb").status_code == 200
    thief = TestClient(A.create_app(tmp_path))
    thief.post("/api/login", data={"password": "pw"})
    # Codex review 28/10: every judge knows the password, so a cookie signed with it is a forgery too
    for forged in ("6fa922fb", "6fa922fb.0000", "a b", "../x", H.judge_cookie("other", "6fa922fb"),
                   H.judge_cookie("pw", "6fa922fb")):
        thief.cookies.set(H.JUDGE, forged)
        r = thief.get("/api/runs/r1-6fa922fb")
        assert r.status_code == 401, forged  # not a judge the server made: sign in again
        assert thief.post("/api/runs/r1/drop", json={"span": "s1"}).status_code == 401, forged
        r = thief.post("/api/login", data={"password": "pw"}, follow_redirects=False)
        new = r.cookies.get(H.JUDGE)  # a fresh signed id instead
        assert new and new.partition(".")[0] != "6fa922fb" and new == H.judge_cookie(H.judge_key(tmp_path), new.partition(".")[0])
        thief.cookies.set(H.JUDGE, new)
        assert thief.get("/api/runs/r1-6fa922fb").status_code == 404, forged
    assert not (tmp_path / "runs" / "r1-a b").exists()


def test_a_second_copy_after_archiving_the_first_still_counts_its_spend(tmp_path, hosted_env):
    # code review, debugging day 1: the budget keyed ledgers by folder name, so runs/_archive/<id> replaced runs/<id>
    # and a judge's second copy of a sample took the archived ledger as its baseline: nothing it spent counted
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    fake_jobs(hosted_env)
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, "aa11")
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}).json()["job"]["run"] == "r1-aa11"
    mine = tmp_path / "runs" / "r1-aa11"
    ledger(mine, 0.66)  # $0.30 spent on the host
    assert c.post("/api/runs/r1-aa11/archive").status_code == 200
    assert c.get("/api/runs").json()["budget"]["spent"] == pytest.approx(0.30)
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}).json()["job"]["run"] == "r1-aa11"  # a new copy
    ledger(mine, 0.36 + 5.0)
    assert c.get("/api/runs").json()["budget"]["spent"] == pytest.approx(5.30)
    # an old budget.json (archived runs under their bare name) moves the baseline to the archived key
    b = json.loads((tmp_path / "budget.json").read_text())
    b["baseline"]["gone"] = b["baseline"].pop("_archive/r1-aa11")
    (tmp_path / "budget.json").write_text(json.dumps(b))
    (tmp_path / "runs" / "_archive" / "r1-aa11").rename(tmp_path / "runs" / "_archive" / "gone")
    assert H.Budget(tmp_path, 15).baseline["_archive/gone"] == pytest.approx(0.36)


def test_try_again_checks_what_the_job_costs_not_the_cheapest_action(tmp_path, hosted_env):
    # code review, debugging day 1: a stopped run or review loop ran again on the last cent of the budget
    est = A.retry_estimate(tmp_path, "run", ["-m", "montivo.cli", "run", "footage/x.mp4", "--brief", "b", "--review",
                                             "--out", "runs/r", "--resume"], duration=lambda p: None)
    assert est == pytest.approx(A.COST["run_fixed"] + A.COST["run_per_s"] * 95 + A.COST["review_loop"])
    assert A.retry_estimate(tmp_path, "review", ["-m", "montivo.cli", "review", "runs/r", "--passes", "3"]) == \
        pytest.approx(3 * A.COST["review"])
    assert A.retry_estimate(tmp_path, "drop", ["-m", "montivo.human", "drop", "runs/r", "--span", "s1"]) == H.MIN_PAID_USD


def test_two_paid_calls_in_requests_never_run_at_once(tmp_path, hosted_env):
    # code review, debugging day 1: the cover and post-text routes run their Spark call inside the request; two at once
    # both passed the budget check before either spent, and the run ledger's last writer dropped the other's cost
    import threading
    d = _run_folder(tmp_path)
    (d / "cut_v2.mp4").write_bytes(b"x")
    started, release = threading.Event(), threading.Event()

    class Done:
        returncode, stdout, stderr = 0, json.dumps({"platforms": {}}), ""

    def slow(*a, **k):
        started.set()
        release.wait(5)
        return Done()
    hosted_env.setattr(A.subprocess, "run", slow)
    c = TestClient(A.create_app(tmp_path))
    first = {}
    t = threading.Thread(target=lambda: first.update(r=c.post("/api/runs/r1/export/post", json={"v": 2})))
    t.start()
    assert started.wait(5)
    r = c.post("/api/runs/r1/export/cover", json={"v": 2})
    assert r.status_code == 409 and "try again" in r.json()["error"]
    release.set()
    t.join(5)
    assert first["r"].status_code == 200
    assert c.post("/api/runs/r1/export/post", json={"v": 2}).status_code == 200  # free again


def test_an_edit_the_one_job_rule_refuses_makes_no_copy_of_the_sample(tmp_path, hosted_env):
    # bug bash 28/10: judge 2 deleted a clip on sample 014 while judge 1's review ran; the 409 came after a full copy
    # (014-<judge>, 265 MB, ~13 s on the Modal Volume) that then replaced the sample on judge 2's project list
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    hosted_env.setenv("MONTIVO_ONE_JOB", "1")
    running = A.Job(id="j", run="r9-aa11", kind="review", label="Blind review", cmd=[])
    init = A.State.__init__
    hosted_env.setattr(A.State, "__init__", lambda self, root: (init(self, root), self.jobs.update({"r9-aa11": running}))[0])
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, "bb22")
    r = c.post("/api/runs/r1/drop", json={"span": "s1", "from": 2})
    assert r.status_code == 409 and "one job at a time" in r.json()["error"]
    assert not (tmp_path / "runs" / "r1-bb22").exists()
    assert [x["id"] for x in c.get("/api/runs").json()["runs"]] == ["r1"]


def test_an_upload_over_the_limit_is_refused_before_its_body_and_the_limits_are_public(tmp_path, hosted_env):
    # bug bash 28/10: a judge's 847 MB iPhone clip went up whole before the 413 (minutes on a home uplink); the
    # browser now checks size and length before it sends, so the state names the limits, and a declared length over
    # the limit is refused before a byte is read
    (tmp_path / "footage").mkdir()
    hosted_env.setenv("MONTIVO_MAX_UPLOAD_MB", "1")
    hosted_env.setenv("MONTIVO_MAX_FOOTAGE_S", "95")
    c = TestClient(A.create_app(tmp_path))
    assert c.get("/api/runs").json()["limits"] == {"footage_s": 95.0, "upload_mb": 1.0, "uploads": None}
    read = []

    def body():
        read.append(1)
        yield b"x" * 10

    r = c.put("/api/footage/big.mov", content=body(), headers={"content-length": str(2_000_000)})
    assert r.status_code == 413 and "1 MB" in r.json()["error"] and read == []
    assert c.get("/api/health").json()["limits"]["upload_mb"] == 1.0


def test_a_clip_over_the_length_limit_is_refused_at_upload_and_not_counted(tmp_path, hosted_env):
    # bug bash day 2: a 135 s clip went up, counted in the judge's 5 uploads, and only Create project said it was
    # too long; the server now probes the upload and keeps none it would refuse to run
    _clip(tmp_path / "long.mp4", size="160x120", seconds=3.0)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    hosted_env.setenv("MONTIVO_MAX_UPLOADS", "1")
    hosted_env.setenv("MONTIVO_MAX_FOOTAGE_S", "2")
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, "aaaa")
    r = c.put("/api/footage/long.mp4", content=(tmp_path / "long.mp4").read_bytes())
    assert r.status_code == 400 and "is 3 s" in r.json()["error"] and "up to 2 s" in r.json()["error"]
    assert sorted(p.name for p in (tmp_path / "footage").iterdir()) == []  # no clip, no .part, no owner entry
    _clip(tmp_path / "short.mp4", size="160x120", seconds=1.0)
    assert c.put("/api/footage/short.mp4", content=(tmp_path / "short.mp4").read_bytes()).status_code == 200


def test_health_names_no_server_path_on_the_host(tmp_path, hosted_env):
    assert "root" in TestClient(A.create_app(tmp_path)).get("/api/health").json()  # local: the path helps
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")
    r = TestClient(A.create_app(tmp_path)).get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True and "root" not in r.json()


def test_the_judge_key_is_made_once_kept_and_never_the_password(tmp_path, hosted_env):
    key = H.judge_key(tmp_path)
    assert len(key) == 64 and key != "pw" and H.judge_key(tmp_path) == key  # kept across restarts (the Volume)
    assert (tmp_path / "judge.key").stat().st_mode & 0o077 == 0
    hosted_env.setenv("MONTIVO_JUDGE_SECRET", "from-the-modal-secret")
    assert H.judge_key(tmp_path) == "from-the-modal-secret"


def test_the_upload_probe_runs_off_the_loop_and_a_hung_probe_is_refused(tmp_path, hosted_env):
    # Codex review 28/10: ffprobe ran on the event loop with no timeout, so one file that made it hang held every
    # other request (progress, Stop) until it returned
    import asyncio
    import subprocess
    seen = []

    def on_loop() -> bool:
        try:
            return asyncio.get_running_loop() is not None
        except RuntimeError:
            return False

    def hung(src, timeout=None):
        seen.append((on_loop(), timeout))
        raise subprocess.TimeoutExpired("ffprobe", timeout)

    hosted_env.setattr(A.media, "probe_duration", hung)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    hosted_env.setenv("MONTIVO_MAX_FOOTAGE_S", "95")
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, "aaaa")
    r = c.put("/api/footage/odd.mp4", content=b"\0" * 1000)
    assert r.status_code == 400 and "could not read this clip" in r.json()["error"]
    assert seen == [(False, A.UPLOAD_PROBE_S)]
    assert sorted(p.name for p in (tmp_path / "footage").iterdir()) == []  # no clip, no .part, no owner entry


def _wait(cond, s=10.0):
    import time
    t0 = time.time()
    while not cond() and time.time() - t0 < s:
        time.sleep(0.05)
    return cond()


def _state(app) -> "A.State":
    """The app's own State (the routes' closure), so a test can start a real job the routes then see."""
    stop = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/runs/{run_id}/stop")
    return next(c.cell_contents for c in stop.__closure__ if isinstance(c.cell_contents, A.State))


def test_stop_ends_the_jobs_encodes_and_frees_the_slot_after_them(tmp_path, hosted_env):
    # Codex review 28/10: Stop terminated the Python job only; its ffmpeg children kept writing into render/ while the
    # next job could already start there
    import os
    _run_folder(tmp_path)
    c = TestClient(app := A.create_app(tmp_path))
    pidf = tmp_path / "child.pid"
    code = (f"import subprocess, time; c = subprocess.Popen(['sleep', '60']); open({str(pidf)!r}, 'w').write(str(c.pid)); "
            "time.sleep(60)")
    job = A.start_job(_state(app), "r1", "render", "Render", ["-c", code])
    assert _wait(lambda: pidf.is_file() and pidf.read_text())
    child = int(pidf.read_text())
    assert c.post("/api/runs/r1/stop").status_code == 200
    assert _wait(lambda: job.ended is not None) and job.rc < 0
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)  # the encode went with the job, before its slot was free
    assert not _state(app).busy("r1")


def test_a_job_never_starts_beside_a_paid_request_call(tmp_path, hosted_env):
    # Codex review 28/10: a cover or post-text call held its own lock, but start_job never looked at it: a job could
    # start against the same balance (any run on the host) and write the same run's ledger
    (tmp_path / "runs" / "r2").mkdir(parents=True)
    _run_folder(tmp_path)
    st = A.State(tmp_path)
    st.paid = "r1"
    with pytest.raises(A.HTTPException) as e:
        A.start_job(st, "r1", "drop", "Delete", ["-c", "pass"])
    assert e.value.status_code == 409 and "Muse Spark request" in e.value.detail
    st.one_job = True
    with pytest.raises(A.HTTPException):
        A.start_job(st, "r2", "drop", "Delete", ["-c", "pass"])  # on the hosted demo, beside any run
    st.one_job, st.paid = False, None
    job = A.start_job(st, "r1", "drop", "Delete", ["-c", "import time; time.sleep(0.3)"])
    assert _wait(lambda: job.ended is not None) and job.rc == 0


def test_the_cutout_mask_waits_for_a_job_on_its_run(tmp_path, hosted_env):
    # Codex review 28/10: the mask route was the one paid request call with no check for a job on its run
    d = _run_folder(tmp_path)
    (d / "cut_v2.mp4").write_bytes(b"x")
    c = TestClient(app := A.create_app(tmp_path))
    job = A.start_job(_state(app), "r1", "render", "Render", ["-c", "import time; time.sleep(5)"])
    r = c.post("/api/runs/r1/cover/mask", json={"v": 2, "t": 1.0})
    assert r.status_code == 409 and "Render is running" in r.json()["error"]
    assert c.post("/api/runs/r1/stop").status_code == 200 and _wait(lambda: job.ended is not None)


def test_a_stopped_paid_job_holds_the_budget_for_its_call_in_flight(tmp_path, hosted_env):
    # Codex review 28/10: a call reaches the model (and is billed) before its answer and its ledger entry come back
    review = A.Job(id="j", run="r1", kind="review", label="Review", cmd=["py", "-m", "montivo.cli", "review", "runs/r1",
                                                                         "--passes", "3"], charged=0.0)
    assert A.stop_hold(tmp_path, review) == A.STOP_HOLD_USD
    review.charged = 0.2  # it recorded more than it could still spend
    assert A.stop_hold(tmp_path, review) == 0.0
    drop = A.Job(id="j", run="r1", kind="drop", label="Delete", cmd=["py", "-m", "montivo.human", "drop", "runs/r1"])
    assert A.stop_hold(tmp_path, drop) == 0.0  # a $0 edit holds nothing
    b = H.Budget(tmp_path, 1.0)
    b.hold(0.05, "r1")
    b.hold(0.0, "r1")
    assert b.spent() == 0.05 and H.Budget(tmp_path, 1.0).spent() == 0.05  # kept in budget.json


def test_the_host_sets_aside_versions_a_restart_left_without_a_cut(tmp_path, hosted_env):
    # Codex review 28/10: a container stopped mid-job never ran the job's clean-up, so an unrendered edl_vN became the
    # newest version after the restart
    d = _run_folder(tmp_path)
    (d / "cut_v2.mp4").write_bytes(b"x")
    A.create_app(tmp_path)
    assert (d / "edl_v3.json").is_file()  # a laptop's run folders are left as they are
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    A.create_app(tmp_path)
    assert not (d / "edl_v3.json").exists() and (d / "stopped" / "edl_v3-restart.json").is_file()
    assert (d / "edl_v1.json").is_file() and (d / "edl_v2.json").is_file()
