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
        ln = o["args"]["lines"][0]
        assert ln == {"span": "s2", "src_t": ln["src_t"], "text": "Four Meta models", "was": "meta meta models", "t": 3.1,
                      "until": round(ln["src_t"] - 0.01 + 0.78, 3)}  # the line's words: 2.72-3.5 cut seconds
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


# --------------------------------------------------------------------------- Captions panel: correct all, line breaks


def test_correct_all_is_a_glossary_entry_kept_by_a_replan(tmp_path, monkeypatch):
    """C2: one fix for every line that says it: the run's glossary (an old EDL starts from the demo words it used)."""
    import montivo.human as H
    from montivo.engine.captions import DEMO_GLOSSARY
    from montivo.engine.edl import carry_run_words
    d = _panel_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    new, rec = H.apply_op(edl, H._words(d), None, "glossary", {"add": {"meta  meta.": "Four Meta"}})
    assert new["glossary"] == {**DEMO_GLOSSARY, "meta meta": "Four Meta"} and "glossary" not in edl
    assert rec.say == "correct everywhere: “meta meta” -> “Four Meta”" and rec.fixes[0]["op"] == "glossary"
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    H.main(["glossary", str(d), "--add", "meta meta.=Four Meta"])
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()
    with pytest.raises(SystemExit, match="already says"):
        H.apply_op(new, H._words(d), None, "glossary", {"add": {"meta meta": "Four Meta"}})
    gone, _ = H.apply_op(new, H._words(d), None, "glossary", {"drop": ["meta meta"]})
    assert "meta meta" not in gone["glossary"]
    replanned = {"spans": []}
    carry_run_words(new, replanned)
    assert replanned["glossary"]["meta meta"] == "Four Meta"


def test_hand_line_breaks_move_with_their_word():
    """C4: a break starts a line at its word, a join puts that line back on the one before; a word that is gone or a
    break already there changes nothing."""
    from montivo.engine.captions import CWord, apply_breaks
    ws = [CWord(w=f"w{k}", t_in=10.0 + k * 0.3, t_out=10.25 + k * 0.3, span="s2") for k in range(6)]
    lines = [ws[:3], ws[3:]]
    text = lambda ls: [" ".join(w.w for w in ln) for ln in ls]  # noqa: E731
    assert text(apply_breaks(lines, [{"span": "s2", "src_t": 10.31}])) == ["w0", "w1 w2", "w3 w4 w5"]
    assert text(apply_breaks(lines, [{"span": "s2", "src_t": 10.9, "join": True}])) == ["w0 w1 w2 w3 w4 w5"]
    for noop in [{"span": "s2", "src_t": 10.0}, {"span": "s3", "src_t": 10.3}, {"span": "s2", "src_t": 10.6, "join": True},
                 {"span": "s2", "src_t": 50.0}]:
        assert text(apply_breaks(lines, [noop])) == ["w0 w1 w2", "w3 w4 w5"], noop
    assert lines == [ws[:3], ws[3:]]  # the lines it is given stay as they were


def test_a_line_break_is_resolved_to_its_word_and_its_opposite_takes_it_out(tmp_path, monkeypatch):
    import montivo.human as H
    d = _panel_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    with pytest.raises(SystemExit, match="already starts a line"):
        H.caption_break_at(edl, 2.72, False)
    with pytest.raises(SystemExit, match="joins the one before at its first word"):
        H.caption_break_at(edl, 3.0, True)
    brk = H.caption_break_at(edl, 3.0, False)
    assert brk["span"] == "s2" and brk["word"] == "models" and not brk["join"]
    new, rec = H.apply_op(edl, H._words(d), None, "caption_break", brk)
    assert new["caption_breaks"] == [{"span": "s2", "src_t": brk["src_t"]}] and rec.say == "new caption line at “models”"
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    H.main(["linebreak", str(d), "--at", "3.0"])
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()
    back, _ = H.apply_op(new, H._words(d), None, "caption_break", {**brk, "join": True})
    assert "caption_breaks" not in back  # a break joined again is no entry at all
    with pytest.raises(SystemExit, match="already breaks"):
        H.apply_op(new, H._words(d), None, "caption_break", brk)


