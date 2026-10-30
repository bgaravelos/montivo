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
    assert data["title"] == {"seconds": 1.5, "png": "t.png", "fade_s": 0.3, "text": ""} and data["segments"][0]["cut_t0"] == 1.5
    assert P.title_seconds(tmp_path, _edl(tmp_path / "x.mp4", render={"title": None})) == (0.0, None)  # rendered without one
    draft = _edl(tmp_path / "x.mp4", title_card_text="Raw in")
    assert P.title_seconds(tmp_path, draft) == (0.0, None)  # a draft never generates a card
    (tmp_path / "title").mkdir()
    (tmp_path / "title" / "title_card.png").write_bytes(b"png")
    assert P.title_seconds(tmp_path, draft)[0] == 1.5
    assert P.title_seconds(tmp_path, {**draft, "title_card": False}) == (0.0, None)
    # new words in a draft (A3): the card on disk was made for other words, so the browser draws these until the commit
    (tmp_path / "title" / "request.json").write_text(json.dumps({"prompt": 'a card that reads "Raw in"'}))
    assert P.title_seconds(tmp_path, draft) == (1.5, (tmp_path / "title" / "title_card.png").as_posix())
    assert P.title_seconds(tmp_path, {**draft, "title_card_text": "Cut out"}) == (1.5, None)
    assert P.preview_data(tmp_path, {**draft, "title_card_text": "Cut out"}, overlays=False)["title"]["text"] == "Cut out"


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


# --------------------------------------------------------------------------- phase 1: graphics, gain, drafts


def _talk_run(tmp_path, clip_s=None):
    """tests/test_review's three-span EDL (turn 0 at 10 s, turn 1 at 30 s) with Voice words and a callout; `clip_s`
    adds a small source clip of that length (for the edit media)."""
    from tests.test_review import make_edl, voice_words
    d = tmp_path / "runs" / "r1"
    (d / "voice").mkdir(parents=True)
    edl, _ = make_edl(title=False)
    edl["spans"][1]["callout"] = "Four Meta models"
    edl["highlight"] = ["w02"]
    if clip_s:
        src = tmp_path / "clip.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={clip_s}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={clip_s}", "-shortest", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
        edl["source"]["path"] = str(src)
    (d / "edl_v1.json").write_text(json.dumps(edl))
    (d / "run.json").write_text(json.dumps({"footage": "x.mp4", "brief": "b"}))
    (d / "voice" / "voice.json").write_text(json.dumps({"words": voice_words(), "speakers": ["A", "B"]}))
    return d, edl


def test_graphics_are_laid_out_as_the_render_lays_them_out(tmp_path):
    from montivo.engine import look
    d, edl = _talk_run(tmp_path)
    data = P.preview_data(d, edl)
    fields = look.layout(d, edl)
    assert [(c["box"], c["t_in"], c["t_out"]) for c in data["captions"]] == [(c["box"], c["t_in"], c["t_out"]) for c in fields["captions"]]
    assert [(c["box"], c["text"]) for c in data["callouts"]] == [(c["box"], c["text"]) for c in fields["callouts"]] != []
    line = data["captions"][0]
    assert line["size"] == 84 and line["stroke"] == 7 and line["ascent"] > 0  # the marker look's style
    assert [w["hl"] for w in line["words"]] == [w["w"] == "w02" for w in line["words"]]
    assert data["look"]["active"] == "pill" and data["fonts"] == {"inter-tight": "Inter Tight"}
    assert data["gain_db"] is None  # never rendered


def test_loudness_gain_reads_the_first_pass():
    edl = {"render": {"loudnorm": {"target_i": -14.0, "first_pass": {"input_i": -29.73}}}}
    assert P.loudness_gain(edl) == 15.73
    assert P.loudness_gain({"render": {"loudnorm": {"speech": {"first_pass": {"input_i": -20}}}}}) == 6.0
    with_music = {"render": {"loudnorm": {"first_pass": {"input_i": -14.6}, "speech": {"first_pass": {"input_i": -29.0}}}}}
    assert P.loudness_gain(with_music) == 15.0  # the mix's own pass measured speech already normalised
    assert P.loudness_gain({}) is None


def test_the_music_bed_ducks_where_the_render_ducks(tmp_path):
    from montivo.engine import music as bed
    from montivo.engine.render import plan_paths
    d, edl = _talk_run(tmp_path)
    t = bed.tracks()[0]
    edl["music"] = {"track": t["id"], "level_db": -22.0, "duck_db": -10.0, "voice_db": 2.0}
    m = P.preview_data(d, edl, overlays=False)["music"]
    segs, _ = plan_paths(edl, None, {})
    want, how = bed.speech_intervals(segs, json.loads((d / "voice" / "voice.json").read_text())["words"], 0.0)
    assert m["ducked"] == [list(x) for x in want] and how == m["duck_source"] == "voice words"
    assert m["gain_db"] == round(-22.0 - float(t["lufs"]), 2) and (m["duck_db"], m["voice_db"]) == (-10.0, 2.0)
    assert (m["attack_s"], m["release_s"]) == (bed.ATTACK_S, bed.RELEASE_S)
    del edl["music"]
    assert "music" not in P.preview_data(d, edl, overlays=False)


