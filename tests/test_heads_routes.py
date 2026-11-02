"""Offline: GET /api/runs/{id}/heads/at (SP4, the faces the viewer offers to pin) through create_app on test_draft's
run folder with a SAM head track: the speaker, clip and head boxes at a cut second of a version or of the open Live
draft, nothing on the title card, 400 for a cut second that is no number, 404 for a version or draft that is not
there; and /head/{id}.jpg answering 404 without a tracked video (test_ui covers the thumbnail itself)."""
import json

from fastapi.testclient import TestClient

import montivo.server.app as A
from tests.test_draft import _run

STEADY = [400, 300, 520, 460]  # in a 960x540 track: about the middle of the frame, inside the 9:16 crop


def _tracked(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    d = _run(tmp_path, monkeypatch)
    every = [{"f": k, "t": 9.0 + 0.2 * k, "box": STEADY} for k in range(130)]  # SAM at 5 fps over 9-35 s
    (d / "track" / "head").mkdir(parents=True)
    (d / "track" / "head" / "tracks.json").write_text(json.dumps({"source": {"width": 960, "height": 540},
                                                                  "objects": {"0": every, "7": []}}))
    return d, TestClient(A.create_app(tmp_path))


def test_heads_at_a_cut_second_of_a_version(tmp_path, monkeypatch):
    d, c = _tracked(tmp_path, monkeypatch)
    at = c.get("/api/runs/h1/heads/at", params={"t": 2.0, "v": 1}).json()  # 0.5 s into s1 (title card 1.5 s)
    assert at["span"] == "s1" and at["speaker"] == "A" and at["src_t"] == 10.5
    assert [h["id"] for h in at["heads"]] == ["0"]  # head 7 has no box
    x0, y0, x1, y1 = at["heads"][0]["box"]
    assert 0 <= x0 < x1 <= 1080 and 0 <= y0 < y1 <= 1920
    s3 = c.get("/api/runs/h1/heads/at", params={"t": 4.0}).json()  # v omitted: the newest version
    assert s3["span"] == "s3" and s3["src_t"] == 31.0
    assert c.get("/api/runs/h1/heads/at", params={"t": 0.5}).json()["heads"] == []  # the title card


def test_heads_at_refuses_what_is_not_there(tmp_path, monkeypatch):
    d, c = _tracked(tmp_path, monkeypatch)
    for t in ("nan", "inf", "x"):
        r = c.get("/api/runs/h1/heads/at", params={"t": t})
        assert r.status_code in (400, 422), (t, r.status_code)
    assert c.get("/api/runs/h1/heads/at").status_code in (400, 422)  # t is required
    assert c.get("/api/runs/h1/heads/at", params={"t": 2.0, "v": 9}).status_code == 404
    assert c.get("/api/runs/h1/heads/at", params={"t": 2.0, "draft": 1}).status_code == 404  # no draft open
    assert c.get("/api/runs/nope/heads/at", params={"t": 2.0}).status_code == 404


def test_heads_at_reads_the_open_draft(tmp_path, monkeypatch):
    d, c = _tracked(tmp_path, monkeypatch)
    r = c.post("/api/runs/h1/draft", json={"op": "drop", "args": {"span": "s2"}, "from": 1})
    assert r.status_code == 200, r.text
    # a draft plays no title card until one is made (preview.title_seconds) and s2 is out of it: 1.5 s is 0.3 s into
    # s3 (s1 1.2 s); in the rendered version 1.5 s is s1's first frame, after its 1.5 s card
    drafted = c.get("/api/runs/h1/heads/at", params={"t": 1.5, "draft": 1}).json()
    assert drafted["span"] == "s3" and [h["id"] for h in drafted["heads"]] == ["0"]
    assert 30.6 <= drafted["src_t"] < 32.1  # inside s3 (the drop re-tightens the edges that now meet)
    assert c.get("/api/runs/h1/heads/at", params={"t": 1.5, "v": 1}).json()["span"] == "s1"
    # past the draft's end: no clip, no heads
    assert c.get("/api/runs/h1/heads/at", params={"t": 9.0, "draft": 1}).json()["heads"] == []


def test_a_head_thumbnail_needs_the_tracked_video(tmp_path, monkeypatch):
    d, c = _tracked(tmp_path, monkeypatch)
    assert c.get("/api/runs/h1/head/0.jpg").status_code == 404  # no track_source video nor proxy in the folder
    assert c.get("/api/runs/h1/head/99.jpg").status_code == 404
