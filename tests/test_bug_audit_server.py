"""Offline: the server bugs of the 1/11 bug audit. The suggest routes leave a judge's Live draft on the sample, a draft
on a picked older version commits from it, a sample's failed first commit takes the draft to the judge's copy, Try
again keeps a Live Ask's draft and refuses a draft commit, job ids are unique within a second, header-style title
words are $0 and Monti's context fields are capped."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import montivo.server.app as A
from montivo.server import draft
from montivo.server import hosted as H
from tests.test_draft import TALK, _client, _fake_jobs, _run
from tests.test_hosted import fake_jobs, hosted_env  # noqa: F401 (the fixture)
from tests.test_hosted_samples import sample_client


def _open_draft(d, judge):
    fd = draft.folder(d, judge)
    fd.mkdir(parents=True)
    (fd / "state.json").write_text(json.dumps({"id": "dr1", "base": 2, "ops": [{"op": "drop", "args": {"span": "s1"}}]}))
    (fd / "edl.json").write_text(json.dumps({"spans": [], "runtime_s": 41}))
    return fd


@pytest.mark.parametrize("route", ["style", "title"])
def test_a_suggestion_on_a_sample_leaves_the_judges_draft_there(tmp_path, hosted_env, route):
    """The editor ignores the suggest routes' "run" and stays on the sample: a draft moved into the copy was lost (the
    next draft edit started a new draft on the sample, and that one was committed)."""
    import montivo.engine.look as L
    import montivo.engine.titles as T
    hosted_env.setattr(L, "suggest", lambda d, edl, prompt, ledger, log: ({"preset": "box"}, "stub", []))
    hosted_env.setattr(T, "suggest", lambda d, edl, brief, ledger, log: ["One", "Two", "Three"])
    d, c = sample_client(tmp_path, hosted_env, "ee55")
    fd = _open_draft(d, "ee55")
    r = c.post(f"/api/runs/r1/{route}/suggest", json={"prompt": "bold yellow", "draft": True})
    assert r.status_code == 200, r.text
    assert r.json()["run"] == "r1-ee55"  # the call is spent in the judge's copy
    assert draft.load(fd)["id"] == "dr1"  # the draft stays where the editor is
    assert draft.load(draft.folder(tmp_path / "runs" / "r1-ee55", "ee55")) is None


def test_a_draft_on_a_picked_older_version_commits_from_it(tmp_path, monkeypatch):
    """v1 picked while v2 exists: v2 did not come after the draft, so the commit is made from v1 (it was rebased onto
    v2 and took v2's music)."""
    import montivo.human as H_
    d = _run(tmp_path, monkeypatch, TALK)
    H_.music(d, "ukulele")
    (d / "cut_v2.mp4").write_bytes(b"")
    c, st = _client(tmp_path)
    r = c.post("/api/runs/h1/draft", json={"op": "drop", "args": {"span": "s2"}, "from": 1})
    assert r.status_code == 200 and r.json()["draft"]["stale"] is False
    _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 200 and r.json()["from"] == 1
    v3 = json.loads((d / "edl_v3.json").read_text())
    assert v3["patches"][-1]["from"] == 1 and not v3.get("music")
    assert [s["id"] for s in v3["spans"]] == ["s1", "s3"]


def test_a_version_made_after_a_draft_on_an_older_pick_is_still_replayed_onto(tmp_path, monkeypatch):
    import montivo.human as H_
    d = _run(tmp_path, monkeypatch, TALK)
    H_.music(d, "ukulele")
    (d / "cut_v2.mp4").write_bytes(b"")
    c, st = _client(tmp_path)
    c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}, "from": 1})
    H_.drop(d, "s2", from_v=2)  # v3, made elsewhere while the draft is open
    (d / "cut_v3.mp4").write_bytes(b"")
    assert c.get("/api/runs/h1/draft").json()["draft"]["stale"] is True
    _fake_jobs(monkeypatch, st)
    assert c.post("/api/runs/h1/draft/commit", json={"rebase": False}).status_code == 409
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 200 and r.json()["from"] == 3


def test_a_samples_failed_first_commit_takes_the_draft_to_the_copy(tmp_path, monkeypatch):
    import shutil
    from fastapi import Request
    d = _run(tmp_path, monkeypatch)

    async def own(run_id, request: Request, **kw):
        mine = f"{run_id}-{request.headers['x-judge']}"
        if not (tmp_path / "runs" / mine).exists():
            shutil.copytree(d, tmp_path / "runs" / mine)
        return mine
    c, st = _client(tmp_path, hosted=True, own=own)
    a = {"x-judge": "aaa"}
    assert c.post("/api/runs/h1/draft", headers=a, json={"op": "drop", "args": {"span": "s2"}}).status_code == 200
    _fake_jobs(monkeypatch, st, run_inline=False)
    r = c.post("/api/runs/h1/draft/commit", headers=a, json={})
    assert r.status_code == 200 and r.json()["run"] == "h1-aaa"
    st.jobs["h1-aaa"].rc, st.jobs["h1-aaa"].ended = -15, time.time()  # stopped
    assert c.get("/api/runs/h1/draft", headers=a).json()["draft"] is None  # off the sample...
    dr = c.get("/api/runs/h1-aaa/draft", headers=a).json()["draft"]  # ...onto the copy the editor is on
    assert dr["ops"][0]["op"] == "drop" and "did not finish" in dr["commit_failed"] and dr["committing"] is None
    assert not (d / "ui" / "draft" / "aaa").exists()


