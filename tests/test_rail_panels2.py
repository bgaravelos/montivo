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



# --------------------------------------------------------------------------- T1-T3: callouts at a moment, title hooks


def test_a_callout_starts_where_the_playhead_put_it(tmp_path, monkeypatch):
    import montivo.human as H
    from montivo.engine.captions import CALLOUT_LEAD_S, CALLOUT_MIN_S
    d = _panel_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    s3 = next(s for s in edl["spans"] if s["id"] == "s3")
    s3["duration"] = 6.0  # room for a callout to start later in the clip (the test clips are 1-2 s)
    (d / "edl_v1.json").write_text(json.dumps(edl))
    words = H._words(d)
    # a new callout on s3, 1 s into it: one text edit with its words and its start
    new, rec = H.apply_op(edl, words, None, "text", {"callouts": {"s3": "Watch this"}, "callout_at": {"s3": 1.0}})
    n3 = next(s for s in new["spans"] if s["id"] == "s3")
    assert n3["callout"] == "Watch this" and n3["callout_shift_s"] == round(1.0 - CALLOUT_LEAD_S, 2)
    assert [f["op"] for f in rec.fixes] == ["callout_text", "callout_time"]
    # past the end of the clip it keeps CALLOUT_MIN_S on screen; before the lead it starts at the lead
    assert H.callout_shift(s3, 99) == round(s3["duration"] - CALLOUT_LEAD_S - CALLOUT_MIN_S, 2)
    assert H.callout_shift(s3, 0.1) == 0.0 and H.callout_shift({"duration": 1.5}, 1.0) == 0.0  # no room: at the lead
    # a time for a clip with no callout is refused; removing the callout drops its time
    with pytest.raises(SystemExit, match="no callout to time"):
        H.apply_op(edl, words, None, "text", {"callout_at": {"s3": 1.0}})
    gone, _ = H.apply_op(new, words, None, "text", {"callouts": {"s3": ""}})
    assert "callout_shift_s" not in next(s for s in gone["spans"] if s["id"] == "s3")
    # the CLI writes the same EDL byte for byte
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    H.main(["text", str(d), "--callout", "s3=Watch this", "--callout-at", "s3=1.0"])
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()


def test_title_hooks_are_settled(tmp_path, monkeypatch):
    from montivo.engine import titles as T
    out = T.settle([{"text": ' "Raw in, cut out" ', "why": "a"}, {"text": "raw in, cut out", "why": "dup"},
                    {"text": "", "why": "x"}, {"text": "Raw In", "why": "the current"}, {"text": "x" * 60, "why": "long"},
                    {"text": "Four models, one cut", "why": "b"}, {"text": "Who reviews the editor?", "why": "c"},
                    {"text": "A fourth", "why": "d"}], current="Raw In")
    assert [x["text"] for x in out] == ["Raw in, cut out", "Four models, one cut", "Who reviews the editor?"]
    seen = {}

    class FakeSpark:
        def __init__(self, *a, **k): pass

        def call(self, name, prompt, schema, **k):
            seen.update(name=name, prompt=prompt, stage=k.get("stage"))
            return SimpleNamespace(parsed={"titles": [{"text": "Cut by its own reviewer", "why": "twist"}]})
    monkeypatch.setattr("montivo.engine.spark.Spark", FakeSpark)
    edl = {"title_card_text": "Raw In", "spans": [{"words": "Hi. I'm Billy."}, {"words": "Four Meta models do all of it."}]}
    assert T.suggest(tmp_path, edl, "make a reel", ledger=None) == [{"text": "Cut by its own reviewer", "why": "twist"}]
    assert seen["stage"] == "spark.titles" and "- Four Meta models do all of it." in seen["prompt"] and '"Raw In"' in seen["prompt"]
    assert set(T.schema()["required"]) == set(T.schema()["properties"])
    monkeypatch.setattr(FakeSpark, "call", lambda self, *a, **k: SimpleNamespace(parsed={"titles": [{"text": "Raw In", "why": ""}]}))
    with pytest.raises(ValueError, match="no usable title"):
        T.suggest(tmp_path, edl, "b", ledger=None)


