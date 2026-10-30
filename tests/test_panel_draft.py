"""One draft for every panel (rail panels A3): the panels' edits (caption, text, style, music, pin, punch) as pure ops
in human.OPS give the CLI's EDL byte for byte, ride a draft next to the structural edits, and commit as one version
that is the draft's EDL; a caption edit is kept resolved to its line, so a replay retexts the same line."""
import json

import pytest

from montivo.engine.api import dump_json
from tests.test_draft import TALK, _bare, _client, _fake_jobs, _run


def _panel_run(tmp_path, monkeypatch):
    """test_draft's run with what the panels need: a rendered caption line on s2 and two tracked heads."""
    d = _run(tmp_path, monkeypatch, TALK)
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["captions"] = [{"id": "c1", "t_in": 2.7, "t_out": 3.6, "spans": ["s2"], "text": "meta meta models",
                        "words": [{"w": "meta", "t_in": 2.72, "t_out": 3.0}, {"w": "models", "t_in": 3.0, "t_out": 3.5}],
                        "placement": "below_head", "box": [0, 1300, 1080, 1400]}]
    edl["punch_ins"] = [{"key": "jump_cut:s2", "kind": "jump_cut", "span": "s2", "t_in": 2.7, "t_out": 3.2, "zoom": 1.06}]
    edl["punch"] = True
    (d / "edl_v1.json").write_text(json.dumps(edl))
    (d / "track" / "head").mkdir(parents=True)
    (d / "track" / "head" / "tracks.json").write_text(json.dumps({"objects": {"0": {}, "3": {}}}))
    return d


CASES = {
    "caption": ("caption", {"edits": [(3.1, "Four Meta models")]}, None),
    "callout": ("text", {"callouts": {"s2": "Four Meta"}}, {"op": "text", "args": {"callouts": {"s2": "Four Meta"}}}),
    "callout_off": ("text", {"callouts": {"s2": ""}}, {"op": "text", "args": {"callouts": {"s2": ""}}}),
    "title_off": ("text", {"title_card": False}, {"op": "text", "args": {"title_card": False}}),
    "title_words": ("text", {"title": "Raw in, cut out"}, {"op": "text", "args": {"title": "Raw in, cut out"}}),
    "look": ("style", {"look": {"preset": "box"}}, {"op": "style", "args": {"look": {"preset": "box"}}}),
    "callout_pin": ("style", {"callouts": {"s2": [120, 400]}}, {"op": "style", "args": {"callouts": {"s2": [120, 400]}}}),
    "music": ("music", {"track": "ukulele"}, {"op": "music", "args": {"track": "ukulele"}}),
    "mix": ("music", {"track": "ukulele", "level_db": -16, "voice_db": 3},
            {"op": "music", "args": {"track": "ukulele", "level_db": -16, "voice_db": 3}}),
    "pin": ("pin", {"speaker": "B", "head": "3"}, {"op": "pin", "args": {"speaker": "B", "head": "3"}}),
    "punch_off": ("punch", {"on": False}, {"op": "punch", "args": {"on": False}}),
    "punch_skip": ("punch", {"skip": "jump_cut:s2"}, {"op": "punch", "args": {"skip": "jump_cut:s2"}}),
}


@pytest.mark.parametrize("case", list(CASES))
def test_every_panel_op_writes_the_cli_edl_byte_for_byte(tmp_path, monkeypatch, case):
    import montivo.human as H
    cli, cli_args, o = CASES[case]
    d = _panel_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    if o is None:  # the caption edit: the editor's cut second, resolved to the line it names
        o = {"op": "caption", "args": {"lines": H.caption_lines(edl, cli_args["edits"])}}
        assert o["args"]["lines"] == [{"span": "s2", "src_t": o["args"]["lines"][0]["src_t"], "text": "Four Meta models",
                                       "was": "meta meta models", "t": 3.1}]
    new, rec = H.apply_op(edl, H._words(d), H.Audio.load(d), o["op"], o["args"])
    assert json.loads((d / "edl_v1.json").read_text()) == edl  # the op never changes the EDL it is given
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    getattr(H, cli)(d, **cli_args)
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()
    assert rec.say and rec.head(1, 2).startswith(f"montivo {cli}: ")