def _failed(st, run_id, kind, cmd, draft_dir=None):
    j = A.Job(id="old", run=run_id, kind=kind, label="Ask Montivo" if kind == "ask" else "Make a version", cmd=cmd,
              draft_dir=draft_dir)
    j.rc, j.ended = 1, time.time()
    return j


def test_try_again_keeps_a_live_asks_draft_and_refuses_a_draft_commit(tmp_path, hosted_env):
    from tests.test_ui import _run_folder
    _run_folder(tmp_path)
    app = A.create_app(tmp_path)
    started = []

    def start_job(st, rid, kind, label, args, chat=None):
        job = A.Job(id=f"j{len(started)}", run=rid, kind=kind, label=label, cmd=["py", *args])
        started.append(job)
        return job
    hosted_env.setattr(A, "start_job", start_job)
    c = TestClient(app)
    S = _state(app)
    fd = str(tmp_path / "runs" / "r1" / "ui" / "draft")
    S.jobs["r1"] = _failed(S, "r1", "ask", ["py", "-m", "montivo.agent", "runs/r1", "--message", "m", "--from", "2",
                                            "--review", "0", "--planner", "spark", "--no-model", "--draft"], fd)
    r = c.post("/api/runs/r1/retry", json={})
    assert r.status_code == 200, r.text
    assert started[-1].draft_dir == fd
    S.jobs["r1"] = _failed(S, "r1", "draft", ["py", "-m", "montivo.human", "draft", "runs/r1", "--ops", "[]", "--id",
                                              "x", "--from", "2", "--review", "0"])
    r = c.post("/api/runs/r1/retry", json={})
    assert r.status_code == 409 and "Make Version" in r.text and len(started) == 1


def _state(app):
    """create_app's State, from a route closure that holds it."""
    for route in app.routes:
        for cell in getattr(getattr(route, "endpoint", None), "__closure__", None) or []:
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if isinstance(v, A.State):
                return v
    raise AssertionError("no State")


def test_two_jobs_in_one_second_get_their_own_ids_and_outputs(tmp_path, monkeypatch):
    st = A.State(tmp_path.resolve())
    for rid in ("a", "b"):
        (tmp_path / "runs" / rid).mkdir(parents=True)
    monkeypatch.setattr(A.time, "strftime", lambda fmt, *a: "20261101-120000")
    j1 = A.start_job(st, "a", "drop", "x", ["-c", "pass"])
    j2 = A.start_job(st, "b", "drop", "x", ["-c", "pass"])
    assert j1.id != j2.id and j1.out != j2.out and j1.id.startswith("20261101-120000")
    for j in (j1, j2):
        j.proc.wait(5)


def test_header_style_title_words_cost_nothing(tmp_path):
    base = {"title_card_text": "Old", "title_style": "header", "spans": []}
    made = {**base, "title_card_text": "A brand new title"}
    o = [{"op": "text", "args": {"title": "A brand new title"}}]
    assert draft.cost(o, tmp_path, made, base) == 0.0
    card = {**base, "title_style": "card"}
    assert draft.cost(o, tmp_path, {**made, "title_style": "card"}, card) == pytest.approx(draft.TITLE_COST)
    # Try again prices it the same way: from the run's version
    rd = tmp_path / "runs" / "r"
    rd.mkdir(parents=True)
    (rd / "edl_v3.json").write_text(json.dumps(base))
    args = ["-m", "montivo.human", "draft", "runs/r", "--ops", json.dumps(o), "--id", "x", "--from", "3", "--review", "0"]
    assert A.retry_estimate(tmp_path, "draft", args, floor=False) == 0.0
    turned = [{"op": "text", "args": {"title_style": "card"}}, *o]
    assert A.retry_estimate(tmp_path, "draft", [*args[:5], json.dumps(turned), *args[6:]], floor=False) == \
        pytest.approx(0.01)
    text = ["-m", "montivo.human", "text", "runs/r", "--title=New words", "--from", "3", "--review", "0"]
    assert A.retry_estimate(tmp_path, "text", text, floor=False) == 0.0
    (rd / "edl_v3.json").write_text(json.dumps(card))
    assert A.retry_estimate(tmp_path, "draft", args, floor=False) == pytest.approx(0.01)
    assert A.retry_estimate(tmp_path, "text", text, floor=False) == pytest.approx(0.01)


def test_a_keyless_draft_commit_of_header_title_words_is_not_refused(tmp_path, monkeypatch):
    import montivo.human as H_
    d = _run(tmp_path, monkeypatch, TALK)
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["title_style"] = "header"
    (d / "edl_v1.json").write_text(json.dumps(edl))
    c, st = _client(tmp_path)  # its need_key refuses anything paid
    assert c.post("/api/runs/h1/draft", json={"op": "text", "args": {"title": "Fresh words"}}).status_code == 200
    _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 200, r.text
    assert json.loads((d / "edl_v2.json").read_text())["title_card_text"] == "Fresh words"
    assert H_.latest_edl(d) == 2


def test_monti_caps_the_version_and_panel_it_is_sent():
    from montivo import monti
    text = monti.context_text({"page": "editor", "version": "9" * 500, "panel": "p" * 500})
    assert "9" * 41 not in text and "p" * 41 not in text and "9" * 40 in text
