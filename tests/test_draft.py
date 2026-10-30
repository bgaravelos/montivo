"""Live preview drafts: the structural edits as pure functions (human.OPS) give the CLI's EDL byte for byte, a chain
committed as one version gives the EDL of the edits one by one, and the draft routes (montivo/server/draft.py): undo /
redo, refusals, the per-run lock, a job on the run, a newer version under the draft, the hosted judge folders."""
import json
import shutil
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from montivo.engine.api import dump_json
from tests.test_edges import SynthAudio
from tests.test_review import make_edl, voice_words

TALK = [(10.0, 11.15), (11.25, 12.95), (30.0, 30.95), (31.6, 32.95)]
PAUSED = [(10.0, 11.2), (11.8, 12.7), (30.6, 30.95), (31.6, 32.1)]  # a 0.63 s pause inside s3, around w13 | w14

# (CLI function, its arguments, the pure op, its arguments): s1 = turn 0 words 0-3 (hook), s2 = turn 0 words 6-8,
# s3 = turn 1 words 2-6
CASES = {
    "drop": ("drop", {"span_id": "s2"}, {"op": "drop", "args": {"span": "s2"}}),
    "trim_out": ("trim", {"span_id": "s3", "edge": "out", "word": 5}, {"op": "trim", "args": {"span": "s3", "edge": "out", "word": 5}}),
    "trim_in": ("trim", {"span_id": "s1", "edge": "in", "word": 1}, {"op": "trim", "args": {"span": "s1", "edge": "in", "word": 1}}),
    "extend": ("trim", {"span_id": "s2", "edge": "out", "word": 9}, {"op": "trim", "args": {"span": "s2", "edge": "out", "word": 9}}),
    "reorder": ("reorder", {"span_id": "s3", "before": "s1"}, {"op": "reorder", "args": {"span": "s3", "before": "s1"}}),
    "reorder_end": ("reorder", {"span_id": "s1", "before": None}, {"op": "reorder", "args": {"span": "s1", "before": None}}),
    "cut_split": ("cut", {"ranges": [("s3", 4, 4)]}, {"op": "cut", "args": {"ranges": [["s3", 4, 4]]}}),
    "cut_mix": ("cut", {"ranges": [("s1", 0, 1), ("s2", 6, 8)]}, {"op": "cut", "args": {"ranges": [["s1", 0, 1], ["s2", 6, 8]]}}),
    "split": ("split", {"span_id": "s3", "word": 4}, {"op": "split", "args": {"span": "s3", "word": 4}}),
    "insert": ("insert", {"turn": 0, "a": 4, "b": 5}, {"op": "insert", "args": {"turn": 0, "a": 4, "b": 5, "before": None}}),
    "insert_end": ("insert", {"turn": 1, "a": 7, "b": 8, "before": "end"},
                   {"op": "insert", "args": {"turn": 1, "a": 7, "b": 8, "before": "end"}}),
    "pauses": ("pauses", {"over": 0.6, "keep": 0.3}, {"op": "pauses", "args": {"over": 0.6, "keep": 0.3}}),
}
CHAIN = [{"op": "split", "args": {"span": "s3", "word": 4}}, {"op": "reorder", "args": {"span": "s4", "before": "s1"}},
         {"op": "insert", "args": {"turn": 0, "a": 4, "b": 5, "before": None}},
         {"op": "trim", "args": {"span": "s1", "edge": "in", "word": 1}}, {"op": "drop", "args": {"span": "s2"}}]


