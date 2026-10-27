"""Offline: removing punch-ins by hand. The skip rule in plan_punches (a skipped punch-in holds its place, so none
appears elsewhere), `montivo.human punch` (on, off, skip -> the next edl_vN, nothing overwritten) and the server's
/punch endpoint (argument checks, the engine subprocess it starts)."""
import json

import pytest

from montivo import human
from montivo.engine.punch import JUMP_Z, parse_key, plan_punches
from montivo.engine.reframe import SegmentPath
from montivo.engine.render import Segment
from tests.test_punch import FPS, HEAD, FakeAudio, fixture, words_for


def test_keys_round_trip():
    assert parse_key("emphasis:Montivo.@12.340") == {"kind": "emphasis", "word": "Montivo.", "src_t": 12.34}
    assert parse_key("jump_cut:s4") == {"kind": "jump_cut", "span": "s4"}
    for bad in ("emphasis:Montivo", "jump_cut:", "zoom:s1", "emphasis:@1.0"):
        with pytest.raises(ValueError):
            parse_key(bad)


def test_a_skipped_emphasis_goes_and_nothing_takes_its_place():
    edl, segs, paths, words, track = fixture()
    _, before = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[])
    emph = [e for e in before if e["kind"] == "emphasis"]
    assert sorted(e["word"] for e in emph) == ["everything", "reviewed"]
    gone = next(e for e in emph if e["word"] == "everything")
    assert gone["key"] == "emphasis:everything@3.000" and gone["span"] == "s1"
    edl["punch_skip"] = [gone["key"]]
    plan, after = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[])
    # "Montivo" (the highlight word 1.2 s earlier) stays out: the skipped word still holds the slot and the spacing
    assert [e["word"] for e in after if e["kind"] == "emphasis"] == ["reviewed"]
    assert [e for e in after if e["kind"] == "jump_cut"] == [e for e in before if e["kind"] == "jump_cut"]
    assert not plan.get(0, {}).get("events")
    # a span edge clips the word's onset a little: the key still matches within SKIP_MATCH_S
    edl["punch_skip"] = ["emphasis:everything@3.200"]
    _, again = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[])
    assert "everything" not in [e.get("word") for e in again]


def three_takes():
    """Three takes of one shot, same head: s2 gets the jump-cut scale, s3 alternates back to 1."""
    words = words_for("one two three four", 0.2, 0) + words_for("five six seven eight", 6.2, 1) + \
        words_for("nine ten eleven twelve", 12.2, 2)
    spans = [{"id": f"s{k + 1}", "turn_id": k, "word_from": 0, "word_to": 3, "t_in": 6.0 * k, "t_out": 6.0 * k + 4.0}
             for k in range(3)]
    segs = [Segment(index=k, t_in=s["t_in"], t_out=s["t_out"], span_ids=[s["id"]], frames=int(4.0 * FPS), fps=FPS)
            for k, s in enumerate(spans)]
    paths = [SegmentPath(t_in=s.t_in, t_out=s.t_out, track_id="1", crop_w=404, crop_h=720, xs=[438] * s.frames, fps=FPS)
             for s in segs]
    track = {"track_fps": 5.0, "source": {"width": 1280, "height": 720},
             "objects": {"1": [{"t": round(f / 5, 3), "box": list(HEAD)} for f in range(0, 90)]}}
    return {"source": {"width": 1280, "height": 720, "fps": FPS}, "spans": spans}, segs, paths, words, track


def test_a_skipped_jump_cut_keeps_its_turn_in_the_alternation():
    edl, segs, paths, words, track = three_takes()
    plan, entries = plan_punches(edl, segs, paths, words, None, track, captions=[])
    assert [e["key"] for e in entries] == ["jump_cut:s2"] and plan[1]["base"] == JUMP_Z
    edl["punch_skip"] = ["jump_cut:s2"]
    plan, entries = plan_punches(edl, segs, paths, words, None, track, captions=[])
    assert entries == [] and plan == {}  # s3 does not become the zoomed take


# ------------------------------------------------------------------- montivo.human punch

def run_folder(tmp_path, **edl):
    d = tmp_path / "run"
    d.mkdir()
    base = {"version": 1, "spans": [{"id": "s1"}], "punch": True, "patches": [],
            "punch_ins": [{"kind": "emphasis", "key": "emphasis:Montivo@47.120", "word": "Montivo", "span": "s1",
                           "t_in": 12.5, "t_out": 13.2, "zoom": 1.12}],
            "captions": [{"id": "c1"}], "callouts": [], "reframe": [{"t": 0}], "render": {"output": "cut_v1.mp4"}}
    (d / "edl_v1.json").write_text(json.dumps({**base, **edl}))
    (d / "cut_v1.mp4").write_bytes(b"")
    return d