def test_draft_media_follow_the_segments(tmp_path):
    from PIL import Image

    from montivo.server import media
    segs = [{"t_in": 2.0, "cut_t0": 1.0, "frames": 30}, {"t_in": 0.0, "cut_t0": 2.0, "frames": 15}]
    pk = media.draft_peaks({"rate": 100, "peaks": [round(i / 1000, 3) for i in range(400)]}, segs, 30.0, 1.0)
    assert len(pk["peaks"]) == 100 + 100 + 50 and pk["peaks"][:100] == [0.0] * 100
    assert pk["peaks"][100] == 0.2 and pk["peaks"][200] == 0.0  # source 2.0 s, then source 0.0 s
    strip = Image.new("RGB", (8 * media.THUMB_W, media.THUMB_H))
    for k in range(8):  # source tile k painted k * 30
        strip.paste((k * 30, 0, 0), (k * media.THUMB_W, 0, (k + 1) * media.THUMB_W, media.THUMB_H))
    strip.save(tmp_path / "src.jpg", quality=100)
    out = Image.open(media.draft_filmstrip(tmp_path / "src.jpg", segs, 2.5, 1.0, tmp_path / "d.jpg"))
    reds = [out.getpixel((k * media.THUMB_W + 20, 40))[0] for k in range(6)]
    assert reds[0] < 10 and reds[1] < 10  # the title card
    assert abs(reds[2] - 120) < 12 and abs(reds[3] - 150) < 12 and abs(reds[4] - 0) < 12  # source 2.0, 2.5, then 0.0


def test_draft_preview_and_its_timeline_media(tmp_path):
    from fastapi.testclient import TestClient

    from montivo.server import draft
    from montivo.server.app import create_app
    d, _ = _talk_run(tmp_path, clip_s=34)
    draft._inputs.clear()
    c = TestClient(create_app(tmp_path))
    assert c.get("/api/runs/r1/draft/preview").status_code == 404
    r = c.post("/api/runs/r1/draft", json={"op": "drop", "args": {"span": "s2"}, "from": 1})
    assert r.status_code == 200, r.text
    p = c.get("/api/runs/r1/draft/preview").json()
    assert p["draft"]["n"] == 2 and p["draft"]["ops"] == 1 and p["version"] is None
    v = p["version_entry"]
    assert v["draft"] and v["cut"] is None and v["n"] == 2 and [s["id"] for s in v["spans"]] == ["s1", "s3"]
    assert v["origin"]["action"] == "draft" and v["origin"]["from"] == 1 and len(v["caption_lines"]) == len(p["captions"])
    assert not any(c["span"] == "s2" for c in p["callouts"])  # the callout went with its clip
    f = c.get("/api/runs/r1/filmstrip?draft=1")
    assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg"
    pk = c.get("/api/runs/r1/peaks?draft=1").json()
    assert abs(len(pk["peaks"]) - p["duration_s"] * 100) <= 2
    assert c.get("/api/fonts/inter-tight").status_code == 200 and c.get("/api/fonts/x").status_code == 404
    assert "music" not in p
    c.delete("/api/runs/r1/draft")
    assert c.get("/api/runs/r1/filmstrip?draft=1").status_code == 404


def test_a_staged_look_plays_live_on_a_version_and_on_a_draft(tmp_path):
    """A4: POST /preview/styled lays the version (or the open draft) out with the Style panel's staged look and callout
    places, nothing written; the same caption lines in the look's own font and size."""
    from fastapi.testclient import TestClient

    from montivo.server import draft
    from montivo.server.app import create_app
    d, _ = _talk_run(tmp_path, clip_s=34)
    draft._inputs.clear()
    c = TestClient(create_app(tmp_path))
    plain = c.get("/api/runs/r1/preview?v=1").json()
    r = c.post("/api/runs/r1/preview/styled", json={"v": 1, "look": {"preset": "bold_pop"}, "callouts": {"s2": [100, 300]}})
    assert r.status_code == 200, r.text
    styled = r.json()
    assert styled["version"] == 1 and styled["segments"] == plain["segments"]
    assert styled["look"] != plain["look"] and styled["captions"][0]["size"] != plain["captions"][0]["size"]
    assert styled["callouts"][0]["box"][:2] == [100, 300]
    assert json.loads((d / "edl_v1.json").read_text()).get("look") is None and not list(d.glob("edl_v2.json"))
    assert c.post("/api/runs/r1/preview/styled", json={"v": 1, "look": {"preset": "nope"}}).status_code == 400
    assert c.post("/api/runs/r1/preview/styled", json={"draft": True, "look": {"preset": "box"}}).status_code == 404
    c.post("/api/runs/r1/draft", json={"op": "drop", "args": {"span": "s3"}, "from": 1})
    r = c.post("/api/runs/r1/preview/styled", json={"draft": True, "look": {"preset": "box"}})
    assert r.status_code == 200 and r.json()["version"] is None
    assert r.json()["segments"] == c.get("/api/runs/r1/draft/preview").json()["segments"]  # the draft's clips, s3 gone