def _run(tmp_path, monkeypatch, speech=PAUSED, name="h1"):
    """A run folder (run.json, edl_v1, Voice words) with the source audio stubbed and the render stubbed out."""
    import montivo.human as H
    from montivo.server import draft
    d = tmp_path / "runs" / name
    (d / "voice").mkdir(parents=True)
    edl, _ = make_edl()
    edl.update(title_card_text="Raw In", reframe_concept="head")
    edl["spans"][1]["callout"] = "Four Meta models"
    (d / "edl_v1.json").write_text(json.dumps(edl))
    (d / "cut_v1.mp4").write_bytes(b"")
    (d / "run.json").write_text(json.dumps({"footage": "footage/x.mp4", "brief": "b"}))
    (d / "voice" / "voice.json").write_text(json.dumps({"words": voice_words(), "speakers": ["A", "B"]}))
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: SynthAudio(speech) if speech else None))
    monkeypatch.setattr(H, "_finish", lambda run_dir, edl_path, n, *a: {"version": n, "edl": edl_path.name})
    draft._inputs.clear()
    return d


def _bare(edl):
    return {k: v for k, v in edl.items() if k not in ("version", "patches")}


# --------------------------------------------------------------------------- pure ops = the CLI


@pytest.mark.parametrize("speech", [TALK, PAUSED, None], ids=["talk", "paused", "no-audio"])
@pytest.mark.parametrize("case", list(CASES))
def test_every_pure_op_writes_the_cli_edl_byte_for_byte(tmp_path, monkeypatch, case, speech):
    import montivo.human as H
    cli, cli_args, o = CASES[case]
    if case == "pauses" and speech is None:
        pytest.skip("pauses needs the source audio")
    d = _run(tmp_path, monkeypatch, speech)
    edl = json.loads((d / "edl_v1.json").read_text())
    new, rec = H.apply_op(edl, H._words(d), H.Audio.load(d), o["op"], o["args"])
    assert json.loads((d / "edl_v1.json").read_text()) == edl  # the op never changes the EDL it is given
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    getattr(H, cli)(d, **cli_args)
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()
    assert rec.head(1, 2).startswith(f"montivo {cli}: ") and rec.say


def test_an_op_refuses_what_the_cli_refuses(tmp_path, monkeypatch):
    import montivo.human as H
    d = _run(tmp_path, monkeypatch)
    edl, words, audio = json.loads((d / "edl_v1.json").read_text()), H._words(d), H.Audio.load(d)
    for op, args, why in [("drop", {"span": "s1"}, "the hook"), ("drop", {"span": "s9"}, "not a span of edl_v1"),
                          ("split", {"span": "s3", "word": 3}, "one-word clip"),
                          ("reorder", {"span": "s2", "before": "s2"}, "before itself"),
                          ("insert", {"turn": 0, "a": 2, "b": 3}, "already in s1"), ("pauses", {"over": 3.0}, "no pause"),
                          ("split", {"span": "s3"}, "missing word"), ("drop", {"span": "s2", "x": 1}, "unknown argument"),
                          ("render", {}, "not an edit")]:
        with pytest.raises(SystemExit, match=why):
            H.apply_op(edl, words, audio, op, args)


def test_a_chain_is_the_edits_one_by_one(tmp_path, monkeypatch):
    import montivo.human as H
    d = _run(tmp_path, monkeypatch, TALK)
    H.DEFER = []
    try:  # the agent's way: each edit its own unrendered edl_vN, read back from the file
        H.split(d, "s3", 4)
        H.reorder(d, "s4", "s1")
        H.insert(d, 0, 4, 5)
        H.trim(d, "s1", "in", 1)
        H.drop(d, "s2")
    finally:
        H.DEFER = None
    one_by_one = json.loads((d / "edl_v6.json").read_text())
    for p in d.glob("edl_v[2-6].json"):
        p.unlink()
    H.main(["draft", str(d), "--ops", json.dumps(CHAIN), "--id", "abc", "--from", "1"])
    chained = json.loads((d / "edl_v2.json").read_text())
    assert _bare(chained) == _bare(one_by_one)
    assert chained["version"] == 2 and len(chained["patches"]) == 1
    patch = chained["patches"][0]
    assert (patch["from"], patch["source"], patch["action"]) == (1, "human", "edits")
    assert patch["fixes"] == [f for p in one_by_one["patches"] for f in p["fixes"]]
    assert patch["draft"] == {"id": "abc", "ops": CHAIN} and patch["instruction"].startswith("5 edits: Split clip s3")
    log = (d / "run.log").read_text()
    assert "montivo draft: 5 edits" in log and "patch v1 -> v2: reorder s4" in log