@pytest.fixture
def no_render(monkeypatch):
    calls = []
    monkeypatch.setattr(human, "_finish", lambda run_dir, edl_path, n, passes, budget, ledger, log:
                        calls.append((edl_path.name, n, passes)) or {"version": n})
    monkeypatch.setattr(human.Ledger, "resume", staticmethod(lambda p: None))
    return calls


def test_skip_writes_the_next_version(tmp_path, no_render):
    d = run_folder(tmp_path)
    out = human.punch(d, skip="emphasis:Montivo@47.120")
    assert out == {"version": 2} and no_render == [("edl_v2.json", 2, 0)]
    v1, v2 = (json.loads((d / f"edl_v{n}.json").read_text()) for n in (1, 2))
    assert "punch_skip" not in v1 and v2["punch_skip"] == ["emphasis:Montivo@47.120"]
    assert v2["punch"] is True and v2["version"] == 2
    assert v2["punch_ins"] == [] and v2["captions"] == [] and v2["reframe"] == [] and "render" not in v2
    p = v2["patches"][-1]
    assert (p["from"], p["source"], p["action"]) == (1, "human", "punch")
    assert p["fixes"][0]["op"] == "punch_skip" and "Montivo at 12.50 s" in p["fixes"][0]["note"]


def test_on_off_and_the_guards(tmp_path, no_render):
    d = run_folder(tmp_path, punch_skip=["jump_cut:s3"])
    with pytest.raises(SystemExit, match="already on"):
        human.punch(d, on=True)
    with pytest.raises(SystemExit, match="not a punch-in of edl_v1"):
        human.punch(d, skip="emphasis:Meta@3.000")
    with pytest.raises(SystemExit, match="not a punch-in key"):
        human.punch(d, skip="zoom:s1")
    with pytest.raises(SystemExit):
        human.punch(d)
    human.punch(d, on=False)
    v2 = json.loads((d / "edl_v2.json").read_text())
    assert v2["punch"] is False and v2["punch_skip"] == ["jump_cut:s3"]  # the hand-removed one stays removed
    assert "1 removed by hand stay removed" in v2["patches"][-1]["fixes"][0]["note"]
    with pytest.raises(SystemExit, match="not a punch-in of edl_v2"):
        human.punch(d, skip="emphasis:Montivo@47.120")  # off: nothing to remove
    human.punch(d, on=True, from_v=2)
    assert json.loads((d / "edl_v3.json").read_text())["punch"] is True
    assert (d / "edl_v1.json").is_file() and (d / "edl_v2.json").is_file()  # nothing overwritten


def test_cli_parses_the_punch_flags(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(human, "punch", lambda *a: got.append(a) or {})
    d = run_folder(tmp_path)
    human.main(["punch", str(d), "--off", "--from", "1"])
    human.main(["punch", str(d), "--skip", "jump_cut:s2", "--review", "1"])
    assert got == [(d, False, None, 1, 0, 0.15), (d, None, "jump_cut:s2", None, 1, 0.15)]
    with pytest.raises(SystemExit):
        human.main(["punch", str(d), "--on", "--off"])


# ------------------------------------------------------------------- the server endpoint

def test_punch_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server import app as A
    started = []

    class FakeJob:
        def public(self):
            return {"id": "j"}

    monkeypatch.setattr(A, "start_job", lambda st, run_id, kind, label, args, chat=None:
                        started.append((kind, label, args)) or FakeJob())
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    (tmp_path / "runs" / "r1").mkdir(parents=True)
    (tmp_path / "runs" / "r1" / "run.json").write_text("{}")
    (tmp_path / "runs" / "r1" / "edl_v3.json").write_text("{}")
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/runs/r1/punch", json={"on": True}).status_code == 200
    assert c.post("/api/runs/r1/punch", json={"skip": "emphasis:Montivo@47.120", "from": 2}).status_code == 200
    assert started[0] == ("punch", "Punch-ins on",
                          ["-m", "montivo.human", "punch", "runs/r1", "--on", "--from", "3", "--review", "0"])
    assert started[1][2][4:7] == ["--skip", "emphasis:Montivo@47.120", "--from"]
    for bad in ({"skip": "emphasis:x y@1.0"}, {"skip": "--on"}, {"on": "yes"}, {}):
        assert c.post("/api/runs/r1/punch", json=bad).status_code == 400
    assert c.post("/api/runs/r1/punch", json={"on": False, "review": 1}).status_code == 409  # review needs the key
