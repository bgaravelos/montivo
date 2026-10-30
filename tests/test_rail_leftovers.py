"""The rail panels' leftovers (30/10): duck edits that follow the cut, a clip's own look through a re-plan, the
click-a-face boxes inside a punch-in."""
import copy
import json
from types import SimpleNamespace

from montivo.engine import music as M
from montivo.engine.api import dump_json
from tests.test_draft import TALK, _run

# test_review.make_edl's cut: title 1.5 s, s1 1.5-2.7 (source 10.0-11.2), s2 2.7-3.6 (11.8-12.7), s3 3.6-5.1 (30.6-32.1)
DUCKS = [{"a": 1.8, "b": 2.4, "duck": True}, {"a": 3.7, "b": 4.9, "duck": False}]


def _with_bed(d):
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["music"] = {"track": "ukulele", "level_db": -18.0, "duck_db": -6.0, "duck_edits": copy.deepcopy(DUCKS)}
    (d / "edl_v1.json").write_text(json.dumps(edl))
    return edl


def _op(d, edl, op, args):
    import montivo.human as H
    return H.apply_op(edl, H._words(d), H.Audio.load(d), op, args)


def _windows(edl):
    return [(e["a"], e["b"], e["duck"]) for e in edl["music"].get("duck_edits") or []]


def test_duck_edits_move_with_their_clip(tmp_path, monkeypatch):
    d = _run(tmp_path, monkeypatch, TALK)
    edl = _with_bed(d)
    new, rec = _op(d, edl, "reorder", {"span": "s3", "before": "s1"})
    from montivo.engine.review import cut_map
    cm = {c.span: c for c in cut_map({**new, "render": edl["render"]})}  # the op's EDL has no render record yet
    lift = next(e for e in new["music"]["duck_edits"] if not e["duck"])
    hold = next(e for e in new["music"]["duck_edits"] if e["duck"])
    # each window keeps its offset into its clip: s3 now opens the cut, s1 comes after it
    assert abs(lift["a"] - (cm["s3"].cut_in + 0.1)) < 0.011 and abs(lift["b"] - lift["a"] - 1.2) < 0.02
    s1 = next(s for s in new["spans"] if s["id"] == "s1")  # the cut that now meets s1 widened its in edge
    assert abs(hold["a"] - (cm["s1"].cut_in + 10.3 - s1["t_in"])) < 0.011 and abs(hold["b"] - hold["a"] - 0.6) < 0.02
    notes = [f for f in rec.fixes if f["op"] == "duck_follow"]
    assert len(notes) == 2 and all("follows its words" in f["note"] for f in notes)
    assert edl["music"]["duck_edits"] == DUCKS  # the op never changes the EDL it is given


def test_a_window_whose_words_left_the_cut_goes_and_says_so(tmp_path, monkeypatch):
    d = _run(tmp_path, monkeypatch, TALK)
    edl = _with_bed(d)
    new, rec = _op(d, edl, "drop", {"span": "s3"})
    assert _windows(new) == [(1.8, 2.4, True)]  # s1 did not move
    gone = [f for f in rec.fixes if f["op"] == "duck_follow"]
    assert len(gone) == 1 and "left the cut" in gone[0]["note"] and gone[0]["args"]["to"] == []


def test_the_title_card_off_moves_every_window_by_its_length(tmp_path, monkeypatch):
    d = _run(tmp_path, monkeypatch, TALK)
    edl = _with_bed(d)
    new, _ = _op(d, edl, "text", {"title_card": False})
    assert _windows(new) == [(0.3, 0.9, True), (2.2, 3.4, False)]
    back, _ = _op(d, json.loads(json.dumps(new)), "text", {"title_card": True})  # a draft's EDL: its render record is v1's
    assert _windows(back) == _windows(edl)


def test_an_edit_that_leaves_the_cut_alone_leaves_the_windows(tmp_path, monkeypatch):
    d = _run(tmp_path, monkeypatch, TALK)
    edl = _with_bed(d)
    for op, args in (("style", {"look": {"preset": "box"}}), ("music", {"track": "ukulele", "level_db": -16})):
        new, rec = _op(d, edl, op, args)
        assert _windows(new) == _windows(edl) and not any(f["op"] == "duck_follow" for f in rec.fixes)
    new, _ = _op(d, edl, "music", {"track": "ukulele", "duck_edits": [{"a": 2.0, "b": 3.0, "duck": False}]})
    assert _windows(new) == [(2.0, 3.0, False)]  # the music op sets its own windows