def test_a_one_op_draft_keeps_that_ops_patch(tmp_path, monkeypatch):
    import montivo.human as H
    d = _run(tmp_path, monkeypatch)
    H.apply_draft(d, [CASES["pauses"][2]], 1, "x")
    H.pauses(d, 0.6, 0.3, from_v=1)
    a, b = (json.loads((d / f"edl_v{k}.json").read_text()) for k in (2, 3))
    assert a["patches"][-1].pop("draft") == {"id": "x", "ops": [CASES["pauses"][2]]}
    assert _bare(a) == _bare(b) and a["patches"][-1] == b["patches"][-1]


# --------------------------------------------------------------------------- the draft routes


def _client(tmp_path, hosted=False, own=None):
    """The draft router alone on a real State, with create_app's helpers stubbed: judge from a header, own = the run
    itself unless given."""
    from montivo.server import draft
    from montivo.server.app import State
    st = State(tmp_path.resolve())
    app = FastAPI()

    async def own_default(run_id, request):
        return run_id

    def need_key(what, est=0.0):
        raise HTTPException(409, f"{what} calls Meta models")

    app.include_router(draft.router(st, own or own_default, lambda request: request.headers.get("x-judge", "guest"),
                                    need_key, hosted=hosted))

    @app.exception_handler(HTTPException)
    async def http_error(_req, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return TestClient(app), st


def _fake_jobs(monkeypatch, st, run_inline=True):
    """start_job that runs the engine command in this process (render stubbed) and ends at once."""
    import montivo.human as H
    from montivo.server import app as A
    started = []

    def start_job(st_, run_id, kind, label, args, chat=None):
        job = A.Job(id=f"j{len(started)}", run=run_id, kind=kind, label=label, cmd=args)
        started.append((run_id, args, chat))
        if run_inline:
            assert args[:3] == ["-m", "montivo.human", "draft"]
            H.main([args[2], str(st_.root / args[3]), *args[4:]])
            job.rc, job.ended = 0, time.time()
        st_.jobs[run_id] = job
        return job
    monkeypatch.setattr(A, "start_job", start_job)
    return started


def test_draft_ops_undo_redo_and_one_commit(tmp_path, monkeypatch):
    import montivo.human as H
    d = _run(tmp_path, monkeypatch, TALK)
    c, st = _client(tmp_path)
    assert c.get("/api/runs/h1/draft").json() == {"draft": None, "latest": 1}
    edls = []
    for o in CHAIN:
        r = c.post("/api/runs/h1/draft", json={**o, "from": 1} if not edls else o)
        assert r.status_code == 200, r.text
        edls.append(c.get("/api/runs/h1/draft?edl=1").json()["edl"])
    dr = r.json()["draft"]
    assert dr["base"] == 1 and [o["op"] for o in dr["ops"]] == [o["op"] for o in CHAIN] and dr["can_undo"]
    assert dr["ops"][0]["say"] == "split clip s3 in front of 'w14'" and r.json()["ms"] < 1000
    assert [s["id"] for s in dr["spans"]] == [s["id"] for s in edls[-1]["spans"]]
    assert not list(d.glob("edl_v2.json"))  # nothing written beside the draft
    # undo replays from the base; redo puts the op back on top
    assert c.post("/api/runs/h1/draft/undo").json()["draft"]["can_redo"]
    assert c.get("/api/runs/h1/draft?edl=1").json()["edl"] == edls[-2]
    c.post("/api/runs/h1/draft/undo")
    assert c.get("/api/runs/h1/draft?edl=1").json()["edl"] == edls[-3]
    c.post("/api/runs/h1/draft/redo")
    c.post("/api/runs/h1/draft/redo")
    assert c.get("/api/runs/h1/draft?edl=1").json()["edl"] == edls[-1]
    assert c.post("/api/runs/h1/draft/redo").status_code == 409
    # the draft's EDL is the committed one, byte for byte apart from version and patch
    started = _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 200, r.text
    assert r.json()["from"] == 1 and r.json()["ops"] == 5 and started[0][2]["kind"] == "draft"
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert _bare(v2) == _bare(edls[-1]) and v2["patches"][-1]["draft"]["ops"] == CHAIN
    (d / "cut_v2.mp4").write_bytes(b"")
    assert c.get("/api/runs/h1/draft").json() == {"draft": None, "latest": 2}  # the commit ended: the draft is gone
    assert not (d / "ui" / "draft" / "state.json").exists()
    assert H.latest_edl(d) == 2


def test_draft_refusals_leave_it_as_it_was(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch)
    c, st = _client(tmp_path)
    assert c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}}).status_code == 200
    before = c.get("/api/runs/h1/draft?edl=1").json()
    r = c.post("/api/runs/h1/draft", json={"op": "drop", "args": {"span": "s1"}})
    assert r.status_code == 409 and "hook" in r.json()["error"]
    for bad in [{"op": "render"}, {"op": "split", "args": {"span": "x", "word": 1}},
                {"op": "split", "args": {"span": "s3", "word": 1.5}}, {"op": "trim", "args": {"span": "s3", "word": 4}},
                {"op": "cut", "args": {"ranges": [["s3", 1]]}}, {"op": "pauses", "args": {"over": "nan"}}]:
        assert c.post("/api/runs/h1/draft", json=bad).status_code == 400, bad
    r = c.post("/api/runs/h1/draft", json={"op": "drop", "args": {"span": "s2"}, "from": 3})
    assert r.status_code == 409 and "a draft from v1 is open" in r.json()["error"]
    assert c.get("/api/runs/h1/draft?edl=1").json() == before
    assert c.post("/api/runs/h1/draft/commit", json={"review": 1}).status_code == 409  # the review needs the key
    assert c.delete("/api/runs/h1/draft").json() == {"draft": None, "latest": 1}
    assert c.post("/api/runs/h1/draft/commit", json={}).status_code == 409  # nothing to make


