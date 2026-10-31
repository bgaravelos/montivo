"""The server and hosted findings of the full code audit (31/10): the sign-in never locks a judge out, a judge's share
of the budget, paid suggestions and covers on a sample stay the judge's own, timeline media replaced and never
rewritten in place, a job that always ends (and a wall-clock limit), the upload caps, a busy export or Monti answers
at once instead of queueing, and the smaller input and stream fixes. Each test fails on the code before the fix."""
import asyncio
import json
import os
import shutil
import threading
import time

from fastapi.testclient import TestClient

import montivo.server.app as A
from montivo.server import hosted as H
from montivo.server import media
from tests.test_hosted import fake_jobs, hosted_env, ledger  # noqa: F401 (the fixture)
from tests.test_ui import _clip, _run_folder


def _no_sleep(monkeypatch):
    real = H.asyncio.sleep

    async def no_wait(s):
        await real(0)
    monkeypatch.setattr(H.asyncio, "sleep", no_wait)


def _sample(tmp_path, env):
    """r1 as a hosted sample (adopted, no owner) with a cut for v2."""
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    (d / "cut_v2.mp4").write_bytes(b"cut")
    env.setenv("MONTIVO_BUDGET_USD", "15")
    return d


def _judge(app, jid):
    c = TestClient(app)
    c.cookies.set(H.JUDGE, jid)
    return c


