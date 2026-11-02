"""Offline: the Captions panel's two version routes, POST /api/runs/{id}/glossary (Correct all) and /linebreak (a hand
line break or join), through create_app: the engine command the route starts (run here in this process with the
render stubbed, so the next edl_vN and its patch are checked), bad input answered 400, a review asked without the key
refused, and on the hosted demo an edit of a sample going to the judge's own copy. Plus human.glossary_edit's own
refusals. test_panel_draft covers the pure ops and the draft path."""
import json

import pytest
from fastapi.testclient import TestClient

import montivo.human as H
import montivo.server.app as A
from tests.test_hosted import fake_jobs, hosted_env  # noqa: F401 (the fixture)
from tests.test_hosted_samples import sample_client
from tests.test_panel_draft import _panel_run


def _inline_jobs(monkeypatch):
    """start_job that runs the montivo.human command at once in this process (test_draft's _run stubs the render);
    a refusal (SystemExit) is kept as the job's rc, as the subprocess would end."""
    seen = []

    def start_job(st, rid, kind, label, args, chat=None):
        job = A.Job(id=f"j{len(seen)}", run=rid, kind=kind, label=label, cmd=args)
        seen.append({"run": rid, "kind": kind, "args": args, "chat": chat})
        assert args[:2] == ["-m", "montivo.human"]
        try:
            H.main([args[2], str(st.root / args[3]), *args[4:]])
            job.rc = 0
        except SystemExit as e:
            job.rc = 1
            seen[-1]["refused"] = str(e)
        return job
    monkeypatch.setattr(A, "start_job", start_job)
    return seen


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    d = _panel_run(tmp_path, monkeypatch)
    seen = _inline_jobs(monkeypatch)
    return d, TestClient(A.create_app(tmp_path)), seen


def test_correct_all_makes_the_next_version_with_a_glossary_patch(panel):
    d, c, seen = panel
    r = c.post("/api/runs/h1/glossary", json={"add": {"meta  meta.": "Four Meta"}, "drop": ["news voice"]})
    assert r.status_code == 200, r.text
    assert r.json()["job"]["kind"] == "glossary" and r.json()["job"]["label"] == "Correct everywhere"
    job = seen[-1]
    assert job["args"] == ["-m", "montivo.human", "glossary", "runs/h1", "--add=meta meta.=Four Meta",
                           "--drop=news voice", "--from", "1", "--review", "0"]
    assert job["chat"]["kind"] == "caption" and "“meta meta.” → “Four Meta”" in job["chat"]["text"]
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert v2["glossary"]["meta meta"] == "Four Meta" and "news voice" not in v2["glossary"]
    patch = v2["patches"][-1]
    assert (patch["from"], patch["source"], patch["action"]) == (1, "human", "glossary")
    assert patch["fixes"][0]["args"] == {"add": {"meta meta": "Four Meta"}, "drop": ["news voice"]}
    # the same entry again: the engine refuses it and no version is made
    assert c.post("/api/runs/h1/glossary", json={"add": {"meta meta": "Four Meta"}}).status_code == 200
    assert "already says" in seen[-1]["refused"] and not (d / "edl_v3.json").exists()


def test_a_line_break_makes_the_next_version_on_the_word(panel):
    d, c, seen = panel
    r = c.post("/api/runs/h1/linebreak", json={"at": 3.0, "from": 1})
    assert r.status_code == 200, r.text
    assert seen[-1]["args"] == ["-m", "montivo.human", "linebreak", "runs/h1", "--at=3.000", "--from", "1",
                                "--review", "0"]
    assert seen[-1]["chat"]["text"] == "Break the caption line at 3.00 s in v1."
    v2 = json.loads((d / "edl_v2.json").read_text())
    brk = v2["caption_breaks"]
    assert len(brk) == 1 and brk[0]["span"] == "s2" and "join" not in brk[0]
    assert v2["patches"][-1]["fixes"][0]["op"] == "caption_break" and v2["patches"][-1]["source"] == "human"
    # a join at a line's first word is the line before's: refused, nothing written
    r = c.post("/api/runs/h1/linebreak", json={"at": 3.0, "join": True, "from": 1})
    assert r.status_code == 200 and seen[-1]["args"][5] == "--join"
    assert "joins the one before at its first word" in seen[-1]["refused"] and not (d / "edl_v3.json").exists()


@pytest.mark.parametrize("route,body", [
    ("glossary", {}), ("glossary", {"add": {"x": ""}}), ("glossary", {"add": "meta=Four"}),
    ("glossary", {"drop": "x"}), ("glossary", {"add": {"x" * 81: "y"}}),
    ("linebreak", {}), ("linebreak", {"at": "x"}), ("linebreak", {"at": "nan"}), ("linebreak", {"at": "inf"}),
    ("glossary", {"add": {"a": "b"}, "review": 7}), ("linebreak", {"at": 3.0, "review": -1})])
def test_bad_input_is_400_and_starts_nothing(panel, route, body):
    d, c, seen = panel
    r = c.post(f"/api/runs/h1/{route}", json=body)
    assert r.status_code == 400, (route, body, r.status_code, r.text)
    assert seen == [] and not (d / "edl_v2.json").exists()


def test_a_review_with_the_edit_needs_the_key(panel):
    d, c, seen = panel
    for route, body in (("glossary", {"add": {"a": "b"}}), ("linebreak", {"at": 3.0})):
        r = c.post(f"/api/runs/h1/{route}", json={**body, "review": 1})
        assert r.status_code == 409 and "MODEL_API_KEY" in r.json()["error"], route
    assert seen == []


def test_on_the_hosted_demo_a_samples_edit_goes_to_the_judges_copy(tmp_path, hosted_env):
    d, c = sample_client(tmp_path, hosted_env, "cc33")
    seen = fake_jobs(hosted_env)
    r = c.post("/api/runs/r1/glossary", json={"add": {"meta meta": "Four Meta"}})
    assert r.status_code == 200, r.text
    assert r.json()["job"]["run"] == "r1-cc33" and seen[-1][3] == "runs/r1-cc33"
    assert (tmp_path / "runs" / "r1-cc33" / "edl_v2.json").is_file()
    r = c.post("/api/runs/r1/linebreak", json={"at": 2.5})
    assert r.status_code == 200 and r.json()["job"]["run"] == "r1-cc33" and seen[-1][3] == "runs/r1-cc33"
    assert not (d / "edl_v4.json").exists()  # the sample itself is never written


def test_glossary_edit_refuses_what_it_cannot_write(tmp_path, monkeypatch):
    d = _panel_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    words = H._words(d)
    for add in ({"": "x"}, {"x": ""}, {"x" * 81: "y"}, {"x": "y" * 81}):
        with pytest.raises(SystemExit, match="1 to 80 characters"):
            H.glossary_edit(edl, words, None, add=add)
    with pytest.raises(SystemExit, match="nothing to change"):
        H.glossary_edit(edl, words, None, drop=["never heard this"])
    new, rec = H.glossary_edit(edl, words, None, add={"Meta meta!": "Four Meta."})
    assert new["glossary"]["Meta meta"] == "Four Meta"  # punctuation off both sides, the case kept
    assert rec.fixes[0]["args"] == {"add": {"Meta meta": "Four Meta"}, "drop": []} and "glossary" not in edl
    again, _ = H.glossary_edit(new, words, None, add={"META META": "4 Meta"})
    assert [k for k in again["glossary"] if k.lower() == "meta meta"] == ["META META"]  # one entry per heard phrase
