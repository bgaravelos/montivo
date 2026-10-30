"""Rail panels, second round: Style S3 brand kits (engine/kit.py, the kit op, saved kits, `montivo run --kit`, the
generic POST /op) and S4 a look per clip (span `look` through build_overlays, the style op's `clips`)."""
import json
from types import SimpleNamespace

import pytest

from montivo.engine.api import dump_json
from montivo.engine.captions import build_overlays
from tests.test_look import scene
from tests.test_panel_draft import _panel_run


def two_clips(look_b=None, callout_b=None):
    """test_look's scene split into two clips of one segment: s6a = words 71-76, s6b = words 77-82."""
    edl, words, seg, path, head, person = scene()
    s = edl["spans"][0]
    a = {**s, "id": "s6a", "word_to": 76, "t_out": 120.9, "callout": ""}
    b = {**s, "id": "s6b", "word_from": 77, "t_in": 120.9, **({"look": look_b} if look_b else {}),
         "callout": callout_b or ""}
    edl["spans"] = [a, b]
    seg.span_ids = ["s6a", "s6b"]
    return edl, words, seg, path, head, person


# --------------------------------------------------------------------------- S4: a look per clip


def test_a_clip_with_its_own_look_styles_only_its_lines_and_callout(tmp_path):
    from montivo.engine.graphics import normalize_look
    own = normalize_look({"preset": "bold_pop"}).to_json()
    edl, words, seg, path, head, person = two_clips(own, "Raw in")
    fields, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path)
    lines = fields["captions"]
    assert {tuple(c["spans"]) for c in lines} <= {("s6a",), ("s6b",)}  # a line never mixes two looks
    a = [c for c in lines if c["spans"] == ["s6a"]]
    b = [c for c in lines if c["spans"] == ["s6b"]]
    assert a and b and all("look" not in c for c in a)
    assert all(c["look"]["preset"] == "bold_pop" and len(c["words"]) <= 2 for c in b)  # bold_pop: 2 words a line
    assert fields["callouts"][0]["look"]["font"] == "anton"
    assert fields["caption_style"]["look"]["preset"] == "marker"  # the cut's look stays the cut's
    # without a clip look nothing carries one (every EDL before S4 lays out as it did)
    edl, words, seg, path, head, person = two_clips()
    plain, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path / "b")
    assert not any("look" in c for c in plain["captions"] + plain["callouts"])


def test_style_op_gives_a_clip_its_own_look_and_takes_it_back(tmp_path, monkeypatch):
    import montivo.human as H
    d = _panel_run(tmp_path, monkeypatch)
    edl, words = json.loads((d / "edl_v1.json").read_text()), H._words(d)
    new, rec = H.apply_op(edl, words, None, "style", {"clips": {"s2": {"preset": "box"}}})
    s2 = next(s for s in new["spans"] if s["id"] == "s2")
    assert s2["look"]["preset"] == "box" and "look" not in new  # the cut keeps its own (none: marker)
    assert rec.fixes[0]["op"] == "clip_look" and "s2 has its own look" in rec.say
    back, rec2 = H.apply_op(new, words, None, "style", {"clips": {"s2": None}})
    assert "look" not in next(s for s in back["spans"] if s["id"] == "s2") and rec2.fixes[0]["op"] == "clip_look_off"
    with pytest.raises(SystemExit, match="nothing to change"):  # the cut's own look is no look of the clip's own
        H.apply_op(edl, words, None, "style", {"clips": {"s2": {"preset": "marker"}}})
    with pytest.raises(SystemExit, match="not a span"):
        H.apply_op(edl, words, None, "style", {"clips": {"s9": {"preset": "box"}}})
    # the CLI writes the same EDL byte for byte
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    H.main(["style", str(d), "--clip", 's2={"preset": "box"}'])
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()


def test_with_style_stages_clip_looks_for_the_preview():
    from montivo.engine.look import with_style
    edl = {"look": {"preset": "clean"}, "spans": [{"id": "s1"}, {"id": "s2", "look": {"preset": "box"}}]}
    out = with_style(edl, None, None, {"s1": {"preset": "karaoke"}, "s2": None})
    assert out["spans"][0]["look"]["preset"] == "karaoke" and "look" not in out["spans"][1]
    assert edl["spans"][1]["look"] == {"preset": "box"}  # a copy
    assert "look" not in with_style(edl, None, None, {"s1": {"preset": "clean"}})["spans"][0]  # = the cut's


# --------------------------------------------------------------------------- S3: brand kits


def test_a_kit_is_checked():
    from montivo.engine.kit import normalize_kit
    k = normalize_kit({"look": {"preset": "box"}, "music": {"track": "ukulele"}, "title_card": False})
    assert k["look"]["font"] == "poppins" and k["music"] == {"track": "ukulele", "level_db": -18.0, "duck_db": -6.0}
    assert normalize_kit({"music": "off"}) == {"music": "off"}
    for bad, msg in [({}, "give a look"), ({"look": {"preset": "neon"}}, "preset"), ({"music": {"track": "x"}}, "track"),
                     ({"title_card": "yes"}, "true or false"), ({"colour": 1}, "unknown keys"), ([], "an object")]:
        with pytest.raises(ValueError, match=msg):
            normalize_kit(bad)


