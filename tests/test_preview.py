"""Offline: the live preview's data (engine/preview.py): the same segments and crop path as the render plans, cut
times after the title card, the edit proxy and audio made once per source, and GET /preview."""
import json
import subprocess

from montivo.engine import preview as P
from montivo.engine.render import plan_paths

SPANS = [  # s1 and s2 meet in the source (one segment), s3 jumps back: three cuts become two segments
    {"id": "s1", "turn_id": 0, "word_from": 0, "word_to": 3, "t_in": 0.2, "t_out": 0.9, "duration": 0.7, "role": "hook"},
    {"id": "s2", "turn_id": 0, "word_from": 4, "word_to": 6, "t_in": 0.9, "t_out": 1.5, "duration": 0.6, "role": "body"},
    {"id": "s3", "turn_id": 0, "word_from": 7, "word_to": 9, "t_in": 0.1, "t_out": 0.6, "duration": 0.5, "role": "payoff"},
]


def _clip(path, seconds=2.0):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=1280x720:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


def _edl(src, **kw):
    return {"source": {"path": str(src), "width": 1280, "height": 720, "fps": 30.0}, "spans": SPANS, **kw}


def test_segments_and_crop_are_the_renders(tmp_path):
    edl = _edl(tmp_path / "x.mp4")
    data = P.preview_data(tmp_path, edl)
    segs, paths = plan_paths(edl, None, {})
    assert [s["span_ids"] for s in data["segments"]] == [["s1", "s2"], ["s3"]] == [s.span_ids for s in segs]
    for got, seg, path in zip(data["segments"], segs, paths):
        assert (got["t_in"], got["t_out"], got["frames"]) == (seg.t_in, seg.t_out, seg.frames)
        assert got["crop"] == {"w": path.crop_w, "h": path.crop_h, "y": path.crop_y, "xs": path.xs}
        assert len(got["crop"]["xs"]) == seg.frames
    a, b = data["segments"]
    assert a["cut_t0"] == 0.0 and abs(b["cut_t0"] - a["frames"] / 30) < 1e-6
    assert abs(data["duration_s"] - (a["frames"] + b["frames"]) / 30) < 1e-6
    assert data["frame"] == {"aspect": "9:16", "width": 1080, "height": 1920} and data["title"] is None


def test_the_title_card_comes_first(tmp_path):
    rendered = _edl(tmp_path / "x.mp4", render={"title": {"png": "t.png", "seconds": 1.5}})
    data = P.preview_data(tmp_path, rendered)
    assert data["title"] == {"seconds": 1.5, "png": "t.png"} and data["segments"][0]["cut_t0"] == 1.5
    assert P.title_seconds(tmp_path, _edl(tmp_path / "x.mp4", render={"title": None})) == (0.0, None)  # rendered without one
    draft = _edl(tmp_path / "x.mp4", title_card_text="Raw in")
    assert P.title_seconds(tmp_path, draft) == (0.0, None)  # a draft never generates a card
    (tmp_path / "title").mkdir()
    (tmp_path / "title" / "title_card.png").write_bytes(b"png")
    assert P.title_seconds(tmp_path, draft)[0] == 1.5
    assert P.title_seconds(tmp_path, {**draft, "title_card": False}) == (0.0, None)


def _keyframes(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries",
                          "frame=pts_time", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return [float(x.strip(",")) for x in out.split()]


def test_edit_media_is_made_once_per_source(tmp_path):
    src = tmp_path / "clip.mp4"
    _clip(src)
    edl = _edl(src)
    rec = P.edit_media(tmp_path, edl)
    video, audio = tmp_path / rec["video"], tmp_path / rec["audio"]
    assert (rec["width"], rec["height"]) == (1280, 720) and rec["fps"] == 30
    assert _keyframes(video) == [0.0, 0.5, 1.0, 1.5]  # a keyframe every half second
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,sample_rate,channels",
                                         "-of", "json", str(audio)], capture_output=True, text=True, check=True).stdout)["streams"]
    assert [(s["codec_type"], s["codec_name"], s["sample_rate"], s["channels"]) for s in streams] == [("audio", "flac", "48000", 2)]
    no_audio = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(video)],
                                         capture_output=True, text=True, check=True).stdout)["streams"]
    assert [s["codec_type"] for s in no_audio] == ["video"]
    stamp = video.stat().st_mtime_ns
    assert P.edit_media(tmp_path, edl) == rec and video.stat().st_mtime_ns == stamp  # cached
    _clip(src, seconds=1.0)  # another source under the same name
    again = P.edit_media(tmp_path, edl)
    assert again["key"] != rec["key"] and abs(again["duration_s"] - 1.0) < 0.1


def test_edit_proxy_keeps_a_small_source(tmp_path):
    from montivo.engine.ingest import Probe, edit_proxy_size
    p = lambda w, h: Probe(width=w, height=h, fps=30, duration=1, bytes=1, has_audio=True, codec="h264")  # noqa: E731
    assert edit_proxy_size(p(1920, 1080)) == (1280, 720)
    assert edit_proxy_size(p(1080, 1920)) == (720, 1280)  # a phone clip: the short side is the width
    assert edit_proxy_size(p(960, 540)) == (960, 540)


def test_preview_route(tmp_path):
    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    src = tmp_path / "clip.mp4"
    _clip(src)
    (d / "run.json").write_text(json.dumps({"footage": str(src), "brief": "b"}))
    (d / "edl_v1.json").write_text(json.dumps({**_edl(src), "runtime_s": 1.8}))
    c = TestClient(create_app(tmp_path))
    r = c.get("/api/runs/r1/preview?v=1")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 1 and len(data["segments"]) == 2
    assert c.get(data["media"]["video"]).status_code == 200 and c.get(data["media"]["audio"]).status_code == 200
    assert c.get("/api/runs/r1/preview?v=9").status_code == 404
    assert c.get("/api/runs/r1/preview/title.png?v=1").status_code == 404
