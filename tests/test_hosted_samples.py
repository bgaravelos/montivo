"""Offline: the hosted demo's shared samples and the money checks (cloud review, 29/10). The review passes are
clamped, a refused edit copies nothing, the Ask Montivo keyword reader works on the last cents, stopped jobs hold
what they could have spent, and float query parameters are finite."""
import pytest
from fastapi.testclient import TestClient

import montivo.server.app as A
from montivo.server import hosted as H
from tests.test_hosted import fake_jobs, hosted_env, ledger  # noqa: F401 (the fixture)
from tests.test_ui import _run_folder


def sample_client(tmp_path, env, judge="aa11"):
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    (d / "cut_v2.mp4").write_bytes(b"x")
    env.setenv("MONTIVO_BUDGET_USD", "15")
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, judge)
    return d, c


def test_review_passes_are_clamped_on_every_route(tmp_path, hosted_env):
    d = _run_folder(tmp_path)
    seen = fake_jobs(hosted_env)
    c = TestClient(A.create_app(tmp_path))
    for route, body in (("drop", {"span": "s1"}), ("caption", {"lines": []}), ("music", {"off": True}),
                        ("reorder", {"span": "s2", "before": "s1"}), ("review", {})):
        for bad in (400, -1, 4, "x"):
            key = "passes" if route == "review" else "review"
            r = c.post(f"/api/runs/r1/{route}", json={**body, key: bad})
            assert r.status_code == 400 and "review passes" in r.json()["error"], (route, bad)
    n = len(seen)
    assert c.post("/api/runs/r1/review", json={"passes": 3}).status_code == 200
    assert c.post("/api/runs/r1/review", json={}).status_code == 200 and seen[-1][-1] == "3"  # the default stays 3
    assert c.post("/api/runs/r1/drop", json={"span": "s1", "review": 0}).status_code == 200
    assert len(seen) == n + 3


def test_a_paid_edit_the_budget_or_key_refuses_copies_nothing(tmp_path, hosted_env):
    d, c = sample_client(tmp_path, hosted_env, "bb22")
    seen = fake_jobs(hosted_env)
    ledger(d, 0.36)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    H.Budget(tmp_path, 15.0).hold(14.995, "elsewhere")  # half a cent left: no paid call fits
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.JUDGE, "bb22")
    for route, body in (("review", {"passes": 1}), ("replan", {"instruction": "shorter"}), ("hook", {}),
                        ("drop", {"span": "s1", "review": 1}), ("style", {"prompt": "bold yellow"})):
        r = c.post(f"/api/runs/r1/{route}", json=body)
        assert r.status_code == 409 and "budget" in r.json()["error"], route
        assert not (tmp_path / "runs" / "r1-bb22").exists(), route
    hosted_env.delenv("MODEL_API_KEY")
    r = c.post("/api/runs/r1/review", json={"passes": 1})
    assert r.status_code == 409 and not (tmp_path / "runs" / "r1-bb22").exists()
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}).json()["job"]["run"] == "r1-bb22"  # $0 still copies
    assert len(seen) == 1


def test_a_stopped_job_holds_what_it_could_have_spent(tmp_path):
    def job(kind, *args):
        return A.Job(id="j", run="r1", kind=kind, label=kind, cmd=["py", *args], charged=0.0)
    ask = ["-m", "montivo.agent", "runs/r1", "--message", "music", "--from", "2", "--review", "0", "--planner", "spark"]
    assert A.stop_hold(tmp_path, job("ask", *ask, "--no-model")) == 0.0  # the keyword reader calls no model
    assert A.stop_hold(tmp_path, job("ask", *ask)) == A.STOP_HOLD_USD
    ov = ["-m", "montivo.human", "overlay", "runs/r1"]
    assert A.stop_hold(tmp_path, job("overlay", *ov, "--remove", "b1", "--from", "2", "--review", "0")) == 0.0
    assert A.stop_hold(tmp_path, job("overlay", *ov, "--set", "b1", "--at", "2.00", "--review", "0")) == 0.0
    assert A.stop_hold(tmp_path, job("overlay", *ov, "--prompt", "a cat", "--style", "auto", "--review", "0")) == 0.01
    text = ["-m", "montivo.human", "text", "runs/r1", "--title=New words", "--from", "2", "--review", "0"]
    assert A.stop_hold(tmp_path, job("text", *text)) == 0.01
    assert A.stop_hold(tmp_path, job("text", "-m", "montivo.human", "text", "runs/r1", "--title-off")) == 0.0


def test_ask_montivo_on_the_last_cents_falls_back_to_the_keyword_reader(tmp_path, hosted_env):
    seen = fake_jobs(hosted_env)
    hosted_env.setenv("MONTIVO_BUDGET_USD", "1")
    for left, model, review in ((0.03, False, "0"), (0.07, True, "0"), (1.0, True, "1")):
        root = tmp_path / f"left{left}"
        d = _run_folder(root)
        (d / "cut_v2.mp4").write_bytes(b"x")
        (d / "ui").mkdir(exist_ok=True)
        (d / "ui" / "owner").write_text("aa11")  # the judge's own project, not a sample
        if left < 1:  # $0.03: under the router's $0.056, over the $0.01 floor; $0.07: the router, not its review
            H.Budget(root, 1.0).hold(1.0 - left, "elsewhere")
        c = TestClient(A.create_app(root))
        c.cookies.set(H.JUDGE, "aa11")
        r = c.post("/api/runs/r1/ask", json={"message": "put some music on", "review": 1})
        assert r.status_code == 200, (left, r.json())
        assert ("--no-model" not in seen[-1]) == model and seen[-1][seen[-1].index("--review") + 1] == review, left


def test_the_budget_check_is_exact_to_the_cent(tmp_path):
    b = H.Budget(tmp_path, 0.105)
    b.require("Three reviews", 0.035 * 3)  # 0.10500000000000001 used to be refused with $0.105 left
    with pytest.raises(A.HTTPException):
        b.require("Three reviews", 0.1051)


def test_nan_and_inf_are_a_400_not_a_500(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    d = _run_folder(tmp_path)
    (d / "cut_v2.mp4").write_bytes(b"x")
    c = TestClient(A.create_app(tmp_path), raise_server_exceptions=False)
    for bad in ("nan", "inf", "-inf"):
        assert c.get(f"/api/runs/r1/frame?v=2&t={bad}").status_code == 400, bad
        assert c.get(f"/api/runs/r1/pauses?v=2&over={bad}").status_code == 400, bad
        assert c.get(f"/api/runs/r1/events?replay={bad}").status_code == 400, bad
    # Python's json takes NaN and Infinity in a body
    for route, body in (("cover/render", '{"v": 2, "t": NaN}'), ("preview", '{"v": 2, "t": Infinity}'),
                        ("pauses", '{"over": NaN}'), ("caption", '{"edits": [{"at": NaN, "text": "hi"}]}'),
                        ("overlay", '{"set": "b1", "at": Infinity}'),
                        ("export/bundle", '{"v": 2, "include": ["video"], "cover_t": NaN}')):
        r = c.post(f"/api/runs/r1/{route}", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 400, (route, r.status_code, r.text[:200])