def _until(cond, s=15.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < s:
        time.sleep(0.05)
    return cond()


# --------------------------------------------------------------------------- 1. the sign-in (Must 5)

def test_wrong_passwords_with_made_up_addresses_never_lock_a_judge_out(tmp_path, hosted_env):
    _run_folder(tmp_path)
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")
    _no_sleep(hosted_env)
    app = A.create_app(tmp_path)
    bot = TestClient(app)
    for i in range(H.LOGIN_FAILS_ALL_PER_MIN + 5):  # a new first hop each time, the proxy's own hop last
        bot.post("/api/login", data={"password": "no"}, follow_redirects=False,
                 headers={"x-forwarded-for": f"10.0.{i // 250}.{i % 250}, 198.51.100.7"})
    r = bot.post("/api/login", data={"password": "no"}, follow_redirects=False,
                 headers={"x-forwarded-for": "10.9.9.9, 198.51.100.7"})
    assert r.status_code == 429  # held back as the one address it is, whatever it writes first
    judge = TestClient(app)
    r = judge.post("/api/login", data={"password": "pw"}, headers={"x-forwarded-for": "203.0.113.9"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.cookies.get(H.COOKIE)  # was 429: the global limit had tripped
    r = bot.post("/api/login", data={"password": "pw"}, headers={"x-forwarded-for": "1.2.3.4, 198.51.100.7"},
                 follow_redirects=False)
    assert r.status_code == 303  # a right password is never a 429


def test_the_sign_in_address_is_the_proxys_last_hop(tmp_path):
    class R:
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff} if xff is not None else {}
            self.client = type("C", (), {"host": "127.0.0.1"})()
    assert H.client(R("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert H.client(R("203.0.113.9")) == "203.0.113.9"
    assert H.client(R(None)) == "127.0.0.1"


# --------------------------------------------------------------------------- 2. a judge's share, the brief

def test_one_judge_cannot_spend_more_than_their_share(tmp_path, hosted_env):
    d = _sample(tmp_path, hosted_env)
    fake_jobs(hosted_env)
    mine = tmp_path / "runs" / "r2"
    shutil.copytree(d, mine)
    (mine / "ui").mkdir(exist_ok=True)
    (mine / "ui" / "owner").write_text("aa11")
    app = A.create_app(tmp_path)  # adopts r1 and r2 as they are
    ledger(mine, 0.36 + 2.98)  # aa11 spent $2.98 on the host
    a = _judge(app, "aa11")
    r = a.post("/api/runs/r2/review", json={"passes": 1})
    assert r.status_code == 409 and "each judge" in r.json()["error"]
    state = a.get("/api/runs/r2").json()
    assert state["budget"]["judge"] == {"cap": 3.0, "spent": 2.98, "left": 0.02} and state["budget"]["left"] == 12.02
    assert state["has_key"] is True and state["key_note"] is None  # $0.02 still covers the cheapest call
    b = _judge(app, "bb22")  # another judge has their whole share
    assert b.post("/api/runs/r1/review", json={"passes": 1}).status_code == 200
    ledger(mine, 0.36 + 3.0)
    assert "each judge" in a.get("/api/runs/r2").json()["key_note"]
    assert b.get("/api/runs").json()["key_note"] is None


def test_a_new_runs_brief_is_one_line_of_2000_characters_and_its_probe_runs_off_the_loop(tmp_path, monkeypatch):
    (tmp_path / "footage").mkdir()
    _clip(tmp_path / "footage" / "c.mp4", size="320x240", seconds=1.0)
    seen = {}
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None: seen.setdefault("args", args) and
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    monkeypatch.setenv("MODEL_API_KEY", "test")
    on_loop, real = [], media.probe_duration

    def probe(p, timeout=None):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(p, timeout)
    monkeypatch.setattr(media, "probe_duration", probe)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/runs", json={"footage": "footage/c.mp4", "brief": "make it short\n" * 600, "id": "n1"})
    assert r.status_code == 200, r.text
    brief = seen["args"][seen["args"].index("--brief") + 1]
    assert len(brief) == 2000 and "\n" not in brief
    assert on_loop == [False]


# --------------------------------------------------------------------------- 3. a sample stays the sample

def test_a_cover_made_on_a_sample_is_the_judges_own(tmp_path, hosted_env):
    from PIL import Image

    import montivo.engine.thumb as T
    d = _sample(tmp_path, hosted_env)
    hosted_env.setattr(T, "cover_image", lambda run_dir, v, design, aspect: (
        Image.new("RGB", (90, 160)), {"template": design["template"], "t_s": design["t_s"]}))
    hosted_env.setattr(T, "tile", lambda img: img)
    app = A.create_app(tmp_path)
    a, b = _judge(app, "aa11"), _judge(app, "bb22")
    r = a.post("/api/runs/r1/cover/render", json={"v": 2, "t": 1.0, "template": "headline", "text": "A's own words"})
    assert r.status_code == 200 and "/judges/aa11/" in r.json()["image"]
    assert a.get(r.json()["image"]).status_code == 200
    assert [x["template"] for x in a.get("/api/runs/r1/assets?v=2").json()["covers"]] == ["headline"]
    assert b.get("/api/runs/r1/assets?v=2").json()["covers"] == []  # judge 2 used to see judge 1's cover
    assert not list((d / "ui" / "cache" / "cover").glob("*.jpg")) and not list(tmp_path.glob("runs/r1-*"))


def test_paid_suggestions_on_a_sample_are_spent_in_the_judges_copy(tmp_path, hosted_env):
    import montivo.engine.look as L
    import montivo.engine.titles as Ti
    d = _sample(tmp_path, hosted_env)
    seen = []
    hosted_env.setattr(L, "suggest", lambda run_dir, edl, text, led, log: seen.append(run_dir.name) or (
        {"preset": "box"}, "a box", []))
    hosted_env.setattr(Ti, "suggest", lambda run_dir, edl, brief, led, log: seen.append(run_dir.name) or [
        {"text": "Hook", "why": "w"}])
    c = _judge(A.create_app(tmp_path), "aa11")
    r = c.post("/api/runs/r1/style/suggest", json={"prompt": "bold", "v": 2})
    assert r.status_code == 200 and r.json()["run"] == "r1-aa11" and r.json()["look"] == {"preset": "box"}
    r = c.post("/api/runs/r1/title/suggest", json={"v": 2})
    assert r.status_code == 200 and r.json()["run"] == "r1-aa11"
    assert seen == ["r1-aa11", "r1-aa11"]
    assert not (d / "run.log").exists() and (tmp_path / "runs" / "r1-aa11" / "run.log").is_file()


# --------------------------------------------------------------------------- 4. timeline media

def test_timeline_media_is_replaced_never_rewritten_in_place(tmp_path, monkeypatch):
    from PIL import Image
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    sample = tmp_path / "sample" / "peaks_source.json"
    sample.parent.mkdir()
    sample.write_text('{"rate": 100, "peaks": [0.5]}')
    copy = tmp_path / "copy" / "peaks_source.json"
    copy.parent.mkdir()
    os.link(sample, copy)  # copy_sample hard-links ui/cache/edit
    os.utime(copy, (0, 0))  # older than the source: rebuilt
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": b"\x00\x40" * 160})())
    media.peaks(src, copy)
    assert sample.read_text() == '{"rate": 100, "peaks": [0.5]}' and json.loads(copy.read_text())["peaks"][0] == 0.5
    strip = tmp_path / "strip.jpg"
    Image.new("RGB", (media.THUMB_W * 4, media.THUMB_H), "red").save(strip)
    shared = tmp_path / "sample" / "filmstrip.jpg"
    Image.new("RGB", (8, 8), "blue").save(shared)
    mine = tmp_path / "copy" / "filmstrip.jpg"
    os.link(shared, mine)
    before = shared.read_bytes()
    media.draft_filmstrip(strip, [{"cut_t0": 0.0, "t_in": 0.0}], 1.5, 0.0, mine)
    assert shared.read_bytes() == before and Image.open(mine).size == (media.THUMB_W * 4, media.THUMB_H)


def test_a_drafts_filmstrip_is_drawn_in_its_own_folder(tmp_path, monkeypatch):
    from PIL import Image

    from montivo.server.app import create_app
    from tests.test_draft import TALK, _run
    d = _run(tmp_path, monkeypatch, TALK)
    strip = tmp_path / "strip.jpg"
    Image.new("RGB", (media.THUMB_W * 20, media.THUMB_H)).save(strip)
    monkeypatch.setattr(A.live_preview_engine, "edit_media", lambda run_dir, edl: {"video": "v.mp4", "audio": "a.flac"})
    monkeypatch.setattr(media, "filmstrip", lambda src, out: strip)
    c = TestClient(create_app(tmp_path))
    assert c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}, "from": 1}).status_code == 200
    assert c.get("/api/runs/h1/filmstrip?draft=1").status_code == 200
    assert (d / "ui" / "draft" / "filmstrip.jpg").is_file()
    assert not (d / "ui" / "cache" / "edit" / "filmstrip_draft.jpg").exists()  # one file per run, every judge's


# --------------------------------------------------------------------------- 5. jobs

def test_a_job_ends_even_when_its_clean_up_fails(tmp_path, monkeypatch):
    _run_folder(tmp_path)
    st = A.State(tmp_path)

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(A, "set_aside_unrendered", boom)
    job = A.start_job(st, "r1", "drop", "Drop clip", ["-c", "raise SystemExit(3)"])
    assert _until(lambda: job.ended is not None), "the job stayed running for good"
    assert job.rc == 3 and not st.busy("r1")


def test_a_stuck_job_is_stopped_by_the_wall_clock_limit(tmp_path):
    d = _run_folder(tmp_path)
    st = A.State(tmp_path)
    st.job_max_s = 0.5
    job = A.start_job(st, "r1", "drop", "Drop clip", ["-c", "import time; time.sleep(60)"])
    assert _until(lambda: job.ended is not None), "a stuck job kept its slot"
    assert job.timed_out and job.rc < 0 and not st.busy("r1")
    last = json.loads((d / "ui" / "chat.jsonl").read_text().splitlines()[-1])
    assert "past the demo server's limit" in last["text"]
    assert A.JOB_MAX_S == 900


def test_the_budget_holds_up_beside_the_copy_threads(tmp_path):
    (tmp_path / "runs").mkdir()
    b = H.Budget(tmp_path, 15)
    errors = []

    def adopt(k):
        for i in range(400):
            try:
                b.adopt([f"x{k}-{i}"])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    def read():
        for _ in range(400):
            try:
                b.save()
                b.public()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
    ts = [threading.Thread(target=adopt, args=(k,)) for k in range(2)] + [threading.Thread(target=read) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and len(json.loads((tmp_path / "budget.json").read_text())["baseline"]) == 800


# --------------------------------------------------------------------------- 6. upload caps

def test_upload_caps_default_to_3_a_judge_and_60_in_all(tmp_path, hosted_env):
    _sample(tmp_path, hosted_env)
    (tmp_path / "footage").mkdir()
    (tmp_path / "footage" / ".owners.json").write_text(json.dumps({f"c{i}.mp4": "aa11" for i in range(3)}))
    c = _judge(A.create_app(tmp_path), "aa11")
    assert c.get("/api/health").json()["limits"]["uploads"] == 3 and A.MAX_UPLOADS_ALL == 60
    r = c.put("/api/footage/d.mp4", content=b"x")
    assert r.status_code == 429 and "3 uploads per judge" in r.json()["error"]
    hosted_env.setenv("MONTIVO_MAX_UPLOADS", "5")  # the environment still decides
    assert _judge(A.create_app(tmp_path), "aa11").get("/api/health").json()["limits"]["uploads"] == 5


# --------------------------------------------------------------------------- 7. no queue behind a busy lock

def test_a_second_export_and_an_archive_answer_409_while_an_export_is_made(tmp_path, monkeypatch):
    d = _run_folder(tmp_path)
    (d / "cut_v2.mp4").write_bytes(b"cut")
    started, gate = threading.Event(), threading.Event()

    def run(args, **k):
        started.set()
        gate.wait(10)
        out = args[args.index("--out") + 1]
        os.makedirs(out, exist_ok=True)
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"files": [], "zip": None}), "stderr": ""})()
    monkeypatch.setattr(A.subprocess, "run", run)
    app = A.create_app(tmp_path)
    first = []
    t = threading.Thread(target=lambda: first.append(TestClient(app).post(
        "/api/runs/r1/export/bundle", json={"v": 2, "include": ["srt"]})))
    t.start()
    try:
        assert started.wait(10)
        c = TestClient(app)
        t0 = time.time()
        r = c.post("/api/runs/r1/export/bundle", json={"v": 2, "include": ["transcript"]})
        assert r.status_code == 409 and time.time() - t0 < 5  # it used to wait for the first one's lock
        assert c.post("/api/runs/r1/archive").status_code == 409 and d.is_dir()
    finally:
        gate.set()
        t.join(10)
    assert first[0].status_code == 200
    assert TestClient(app).post("/api/runs/r1/export/bundle", json={"v": 2, "include": ["srt"]}).status_code == 200  # made before: served