def test_panel_ops_refuse_what_the_cli_refuses(tmp_path, monkeypatch):
    import montivo.human as H
    d = _panel_run(tmp_path, monkeypatch)
    edl, words = json.loads((d / "edl_v1.json").read_text()), H._words(d)
    for op, args, why in [("text", {"callouts": {"s2": "Four Meta models"}}, "nothing to change"),
                          ("style", {"look": {"preset": "nope"}}, "look"), ("music", {"track": None}, "no music"),
                          ("pin", {"speaker": "B", "head": None}, "already unpinned"),
                          ("punch", {"on": True}, "already on"), ("caption", {"lines": []}, "no caption line"),
                          ("caption", {"lines": [{"span": "s9", "src_t": 1, "text": "x"}]}, "not a span")]:
        with pytest.raises(SystemExit, match=why):
            H.apply_op(edl, words, None, op, args)
    with pytest.raises(SystemExit, match="not a Voice speaker"):
        H.pin_check(d, "Z", "3")
    with pytest.raises(SystemExit, match="head 7"):
        H.pin_check(d, "A", "7")


def test_one_draft_holds_panel_and_structural_edits_and_commits_as_drafted(tmp_path, monkeypatch):
    d = _panel_run(tmp_path, monkeypatch)
    c, st = _client(tmp_path)
    ops = [{"op": "caption", "args": {"edits": [{"at": 3.1, "text": "Four  Meta models"}]}, "from": 1},
           {"op": "split", "args": {"span": "s3", "word": 4}},
           {"op": "style", "args": {"look": {"preset": "box"}, "callouts": {"s2": [120, 400]}}},
           {"op": "music", "args": {"track": "ukulele", "level_db": -16}},
           {"op": "text", "args": {"callouts": {"s3": "Two"}, "title_card": False}},
           {"op": "pin", "args": {"speaker": "B", "head": "3"}},
           {"op": "punch", "args": {"skip": "jump_cut:s2"}}]
    for o in ops:
        r = c.post("/api/runs/h1/draft", json=o)
        assert r.status_code == 200, (o, r.text)
    dr = r.json()["draft"]
    kept = dr["ops"][0]["args"]  # the caption edit is kept as the line it named, not as a cut second
    assert list(kept) == ["lines"] and kept["lines"][0]["span"] == "s2" and kept["lines"][0]["text"] == "Four Meta models"
    assert [o["say"] for o in dr["ops"]][0] == "caption “Four Meta models”" and dr["ops"][5]["say"] == "follow head 3 when B talks"
    drafted = c.get("/api/runs/h1/draft?edl=1").json()["edl"]
    assert drafted["look"]["preset"] == "box" and drafted["music"]["track"] == "ukulele" and drafted["title_card"] is False
    assert drafted["reframe_speaker_ids"] == {"B": "3"} and drafted["punch_skip"] == ["jump_cut:s2"]
    assert drafted["caption_overrides"][-1]["text"] == "Four Meta models"
    # undo the last two and redo them: the replay from the base gives the same EDL (the caption line stays resolved)
    c.post("/api/runs/h1/draft/undo"), c.post("/api/runs/h1/draft/undo")
    c.post("/api/runs/h1/draft/redo"), c.post("/api/runs/h1/draft/redo")
    assert c.get("/api/runs/h1/draft?edl=1").json()["edl"] == drafted
    started = _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 200, r.text
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert _bare(v2) == _bare(drafted) and v2["patches"][-1]["action"] == "edits" and len(started) == 1


def test_draft_refuses_bad_panel_args_and_a_pin_the_run_cannot_follow(tmp_path, monkeypatch):
    _panel_run(tmp_path, monkeypatch)
    c, _ = _client(tmp_path)
    for bad in [{"op": "caption", "args": {"edits": [{"at": "x", "text": "a"}]}}, {"op": "caption", "args": {"edits": []}},
                {"op": "caption", "args": {"edits": [{"at": 3.1, "text": " "}]}}, {"op": "text", "args": {}},
                {"op": "text", "args": {"callouts": {"s2": "x" * 81}}}, {"op": "style", "args": {"look": "box"}},
                {"op": "style", "args": {"callouts": {"s2": [1]}}}, {"op": "music", "args": {"track": "../x"}},
                {"op": "music", "args": {"track": "ukulele", "level_db": "inf"}}, {"op": "pin", "args": {"speaker": "A;"}},
                {"op": "punch", "args": {}}, {"op": "punch", "args": {"skip": "zoom:1"}}]:
        assert c.post("/api/runs/h1/draft", json=bad).status_code == 400, bad
    r = c.post("/api/runs/h1/draft", json={"op": "pin", "args": {"speaker": "A", "head": "7"}})
    assert r.status_code == 409 and "head 7" in r.json()["error"]
    r = c.post("/api/runs/h1/draft", json={"op": "caption", "args": {"edits": [{"at": 9.9, "text": "x"}]}})
    assert r.status_code == 409 and "no caption line on screen" in r.json()["error"]
    assert c.get("/api/runs/h1/draft").json()["draft"] is None  # a refused first op opens no draft


