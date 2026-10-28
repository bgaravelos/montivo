"""Batch C (UX B2, B3, B5): Ask Montivo previews a structural or costly change and runs it on Apply, reads the
selection as scope, offers one more try, and a failed job can run again; share links can be removed (Unshare)."""
import json

from montivo import agent, human
from tests.test_agent import op

SPANS = [{"id": "s1", "role": "hook", "turn_id": 0, "word_from": 0, "word_to": 3, "t_in": 0.0, "t_out": 2.0,
          "duration": 2.0, "words": "Hi I am Billy"},
         {"id": "s2", "role": "body", "turn_id": 0, "word_from": 4, "word_to": 9, "t_in": 3.0, "t_out": 6.0,
          "duration": 3.0, "words": "we cut short form video here"},
         {"id": "s3", "role": "payoff", "turn_id": 0, "word_from": 10, "word_to": 13, "t_in": 7.0, "t_out": 9.0,
          "duration": 2.0, "words": "and it reviews itself"}]
WORDS = [{"turn_id": 0, "i": i, "w": w, "t_start": 0.5 * i, "t_end": 0.5 * i + 0.4}
         for i, w in enumerate("Hi I am Billy we cut short form video here and it reviews itself".split())]


def _run(tmp_path):
    d = tmp_path / "r"
    (d / "voice").mkdir(parents=True)
    (d / "voice" / "voice.json").write_text(json.dumps({"words": WORDS}))
    (d / "run.json").write_text(json.dumps({"brief": "b", "target_s": 30}))
    (d / "edl_v3.json").write_text(json.dumps({"version": 3, "spans": SPANS, "runtime_s": 7.0, "patches": []}))
    return d


def test_a_structural_ask_waits_for_apply_and_apply_runs_it_once(tmp_path, monkeypatch):
    d = _run(tmp_path)
    monkeypatch.setattr(agent, "route", lambda *a, **k: ({"reply": "Dropping s2 and adding lo-fi.", "question": "",
                                                          "ops": [op("drop", span="s2"),
                                                                  op("music_on", track="lofi_hip_hop")]}, 0.004))
    calls = []
    monkeypatch.setattr(human, "drop", lambda run_dir, span, from_v=None, **k: calls.append(("drop", span, from_v)))
    monkeypatch.setattr(human, "music", lambda run_dir, track, from_v=None, **k: calls.append(("music", track, from_v)))
    rec = agent.ask(d, "drop the weak clip and add music", preview=True, passes=1)
    card = rec["pending"]
    assert rec["version"] is None and calls == []  # nothing ran
    assert card["lines"][0] == "3 → 2 clips, about 4 s (now 7.0 s)"
    assert card["lines"][1].startswith("s2 goes: “we cut short form") and card["lines"][-1].startswith("Also: music: ")
    assert card["usd"] == agent.EST["review"] and card["can_retry"] and card["review"] == 1
    saved = json.loads((d / "ui" / "pending" / f"{card['id']}.json").read_text())
    assert saved["status"] == "open" and [o["op"] for o in saved["full_ops"]] == ["music_on", "drop"]

    written = []
    monkeypatch.setattr(agent, "run_ops", lambda run_dir, from_v, ops, planner, log: (written.append([o["op"] for o in ops]) or ([], ["x"], [])))
    out = agent.apply(d, card["id"], passes=0)
    assert written == [["music_on", "drop"]] and out["pending"] == card["id"] and out["router_cost_usd"] == 0.0
    assert json.loads((d / "ui" / "pending" / f"{card['id']}.json").read_text())["status"] == "applied"
    try:
        agent.apply(d, card["id"])
        raise AssertionError("an applied proposal must refuse a second apply")
    except SystemExit as e:
        assert "already applied" in str(e)