def test_a_second_monti_question_answers_from_the_guide_at_once(tmp_path, monkeypatch):
    import montivo.monti as M
    monkeypatch.setenv("MODEL_API_KEY", "test")
    started, gate = threading.Event(), threading.Event()

    def ask(root, message, ctx, history):
        started.set()
        gate.wait(10)
        return M.fallback(message, "editor") | {"source": "spark", "model": "m", "cost_usd": 0.01, "seconds": 0.1}
    monkeypatch.setattr(M, "ask", ask)
    app = A.create_app(tmp_path)
    first = []
    t = threading.Thread(target=lambda: first.append(TestClient(app).post("/api/monti", json={"message": "how do I trim"})))
    t.start()
    try:
        assert started.wait(10)
        t0 = time.time()
        r = TestClient(app).post("/api/monti", json={"message": "how do I export"})
        assert r.status_code == 200 and r.json()["source"] == "guide" and "another question" in r.json()["why"]
        assert time.time() - t0 < 5
    finally:
        gate.set()
        t.join(10)
    assert first[0].json()["source"] == "spark"


# --------------------------------------------------------------------------- 8. the smaller ones

def test_bad_numbers_and_text_that_is_not_text_are_a_400_not_a_500(tmp_path, monkeypatch):
    _run_folder(tmp_path)
    fake_jobs(monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "test")
    c = TestClient(A.create_app(tmp_path), raise_server_exceptions=False)
    big = {"content-type": "application/json"}
    assert c.post("/api/runs/r1/export/bundle", content=b'{"v": 1e400, "include": ["srt"]}', headers=big).status_code == 400
    assert c.post("/api/runs/r1/drop", content=b'{"span": "s1", "from": 1e400}', headers=big).status_code == 400
    assert c.post("/api/runs/r1/ask", json={"message": 5}).status_code < 500
    assert c.post("/api/runs/r1/replan", json={"instruction": 5}).status_code < 500
    assert c.post("/api/runs/r1/ask", json={"message": ["a"]}).status_code < 500


