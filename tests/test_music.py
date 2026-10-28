"""The music bed (engine/music.py): the bundled tracks, the EDL's `music` checked, the speech intervals in cut time and
the duck envelope, the mix in render_audio (ffmpeg on a synthetic source: -14 LUFS, true peak under -1 dBTP, the speech
well over the bed), the speech stem the review listens to, the `music` human edit, replan carrying it and the routes."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from montivo import human
from montivo.engine import music as M
from montivo.engine import review as R
from montivo.engine.render import measure_loudness, render_audio


IDS = ["chill_lofi", "lofi_hip_hop", "funky_jam", "midnight_cruiser", "apple_cider", "ukulele", "raspberry_jam", "cup_of_tea"]


def test_the_bundled_tracks_are_there_and_cc0():
    ts = M.tracks()
    assert [t["id"] for t in ts] == IDS
    for t in ts:
        assert t["licence"] == "CC0-1.0" and -30 < t["lufs"] < -5 and t["loop_s"] > 50
        assert t["source"].startswith("https://opengameart.org/content/") and t["author"]
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", t["path"]],
                             capture_output=True, text=True, check=True).stdout
        assert abs(float(out) - t["loop_s"]) < 0.05  # the AAC priming is trimmed: the loop keeps its length
    readme = (M.MUSIC_DIR / "README.md").read_text()
    assert readme.count("CC0") >= 2 and all(t["source"] in readme for t in ts)


def test_music_is_checked_and_filled_in():
    assert M.normalize_music(None) is None and M.normalize_music({}) is None
    assert M.normalize_music({"track": "apple_cider"}) == {"track": "apple_cider", "level_db": -20.0, "duck_db": -12.0}
    assert M.normalize_music({"track": "apple_cider", "level_db": -18.04, "duck_db": 0}) == {"track": "apple_cider", "level_db": -18.0, "duck_db": 0.0}
    for bad, msg in (({"track": "jazz"}, "no music track"), ({"track": "apple_cider", "level_db": -5}, "outside"),
                     ({"track": "apple_cider", "duck_db": 3}, "outside"), ({"track": "apple_cider", "level_db": "loud"}, "a number"),
                     ({"track": "apple_cider", "level_db": True}, "a number"), ({"track": "apple_cider", "gain": 1}, "unknown keys"),
                     ("apple_cider", "an object")):
        with pytest.raises(ValueError, match=msg):
            M.normalize_music(bad)


def seg(t_in, t_out):
    return SimpleNamespace(t_in=t_in, t_out=t_out, duration=round(t_out - t_in, 4))


WORDS = [{"w": "Hi.", "t_start": 10.0, "t_end": 10.4}, {"w": "I'm", "t_start": 10.5, "t_end": 10.7},
         {"w": "Billy.", "t_start": 10.75, "t_end": 11.2}, {"w": "dropped", "t_start": 12.0, "t_end": 12.5},
         {"w": "Later", "t_start": 30.2, "t_end": 30.6}, {"w": "on.", "t_start": 32.0, "t_end": 32.4}]


def test_speech_intervals_follow_the_words_into_cut_time():
    # a 1.5 s title card, then 9.9-11.5 and 30.0-33.0 of the source: "dropped" falls outside both segments
    iv, src = M.speech_intervals([seg(9.9, 11.5), seg(30.0, 33.0)], WORDS, lead_s=1.5)
    assert src == "voice words"
    # "Hi. I'm Billy." at 1.6-2.8 and, across the cut at 3.1, "Later" at 3.3-3.7 merge (gaps under HOLD_GAP_S);
    # "on." 1.4 s later stays a stretch of its own
    assert iv == [(1.6, 3.7), (5.1, 5.5)]
    assert M.merge(iv, gap_s=2.0) == [(1.6, 5.5)]


def test_without_words_the_audio_speech_runs_stand_in():
    audio = SimpleNamespace(speech_runs=lambda a, b: [(a + 0.2, a + 0.9)])
    iv, src = M.speech_intervals([seg(5.0, 6.0), seg(20.0, 21.0)], None, audio=audio)
    assert src == "audio" and iv == [(0.2, 1.9)]  # 0.3 s across the cut: one stretch
    assert M.speech_intervals([seg(5.0, 6.0)], None) == ([], "none")


def test_the_duck_envelope_ramps_around_each_stretch():
    iv = [(2.0, 4.0), (6.0, 7.0)]
    assert M.duck_at(iv, 1.0) == 0 and M.duck_at(iv, 2.0 - M.ATTACK_S / 2) == pytest.approx(0.5)
    assert M.duck_at(iv, 3.0) == 1 and M.duck_at(iv, 4.0 + M.RELEASE_S) == 0 and M.duck_at(iv, 6.5) == 1
    e = M.duck_expr(iv, -3.0, -12.0)
    assert e.startswith("pow(10,(-3.00+(-12.00)*(clip((t-1.850)/0.15,0,1)*clip((4.450-t)/0.45,0,1)+")
    assert M.duck_expr([], -3.0, -12.0) == "pow(10,(-3.00+(-12.00)*(0))/20)"
    assert M.duck_expr(iv, -3.0, 0.0).endswith("*(0))/20)")  # no duck: no terms


def speech_source(path):
    """A synthetic 'speaker': a 220 Hz voice with harmonics in bursts (0.5-3.5 s and 5.0-8.0 s), silence between."""
    expr = ("0.3*(sin(2*PI*220*t)+0.5*sin(2*PI*440*t)+0.25*sin(2*PI*660*t))"
            "*(between(t,0.5,3.5)+between(t,5,8))*(0.6+0.4*sin(2*PI*3*t))")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=" + expr.replace(",", "\\,") + ":s=48000:d=9", "-ac", "1", str(path)],
                   check=True)
    return path


def test_the_mix_holds_loudness_and_peak_and_keeps_the_speech_on_top(tmp_path):
    src = speech_source(tmp_path / "src.wav")
    words = [{"w": "a", "t_start": 0.5, "t_end": 3.5}, {"w": "b", "t_start": 5.0, "t_end": 8.0}]
    segs = [SimpleNamespace(t_in=0.0, t_out=9.0, duration=9.0)]
    out, rec, bed = render_audio(src, segs, tmp_path, lead_s=1.0, music={"track": "raspberry_jam", "level_db": -20.0, "duck_db": -12.0},
                                 words=words, stem_name="cut_v2")
    loud = measure_loudness(out)
    assert abs(loud["integrated_lufs"] + 14.0) <= 0.5 and loud["true_peak_dbtp"] <= -1.0
    assert rec["encoded_tp"] <= -1.0 and rec["speech"]["target_i"] == -14.0 and rec["mode"] in ("linear", "limiter+linear")
    assert bed["track"] == "raspberry_jam" and bed["duck_source"] == "voice words" and bed["ducked"] == [[1.5, 4.5], [6.0, 9.0]]
    m = bed["measured"]
    assert m["margin_db"] >= 10  # the voice stays well over the ducked bed
    assert m["bed_open_rms_db"] - m["bed_under_speech_rms_db"] > 8  # the duck is audible: the bed rises in the pause
    assert m["open_s"] > 0.5 and m["under_s"] == 6.0
    stem = tmp_path / "cut_v2.speech.m4a"
    assert bed["speech_stem"] == stem.as_posix() and stem.is_file()
    assert not (tmp_path / "speech.wav").exists() and not (tmp_path / "music_bed.wav").exists()
    # the review measures silences on the stem: the bed would fill the pause between the bursts
    assert R.speech_audio({"render": {"music": bed}}, out) == stem
    assert R.silences_in(stem) and not R.silences_in(out)
    assert R.speech_audio({"render": {}}, out) == out


def test_without_music_render_audio_is_as_before(tmp_path):
    src = speech_source(tmp_path / "src.wav")
    out, rec, bed = render_audio(src, [SimpleNamespace(t_in=0.0, t_out=9.0, duration=9.0)], tmp_path)
    assert bed is None and "speech" not in rec and "encoded_tp" not in rec and out.name == "audio.m4a"


def test_the_reviewer_is_told_the_music_is_on_purpose():
    caps = {"lines": 0, "over_face": [], "min_clearance_px": None}
    bed = {"name": "Raspberry Jam", "level_db": -20.0, "duck_db": -12.0, "measured": {"margin_db": 15.9}}
    facts = R.facts_text([], caps, 30.0, music=bed)
    last = facts.splitlines()[-1]
    assert last.startswith("3. Music: a background music bed (\"Raspberry Jam\")") and "12 dB lower" in last and "15.9 dB" in last
    assert "Music" not in R.facts_text([], caps, 30.0)


def test_music_fixes():
    edl = {"spans": []}
    now, fix = human.music_fix(edl, "apple_cider", None, None)
    assert now == {"track": "apple_cider", "level_db": -20.0, "duck_db": -12.0} and fix["op"] == "music" and "ducked -12" in fix["note"]
    edl["music"] = now
    now, fix = human.music_fix(edl, "raspberry_jam", -18, None)  # the duck already chosen stays
    assert now == {"track": "raspberry_jam", "level_db": -18.0, "duck_db": -12.0}
    assert fix["note"] == "music track 'apple_cider' -> 'raspberry_jam', level_db -20.0 -> -18.0"
    for args, msg in ((("apple_cider", None, None), "already"), (("jazz", None, None), "no music track"),
                      (("apple_cider", -50, None), "outside")):
        with pytest.raises(SystemExit, match=msg):
            human.music_fix(edl, *args)
    now, fix = human.music_fix(edl, None)
    assert now is None and fix["op"] == "music_off"
    with pytest.raises(SystemExit, match="no music"):
        human.music_fix({"spans": []}, None)


def test_the_music_edit_makes_the_next_version(tmp_path, monkeypatch):
    d = tmp_path / "r"
    (d / "voice").mkdir(parents=True)
    (d / "voice" / "voice.json").write_text(json.dumps({"words": []}))
    (d / "edl_v3.json").write_text(json.dumps({"spans": [], "look": {"preset": "box"}, "captions": [1], "render": {"x": 1}}))
    done = {}
    monkeypatch.setattr(human, "_finish", lambda run_dir, edl_path, n, *a: done.update(n=n, edl=json.loads(edl_path.read_text())))
    human.music(d, "ukulele", level_db=-24)
    e = done["edl"]
    assert done["n"] == 4 and e["music"] == {"track": "ukulele", "level_db": -24.0, "duck_db": -12.0}
    assert e["look"] == {"preset": "box"} and "render" not in e and e["patches"][-1]["action"] == "music"
    human.music(d, None)
    assert "music" not in done["edl"] and done["edl"]["patches"][-1]["fixes"][0]["op"] == "music_off"


def test_a_replan_and_the_review_patches_keep_the_music(tmp_path, monkeypatch):
    music = {"track": "apple_cider", "level_db": -22.0, "duck_db": -10.0}
    # review patches deep-copy the EDL
    new, _ = R.apply_fixes({"spans": [], "music": music}, [], None)
    assert new["music"] == music
    # replan builds a new EDL from a new plan: music is carried like the look
    d = tmp_path / "r"
    (d / "voice").mkdir(parents=True)
    (d / "voice" / "voice.json").write_text("{}")
    (d / "watch.json").write_text("{}")
    (d / "run.json").write_text(json.dumps({"brief": "b", "target_s": 30}))
    (d / "edl_v2.json").write_text(json.dumps({"spans": [], "runtime_s": 30, "source": {}, "music": music}))
    planned = SimpleNamespace(parsed={}, cost_usd=0.0, seconds=0.0)
    monkeypatch.setattr("montivo.engine.spark.Spark", lambda *a, **k: SimpleNamespace(plan=lambda *a, **k: planned))
    monkeypatch.setattr(human.VoiceResult, "from_json", staticmethod(lambda j: SimpleNamespace(
        transcript_text=lambda: "", speech_rate=lambda: 2.5)))
    monkeypatch.setattr(human, "build_edl", lambda *a, **k: {"spans": [{"id": "s1", "role": "hook", "t_in": 0.0, "t_out": 1.0,
                                                                         "words": "hi"}], "runtime_s": 1.0, "role_fixes": [],
                                                              "title_card_text": "t"})
    done = {}
    monkeypatch.setattr(human, "_finish", lambda run_dir, edl_path, n, *a: done.update(edl=json.loads(edl_path.read_text())))
    human.replan(d, "shorter")
    assert done["edl"]["music"] == music


def test_music_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    from tests.test_ui import _run_folder
    _run_folder(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(create_app(tmp_path))
    info = c.get("/api/music").json()
    assert [t["id"] for t in info["tracks"]] == IDS and info["tracks"][0]["author"].startswith("omfgdude")
    assert info["default"] == {"level_db": -20.0, "duck_db": -12.0} and info["tracks"][0]["url"] == "/api/music/chill_lofi"
    r = c.get("/api/music/apple_cider")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mp4" and len(r.content) > 10000
    assert c.get("/api/music/jazz").status_code == 404
    assert c.get("/api/runs/r1").json()["versions"][0]["music"] is None
    r = c.post("/api/runs/r1/music", json={"track": "jazz"})
    assert r.status_code == 400 and "no music track" in r.json()["error"]
    assert c.post("/api/runs/r1/music", json={"track": "apple_cider", "level_db": 0}).status_code == 400
    assert c.post("/api/runs/r1/music", json={"track": "apple_cider", "review": 1}).status_code == 409  # review needs the key


def test_the_voice_gain_is_checked_and_kept_only_when_not_zero():
    assert M.normalize_music({"track": "ukulele", "voice_db": 0}) == {"track": "ukulele", "level_db": -20.0, "duck_db": -12.0}
    assert M.normalize_music({"track": "ukulele", "voice_db": 2.04})["voice_db"] == 2.0
    for bad in (7, -13, "3", True):
        with pytest.raises(ValueError):
            M.normalize_music({"track": "ukulele", "voice_db": bad})
    edl = {"music": {"track": "ukulele", "level_db": -20.0, "duck_db": -12.0}}
    now, fix = human.music_fix(edl, "ukulele", None, None, 3)  # the timeline's Voice fader alone
    assert now["voice_db"] == 3.0 and fix["note"] == "music voice_db 0.0 -> 3.0"
    edl["music"] = now
    now, fix = human.music_fix(edl, "ukulele", -24, None, None)  # a level change keeps the voice gain
    assert now == {"track": "ukulele", "level_db": -24.0, "duck_db": -12.0, "voice_db": 3.0}
    now, fix = human.music_fix(edl, "ukulele", None, None, 0)
    assert "voice_db" not in now and fix["note"] == "music voice_db 3.0 -> 0.0"
    with pytest.raises(SystemExit, match="voice \\+3 dB"):
        human.music_fix(edl, "ukulele", None, None, 3)
    with pytest.raises(SystemExit, match="no level"):
        human.music_fix(edl, None, None, None, 1)


def test_the_bed_is_kept_as_a_stem_and_the_voice_gain_moves_the_balance(tmp_path):
    src = speech_source(tmp_path / "src.wav")
    words = [{"w": "a", "t_start": 0.5, "t_end": 3.5}, {"w": "b", "t_start": 5.0, "t_end": 8.0}]
    segs = [SimpleNamespace(t_in=0.0, t_out=9.0, duration=9.0)]
    base = {"track": "raspberry_jam", "level_db": -20.0, "duck_db": -12.0}
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _, _, flat = render_audio(src, segs, a, music=base, words=words, stem_name="cut_v2")
    out, _, up = render_audio(src, segs, b, music={**base, "voice_db": 4.0}, words=words, stem_name="cut_v3")
    assert flat["music_stem"] == (a / "cut_v2.music.m4a").as_posix() and (a / "cut_v2.music.m4a").is_file()
    assert flat["voice_db"] == 0.0 and up["voice_db"] == 4.0
    assert up["measured"]["margin_db"] == pytest.approx(flat["measured"]["margin_db"] + 4.0, abs=0.2)
    loud = measure_loudness(out)  # the mix is normalised after the gain: still -14 LUFS
    assert abs(loud["integrated_lufs"] + 14.0) <= 0.5 and loud["true_peak_dbtp"] <= -1.0


def test_an_older_version_gets_its_bed_rebuilt(tmp_path):
    from montivo.engine.render import Segment, music_stem
    d = tmp_path / "r"
    (d / "voice").mkdir(parents=True)
    (d / "voice" / "voice.json").write_text(json.dumps({"words": [{"w": "a", "t_start": 0.5, "t_end": 3.5}]}))
    edl = {"music": {"track": "ukulele", "level_db": -20.0, "duck_db": -12.0},
           "render": {"title": {"seconds": 1.0}, "segments": [{"index": 0, "t_in": 0.0, "t_out": 4.0, "duration": 4.0, "spans": ["s1"]},
                                                              {"index": 1, "t_in": 6.0, "t_out": 8.5, "duration": 2.5, "spans": ["s2"]}]}}
    out = music_stem(d, edl, d / "ui" / "cache" / "music_v2.m4a")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True, check=True).stdout)
    assert abs(dur - 7.5) < 0.06 and not list(out.parent.glob("*.tmp*"))
    with pytest.raises(ValueError):
        music_stem(d, {"render": edl["render"]}, out)
    assert Segment(index=0, t_in=6.0, t_out=8.5, span_ids=[], frames=1, fps=1 / 2.5).duration == 2.5


def test_the_stem_and_track_peak_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    from tests.test_ui import _run_folder
    _run_folder(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(create_app(tmp_path))
    d = tmp_path / "runs" / "r1"
    n = max(int(p.stem.split("_v")[1]) for p in d.glob("edl_v*.json"))
    src = speech_source(tmp_path / "src.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:a", "aac", str(d / f"cut_v{n}.mp4")], check=True)
    r = c.get(f"/api/runs/r1/stem?v={n}&track=music")
    assert r.status_code == 404 and "no music" in r.json()["error"]
    edl = json.loads((d / f"edl_v{n}.json").read_text())
    (d / "render").mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:a", "aac", str(d / "render" / f"cut_v{n}.speech.m4a")], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:a", "aac", str(d / "render" / f"cut_v{n}.music.m4a")], check=True)
    edl["music"] = {"track": "ukulele", "level_db": -20.0, "duck_db": -12.0}
    edl.setdefault("render", {})["music"] = {"ducked": [[1.0, 2.0]]}
    (d / f"edl_v{n}.json").write_text(json.dumps(edl))
    for track in ("voice", "music"):
        r = c.get(f"/api/runs/r1/stem?v={n}&track={track}")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/mp4"
        pk = c.get(f"/api/runs/r1/peaks?v={n}&track={track}").json()
        assert pk["rate"] == 100 and max(pk["peaks"]) > 0.1
    assert c.get(f"/api/runs/r1/stem?v={n}&track=drums").status_code == 400
    mu = next(x for x in c.get("/api/runs/r1").json()["versions"] if x["n"] == n)["music"]
    assert mu["voice_db"] == 0.0 and mu["ducked"] == [[1.0, 2.0]]