def test_the_cli_writes_what_the_pure_op_writes_with_a_bed(tmp_path, monkeypatch):
    import montivo.human as H
    d = _run(tmp_path, monkeypatch, TALK)
    edl = _with_bed(d)
    for cli, cli_args, op, args in (("reorder", {"span_id": "s3", "before": "s1"}, "reorder", {"span": "s3", "before": "s1"}),
                                    ("split", {"span_id": "s3", "word": 4}, "split", {"span": "s3", "word": 4})):
        new, rec = _op(d, edl, op, args)
        H.stamp(new, rec, 1, 2)
        dump_json(tmp_path / "pure.json", new)
        getattr(H, cli)(d, from_v=1, **cli_args)
        assert (tmp_path / "pure.json").read_bytes() == (d / "edl_v2.json").read_bytes()
        assert any(f["op"] == "duck_follow" for f in json.loads((d / "edl_v2.json").read_text())["patches"][-1]["fixes"]) \
            or op == "split"  # a split keeps every word where it was
        (d / "edl_v2.json").unlink()


def test_a_draft_chain_carries_the_windows_op_by_op(tmp_path, monkeypatch):
    import montivo.human as H
    d = _run(tmp_path, monkeypatch, TALK)
    edl = _with_bed(d)
    ops = [{"op": "reorder", "args": {"span": "s3", "before": "s1"}}, {"op": "drop", "args": {"span": "s2"}},
           {"op": "text", "args": {"title_card": False}}]
    chained, rec = H.chain_edit(edl, H._words(d), H.Audio.load(d), ops)
    cur = edl
    for o in ops:
        cur, _ = H.step(cur, H._words(d), H.Audio.load(d), o)
    assert _windows(chained) == _windows(cur)
    lift = next(e for e in chained["music"]["duck_edits"] if not e["duck"])
    assert abs(lift["a"] - 0.1) < 0.011  # s3 first, no card: 0.1 s into the cut, as it was 0.1 s into s3
    assert sum(f["op"] == "duck_follow" for f in rec.fixes) >= 3


def test_follow_cut_splits_a_window_whose_pieces_part():
    edl = {"source": {"fps": 30.0}, "render": {"title": None},
           "spans": [{"id": "s1", "t_in": 10.0, "t_out": 12.0, "role": "hook"}, {"id": "s2", "t_in": 20.0, "t_out": 22.0, "role": "body"}],
           "music": {"track": "ukulele", "duck_edits": [{"a": 1.0, "b": 3.0, "duck": False}]}}
    new = copy.deepcopy(edl)
    new["spans"] = [new["spans"][1], {"id": "s3", "t_in": 40.0, "t_out": 41.0, "role": "body"}, new["spans"][0]]
    fixes = M.follow_cut(edl, new)
    # s1's last second (10.5... 12.0 -> 11.0-12.0) went to the end, s2's first second stays at the start
    assert _windows(new) == [(0.0, 1.0, False), (4.0, 5.0, False)] and "4.00-5.00 s" in fixes[0]["note"]
    tight = copy.deepcopy(edl)
    tight["spans"][1]["t_out"] = 20.5  # only 0.5 s of s2's part is left: too short to lift alone
    tight["spans"] = [tight["spans"][1]]
    assert M.follow_cut(edl, tight)[0]["note"].endswith(f"under {M.HOLD_GAP_S:g} s of it is left in the cut")
    assert "duck_edits" not in tight["music"]


# --------------------------------------------------------------------------- a re-plan


def test_carry_clip_looks_by_source_time():
    import montivo.human as H
    box, pop = {"preset": "box"}, {"preset": "bold_pop"}
    prev = {"spans": [{"id": "s1", "t_in": 10.0, "t_out": 12.0}, {"id": "s2", "t_in": 12.0, "t_out": 15.0, "look": box},
                      {"id": "s3", "t_in": 30.0, "t_out": 33.0, "look": pop}]}
    new = {"spans": [{"id": "s1", "t_in": 10.0, "t_out": 12.2}, {"id": "s2", "t_in": 12.3, "t_out": 14.0},
                     {"id": "s3", "t_in": 40.0, "t_out": 42.0}]}
    fixes = H.carry_clip_looks(prev, new)
    assert [s.get("look") for s in new["spans"]] == [None, box, None]
    assert [(f["op"], f["target"]) for f in fixes] == [("clip_look", "s2"), ("clip_look_off", "s3")]
    assert fixes[0]["args"]["from"] == "s2" and "does not keep its words" in fixes[1]["note"]