def test_kit_op_puts_look_music_and_title_on_in_one_version(tmp_path, monkeypatch):
    import montivo.human as H
    d = _panel_run(tmp_path, monkeypatch)
    edl, words = json.loads((d / "edl_v1.json").read_text()), H._words(d)
    kit = {"look": {"preset": "box", "callout_bg": "#FF5A4E"}, "music": {"track": "ukulele", "level_db": -20},
           "title_card": False}
    new, rec = H.apply_op(edl, words, None, "kit", {"kit": kit, "name": "My brand"})
    assert new["look"]["callout_bg"] == "#FF5A4E" and new["music"]["track"] == "ukulele" and new["title_card"] is False
    assert [f["op"] for f in rec.fixes] == ["look", "music", "title_off"] and rec.action == "kit"
    assert rec.say.startswith("brand kit 'My brand': ")
    with pytest.raises(SystemExit, match="already has the kit"):
        H.apply_op(new, words, None, "kit", {"kit": kit})
    # what the version already has is left out of the patch; a mix's voice gain stays the version's
    mixed = {**new, "music": {"track": "chill_lofi", "level_db": -18.0, "duck_db": -6.0, "voice_db": 3.0}}
    again, rec2 = H.apply_op(mixed, words, None, "kit", {"kit": kit})
    assert [f["op"] for f in rec2.fixes] == ["music"] and again["music"]["voice_db"] == 3.0
    # a title card turned on needs words to show
    bare = {**edl, "title_card": False, "title_card_text": None}
    with pytest.raises(SystemExit, match="already has the kit"):
        H.apply_op(bare, words, None, "kit", {"kit": {"title_card": True}})
    # the CLI writes the same EDL byte for byte
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    H.main(["kit", str(d), "--kit", json.dumps(kit), "--name", "My brand"])
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()


def test_kit_rides_the_draft_and_the_op_route(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import montivo.human as H
    from montivo.server import app as A
    from tests.test_draft import _client
    d = _panel_run(tmp_path, monkeypatch)
    c, _ = _client(tmp_path)
    r = c.post("/api/runs/h1/draft", json={"op": "kit", "args": {"kit": {"look": {"preset": "clean"}}, "name": "Clean"}})
    assert r.status_code == 200, r.text
    assert c.post("/api/runs/h1/draft", json={"op": "kit", "args": {"kit": {"look": {"preset": "neon"}}}}).status_code == 400
    # Rendered mode: POST /op checks the op on the version, then one job runs `python -m montivo.human op`
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    started = []

    def start_job(st_, run_id, kind, label, args, chat=None):
        started.append(args)
        H.main([args[2], str(st_.root / args[3]), *args[4:]])
        job = A.Job(id="j1", run=run_id, kind=kind, label=label, cmd=args)
        st_.jobs[run_id] = job
        return job
    monkeypatch.setattr(A, "start_job", start_job)
    app = TestClient(A.create_app(tmp_path))
    assert app.post("/api/runs/h1/op", json={"op": "kit", "args": {"kit": {"music": "off"}}}).status_code == 409  # no music
    r = app.post("/api/runs/h1/op", json={"op": "kit", "args": {"kit": {"look": {"preset": "clean"}}}, "from": 1})
    assert r.status_code == 200, r.text
    assert started[0][:4] == ["-m", "montivo.human", "op", "runs/h1"]
    assert json.loads((d / "edl_v2.json").read_text())["look"]["preset"] == "clean"
    assert app.post("/api/runs/h1/op", json={"op": "split", "args": {}}).status_code == 400


def test_saved_kit_and_a_new_project_with_it(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server import app as A
    monkeypatch.setenv("MODEL_API_KEY", "test")
    (tmp_path / "footage").mkdir()
    (tmp_path / "footage" / "clip.mp4").write_bytes(b"x")
    monkeypatch.setattr(A.media, "probe_duration", lambda p: 30.0)
    started = []
    monkeypatch.setattr(A, "start_job", lambda st_, rid, kind, label, args, chat=None: started.append(args) or
                        SimpleNamespace(public=lambda: {"id": "j"}))
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/looks/saved", json={"name": "Brand", "look": {"preset": "box"}, "music": {"track": "ukulele"},
                                         "title_card": False})
    assert r.status_code == 200, r.text
    saved = c.get("/api/looks").json()["saved"][0]
    assert saved["music"]["track"] == "ukulele" and saved["title_card"] is False and saved["look"]["preset"] == "box"
    assert c.post("/api/looks/saved", json={"name": "x", "look": {}, "music": {"track": "nope"}}).status_code == 400
    r = c.post("/api/runs", json={"footage": "footage/clip.mp4", "brief": "b", "kit": "brand", "review": False})
    assert r.status_code == 200, r.text
    kit = json.loads(started[0][started[0].index("--kit") + 1])
    assert kit["music"]["track"] == "ukulele" and kit["title_card"] is False
    assert c.post("/api/runs", json={"footage": "footage/clip.mp4", "brief": "b", "kit": "nope"}).status_code == 400


def test_run_kit_sets_the_first_cut(tmp_path):
    from montivo.cli import kit_arg
    import argparse
    from montivo.engine.kit import with_kit
    k = kit_arg(argparse.ArgumentParser(), json.dumps({"look": {"preset": "karaoke"}, "title_card": False}))
    edl = {"spans": [], "title_card_text": "Hi"}
    _, fixes = with_kit(edl, k)
    assert edl["look"]["preset"] == "karaoke" and edl["title_card"] is False and len(fixes) == 2
    with pytest.raises(SystemExit):
        kit_arg(argparse.ArgumentParser(), '{"look": 3}')

