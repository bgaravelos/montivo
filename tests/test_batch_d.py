"""Batch D (engine batch 4): a passage added back (insert), pauses shortened without losing a word (pauses), and the
Muse Image B-roll stills (overlay): the EDL ops, the reviewer giving a pause back, the overlay graph and the cache."""
import copy
import json
import shutil
import subprocess

import pytest
from PIL import Image

from tests.test_edges import SynthAudio
from tests.test_ui import _human_run, _run_folder

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _words(edl):
    return [(s["turn_id"], i) for s in edl["spans"] for i in range(s["word_from"], s["word_to"] + 1)]


# --------------------------------------------------------------------------- insert


def test_insert_places_a_passage_by_source_time_and_joins_the_clips_it_continues(tmp_path, monkeypatch):
    import montivo.human as H
    from montivo.engine.render import merge_segments
    d = _human_run(tmp_path, monkeypatch)  # s1 = turn 0 words 0-3, s2 = turn 0 words 6-8, s3 = turn 1 words 2-6
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["punch_skip"] = ["jump_cut:s7"]
    (d / "edl_v1.json").write_text(json.dumps(edl))
    out = H.insert(d, 0, 4, 5)  # w04 w05: between s1 and s2 in the recording, and it continues both
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert out["version"] == 2 and v2["patches"][-1]["action"] == "insert"
    sp = v2["spans"]
    assert [(s["id"], s["word_from"], s["word_to"]) for s in sp] == [("s1", 0, 3), ("s8", 4, 5), ("s2", 6, 8), ("s3", 2, 6)]
    assert sp[0]["t_out"] == sp[1]["t_in"] and sp[1]["t_out"] == sp[2]["t_in"]  # joined on the boundary: one segment
    assert [seg.span_ids for seg in merge_segments(sp)] == [["s1", "s8", "s2"], ["s3"]]
    assert [s["role"] for s in sp] == ["hook", "body", "body", "payoff"]
    assert sp[1]["why"] == "added back by hand" and sp[1]["words"] == "w04 w05"
    assert _words(v2) == sorted(set(_words(v2)), key=_words(v2).index)  # every word once
    fix = v2["patches"][-1]["fixes"][0]
    assert fix["op"] == "insert_span" and fix["target"] == "s8" and "joined" in fix["note"]


def test_insert_keeps_every_word_once_and_two_words_a_clip(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="already in s1"):
        H.insert(d, 0, 3, 5)
    with pytest.raises(SystemExit, match="two words"):
        H.insert(d, 0, 4, 4)
    with pytest.raises(SystemExit, match="outside turn"):
        H.insert(d, 1, 8, 30)
    with pytest.raises(SystemExit, match="not a span"):
        H.insert(d, 1, 8, 9, before="s9")
    assert not (d / "edl_v2.json").exists()


def test_insert_in_front_of_a_clip_or_at_the_end(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    H.insert(d, 1, 8, 9, before="s1")  # a passage from the end as the new opener: it becomes the hook
    sp = json.loads((d / "edl_v2.json").read_text())["spans"]
    assert [s["id"] for s in sp] == ["s4", "s1", "s2", "s3"] and [s["role"] for s in sp][:2] == ["hook", "body"]
    assert H.main(["insert", str(d), "--words", "1:7-8", "--end", "--from", "1"]) == 0
    sp = json.loads((d / "edl_v3.json").read_text())["spans"]
    assert [s["id"] for s in sp][-1] == "s4" and sp[-1]["role"] == "payoff" and sp[-1]["word_from"] == 7


def test_insert_tightens_the_new_edges_to_the_speech(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    audio = SynthAudio([(10.0, 11.2), (11.8, 12.7), (30.6, 32.1), (32.6, 32.9)])  # turn 1 words 8-9: 32.4-33.0
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: audio))
    H.insert(d, 1, 8, 9)
    s = next(x for x in json.loads((d / "edl_v2.json").read_text())["spans"] if x["word_from"] == 8)
    assert 32.45 <= s["t_in"] < 32.6 and 32.9 < s["t_out"] < 33.3


