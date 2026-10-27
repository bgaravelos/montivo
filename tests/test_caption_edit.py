"""Offline: retexting caption lines by hand (`montivo.human caption`, for words Voice misheard). The line is found by
the cut second the human points at, the patch is the reviewer's caption_text, a human text wins over an earlier
reviewer text on the same line, and nothing is overwritten."""
import json

import pytest

from montivo import human
from montivo.engine import review as R
from montivo.engine.captions import CWord, override_for
from tests.test_review import make_edl, voice_words


def cap(cid, t_in, t_out, sid, words_cut):
    return {"id": cid, "spans": [sid], "text": " ".join(w for w, _, _ in words_cut), "t_in": t_in, "t_out": t_out,
            "words": [{"w": w, "t_in": a, "t_out": b} for w, a, b in words_cut], "box": [0, 0, 10, 10],
            "head_box": None}


def rendered_edl():
    """make_edl's cut (title 1.5 s, s1 at 1.5-2.7 s, s3 from 3.6 s) with two rendered caption lines."""
    edl, _ = make_edl()
    edl["captions"] = [cap("c1", 1.5, 2.1, "s1", [("w00", 1.5, 1.8), ("w01", 1.8, 2.1)]),
                       cap("c2", 3.6, 4.2, "s3", [("w12", 3.6, 3.9), ("w13", 3.9, 4.2)])]
    return edl


def test_the_line_on_screen_gets_the_text_at_its_source_time():
    edl = rendered_edl()
    fixes = human.caption_fixes(edl, [(1.9, "Four Meta"), (4.0, "scores it")])
    assert [(f.op, f.args["span"], f.args["src_t"], f.args["text"]) for f in fixes] == \
        [("caption_text", "s1", 10.01, "Four Meta"), ("caption_text", "s3", 30.61, "scores it")]
    assert fixes[0].args["was"] == "w00 w01" and fixes[0].severity == "human"
    new, log = R.apply_fixes(edl, fixes, R.Words(voice_words()))
    assert [(o["span"], o["src_t"], o["text"], o["why"]) for o in new["caption_overrides"]] == \
        [("s1", 10.01, "Four Meta", "human"), ("s3", 30.61, "scores it", "human")]
    assert new["captions"] == [] and "caption text -> 'Four Meta'" in log[0]["note"]


def test_the_guards():
    edl = rendered_edl()
    with pytest.raises(SystemExit, match="no caption line on screen at 3.00"):
        human.caption_fixes(edl, [(3.0, "x")])
    with pytest.raises(SystemExit, match="two edits"):
        human.caption_fixes(edl, [(1.6, "a"), (2.0, "b")])
    with pytest.raises(SystemExit, match="already reads"):
        human.caption_fixes(edl, [(1.6, "w00  w01")])
    with pytest.raises(SystemExit, match="empty"):
        human.caption_fixes(edl, [(1.6, "  ")])
    edl["captions"] = []
    with pytest.raises(SystemExit, match="no rendered captions"):
        human.caption_fixes(edl, [(1.6, "a")])


def test_a_later_text_wins_and_a_forced_position_stays():
    ws = [CWord(w="meta", t_in=10.0, t_out=10.3, span="s1"), CWord(w="models", t_in=10.3, t_out=10.6, span="s1")]
    ovs = [{"span": "s1", "src_t": 10.01, "top": 500, "why": "review"},
           {"span": "s1", "src_t": 10.01, "text": "meta models", "why": "review"},
           {"span": "s1", "src_t": 10.31, "text": "Four Meta models", "why": "human"},
           {"span": "s2", "src_t": 10.01, "text": "other span"}]
    got = override_for(ws, ovs)
    assert got["text"] == "Four Meta models" and got["top"] == 500 and got["why"] == "human"
    assert override_for(ws, ovs[3:]) is None
    # the next line starts where this one ends: its override (first word + 0.01 s) is not this line's (run 015)
    nxt = [{"span": "s1", "src_t": 10.61, "text": "scores it against"}]
    assert override_for(ws, nxt) is None
    assert override_for([CWord(w="score", t_in=10.6, t_out=10.9, span="s1")], nxt)["text"] == "scores it against"


def test_caption_writes_the_next_version(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(human, "_finish", lambda run_dir, edl_path, n, passes, budget, ledger, log:
                        calls.append((edl_path.name, n, passes)) or {"version": n})
    monkeypatch.setattr(human.Ledger, "resume", staticmethod(lambda p: None))
    d = tmp_path / "run"
    (d / "voice").mkdir(parents=True)
    (d / "voice" / "voice.json").write_text(json.dumps({"words": voice_words()}))
    (d / "edl_v2.json").write_text(json.dumps({**rendered_edl(), "version": 2}))
    (d / "cut_v2.mp4").write_bytes(b"")
    assert human.caption(d, [(4.0, "scores it")]) == {"version": 3} and calls == [("edl_v3.json", 3, 0)]
    v3 = json.loads((d / "edl_v3.json").read_text())
    p = v3["patches"][-1]
    assert (p["from"], p["source"], p["action"]) == (2, "human", "caption") and v3["version"] == 3
    assert v3["caption_overrides"][-1]["text"] == "scores it"
    assert json.loads((d / "edl_v2.json").read_text())["captions"]  # v2 untouched


def test_cli_pairs_at_and_text(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(human, "caption", lambda *a: got.append(a) or {})
    d = tmp_path / "run"
    d.mkdir()
    human.main(["caption", str(d), "--at", "6.5", "--text", "we edit short form", "--at", "18", "--text", "Four Meta"])
    assert got == [(d, [(6.5, "we edit short form"), (18.0, "Four Meta")], None, 0, 0.15)]
    with pytest.raises(SystemExit):
        human.main(["caption", str(d), "--at", "6.5"])