def test_a_replay_stream_ends_and_does_not_play_again(tmp_path):
    d = _run_folder(tmp_path)
    (d / "run.log").write_text("[  0.0s] montivo run: footage/x.mp4\n[  0.1s] plan: 3 spans\n")
    c = TestClient(A.create_app(tmp_path))
    out = []
    t = threading.Thread(target=lambda: out.append(c.get("/api/runs/r1/events?replay=4").text), daemon=True)
    t.start()
    t.join(15)
    assert not t.is_alive(), "the replay stream never ended"
    assert "event: replay_end" in out[0] and "retry: 86400000" in out[0]


def test_a_sample_is_not_copied_for_a_body_the_route_refuses(tmp_path, hosted_env):
    _sample(tmp_path, hosted_env)
    fake_jobs(hosted_env)
    c = _judge(A.create_app(tmp_path), "aa11")
    assert c.post("/api/runs/r1/drop", json={"span": "s1", "from": 99}).status_code == 404
    assert c.post("/api/runs/r1/drop", json={"span": "s1 s2"}).status_code == 400
    assert c.post("/api/runs/r1/drop", json=["s1"]).status_code == 400
    assert c.post("/api/runs/r1/trim", json={"span": "s1", "from": "x"}).status_code == 400
    assert not list(tmp_path.glob("runs/r1-*"))
    assert c.post("/api/runs/r1/drop", json={"span": "s1", "from": 2}).status_code == 200  # a good one still copies
    assert (tmp_path / "runs" / "r1-aa11" / "run.json").is_file()


def test_the_rendered_route_reads_the_runs_audio_off_the_loop(tmp_path, monkeypatch):
    from montivo.server import draft
    from tests.test_draft import TALK, _run
    _run(tmp_path, monkeypatch, TALK)
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None:
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    on_loop, real = [], draft.inputs

    def inputs(run_dir):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(run_dir)
    monkeypatch.setattr(draft, "inputs", inputs)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/runs/h1/op", json={"op": "text", "args": {"callouts": {"s2": "Four"}}, "from": 1})
    assert r.status_code == 200, r.text
    assert on_loop and not any(on_loop)