# --------------------------------------------------------------------------- pauses

PAUSED = [(10.0, 11.2), (11.8, 12.7), (30.6, 30.95), (31.6, 32.1)]  # a 0.63 s pause inside s3, around w13 | w14


def test_pauses_split_the_clip_at_the_pause_and_keep_every_word(tmp_path, monkeypatch):
    import montivo.human as H
    from montivo.engine.render import merge_segments
    d = _human_run(tmp_path, monkeypatch)
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: SynthAudio(PAUSED)))
    plan = H.pauses(d, 0.6, 0.3, dry_run=True)
    assert plan["count"] == 1 and plan["saved_s"] == 0.33 and not (d / "edl_v2.json").exists()
    p = plan["pauses"][0]
    assert (p["span"], p["word"], p["after"], p["before"], p["was_s"]) == ("s3", 3, "w13", "w14", 0.63)
    assert p["t_cut"] == pytest.approx(3.6 + 30.96 - 30.6, abs=0.01)
    H.pauses(d, 0.6, 0.3)
    v1, v2 = (json.loads((d / f"edl_v{k}.json").read_text()) for k in (1, 2))
    assert _words(v2) == _words(v1)  # no word lost, none doubled, same order
    sp = v2["spans"]
    assert [(s["id"], s["word_from"], s["word_to"]) for s in sp][2:] == [("s3", 2, 3), ("s4", 4, 6)]
    assert sp[2]["t_out"] == pytest.approx(31.14) and sp[3]["t_in"] == pytest.approx(31.47)  # 0.3 s of the 0.63 s kept
    assert [seg.span_ids for seg in merge_segments(sp)][-2:] == [["s3"], ["s4"]]  # a cut: the pause is gone
    assert sp[2]["pause_out"] == sp[3]["pause_in"] == {"from": 30.96, "to": 31.59, "join": 31.2}
    assert v2["runtime_s"] == pytest.approx(sum(s["duration"] for s in v1["spans"]) - 0.33, abs=0.01)
    fix = v2["patches"][-1]["fixes"][0]
    assert fix["op"] == "tighten_gaps" and "0.63 s pause after 'w13'" in fix["note"]


def test_pauses_skip_a_one_word_piece_and_short_pauses(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    # a pause after s3's first word only (w12 | w13 at 30.9): splitting there leaves "w12" alone
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: SynthAudio([(30.6, 30.65), (31.3, 32.1)])))
    plan = H.pauses(d, 0.6, 0.3, dry_run=True)
    assert plan["count"] == 0 and plan["skipped"][0]["why"] == "a clip keeps at least two words"
    with pytest.raises(SystemExit, match="no pause over 0.6"):
        H.pauses(d)
    assert H.pauses(d, 0.7, 0.3, dry_run=True)["skipped"] == []  # 0.65 s is not over 0.7


def test_pause_at_a_junction_moves_both_edges_and_the_review_gives_it_back(tmp_path, monkeypatch):
    import montivo.human as H
    from montivo.engine import review as R
    from montivo.engine.render import merge_segments
    d = _human_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    H.split(d, "s3", 4)  # s3 | s4 meet at 31.2 (Voice boundary w13 | w14): one segment
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: SynthAudio(PAUSED)))
    H.pauses(d, from_v=2)
    v3 = json.loads((d / "edl_v3.json").read_text())
    a, b = v3["spans"][2], v3["spans"][3]
    assert (a["id"], b["id"]) == ("s3", "s4") and a["pause_out"]["join"] == pytest.approx(31.2, abs=0.11)
    assert a["t_out"] == pytest.approx(31.14) and b["t_in"] == pytest.approx(31.47)
    words = H._words(d)
    fix = R.Fix(op="extend_span", target="s3", issue=0, severity="major", t_s=4.0, args={"edge": "out", "seconds": 0.5})
    new, log = R.apply_fixes(v3, [fix], words, SynthAudio(PAUSED))
    a, b = new["spans"][2], new["spans"][3]
    assert a["t_out"] == b["t_in"] == v3["spans"][2]["pause_out"]["join"] and "pause_out" not in a and "pause_in" not in b
    assert [seg.span_ids for seg in merge_segments(new["spans"])][-1] == ["s3", "s4"]  # played as one again
    assert "given back whole" in log[0]["note"]
    assert _words(new) == _words(edl) and R.rejoin_pause(new, a, "out") == ""