def test_no_draft_op_beside_a_job_on_the_run(tmp_path, monkeypatch):
    from montivo.server.app import Job
    _run(tmp_path, monkeypatch)
    c, st = _client(tmp_path)
    st.jobs["h1"] = Job(id="j", run="h1", kind="drop", label="Delete clip s2", cmd=[])
    r = c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}})
    assert r.status_code == 409 and "Delete clip s2 is running" in r.json()["error"]
    st.jobs["h1"].ended = time.time()
    assert c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}}).status_code == 200
    started = _fake_jobs(monkeypatch, st, run_inline=False)  # a commit in flight holds the draft
    assert c.post("/api/runs/h1/draft/commit", json={}).status_code == 200
    r = c.post("/api/runs/h1/draft", json={"op": "drop", "args": {"span": "s2"}})
    assert r.status_code == 409 and "being made" in r.json()["error"]
    assert c.get("/api/runs/h1/draft").json()["draft"]["committing"]["job"] == "j0"
    st.jobs["h1"].rc, st.jobs["h1"].ended = 1, time.time()  # it failed: the draft is editable again
    dr = c.get("/api/runs/h1/draft").json()["draft"]
    assert dr["committing"] is None and "did not finish" in dr["commit_failed"] and len(started) == 1
    assert c.post("/api/runs/h1/draft", json={"op": "drop", "args": {"span": "s2"}}).status_code == 200


