"""Editing basics (batch 2): split at the playhead, the two-word rule for split pieces, extending an edge past 25
words, and the version undo left the editor on (ui/current.json)."""
import json

import pytest

from tests.test_ui import _human_run, _run_folder


def test_split_cuts_a_clip_in_two_with_nothing_removed(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["spans"][2]["callout"] = "Raw in"
    edl["punch_skip"] = ["jump_cut:s7"]  # a removed punch-in names s7: the new piece goes past it
    edl["caption_overrides"] = [{"span": "s3", "src_t": 31.81, "text": "W16 w17", "why": "human"}]
    (d / "edl_v1.json").write_text(json.dumps(edl))
    out = H.split(d, "s3", 4)  # s3 = turn 1 words 2-6: w12 w13 | w14 w15 w16
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert out["version"] == 2 and v2["patches"][-1]["action"] == "split"
    sp = v2["spans"]
    assert [(s["id"], s["word_from"], s["word_to"]) for s in sp] == [("s1", 0, 3), ("s2", 6, 8), ("s3", 2, 3), ("s8", 4, 6)]
    assert sp[2]["t_out"] == sp[3]["t_in"] == 31.2  # one point: the render joins them into the same segment
    assert sp[3]["contiguous_with_previous"] and sp[2]["callout"] == "Raw in" and sp[3]["callout"] == ""
    assert [s["role"] for s in sp] == ["hook", "body", "body", "payoff"]
    assert v2["runtime_s"] == pytest.approx(1.2 + 0.9 + 1.5)
    assert v2["caption_overrides"][0]["span"] == "s8"
    fix = v2["patches"][-1]["fixes"][0]
    assert fix["op"] == "split_span" and fix["args"]["ids"] == ["s3", "s8"] and "'w14'" in fix["note"]
    from montivo.engine.render import merge_segments
    assert [seg.span_ids for seg in merge_segments(sp)] == [["s1"], ["s2"], ["s3", "s8"]]


def test_split_lands_on_the_dip_between_the_words(tmp_path, monkeypatch):
    import montivo.human as H
    from tests.test_edges import SynthAudio
    d = _human_run(tmp_path, monkeypatch)
    # Voice says w13 | w14 at 31.2; the sound's dip is at 31.26 (Voice early, as on run 015)
    audio = SynthAudio([(30.6, 31.24), (31.28, 32.1)])
    monkeypatch.setattr(H.Audio, "load", staticmethod(lambda run_dir: audio))
    H.split(d, "s3", 4)
    sp = json.loads((d / "edl_v2.json").read_text())["spans"]
    assert sp[2]["t_out"] == sp[3]["t_in"] and 31.23 <= sp[2]["t_out"] <= 31.29


@pytest.mark.parametrize("span,word", [("s3", 3), ("s3", 6), ("s3", 2), ("s3", 9), ("s9", 4), ("s1", 1)])
def test_split_keeps_two_words_a_piece(tmp_path, monkeypatch, span, word):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        H.split(d, span, word)
    assert not (d / "edl_v2.json").exists()


def test_a_cut_never_leaves_a_one_word_piece(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="one-word clip"):
        H.cut(d, [("s3", 3, 3)])  # s3 = words 2-6: w12 alone, then w14-w16
    with pytest.raises(SystemExit, match="one-word clip"):
        H.cut(d, [("s3", 3, 5)])  # w12 | w16
    H.cut(d, [("s3", 4, 4)])  # two words on both sides: fine
    assert (d / "edl_v2.json").is_file()


def test_split_command_line(tmp_path, monkeypatch):
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    assert H.main(["split", str(d), "--span", "s3", "--word", "5"]) == 0
    assert [s["word_to"] for s in json.loads((d / "edl_v2.json").read_text())["spans"]][2:] == [4, 6]


def test_an_edge_extends_past_25_words_to_the_end_of_the_turn(tmp_path, monkeypatch):
    import montivo.human as H
    from montivo.engine import review as R
    d = _human_run(tmp_path, monkeypatch)
    words = [{"w": f"x{i}", "t_start": round(50.0 + 0.3 * i, 3), "t_end": round(50.3 + 0.3 * i, 3), "turn_id": 2, "i": i}
             for i in range(40)]
    voice = json.loads((d / "voice" / "voice.json").read_text())
    voice["words"] += words
    (d / "voice" / "voice.json").write_text(json.dumps(voice))
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["spans"].append({**edl["spans"][2], "id": "s4", "turn_id": 2, "word_from": 0, "word_to": 3, "t_in": 50.0,
                         "t_out": 51.2, "callout": ""})
    R.refresh_span(edl["spans"][-1], R.Words(voice["words"]))
    (d / "edl_v1.json").write_text(json.dumps(edl))
    H.trim(d, "s4", "out", 39)  # 36 more words, the last of the turn
    s4 = json.loads((d / "edl_v2.json").read_text())["spans"][-1]
    assert (s4["word_from"], s4["word_to"]) == (0, 39) and s4["t_out"] == pytest.approx(62.0, abs=0.01)


def test_split_endpoint_builds_its_command(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import montivo.server.app as A
    _run_folder(tmp_path)
    seen = []
    monkeypatch.setattr(A, "start_job", lambda st, rid, kind, label, args, chat=None: seen.append((kind, label, args, chat)) or
                        A.Job(id="j", run=rid, kind=kind, label=label, cmd=args))
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/runs/r1/split", json={"span": "s3", "word": 12, "from": 2, "text": "the"})
    assert r.status_code == 200
    kind, label, args, chat = seen[-1]
    assert kind == "split" and label == "Split clip s3"
    assert args[2:] == ["split", "runs/r1", "--span", "s3", "--word", "12", "--from", "2", "--review", "0"]
    assert chat["text"] == "Split clip s3 of v2 in front of “the”."
    for body in ({}, {"span": "s3"}, {"span": "s3; rm", "word": 1}, {"span": "s3", "word": "x"}):
        assert c.post("/api/runs/r1/split", json=body).status_code == 400


def test_current_version_is_remembered_and_cleared(tmp_path):
    from fastapi.testclient import TestClient

    import montivo.server.app as A
    d = _run_folder(tmp_path)
    c = TestClient(A.create_app(tmp_path))
    assert c.get("/api/runs/r1/current").json() == {"version": None, "newest": None}
    assert c.post("/api/runs/r1/current", json={"version": 1, "newest": 2}).json() == {"stored": True, "version": 1}
    got = c.get("/api/runs/r1/current").json()
    assert got["version"] == 1 and got["newest"] == 2 and (d / "ui" / "current.json").is_file()
    assert c.post("/api/runs/r1/current", json={"version": 9}).status_code == 404
    assert c.post("/api/runs/r1/current", json={"version": "x"}).status_code == 400
    c.post("/api/runs/r1/current", json={"version": None})
    assert c.get("/api/runs/r1/current").json()["version"] is None


def test_a_cut_that_deletes_the_hook_whole_writes_nothing(tmp_path, monkeypatch):
    # code review, debugging day 1: apply_fixes skips a drop of the hook, and cut() saved and rendered the version
    # anyway, keeping the words the selection deleted
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)
    s1 = json.loads((d / "edl_v1.json").read_text())["spans"][0]
    with pytest.raises(SystemExit, match="s1 cannot go"):
        H.cut(d, [("s1", s1["word_from"], s1["word_to"])])
    assert not (d / "edl_v2.json").exists()


def test_a_split_keeps_the_caption_fix_of_a_clip_whose_edge_a_pause_moved(tmp_path, monkeypatch):
    # combination matrix, debugging day 1 (016: pauses, then split): the clip's in edge sat 0.2 s past its first
    # word's Voice start, the override (that word + 0.01 s) found no piece by time and was dropped
    import montivo.human as H
    d = _human_run(tmp_path, monkeypatch)  # s3 = turn 1 words 2-6 (30.6-32.1)
    edl = json.loads((d / "edl_v1.json").read_text())
    edl["spans"][2]["t_in"] = 30.8
    edl["caption_overrides"] = [{"span": "s3", "src_t": 30.61, "text": "Four Meta", "why": "human"}]
    (d / "edl_v1.json").write_text(json.dumps(edl))
    H.split(d, "s3", 4)
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert v2["caption_overrides"] == [{"span": "s3", "src_t": 30.61, "text": "Four Meta", "why": "human"}]