def test_the_reviewer_is_told_about_shortened_pauses_and_stills():
    from montivo.engine import review as R
    text = R.facts_text([], {"lines": 0}, 30.0, broll=[{"t_in": 3.0, "t_out": 5.5, "placement": "full", "prompt": "a loop"}],
                        pauses=[{"t": 12.3, "was_s": 0.9, "kept_s": 0.3}])
    assert "3.00-5.50 s full (\"a loop\")" in text and "12.30 s, 0.90 s -> 0.30 s" in text and "extend_span" in text


# --------------------------------------------------------------------------- overlays (B-roll stills)


def _fake_image(monkeypatch, colour=(200, 30, 30)):
    import montivo.human as H
    calls = []

    def generate(run_dir, oid, prompt, style, placement, ledger, log=print):
        (run_dir / "broll").mkdir(exist_ok=True)
        out = run_dir / "broll" / f"{oid}.png"
        Image.new("RGB", (1152, 2048) if placement == "full" else (1024, 1024), colour).save(out)
        calls.append((oid, prompt, style, placement))
        return out
    monkeypatch.setattr(H.broll, "generate", generate)
    return calls


def test_overlay_adds_moves_and_removes_a_still_anchored_to_the_words(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    calls = _fake_image(monkeypatch)
    H.overlay(d, prompt="a flow diagram of the loop", at=3.0)  # cut 3.0 = s2 (2.7-3.6), source 11.8 + 0.3
    v2 = json.loads((d / "edl_v2.json").read_text())
    o = v2["overlays"][0]
    assert (o["id"], o["span"], o["src_t"], o["dur"], o["placement"], o["style"]) == ("b1", "s2", 12.1, 2.5, "full", "diagram")
    assert calls == [("b1", "a flow diagram of the loop", "diagram", "full")]
    assert v2["patches"][-1]["action"] == "overlay" and v2["patches"][-1]["fixes"][0]["op"] == "overlay_add"
    v2["render"] = {"title": {"seconds": 1.5}}  # the stub render wrote none
    (d / "edl_v2.json").write_text(json.dumps(v2))
    H.overlay(d, set_id="b1", placement="pip", dur=3, from_v=2)
    o3 = json.loads((d / "edl_v3.json").read_text())["overlays"][0]
    assert (o3["placement"], o3["dur"], o3["src_t"]) == ("pip", 3.0, 12.1) and len(calls) == 1  # same picture, $0
    with pytest.raises(SystemExit, match="already there"):
        H.overlay(d, set_id="b1", placement="full", dur=2.5, from_v=2)
    H.overlay(d, remove="b1", from_v=3)
    assert "overlays" not in json.loads((d / "edl_v4.json").read_text())
    with pytest.raises(SystemExit, match="not an overlay"):
        H.overlay(d, remove="b1", from_v=4)
    H.overlay(d, prompt="a circular loop icon", at=4.0, placement="pip", from_v=2)  # a new id, never b1's picture again
    v5 = json.loads((d / "edl_v5.json").read_text())
    assert [o["id"] for o in v5["overlays"]] == ["b1", "b2"] and v5["overlays"][1]["style"] == "icon"


def test_overlay_needs_a_clip_under_it_and_reads_an_unrendered_version(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    _fake_image(monkeypatch)
    with pytest.raises(SystemExit, match="title card"):
        H.overlay(d, prompt="x", at=0.5)  # the title card's seconds
    edl = json.loads((d / "edl_v1.json").read_text())
    edl.pop("render")  # an agent's chained edit: the title card the render will add is counted (1.5 s at 30 fps)
    assert H.overlay_anchor(edl, 3.0) == ("s2", 12.1)
    edl["title_card"] = False
    assert H.overlay_anchor(edl, 1.3) == ("s2", 11.9)


def test_ask_montivo_makes_a_still_only_when_asked(tmp_path, monkeypatch):
    import montivo.agent as A
    import montivo.human as H
    finish = H._finish  # with human.DEFER set (run_ops) it renders nothing
    d = _human_run(tmp_path, monkeypatch)
    monkeypatch.setattr(H, "_finish", finish)
    calls = _fake_image(monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    ops = [{"op": "overlay", "at_s": 3.0, "text": "a flow diagram of the loop"}, {"op": "overlay", "at_s": -1, "text": ""},
           {"op": "caption", "at_s": 2.0, "text": "x"}]
    kept, skipped = A.check(edl, ops)
    assert [o["op"] for o in kept] == ["overlay", "caption"] and skipped == ["overlay: no cut second or nothing to show"]
    assert A.estimate(kept[:1], None, 0, 0.0)[0] == pytest.approx(0.01)
    assert "B-roll at 3.0 s" in A.op_label(kept[0])
    assert A.check(edl, ops[:1] + [{"op": "replan", "instruction": "shorter"}])[1][0].startswith("overlay skipped")
    written, done, _ = A.run_ops(d, 1, kept[:1], None, print)
    assert written == [2] and done == ["B-roll at 3.0 s: a flow diagram of the loop"] and calls[0][2] == "diagram"
    assert json.loads((d / "edl_v2.json").read_text())["overlays"][0]["src_t"] == 12.1
    assert "overlay" in A.OPS and "Only when the editor asks" in A.PROMPT


def test_replan_carries_the_overlays():
    import inspect

    import montivo.human as H
    assert 'new["overlays"] = [dict(o) for o in prev["overlays"]]' in inspect.getsource(H.replan)


def test_style_from_the_prompt():
    from montivo.engine.broll import style_for
    assert style_for("a laptop showing a video editing timeline") == "photo"
    assert style_for("a flow diagram of four steps") == "diagram" and style_for("a bar chart of views") == "diagram"
    assert style_for("a circular arrow loop icon") == "icon"


def _segments(*spans, fps=30.0):
    from montivo.engine.render import frame_align, merge_segments
    return frame_align(merge_segments(list(spans)), fps)


def test_place_runs_a_still_across_a_cut_and_fades_only_at_its_ends(tmp_path):
    from montivo.engine import broll
    (tmp_path / "broll").mkdir()
    Image.new("RGB", (1024, 1024), (10, 200, 10)).save(tmp_path / "broll" / "b1.png")
    sp = [{"id": "s1", "turn_id": 0, "t_in": 10.0, "t_out": 12.0, "role": "hook"},
          {"id": "s2", "turn_id": 1, "t_in": 30.0, "t_out": 33.0, "role": "payoff"}]
    segs = _segments(*sp)
    edl = {"spans": sp, "overlays": [{"id": "b1", "span": "s1", "src_t": 11.0, "dur": 2.0, "placement": "upper",
                                      "image": "broll/b1.png"},
                                     {"id": "b2", "span": "s9", "src_t": 50.0, "dur": 2.0, "placement": "full",
                                      "image": "broll/b1.png"}],
           "captions": [{"t_in": 0, "t_out": 9, "head_box": [400, 900, 700, 1200]}]}
    per_seg, recs = broll.place(edl, segs, 1.5, tmp_path, tmp_path / "ov")
    a, b = per_seg[0][0], per_seg[1][0]
    assert (a.t_in, a.t_out, a.fade_in, a.fade_out) == (1.0, 2.0, True, False)  # cut 2.5-3.5 in the first segment
    assert (b.t_in, b.t_out, b.fade_in, b.fade_out) == (0.0, 1.0, False, True)  # and on 1 s into the next
    assert recs[0]["t_in"] == 2.5 and recs[0]["t_out"] == 4.5 and recs[0]["shown"]
    assert not recs[1]["shown"] and "no longer in the cut" in recs[1]["why"]
    png = Image.open(recs[0]["png"])
    assert png.size == (1080, 1920)
    assert png.getpixel((540, 600))[:3] == (10, 200, 10)  # the card
    assert png.getpixel((540, 900 - broll.HEAD_GAP + 5))[3] < 255  # it ends above the head
    assert png.getpixel((540, 1500)) == (20, 20, 20, broll.DIM)  # the dim around it


def test_a_pip_goes_to_the_corner_the_head_is_not_in():
    from montivo.engine import broll
    assert broll.pip_spot([]) == broll.PIP_SPOTS[0]
    assert broll.pip_spot([[600, 200, 1000, 700]]) == broll.PIP_SPOTS[1]  # head up right: the card goes left


def test_callouts_stay_out_of_a_full_frame_still(tmp_path):
    from montivo.engine import broll
    from montivo.engine.captions import Overlay
    cap = Overlay(png=tmp_path / "cap_001_1.png", x=0, y=0, t_in=1.0, t_out=3.0)
    early = Overlay(png=tmp_path / "callout_s1.png", x=0, y=0, t_in=0.5, t_out=3.0, fade_s=0.15)
    inside = Overlay(png=tmp_path / "callout_s2.png", x=0, y=0, t_in=2.0, t_out=2.4, fade_s=0.15)
    out, cut = broll.clear_callouts([cap, early, inside], [(1.5, 2.6)])
    assert out[0] is cap and (out[1].t_in, out[1].t_out) == (0.5, 1.5) and len(out) == 2
    assert cut == ["callout_s1.png", "callout_s2.png"]


def test_overlay_graph_fades_only_the_sides_it_is_told_and_is_unchanged_otherwise(tmp_path):
    from montivo.engine import render as R
    from montivo.engine.captions import Overlay
    png = tmp_path / "x.png"
    plain = R._overlay_graph("[base]", [Overlay(png, 0, 0, 1.0, 2.0, fade_s=0.2)], 1)[1]
    assert plain.count("fade=t=") == 2
    assert plain == ("[1:v]format=rgba,fade=t=in:st=1.0000:d=0.2:alpha=1,fade=t=out:st=1.8000:d=0.2:alpha=1,"
                     f"{R.TO_709},format=yuva420p[o0];[base][o0]overlay=x=0:y=0:format=yuv420:eof_action=repeat:"
                     f"enable='gte(t,1.0000)*lt(t,2.0000)'[m0];[m0]format=yuv420p,{R.TAG_709}[v]")
    tail = R._overlay_graph("[base]", [Overlay(png, 0, 0, 1.0, 2.0, fade_s=0.2, fade_in=False)], 1)[1]
    assert "fade=t=in" not in tail and "fade=t=out" in tail


@needs_ffmpeg
def test_a_still_renders_under_the_captions_and_the_cache_keys_on_its_bytes(tmp_path):
    from tests.test_segcache import do_render, edl_for, md5s, words
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    run = tmp_path / "run"
    (run / "broll").mkdir(parents=True)
    Image.new("RGB", (1152, 2048), (220, 20, 20)).save(run / "broll" / "b1.png")
    edl = edl_for(src)
    plain, _ = do_render(run, src, edl, words())
    plain_md5 = md5s(run / "cut_v1.mp4")
    edl["overlays"] = [{"id": "b1", "span": "s2", "src_t": 1.3, "dur": 1.0, "placement": "full", "image": "broll/b1.png"}]
    rec, lines = do_render(run, src, edl, words())
    assert rec["overlays"][0]["shown"] and rec["overlays"][0]["t_in"] == pytest.approx(1.0, abs=0.04)
    assert any("1 of 1 B-roll stills" in ln for ln in lines)

    def pixel(t, y=300):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(run / "cut_v1.mp4"), "-frames:v", "1",
                              "-vf", f"crop=2:2:540:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return tuple(raw[:3])
    r, g, b = pixel(1.5)
    assert r > 180 and g < 70 and b < 70  # the still, full frame
    assert pixel(0.4) != pixel(1.5)
    assert rec["segcache"]["encoded"] >= 1  # the segment with the still is new
    again, _ = do_render(run, src, edl, words())
    assert again["segcache"]["encoded"] == 0  # same picture: every encode from the cache
    Image.new("RGB", (1152, 2048), (20, 20, 220)).save(run / "broll" / "b1.png")
    third, _ = do_render(run, src, edl, words())
    assert third["segcache"]["encoded"] >= 1  # new bytes, new key
    edl.pop("overlays")
    do_render(run, src, edl, words())
    assert md5s(run / "cut_v1.mp4") == plain_md5  # without overlays the cut is the one it was


def test_the_endpoints_build_their_commands(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import montivo.server.app as A
    _run_folder(tmp_path)
    seen = []
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None: seen.append((kind, args, chat)) or
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/r1/insert", json={"turn": 2, "word_from": 11, "word_to": 20, "from": 2,
                                               "text": "your film"}).status_code == 200
    assert seen[-1][1][2:] == ["insert", "runs/r1", "--words", "2:11-20", "--from", "2", "--review", "0"]
    c.post("/api/runs/r1/insert", json={"turn": 2, "word_from": 11, "word_to": 20, "before": "s3"})
    assert seen[-1][1][6:8] == ["--before", "s3"]
    assert c.post("/api/runs/r1/insert", json={"turn": 2, "word_from": 1, "word_to": 3, "before": "x; rm"}).status_code == 400
    assert c.post("/api/runs/r1/pauses", json={"over": 0.6, "keep": 0.3}).status_code == 200
    assert seen[-1][1][2:8] == ["pauses", "runs/r1", "--over", "0.6", "--keep", "0.3"]
    assert c.post("/api/runs/r1/pauses", json={"over": 0.5, "keep": 0.6}).status_code == 400
    r = c.post("/api/runs/r1/overlay", json={"at": 12.4, "prompt": "a loop diagram"})
    assert r.status_code == 409 and "MODEL_API_KEY" in r.json()["error"]  # a new picture calls Muse Image
    assert c.post("/api/runs/r1/overlay", json={"set": "b1", "placement": "pip"}).status_code == 200
    assert seen[-1][1][2:7] == ["overlay", "runs/r1", "--set", "b1", "--placement"]
    assert c.post("/api/runs/r1/overlay", json={"remove": "b2"}).status_code == 200
    assert seen[-1][1][4:6] == ["--remove", "b2"]
    for body in ({"set": "b1"}, {"remove": "x"}, {"set": "b1", "placement": "side"}):
        assert c.post("/api/runs/r1/overlay", json=body).status_code == 400
    monkeypatch.setenv("MODEL_API_KEY", "k")
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/r1/overlay", json={"at": 12.4, "prompt": "a loop diagram", "placement": "upper"}).status_code == 200
    assert seen[-1][1][4:] == ["--prompt", "a loop diagram", "--style", "auto", "--at", "12.40", "--placement", "upper",
                               "--from", "3", "--review", "0"]


def test_the_version_lists_its_overlays(tmp_path):
    from montivo.server.runs import versions
    d = _run_folder(tmp_path)
    edl = json.loads((d / "edl_v3.json").read_text())
    edl["overlays"] = [{"id": "b1", "placement": "full", "prompt": "p", "image": "broll/b1.png", "dur": 2.5, "src_t": 1}]
    edl["render"] = {"overlays": [{"id": "b1", "t_in": 3.0, "t_out": 5.5, "shown": True}]}
    (d / "edl_v3.json").write_text(json.dumps(edl))
    ov = versions(d)[2]["overlays"][0]
    assert (ov["id"], ov["t_in"], ov["t_out"], ov["shown"], ov["placement"]) == ("b1", 3.0, 5.5, True, "full")
    assert versions(d)[0]["overlays"] == []
    assert copy.deepcopy(ov)["image"] == "broll/b1.png"
