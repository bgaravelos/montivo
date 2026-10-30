"""Ask Montivo (montivo/agent.py): the router's ops checked and ordered, the keyword reader, the chain of human edits
folded into one rendered version, a reply without a version, and the /ask route without the key."""
import json

from montivo import agent, human

BLANK = {"span": "", "before": "", "text": "", "at_s": -1, "track": "", "level_db": 0, "on": False, "preset": "",
         "look_prompt": "", "instruction": "", "word_from": -1, "word_to": -1, "word": -1, "edge": "", "turn": -1,
         "version": -1, "speaker": "", "name": "", "head": "", "heard": "", "written": "", "panel": ""}
SPANS = [{"id": "s1", "role": "hook", "t_in": 0.0, "t_out": 1.0, "words": "hi"},
         {"id": "s2", "role": "payoff", "t_in": 2.0, "t_out": 3.0, "words": "bye"}]


def op(op_name, **kw):
    return BLANK | {"op": op_name} | kw


def _run(tmp_path, edl=None):
    d = tmp_path / "r"
    (d / "voice").mkdir(parents=True)
    (d / "voice" / "voice.json").write_text(json.dumps({"words": []}))
    (d / "run.json").write_text(json.dumps({"brief": "b", "target_s": 30}))
    (d / "edl_v3.json").write_text(json.dumps(edl or {"version": 3, "spans": [], "runtime_s": 2.0, "patches": [
        {"from": 2, "source": "review", "action": "review", "fixes": []}]}))
    return d


def test_ops_are_checked_against_the_version_and_ordered():
    edl = {"spans": SPANS}
    ops, skipped = agent.check(edl, [op("drop", span="s9"), op("punch", on=True), op("music_on", track="jazz"),
                                     op("music_on", track="ukulele"), op("style", preset="bold_pop"),
                                     op("caption", at_s=1.2, text="Hi there"), op("style"), op("fly")])
    assert [o["op"] for o in ops] == ["caption", "style", "music_on", "punch"]
    assert len(skipped) == 4 and any("s9" in s for s in skipped) and any("jazz" in s for s in skipped)
    # a re-plan makes new clips: edits naming this version's clips or lines wait for the next message
    ops, skipped = agent.check(edl, [op("drop", span="s2"), op("replan", instruction="make it 20 s"),
                                     op("music_on", track="ukulele")])
    assert [o["op"] for o in ops] == ["replan", "music_on"] and "re-plan makes new clips" in skipped[0]


def test_the_keyword_reader_without_a_key():
    edl = {"spans": SPANS}
    assert [(o["op"], o["track"]) for o in agent.keywords(edl, "Βάλε μουσική")["ops"]] == [("music_on", "chill_lofi")]
    assert agent.keywords(edl, "put the ukulele track under it")["ops"][0]["track"] == "ukulele"
    assert agent.keywords(edl, "βγάλε τη μουσική")["ops"][0]["op"] == "music_off"
    a = agent.keywords(edl, "bold pop captions and punch-ins")
    assert [o["op"] for o in a["ops"]] == ["punch", "style"] and a["ops"][1]["preset"] == "bold_pop" and a["ops"][0]["on"]
    assert agent.keywords(edl, "χωρίς τίτλο")["ops"][0]["op"] == "title_off"
    none = agent.keywords(edl, "make it 30 seconds")
    assert none["ops"] == [] and "MODEL_API_KEY" in none["reply"]


def test_one_message_makes_one_version_rendered_once(tmp_path, monkeypatch):
    d = _run(tmp_path)
    monkeypatch.setattr(agent, "route", lambda *a, **kw: ({"reply": "Lo-fi under it, bold captions, punch-ins on.",
                                                     "question": "", "ops": [op("punch", on=True),
                                                                             op("music_on", track="lofi_hip_hop"),
                                                                             op("style", preset="bold_pop"),
                                                                             op("drop", span="s7")]}, 0.004))
    renders = []
    monkeypatch.setattr(human, "render_run", lambda run_dir, edl_path, out_name, **k: renders.append(out_name))
    rec = agent.ask(d, "βάλε lofi μουσική, bold captions και punch-ins")
    assert rec["version"] == 4 and renders == ["cut_v4.mp4"] and human.DEFER is None
    assert sorted(p.name for p in d.glob("edl_v*.json")) == ["edl_v3.json", "edl_v4.json"]  # the chain v4-v6 folded
    e = json.loads((d / "edl_v4.json").read_text())
    assert e["version"] == 4 and e["music"]["track"] == "lofi_hip_hop" and e["punch"] is True
    assert e["look"]["preset"] == "bold_pop"
    assert len(e["patches"]) == 2  # v3's own and one for the whole message
    p = e["patches"][-1]
    assert p["from"] == 3 and p["action"] == "ask" and p["instruction"].startswith("βάλε")
    assert [f["op"] for f in p["fixes"]] == ["look", "music", "punch_on"]
    assert p["agent"]["reply"].startswith("Lo-fi") and p["agent"]["router"] == "muse-spark-1.3"
    assert p["agent"]["done"] == ["captions: Bold pop", "music lofi_hip_hop", "punch-ins on"]
    assert any("s7" in s for s in p["agent"]["skipped"])


