"""UX B6: the publish pack (montivo/export.py --post-text): a title, description and hashtags per platform, kept to
each platform's limits, in the bundle as post.txt, and the Export page's routes."""
import json

import pytest

from montivo import export


def test_the_schema_asks_every_platform_strictly():
    s = export.POST_SCHEMA
    assert s["required"] == ["reels", "tiktok", "shorts", "linkedin"] and s["additionalProperties"] is False
    one = s["properties"]["shorts"]
    assert one["required"] == ["title", "description", "hashtags"] and one["additionalProperties"] is False
    assert export.POST_PROMPT.format(brief="b", title="t", seconds=30.0, words="w", review="")  # no stray braces


def test_settle_keeps_each_platform_to_its_limits():
    out = export.settle_post({
        "shorts": {"title": "word " * 40, "description": "d", "hashtags": ["#ai", "AI", "video edit", "a", "b", "c"]},
        "reels": {"title": "no title here", "description": "x " * 1500, "hashtags": ["one"]},
        "tiktok": {"title": "", "description": "short", "hashtags": []}})
    sh = out["shorts"]
    assert len(sh["title"]) <= 100 and sh["title"].endswith("…")
    assert sh["hashtags"] == ["ai", "videoedit", "a", "Shorts"]  # # and spaces gone, case-blind dedupe, 3 + Shorts
    assert sh["text"].startswith(sh["title"] + "\n\n") and sh["text"].endswith("#ai #videoedit #a #Shorts")
    assert out["reels"]["title"] == "" and len(out["reels"]["description"]) <= 2200
    assert out["tiktok"]["text"] == "short" and out["linkedin"]["description"] == ""  # a missing platform stays empty


def test_post_txt_goes_in_the_bundle_only_once_written(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    (d / "edl_v2.json").write_text(json.dumps({"version": 2, "spans": [], "runtime_s": 3.0,
                                               "captions": [{"t_in": 0, "t_out": 1, "text": "Hi"}]}))
    (d / "cut_v2.mp4").write_bytes(b"")
    with pytest.raises(SystemExit, match="--post-text first"):
        export.export(d, 2, d / "out", "m", ["post"])
    rec = {"version": 2, "platforms": export.settle_post({k: {"title": "T", "description": f"{k} text", "hashtags": ["x"]}
                                                          for k in export.PLATFORMS})}
    (d / "post_v2.json").write_text(json.dumps(rec))
    got = export.export(d, 2, d / "out", "m", ["post", "srt"])
    txt = (d / "out" / "m-post.txt").read_text()
    assert [f["name"] for f in got["files"]] == ["m.srt", "m-post.txt"] and got["zip"]
    assert txt.startswith("== Instagram Reels ==\nreels text\n\n#x") and "== YouTube Shorts ==\nT\n\nshorts text" in txt


def test_the_post_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server import app as A
    from tests.test_ui import _run_folder
    d = _run_folder(tmp_path)
    (d / "cut_v2.mp4").write_bytes(b"")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(A.create_app(tmp_path))
    assert c.get("/api/runs/r1/export/post?v=2").json() == {"platforms": None}
    assert c.post("/api/runs/r1/export/post", json={"v": 2}).status_code == 409  # one Spark call: needs the key
    (d / "post_v2.json").write_text(json.dumps({"version": 2, "platforms": {"reels": {"text": "t"}}}))
    assert c.get("/api/runs/r1/export/post?v=2").json()["platforms"]["reels"]["text"] == "t"