def test_one_lock_per_run_keeps_the_ops_in_order(tmp_path, monkeypatch):
    from montivo.server import draft
    d = _run(tmp_path, monkeypatch)
    c, _ = _client(tmp_path)
    assert draft.run_lock(d) is draft.run_lock(tmp_path / "runs" / ".." / "runs" / "h1")
    out = {}
    lock = draft.run_lock(d)
    lock.acquire()
    t = threading.Thread(target=lambda: out.setdefault("r", c.post(
        "/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}})))
    t.start()
    time.sleep(0.3)
    assert "r" not in out and not (d / "ui" / "draft" / "state.json").exists()  # waits for the lock
    lock.release()
    t.join(5)
    assert out["r"].status_code == 200


def test_a_newer_version_under_the_draft_is_replayed_or_refused(tmp_path, monkeypatch):
    import montivo.human as H
    d = _run(tmp_path, monkeypatch, TALK)
    c, st = _client(tmp_path)
    c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}, "from": 1})
    H.drop(d, "s2")  # v2, made elsewhere (another tab, the chat) while the draft is open
    (d / "cut_v2.mp4").write_bytes(b"")
    assert c.get("/api/runs/h1/draft").json()["draft"]["stale"] is True
    _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", json={"rebase": False})
    assert r.status_code == 409 and "v2 was made after this draft started from v1" in r.json()["error"]
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 200 and r.json()["from"] == 2
    v3 = json.loads((d / "edl_v3.json").read_text())
    assert v3["patches"][-1]["from"] == 2 and [s["id"] for s in v3["spans"]] == ["s1", "s3", "s4"]
    # an op the newer version no longer allows: refused with the op and why, no job
    (d / "cut_v3.mp4").write_bytes(b"")
    c.get("/api/runs/h1/draft")
    c.post("/api/runs/h1/draft", json={"op": "reorder", "args": {"span": "s4", "before": "s1"}, "from": 3})
    H.drop(d, "s4", from_v=3)
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 409 and "v4 came after this draft (from v3): edit 1 (move clip s4 before s1)" in r.json()["error"]
    assert not (d / "edl_v5.json").exists()


def test_hosted_drafts_are_per_judge_and_copy_a_sample_only_at_the_commit(tmp_path, monkeypatch):
    d = _run(tmp_path, monkeypatch)
    copies = []

    async def own(run_id, request: Request):  # create_app's own(): a sample becomes the judge's copy
        mine = f"{run_id}-{request.headers['x-judge']}"
        if not (tmp_path / "runs" / mine).exists():
            shutil.copytree(d, tmp_path / "runs" / mine)
            copies.append(mine)
        return mine
    c, st = _client(tmp_path, hosted=True, own=own)
    a, b = {"x-judge": "aaa"}, {"x-judge": "bbb"}
    assert c.post("/api/runs/h1/draft", headers=a, json={"op": "split", "args": {"span": "s3", "word": 4}}).status_code == 200
    assert c.post("/api/runs/h1/draft", headers=b, json={"op": "drop", "args": {"span": "s2"}}).status_code == 200
    assert (d / "ui" / "draft" / "aaa" / "state.json").is_file() and (d / "ui" / "draft" / "bbb" / "edl.json").is_file()
    assert [o["op"] for o in c.get("/api/runs/h1/draft", headers=a).json()["draft"]["ops"]] == ["split"]
    assert [o["op"] for o in c.get("/api/runs/h1/draft", headers=b).json()["draft"]["ops"]] == ["drop"]
    assert copies == [] and not (d / "edl_v2.json").exists()  # no copy and no version before the commit
    assert c.post("/api/runs/h1/draft", headers={"x-judge": "../x"}, json={"op": "drop", "args": {"span": "s2"}}).status_code == 400
    _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", headers=a, json={})
    assert r.status_code == 200 and r.json()["run"] == "h1-aaa" and copies == ["h1-aaa"]
    mine = tmp_path / "runs" / "h1-aaa"
    assert json.loads((mine / "edl_v2.json").read_text())["patches"][-1]["action"] == "split"
    assert not (d / "edl_v2.json").exists()  # the sample stays as it was
    assert not (mine / "ui" / "draft" / "bbb").exists()  # the other judge's draft did not travel with the copy
    (mine / "cut_v2.mp4").write_bytes(b"")
    assert c.get("/api/runs/h1/draft", headers=a).json()["draft"] is None
    assert c.get("/api/runs/h1/draft", headers=b).json()["draft"]["ops"][0]["op"] == "drop"