def test_a_single_edit_keeps_its_own_action(tmp_path, monkeypatch):
    d = _run(tmp_path)
    monkeypatch.setattr(human, "render_run", lambda *a, **k: None)
    rec = agent.ask(d, "βάλε μουσική", use_model=False)
    e = json.loads((d / "edl_v4.json").read_text())
    assert rec["router"] == "keywords" and e["patches"][-1]["action"] == "music"
    assert e["patches"][-1]["agent"]["message"] == "βάλε μουσική"


def test_a_question_gets_a_reply_and_no_version(tmp_path, monkeypatch):
    d = _run(tmp_path)
    monkeypatch.setattr(agent, "route", lambda *a, **kw: ({"reply": "v3 is 2 s long.", "question": "", "ops": []}, 0.003))
    rec = agent.ask(d, "πόσο διαρκεί;")
    assert rec["version"] is None and rec["reply"] == "v3 is 2 s long."
    assert sorted(p.name for p in d.glob("edl_v*.json")) == ["edl_v3.json"]


def test_an_edit_the_version_refuses_is_reported(tmp_path, monkeypatch):
    d = _run(tmp_path, {"version": 3, "spans": [], "runtime_s": 2.0, "punch": True})
    monkeypatch.setattr(agent, "route", lambda *a, **kw: ({"reply": "Punch-ins on.", "question": "", "ops": [op("punch", on=True)]}, 0.0))
    rec = agent.ask(d, "punch-ins on")
    assert rec["version"] is None and rec["done"] == [] and "already on" in rec["skipped"][0]