def test_cheap_ops_apply_at_once_and_a_replan_is_previewed_without_planning(tmp_path, monkeypatch):
    d = _run(tmp_path)
    assert not agent.needs_preview([op("music_on", track="ukulele"), op("style", preset="bold_pop"), op("punch", on=True)])
    assert not agent.needs_preview([op("style", look_prompt="huge yellow"), op("title_text", text="Hi")])  # $0.018
    assert agent.estimate([op("style", look_prompt="x"), op("title_text", text="Hi")], None, 1, 10.0) == (0.053, 55)
    assert agent.needs_preview([op("reorder", span="s3", before="s1")])
    monkeypatch.setattr(agent, "route", lambda *a, **k: ({"reply": "Re-planning to 20 s.", "question": "",
                                                          "ops": [op("replan", instruction="make it 20 seconds")]}, 0.004))
    monkeypatch.setattr(human, "replan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("planned in a preview")))
    card = agent.ask(d, "make it 20 seconds", preview=True)["pending"]
    assert card["replan"] and card["lines"][0] == "Plan the edit again: “make it 20 seconds”"
    assert "aiming at about 20 s" in card["lines"][1] and card["usd"] == agent.EST["plan"]


def test_try_something_else_routes_once_more_with_the_turned_down_proposal(tmp_path, monkeypatch):
    d = _run(tmp_path)
    prompts = []

    def fake(run_dir, edl, message, ledger, log, scope=None, note=""):
        prompts.append(note)
        return {"reply": "ok", "question": "", "ops": [op("drop", span="s3" if note else "s2")]}, 0.004
    monkeypatch.setattr(agent, "route", fake)
    first = agent.ask(d, "make it shorter", preview=True, passes=1)["pending"]
    second = agent.ask(d, None, preview=True, retry_of=first["id"])
    assert "turned down" in prompts[1] and "s2 goes" in prompts[1]
    assert second["message"] == "make it shorter" and second["retry_of"] == first["id"]
    assert second["pending"]["can_retry"] is False and second["pending"]["review"] == 0  # once, no review
    assert json.loads((d / "ui" / "pending" / f"{first['id']}.json").read_text())["status"] == "retried"


def test_the_selection_scopes_the_ops_and_the_prompt(tmp_path, monkeypatch):
    d = _run(tmp_path)
    scope = agent.norm_scope({"clips": ["s2", "bad id"], "words": [{"span": "s2", "from": 5, "to": 7, "text": "cut short form"}],
                              "caption": {"t_in": 1.0, "t_out": 2.5, "text": "Hi I am"}, "junk": 1})
    assert scope["clips"] == ["s2"] and scope["words"][0]["to"] == 7 and "junk" not in scope
    edl = json.loads((d / "edl_v3.json").read_text())
    ops, skipped = agent.check(edl, [op("drop", span="s1"), op("cut", span="s2", word_from=5, word_to=6),
                                     op("cut", span="s2", word_from=4, word_to=6), op("caption", at_s=4.0, text="x"),
                                     op("callout", span="s2", text="Short form")], scope)
    assert [o["op"] for o in ops] == ["callout", "cut"] and ops[1]["word_from"] == 5
    assert any("outside your selection" in x and "s1" in x for x in skipped)
    assert any("words outside your selection" in x for x in skipped) and any("selected line" in x for x in skipped)
    seen = {}

    def fake(run_dir, edl, message, ledger, log, scope=None, note=""):
        seen["text"] = agent.scope_text(scope)
        seen["context"] = agent.context(run_dir, edl)
        return {"reply": "Shorter.", "question": "", "ops": [op("cut", span="s2", word_from=5, word_to=6)]}, 0.004
    monkeypatch.setattr(agent, "route", fake)
    rec = agent.ask(d, "make this shorter", preview=True, scope={"words": scope["words"]})
    assert "the words 5-7 of s2 ('cut short form')" in seen["text"] and "numbered words: 4 we 5 cut" in seen["context"]
    assert rec["pending"]["lines"][1] == "s2 loses 2 words: “cut short”" and rec["scope"]["words"][0]["span"] == "s2"


def test_the_keyword_reader_deletes_and_moves_what_is_named_or_selected():
    edl = {"spans": SPANS}
    assert [(o["op"], o["span"]) for o in agent.keywords(edl, "delete s2")["ops"]] == [("drop", "s2")]
    assert [(o["op"], o["span"]) for o in agent.keywords(edl, "σβήσε αυτά", {"clips": ["s1", "s3"]})["ops"]] == \
        [("drop", "s1"), ("drop", "s3")]
    cut = agent.keywords(edl, "delete these words", {"words": [{"span": "s2", "from": 5, "to": 6, "text": ""}]})["ops"][0]
    assert (cut["op"], cut["span"], cut["word_from"], cut["word_to"]) == ("cut", "s2", 5, 6)
    mv = agent.keywords(edl, "move s3 before s1")["ops"][0]
    assert (mv["op"], mv["span"], mv["before"]) == ("reorder", "s3", "s1")
    assert agent.keywords(edl, "remove the music")["ops"][0]["op"] == "music_off"  # not a clip delete
    assert agent.keywords(edl, "delete this")["ops"] == []  # nothing selected, nothing named


def test_the_routes_preview_apply_retry_and_try_again(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server import app as A
    from tests.test_ui import _run_folder
    d = _run_folder(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    started, box = [], {}

    def fake(st, run_id, kind, label, args, chat=None):  # every job ends at once, rc 0
        box["st"] = st
        j = A.Job(id=f"j{len(started)}", run=run_id, kind=kind, label=label, cmd=["python", *args], out="")
        j.ended, j.rc = 1.0, 0
        st.jobs[run_id] = j
        started.append({"kind": kind, "args": args, "chat": chat})
        return j
    monkeypatch.setattr(A, "start_job", fake)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/runs/r1/ask", json={"message": "delete this", "chips": ["s2"], "scope": {"clips": ["s2"]},
                                         "preview": True, "from": 1})
    args = started[-1]["args"]
    assert r.status_code == 200 and "--preview" in args and json.loads(args[args.index("--scope") + 1]) == {"clips": ["s2"]}

    pend = d / "ui" / "pending"
    pend.mkdir(parents=True)
    write = lambda pid, **k: (pend / f"{pid}.json").write_text(json.dumps({"id": pid, "status": "open", "passes": 0,  # noqa: E731
                                                                          "card": {"usd": 0.0}} | k))
    write("p1234")
    r = c.post("/api/runs/r1/ask/apply", json={"pending": "p1234", "review": 1})
    args = started[-1]["args"]
    assert r.status_code == 200 and args[args.index("--apply") + 1] == "p1234" and args[args.index("--review") + 1] == "0"
    assert started[-1]["chat"] == {"role": "user", "kind": "apply", "pending": "p1234", "text": "Apply"}
    assert c.post("/api/runs/r1/ask/apply", json={"pending": "nope!"}).status_code == 404
    write("p5678", card={"usd": 0.05})  # a re-plan: Apply calls the planner
    assert c.post("/api/runs/r1/ask/apply", json={"pending": "p5678"}).status_code == 409
    write("pdone", status="applied")
    assert "already applied" in c.post("/api/runs/r1/ask/apply", json={"pending": "pdone"}).json()["error"]

    r = c.post("/api/runs/r1/ask/retry", json={"pending": "p1234"})
    args = started[-1]["args"]
    assert r.status_code == 200 and args[args.index("--retry") + 1] == "p1234" and "--no-model" in args
    assert started[-1]["chat"]["kind"] == "retry"
    write("pagain", retry_of="p1234")
    assert c.post("/api/runs/r1/ask/retry", json={"pending": "pagain"}).status_code == 409  # one more try per message

    assert c.post("/api/runs/r1/retry", json={}).status_code == 409  # the last job did not fail
    st = box["st"]
    st.jobs["r1"].rc = 1
    n = len(started)
    assert c.post("/api/runs/r1/retry", json={}).status_code == 200 and started[n]["args"] == started[n - 1]["args"]
    assert started[n]["chat"]["kind"] == "retry_job"
    st.jobs["r1"] = A.Job(id="rv", run="r1", kind="review", label="Blind review", out="",
                          cmd=["python", "-m", "montivo.cli", "review", "runs/r1", "--passes", "1"])
    st.jobs["r1"].ended, st.jobs["r1"].rc = 1.0, 1
    assert c.post("/api/runs/r1/retry", json={}).status_code == 409  # a review needs the key


def test_the_chat_line_of_a_preview_and_of_a_failure(tmp_path):
    from montivo.server import app as A
    d = tmp_path / "runs" / "r"
    d.mkdir(parents=True)
    out = tmp_path / "job.out"
    rec = {"message": "drop s2", "reply": "Dropping s2.", "from": 3, "router": "keywords", "scope": {"clips": ["s2"]},
           "pending": {"id": "p1", "lines": ["3 → 2 clips"], "usd": 0.0, "seconds": 4, "can_retry": True}}
    out.write_text("montivo ask: ...\n" + json.dumps(rec))
    st = A.State(tmp_path)
    job = A.Job(id="j", run="r", kind="ask", label="Ask Montivo", cmd=[], out=str(out))
    job.rc = 0
    line = A.result_message(st, job)
    assert line["kind"] == "preview" and line["pending"]["id"] == "p1" and line["after"] == 3 and line["scope"] == {"clips": ["s2"]}
    job.rc, job.charged = 1, 0.0
    assert "Nothing was charged." in A.result_message(st, job)["text"]
    job.charged = 0.012
    assert "spent $0.012" in A.result_message(st, job)["text"]


def test_unshare_removes_the_link_and_a_new_share_gets_a_new_token(tmp_path):
    from montivo.server.share import Shares
    s = Shares(tmp_path)
    a, b = s.create("015", 5), s.create("015", 3)
    s.create("other", 1)
    assert [x["version"] for x in s.of_run("015")] == [5, 3] and s.of_run("015")[0]["path"] == f"/s/{a}"
    assert not s.delete("other", a) and s.delete("015", a)
    assert [x["token"] for x in s.of_run("015")] == [b] and s.create("015", 5) != a