def test_a_replan_keeps_a_clips_look_and_its_duck_edits(tmp_path, monkeypatch):
    import montivo.engine.spark as S
    import montivo.human as H
    d = _run(tmp_path, monkeypatch, TALK)
    edl = _with_bed(d)
    edl["spans"][2]["look"] = {"preset": "box"}
    edl["spans"][1]["look"] = {"preset": "karaoke"}
    (d / "edl_v1.json").write_text(json.dumps(edl))
    (d / "watch.json").write_text("{}")
    planned = copy.deepcopy(edl)
    for s in planned["spans"]:
        s.pop("look", None)
    planned["spans"] = [planned["spans"][2], planned["spans"][0]]  # s2 is gone, s3 opens the new plan
    for k, s in enumerate(planned["spans"]):
        s["id"] = f"s{k + 1}"
    planned.pop("music")
    planned.update(role_fixes=[], runtime_s=2.7)

    class FakeSpark:
        def __init__(self, *a, **k):
            pass

        def plan(self, *a, **k):
            return SimpleNamespace(parsed={"spans": []}, cost_usd=0.0, seconds=0.0)
    monkeypatch.setattr(S, "Spark", FakeSpark)
    monkeypatch.setattr(H, "build_edl", lambda *a, **k: copy.deepcopy(planned))
    monkeypatch.setattr(H.VoiceResult, "from_json", staticmethod(lambda j: SimpleNamespace(
        transcript_text=lambda: "", speech_rate=lambda: 3.0)))
    H.replan(d, "shorter", from_v=1)
    new = json.loads((d / "edl_v2.json").read_text())
    assert new["spans"][0]["look"] == {"preset": "box"} and "look" not in new["spans"][1]
    fixes = new["patches"][-1]["fixes"]
    ops = [(f["op"], f["target"]) for f in fixes]
    assert ("clip_look", "s1") in ops and ("clip_look_off", "s2") in ops
    lift = next(e for e in new["music"]["duck_edits"] if not e["duck"])
    assert abs(lift["a"] - 1.6) < 0.011  # s3's window, 0.1 s into the clip that now opens after the card


# --------------------------------------------------------------------------- SP4 inside a punch-in


def test_the_face_boxes_zoom_with_a_punch_in(tmp_path, monkeypatch):
    from montivo.engine import preview as P
    from montivo.engine.punch import zoom_at
    zoom = {"base": 1.0, "anchor": [540.0, 700.0], "events": [{"a": 1.0, "b": 1.2, "c": 2.0, "d": 2.2, "z": 1.12}]}
    seg = {"t_in": 10.0, "cut_t0": 1.5, "frames": 300, "span_ids": ["s1"], "zoom": zoom,
           "crop": {"w": 608, "h": 1080, "y": 0, "xs": [656.0] * 300}}
    asked = {}
    monkeypatch.setattr(P, "preview_data", lambda run_dir, edl, overlays=True: asked.update(overlays=overlays) or
                        {"fps": 30.0, "segments": [seg]})
    every = [{"t": 9.0 + k * 0.2, "box": [400, 50, 500, 200]} for k in range(100)]
    monkeypatch.setattr(P, "load_track", lambda run_dir, edl, concept: {"source": {"width": 960, "height": 540},
                                                                         "objects": {"0": every}})
    edl = {"source": {"width": 1920, "height": 1080, "fps": 30.0}, "punch": True,
           "spans": [{"id": "s1", "speaker": "A", "t_in": 10.0, "t_out": 20.0}]}
    flat = [round((800 - 656) * 1080 / 608), round(100 * 1920 / 1080), round((1000 - 656) * 1080 / 608), round(400 * 1920 / 1080)]
    assert P.heads_at(tmp_path, edl, 1.5 + 0.5)["heads"][0]["box"] == flat  # before the punch-in: no zoom
    assert asked["overlays"] is True  # the punch-ins are planned only with the layout
    z = zoom_at(zoom, 1.6)
    assert abs(z - 1.12) < 1e-9
    box = P.heads_at(tmp_path, edl, 1.5 + 1.6)["heads"][0]["box"]
    want = [540 + (flat[0] - 540) * z, 700 + (flat[1] - 700) * z, 540 + (flat[2] - 540) * z, 700 + (flat[3] - 700) * z]
    assert all(abs(a - b) <= 1 for a, b in zip(box, want)) and box != flat


# --------------------------------------------------------------------------- the kit picker on New project


def test_new_project_offers_the_saved_kits_and_posts_the_pick():
    from pathlib import Path
    home = (Path(__file__).resolve().parent.parent / "web" / "src" / "Home.tsx").read_text()
    sheet = home[home.index("function NewProject("):]
    assert 'api<LooksInfo>("/api/looks")' in sheet and "kit: picked.id" in sheet  # POST /api/runs {kit} -> montivo run --kit
    assert "Brand kit" in sheet and "kitNote(x)" in sheet