def test_the_server_serves_the_draft_routes(tmp_path, monkeypatch):
    from montivo.server.app import create_app
    _run(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    r = c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}})
    assert r.status_code == 200 and r.json()["draft"]["base"] == 1
    assert (tmp_path / "runs" / "h1" / "ui" / "draft" / "edl.json").is_file()
    assert c.post("/api/runs/nope/draft", json={"op": "drop", "args": {"span": "s2"}}).status_code == 404


def test_a_draft_shows_its_music_bed(tmp_path):
    """The draft has no render, so its version entry took no music and the lane said "Add music" while the bed played."""
    from montivo.server.runs import draft_music
    played = {"ducked": [[0.0, 3.2]], "gain_db": -4.4}
    edl = {"music": {"track": "chill_lofi", "level_db": -18.0, "duck_db": -10.0}}
    m = draft_music(edl, played)
    assert m["track"] == "chill_lofi" and m["level_db"] == -18.0 and m["ducked"] == [[0.0, 3.2]]
    assert m["margin_db"] is None and m["voice_db"] == 0.0  # nothing measured before the render
    assert draft_music({}, played) is None and draft_music(edl, None) is None


def test_a_drafts_bed_waveform_follows_the_live_gain():
    from montivo.server.media import draft_bed_peaks
    music = {"gain_db": 0.0, "duck_db": -20.0, "ducked": [[2.0, 3.0]], "attack_s": 0.15, "release_s": 0.45,
             "fade_in_s": 0.5, "fade_out_s": 0.5}
    out = draft_bed_peaks({"rate": 100, "peaks": [0.5] * 150}, music, 4.0)  # a 1.5 s loop under a 4 s cut
    p = out["peaks"]
    assert out["rate"] == 100 and len(p) == 400
    assert p[0] == 0.0 and p[100] == 0.5 and p[170] == 0.5  # faded in from 0, looped past 1.5 s
    assert p[250] == 0.05  # ducked 20 dB under the speech
    assert p[399] < 0.02  # faded out at the end


def test_chat_edits_join_the_draft_and_a_refused_one_is_said(tmp_path, monkeypatch):
    """A5: Ask Montivo's ops in Live (draft.push) go into the draft as clicks would, a new draft from the version asked
    about or the open one; an op the draft refuses is skipped with why and the others stay."""
    from montivo.server import draft
    d = _run(tmp_path, monkeypatch)
    fd = draft.folder(d, None)
    out, done, skipped = draft.push(d, fd, [{"op": "drop", "args": {"span": "s2"}},
                                            {"op": "drop", "args": {"span": "s9"}}], 1)
    assert len(done) == 1 and len(skipped) == 1 and "s9" in skipped[0]
    st = draft.load(fd)
    assert st["base"] == 1 and [o["op"] for o in st["ops"]] == ["drop"]
    assert [s["id"] for s in json.loads((fd / "edl.json").read_text())["spans"]] == ["s1", "s3"]
    _, done, _ = draft.push(d, fd, [{"op": "punch", "args": {"on": True}}], 1)  # the open draft grows
    assert done and [o["op"] for o in draft.load(fd)["ops"]] == ["drop", "punch"]
    _, done, skipped = draft.push(d, fd, [{"op": "nope", "args": {}}], 1)
    assert not done and skipped and len(draft.load(fd)["ops"]) == 2