def test_the_ask_route_without_the_key_reads_keywords(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server import app as A
    from tests.test_ui import _run_folder
    _run_folder(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    seen = {}

    def fake(st, run_id, kind, label, args, chat=None):
        seen.update(kind=kind, args=args, chat=chat)
        return A.Job(id="j", run=run_id, kind=kind, label=label, cmd=args, out="")
    monkeypatch.setattr(A, "start_job", fake)
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/r1/ask", json={"message": " "}).status_code == 400
    r = c.post("/api/runs/r1/ask", json={"message": "add some music", "chips": ["s2"], "from": 3})
    assert r.status_code == 200 and seen["kind"] == "ask" and "--no-model" in seen["args"]
    assert seen["args"][seen["args"].index("--message") + 1] == "add some music (about s2)"
    assert seen["chat"]["text"] == "add some music" and seen["chat"]["kind"] == "ask"


WSPANS = [{"id": "s1", "role": "hook", "t_in": 0.0, "t_out": 1.0, "words": "hi there you", "turn_id": 0, "word_from": 0,
           "word_to": 2},
          {"id": "s2", "role": "payoff", "t_in": 2.0, "t_out": 3.0, "words": "bye now", "turn_id": 1, "word_from": 3,
           "word_to": 4}]


def test_the_editors_own_edits_are_checked_too():
    edl = {"version": 4, "spans": WSPANS, "runtime_s": 2.0}
    ops, skipped = agent.check(edl, [op("split", span="s1", word=1), op("split", span="s1", word=0),
                                     op("trim", span="s2", edge="out", word=3), op("trim", span="s2", word=3),
                                     op("insert", turn=0, word_from=5, word_to=7, before="s2"),
                                     op("insert", turn=0, word_from=5, word_to=5),
                                     op("glossary", heard="news", written="Muse"), op("speakers", speaker="A"),
                                     op("seek", at_s=1.5), op("seek", at_s=9.0), op("open", panel="music"),
                                     op("open", panel="nope"), op("pauses")])
    assert [o["op"] for o in ops] == ["glossary", "trim", "split", "insert", "pauses", "seek", "open"]
    assert len(skipped) == 6
    # a restore brings a version back as it was: the other edits wait, the playhead still moves
    ops, skipped = agent.check(edl, [op("restore", version=2), op("drop", span="s2"), op("seek", at_s=0.5)])
    assert [o["op"] for o in ops] == ["restore", "seek"] and "restore brings v2 back" in skipped[0]
    assert agent.check(edl, [op("restore", version=4)])[0] == []  # this version already
    # an insert adds to the cut: not with a selection; reorder takes "end" as the end
    assert agent.check(edl, [op("insert", turn=0, word_from=5, word_to=7)], {"clips": ["s1"]})[0] == []
    assert agent.check(edl, [op("reorder", span="s1", before="end")])[0][0]["before"] == ""


def test_pure_ops_name_the_editors_edits():
    assert agent.pure(None, op("insert", turn=2, word_from=5, word_to=9, before="end"))[1:] == (
        "insert", {"turn": 2, "a": 5, "b": 9, "before": "end"})
    assert agent.pure(None, op("insert", turn=2, word_from=5, word_to=9))[2]["before"] is None
    assert agent.pure(None, op("glossary", heard="news ", written="Muse"))[2] == {"add": {"news": "Muse"}}
    assert agent.pure(None, op("pin", speaker="B", head=""))[2] == {"speaker": "B", "head": None}
    assert all(agent.pure(None, op(n, span="s1", edge="in", word=1, speaker="A", name="Ann", heard="a", written="b"))[1]
               in human.OPS for n in ("trim", "split", "pauses", "speakers"))


def test_the_router_reads_the_chat_and_the_recording(tmp_path):
    d = _run(tmp_path, {"version": 3, "spans": WSPANS, "runtime_s": 2.0, "source": {"fps": 30}, "patches": []})
    words = [{"w": w, "i": i, "turn_id": t, "speaker": "A", "t_start": i * 0.5, "t_end": i * 0.5 + 0.4}
             for t, ws in ((0, ["hi", "there", "you", "okay", "so"]), (1, ["ah", "um", "er", "bye", "now"]))
             for i, w in enumerate(ws)]  # s1 keeps turn 0 words 0-2, s2 turn 1 words 3-4
    (d / "voice" / "voice.json").write_text(json.dumps({"words": words}))
    (d / "ui").mkdir()
    (d / "ui" / "chat.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"role": "user", "text": "put chill lofi under it"}, {"role": "agent", "text": "Chill Lofi.", "version": 3},
        {"role": "user", "text": "something livelier"}]))
    turns = agent.history(d, "something livelier")
    assert [t["text"] for t in turns] == ["put chill lofi under it", "Chill Lofi."]
    assert "(made v3)" in agent.history_text(turns) and agent.history_text([]) == ""
    rows = agent.footage(d, json.loads((d / "edl_v3.json").read_text()))
    assert any("turn 0" in r and "3 okay 4 so" in r for r in rows)  # what insert can put back, by turn and number
    assert any("turn 1" in r and "0 ah 1 um 2 er" in r for r in rows)


def test_live_asks_become_draft_ops():
    """A5: in Live the routed edits go into the editor's draft as its own ops; a re-plan, a still, a restore or a look
    from words still make a version."""
    ops = [op("caption", at_s=1.2, text="Hi there"), op("callout", span="s1", text="Hey"), op("title_on"),
           op("music_on", track="ukulele"), op("punch", on=False), op("drop", span="s2"),
           op("cut", span="s1", word_from=1, word_to=1), op("reorder", span="s1", before=""),
           op("insert", turn=0, word_from=3, word_to=4, before="end"), op("style", preset="karaoke")]
    d = agent.draft_ops(ops)
    assert [o["op"] for o in d] == ["caption", "text", "music", "punch", "drop", "style", "cut", "reorder", "insert"]
    assert d[0]["args"] == {"edits": [{"at": 1.2, "text": "Hi there"}]}
    assert d[1]["args"] == {"callouts": {"s1": "Hey"}, "title_card": True}
    assert d[2]["args"] == {"track": "ukulele", "level_db": None} and d[7]["args"] == {"span": "s1", "before": None}
    from montivo.server import draft
    assert all(draft.clean_op(o)["op"] == o["op"] for o in d)  # every one as the draft's own route takes it
    for no in (op("replan", instruction="x"), op("overlay", at_s=1, text="a"), op("restore", version=2),
               op("style", look_prompt="big yellow")):
        assert agent.draft_ops([op("punch", on=True), no]) is None