def test_a_caption_edit_after_a_cut_is_resolved_on_the_draft_as_laid_out(tmp_path, monkeypatch):
    """A structural op clears the rendered captions: the caption edit's cut second is read on the layout the
    preview gives the draft (the render's own code), and kept as that line."""
    from montivo.server import draft
    d = _panel_run(tmp_path, monkeypatch)
    c, _ = _client(tmp_path)
    assert c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}, "from": 1}).status_code == 200
    seen = []

    def laid(run_dir, edl, overlays=True, laid=False):
        seen.append(edl["spans"][-1]["id"])
        out = json.loads(json.dumps(edl))
        out["render"] = {"title": {"seconds": 1.5}}
        out["captions"] = json.loads((d / "edl_v1.json").read_text())["captions"]
        return {"_edl": out}
    monkeypatch.setattr(draft, "preview_data", laid)
    r = c.post("/api/runs/h1/draft", json={"op": "caption", "args": {"edits": [{"at": 3.1, "text": "Four Meta models"}]}})
    assert r.status_code == 200, r.text
    assert seen == ["s4"] and r.json()["draft"]["ops"][1]["args"]["lines"][0]["span"] == "s2"


def test_new_title_words_in_a_draft_need_the_key_at_the_commit(tmp_path, monkeypatch):
    _panel_run(tmp_path, monkeypatch)
    c, st = _client(tmp_path)
    assert c.post("/api/runs/h1/draft", json={"op": "text", "args": {"title": "Raw in, cut out"}}).status_code == 200
    started = _fake_jobs(monkeypatch, st)
    r = c.post("/api/runs/h1/draft/commit", json={})
    assert r.status_code == 409 and "Muse Image" in r.json()["error"] and not started


def test_a_look_from_words_goes_into_the_draft_without_a_version(tmp_path, monkeypatch):
    """POST /style/suggest: one Spark call for the look (stubbed here), on the open draft's EDL, no version made; the
    editor then adds it to the draft as a style edit."""
    from fastapi.testclient import TestClient
    import montivo.engine.look as L
    from montivo.server.app import create_app
    d = _panel_run(tmp_path, monkeypatch)
    seen = []

    def suggest(run_dir, edl, text, ledger, log=print, model=None):
        seen.append(([s["id"] for s in edl["spans"]], text))
        return {"preset": "box"}, "a box reads on a busy shirt", []
    monkeypatch.setattr(L, "suggest", suggest)
    c = TestClient(create_app(tmp_path))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    r = c.post("/api/runs/h1/style/suggest", json={"prompt": "bold and boxed", "v": 1})
    assert r.status_code == 409 and not seen
    monkeypatch.setenv("MODEL_API_KEY", "test")
    assert c.post("/api/runs/h1/draft", json={"op": "split", "args": {"span": "s3", "word": 4}, "from": 1}).status_code == 200
    r = c.post("/api/runs/h1/style/suggest", json={"prompt": "bold  and boxed", "draft": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"look": {"preset": "box"}, "why": "a box reads on a busy shirt", "prompt": "bold and boxed"}
    assert seen == [(["s1", "s2", "s3", "s4"], "bold and boxed")] and not (d / "edl_v2.json").exists()
    r = c.post("/api/runs/h1/draft", json={"op": "style", "args": r.json()})
    assert r.status_code == 200 and r.json()["draft"]["ops"][1]["say"] == "style: bold and boxed"
    assert c.post("/api/runs/h1/style/suggest", json={"prompt": " "}).status_code == 400