def test_title_suggest_route(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.engine import titles as T
    from montivo.server import app as A
    _panel_run(tmp_path, monkeypatch)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/h1/title/suggest", json={"v": 1}).status_code == 409  # no key: Spark is not called
    monkeypatch.setenv("MODEL_API_KEY", "test")
    monkeypatch.setattr(T, "suggest", lambda d, edl, brief, ledger, log: [{"text": f"{edl['title_card_text']} / {brief}", "why": "w"}])
    r = c.post("/api/runs/h1/title/suggest", json={"v": 1})
    assert r.status_code == 200 and r.json()["titles"][0]["text"] == "Raw In / b"


# --------------------------------------------------------------------------- M4, M5: where the bed starts, its fade, duck edits


def test_music_start_fade_and_duck_edits_are_checked_and_kept_only_when_set():
    from montivo.engine.music import normalize_music
    m = normalize_music({"track": "ukulele", "start_s": 12.34, "fade_out_s": 3,
                         "duck_edits": [{"a": 10, "b": 12, "duck": False}, {"a": 2, "b": 3, "duck": True}]})
    assert m["start_s"] == 12.3 and m["fade_out_s"] == 3.0 and [e["a"] for e in m["duck_edits"]] == [2.0, 10.0]
    assert normalize_music({"track": "ukulele", "start_s": 0, "fade_out_s": 1.5, "duck_edits": []}) == \
        {"track": "ukulele", "level_db": -18.0, "duck_db": -6.0}  # the defaults are not written
    for bad, msg in [({"start_s": 9999}, "start_s"), ({"fade_out_s": 20}, "fade_out_s"),
                     ({"duck_edits": [{"a": 3, "b": 2, "duck": True}]}, "0 <= a < b"),
                     ({"duck_edits": [{"a": 1, "b": 1.5, "duck": False}]}, "at least"),
                     ({"duck_edits": [{"a": 1}]}, "a duck edit is")]:
        with pytest.raises(ValueError, match=msg):
            normalize_music({"track": "ukulele", **bad})


def test_duck_edits_hold_down_and_lift_the_bed():
    from montivo.engine.music import edit_ducks
    speech = [(1.0, 5.0), (8.0, 12.0)]
    assert edit_ducks(speech, None) == speech
    lifted = edit_ducks(speech, {"duck_edits": [{"a": 2.0, "b": 3.5, "duck": False}]})
    assert lifted == [(1.0, 2.0), (3.5, 5.0), (8.0, 12.0)]
    held = edit_ducks(speech, {"duck_edits": [{"a": 5.0, "b": 8.0, "duck": True}]})
    assert held == [(1.0, 12.0)]  # the pause between the two runs stays down
    gone = edit_ducks(speech, {"duck_edits": [{"a": 0.0, "b": 20.0, "duck": False}]})
    assert gone == []


def test_bed_graph_starts_into_the_loop_and_fades_as_asked():
    from montivo.engine.music import bed_graph
    plain = bed_graph(30.0, -3.0, -6.0, [(1.0, 2.0)], "aformat=x")
    assert "atrim=duration=30.000000," in plain and "afade=t=out:st=28.500000:d=1.500" in plain
    moved = bed_graph(30.0, -3.0, -6.0, [(1.0, 2.0)], "aformat=x", start_s=12.5, fade_out_s=4.0)
    assert "atrim=start=12.500:duration=30.000000" in moved and "afade=t=out:st=26.000000:d=4.000" in moved
    assert "afade=t=out" not in bed_graph(30.0, -3.0, -6.0, [], "aformat=x", fade_out_s=0.0)


def test_music_op_keeps_start_fade_and_duck_edits(tmp_path, monkeypatch):
    import montivo.human as H
    d = _panel_run(tmp_path, monkeypatch)
    edl, words = json.loads((d / "edl_v1.json").read_text()), H._words(d)
    edits = [{"a": 1.0, "b": 2.0, "duck": False}]
    a, rec = H.apply_op(edl, words, None, "music", {"track": "ukulele", "start_s": 20, "fade_out_s": 3, "duck_edits": edits})
    assert a["music"]["start_s"] == 20.0 and a["music"]["duck_edits"] == edits
    b, rec2 = H.apply_op(a, words, None, "music", {"track": "ukulele", "level_db": -15})  # the fader: the rest stays
    assert b["music"]["start_s"] == 20.0 and b["music"]["fade_out_s"] == 3.0 and b["music"]["duck_edits"] == edits
    c, _ = H.apply_op(b, words, None, "music", {"track": "ukulele", "duck_edits": []})
    assert "duck_edits" not in c["music"] and "duck edits 1 -> 0" in _.fixes[0]["note"]
    e, _ = H.apply_op(b, words, None, "music", {"track": "cup_of_tea"})  # another track keeps a start inside its loop
    assert e["music"]["start_s"] == 20.0
    # the CLI writes the same EDL byte for byte
    H.stamp(a, rec, 1, 2)
    dump_json(tmp_path / "pure.json", a)
    H.main(["music", str(d), "--track", "ukulele", "--start", "20", "--fade-out", "3", "--duck-edits", json.dumps(edits)])
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()


def test_draft_and_route_take_the_bed_choices(tmp_path, monkeypatch):
    from montivo.server import draft
    o = draft.clean_op({"op": "music", "args": {"track": "ukulele", "start_s": 4, "duck_edits": [{"a": 1, "b": 2, "duck": True}]}})
    assert o["args"] == {"track": "ukulele", "level_db": None, "duck_db": None, "voice_db": None, "start_s": 4.0,
                         "duck_edits": [{"a": 1.0, "b": 2.0, "duck": True}]}
    with pytest.raises(Exception):
        draft.clean_op({"op": "music", "args": {"track": "ukulele", "duck_edits": "x"}})


# --------------------------------------------------------------------------- SP1, SP2, SP4: names, merges, heads on the frame


def test_speakers_op_names_and_merges(tmp_path, monkeypatch):
    import montivo.human as H
    d = _panel_run(tmp_path, monkeypatch)
    edl, words = json.loads((d / "edl_v1.json").read_text()), H._words(d)
    new, rec = H.apply_op(edl, words, None, "speakers", {"names": {"A": " Billy  G "}, "merge": {"B": "A"}})
    assert new["speaker_names"] == {"A": "Billy G"} and new["speaker_merge"] == {"B": "A"}
    assert [f["op"] for f in rec.fixes] == ["speaker_name", "speaker_merge"] and rec.action == "speakers"
    back, _ = H.apply_op(new, words, None, "speakers", {"names": {"A": ""}, "merge": {"B": None}})
    assert "speaker_names" not in back and "speaker_merge" not in back
    for args, msg in [({"names": {"A": "Billy G"}}, "nothing to change"), ({"merge": {"A": "A"}}, "into itself"),
                      ({"merge": {"C": "B"}}, "merged itself"), ({"names": {"A b": "x"}}, "not a Voice speaker")]:
        with pytest.raises(SystemExit, match=msg):
            H.apply_op(new, words, None, "speakers", args)
    # the CLI writes the same EDL byte for byte (the generic op command)
    H.stamp(new, rec, 1, 2)
    dump_json(tmp_path / "pure.json", new)
    H.main(["op", str(d), "--op", "speakers", "--args", json.dumps({"names": {"A": " Billy  G "}, "merge": {"B": "A"}})])
    assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()


def test_a_merged_speaker_follows_the_other_ones_head(tmp_path):
    from montivo.engine.render import load_speaker_ids
    (tmp_path / "speakers.json").write_text(json.dumps({"speakers": {"A": {"ids": ["3"]}, "B": {"ids": ["0"]}}}))
    assert load_speaker_ids(tmp_path, "head", {}) == {"A": ["3"], "B": ["0"]}
    assert load_speaker_ids(tmp_path, "head", {"speaker_merge": {"B": "A"}}) == {"A": ["3"], "B": ["3"]}
    pinned = {"speaker_merge": {"B": "A"}, "reframe_speaker_ids": {"A": "7"}}
    assert load_speaker_ids(tmp_path, "head", pinned) == {"A": ["7"], "B": ["7"]}  # A's pin carries to B


def test_replan_keeps_speaker_names(tmp_path, monkeypatch):
    import inspect
    import montivo.human as H
    src = inspect.getsource(H.replan)
    assert '"speaker_names", "speaker_merge"' in src  # carried like the pins, the look and the music


def test_agent_context_names_the_speakers(tmp_path):
    from montivo import agent
    (tmp_path / "run.json").write_text(json.dumps({"brief": "b"}))
    edl = {"spans": [{"id": "s1", "speaker": "B", "words": "hi", "role": "hook"}], "speaker_names": {"A": "Billy"},
           "speaker_merge": {"B": "A"}}
    text = agent.context(tmp_path, edl)
    assert "A is Billy" in text and "B is the same person as A" in text and "[hook Billy]" in text


def test_heads_on_the_frame_at_a_cut_second(tmp_path, monkeypatch):
    from montivo.engine import preview as P
    seg = {"t_in": 10.0, "cut_t0": 1.5, "frames": 300, "span_ids": ["s1"], "crop": {"w": 608, "h": 1080, "y": 0, "xs": [656.0] * 300}}
    monkeypatch.setattr(P, "preview_data", lambda run_dir, edl, overlays=True: {"fps": 30.0, "segments": [seg]})
    every = lambda box: [{"t": 9.0 + k * 0.2, "box": box} for k in range(100)]  # noqa: E731 (SAM at 5 fps)
    track = {"source": {"width": 960, "height": 540}, "objects": {
        "0": every([400, 50, 500, 200]), "5": every([0, 50, 40, 100])}}  # head 5 is outside the crop
    monkeypatch.setattr(P, "load_track", lambda run_dir, edl, concept: track)
    edl = {"source": {"width": 1920, "height": 1080, "fps": 30.0}, "spans": [{"id": "s1", "speaker": "A", "t_in": 10.0, "t_out": 20.0}]}
    at = P.heads_at(tmp_path, edl, 3.0)
    assert at["speaker"] == "A" and at["span"] == "s1" and at["src_t"] == 11.5
    assert [h["id"] for h in at["heads"]] == ["0"]
    x0, y0, x1, y1 = at["heads"][0]["box"]
    assert (x0, x1) == (round((800 - 656) * 1080 / 608), round((1000 - 656) * 1080 / 608)) and y0 == round(100 * 1920 / 1080)
    assert P.heads_at(tmp_path, edl, 0.5)["heads"] == []  # the title card


# --------------------------------------------------------------------------- V3: a version's name and star


def test_version_labels(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server import app as A
    d = _panel_run(tmp_path, monkeypatch)
    c = TestClient(A.create_app(tmp_path))
    assert c.get("/api/runs/h1/labels").json() == {"labels": {}}
    r = c.post("/api/runs/h1/labels", json={"version": 1, "name": "  for the X   post ", "star": True})
    assert r.status_code == 200 and r.json()["labels"] == {"1": {"name": "for the X post", "star": True}}
    assert c.post("/api/runs/h1/labels", json={"version": 1, "star": False}).json()["labels"] == {"1": {"name": "for the X post"}}
    assert c.post("/api/runs/h1/labels", json={"version": 1, "name": ""}).json()["labels"] == {}
    assert c.post("/api/runs/h1/labels", json={"version": 9, "star": True}).status_code == 404
    assert c.post("/api/runs/h1/labels", json={"version": 1, "star": "yes"}).status_code == 400
    assert (d / "ui" / "labels.json").is_file() and not list(d.glob("edl_v2.json"))  # no version made


# --------------------------------------------------------------------------- ME2, ME3: a new brief, the assets


def test_replan_with_a_new_brief_reaches_the_planner(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import montivo.human as H
    from montivo.server import app as A
    _panel_run(tmp_path, monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "test")
    started = []
    monkeypatch.setattr(A, "start_job", lambda st_, rid, kind, label, args, chat=None: started.append((args, label, chat)) or
                        SimpleNamespace(public=lambda: {"id": "j"}))
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/h1/replan", json={"instruction": "", "from": 1}).status_code == 400  # neither
    r = c.post("/api/runs/h1/replan", json={"brief": "A 30 s reel  about the review loop", "instruction": "", "from": 1})
    assert r.status_code == 200, r.text
    args, label, chat = started[0]
    assert args[args.index("--brief") + 1] == "A 30 s reel about the review loop" and label == "Re-plan with the new brief"
    assert chat["text"] == "New brief: A 30 s reel about the review loop"
    got = {}
    monkeypatch.setattr(H, "replan", lambda *a, **k: got.update(a=a, k=k) or {})
    H.main([args[2], str(tmp_path / args[3]), *args[4:]])
    assert got["k"]["brief"] == "A 30 s reel about the review loop" and got["a"][1] == ""


def test_assets_of_a_version(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server import app as A
    d = _panel_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    (d / "broll").mkdir()
    (d / "broll" / "o1.png").write_bytes(b"png")
    edl.update(overlays=[{"id": "o1", "prompt": "a server rack", "placement": "full", "image": "broll/o1.png"},
                         {"id": "o2", "prompt": "not drawn", "placement": "pip"}], music={"track": "ukulele", "level_db": -18, "duck_db": -6})
    (d / "edl_v1.json").write_text(json.dumps(edl))
    (d / "title").mkdir()
    (d / "title" / "title_card.png").write_bytes(b"png")
    cov = d / "ui" / "cache" / "cover"
    cov.mkdir(parents=True)
    (cov / "abc.jpg").write_bytes(b"jpg")
    (cov / "abc.json").write_text(json.dumps({"template": "headline", "t_s": 1.5}))
    (cov / "mask.json").write_text("{}")  # not a cover record
    a = TestClient(A.create_app(tmp_path)).get("/api/runs/h1/assets?v=1").json()
    assert [s["url"] for s in a["stills"]] == ["/api/runs/h1/file/broll/o1.png", None]
    assert a["title"] == {"url": "/api/runs/h1/file/title/title_card.png", "text": "Raw In", "on": True}
    assert a["covers"] == [{"url": "/api/runs/h1/file/ui/cache/cover/abc.jpg", "template": "headline", "t_s": 1.5}]
    assert a["music"]["track"] == "ukulele"