def test_correct_all_and_a_line_break_ride_the_draft(tmp_path, monkeypatch):
    d = _panel_run(tmp_path, monkeypatch)
    c, st = _client(tmp_path)
    r = c.post("/api/runs/h1/draft", json={"op": "caption_break", "args": {"at": 3.0}, "from": 1})
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["ops"][0]["args"] == {"span": "s2", "src_t": r.json()["draft"]["ops"][0]["args"]["src_t"],
                                                   "join": False, "word": "models"}
    r = c.post("/api/runs/h1/draft", json={"op": "glossary", "args": {"add": {"meta meta": "Four Meta"}}})
    assert r.status_code == 200, r.text
    for bad in [{"op": "glossary", "args": {}}, {"op": "glossary", "args": {"add": {"x": ""}}},
                {"op": "caption_break", "args": {"at": "x"}}]:
        assert c.post("/api/runs/h1/draft", json=bad).status_code == 400, bad
    drafted = c.get("/api/runs/h1/draft?edl=1").json()["edl"]
    _fake_jobs(monkeypatch, st)
    assert c.post("/api/runs/h1/draft/commit", json={}).status_code == 200
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert _bare(v2) == _bare(drafted) and v2["glossary"]["meta meta"] == "Four Meta" and v2["caption_breaks"]


def test_a_retexted_line_keeps_its_words_through_any_style():
    """C6: a person's retext names the words it was written for (src_t .. until), so a look with 1 or 6 words per line
    shows it once, with nothing doubled and nothing else replaced (015 at 1 word: "we edit short form | added | short")."""
    from montivo.engine.captions import CWord, apply_overrides
    said = "so we added short form videos".split()
    ws = [CWord(w=x, t_in=20.0 + k * 0.4, t_out=20.35 + k * 0.4, span="s3") for k, x in enumerate(said)]
    ov = {"span": "s3", "src_t": 20.41, "text": "we edit short form", "until": 21.95, "why": "human"}
    text = lambda ls: [" ".join(w.w for w in ln) for ln in ls]  # noqa: E731
    for grouping in ([[w] for w in ws], [ws[:2], ws[2:4], ws[4:]], [ws]):
        assert text(apply_overrides(grouping, [ov])[0]) == ["so", "we edit short form", "videos"], text(grouping)
    old = {k: v for k, v in ov.items() if k != "until"}  # an override from before: it retexts the line it lands on,
    assert "so" not in text(apply_overrides([ws[:2], ws[2:4], ws[4:]], [old])[0])  # "so" with it (why `until` is kept)


def test_a_lower_case_correction_keeps_the_capital_it_replaces():
    from montivo.engine.captions import CWord, apply_glossary
    ws = [CWord(w=x, t_in=k, t_out=k + 0.5, span="s1") for k, x in enumerate(["Every", "editor", "hears", "every", "word."])]
    out, fixes = apply_glossary(ws, {"every": "each"})
    assert [w.w for w in out] == ["Each", "editor", "hears", "each", "word."] and len(fixes) == 2
    out, _ = apply_glossary(ws, {"every": "EACH"})
    assert out[0].w == "EACH"


def test_the_style_panel_counts_the_coloured_words():
    """S2: which words the captions colour, how many of this version's caption words are, in about how many sentences."""
    from montivo.server.runs import _highlight
    edl = {"highlight": ["montivo"], "look": {"highlight_add": ["agent"]},
           "captions": [{"words": [{"w": "Montivo's"}, {"w": "agent"}, {"w": "edits."}]}, {"words": [{"w": "Montivo"}, {"w": "again"}]}]}
    assert _highlight(edl) == {"words": ["agent", "montivo"], "shown": 3, "sentences": 1}
    assert _highlight({"captions": []}) is None
