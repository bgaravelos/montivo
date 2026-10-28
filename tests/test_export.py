"""Offline: the export bundle's text files (SRT, WebVTT, transcript) and the Export page's pre-flight checks."""
import json

from montivo import export as X
from montivo.server import preflight as P


def edl(**kw):
    caps = [{"text": "Hi. I'm Billy.", "t_in": 0.013, "t_out": 1.313, "box": [281, 1450, 518, 137], "placement": "below_head"},
            {"text": "Four Meta models do all", "t_in": 61.5, "t_out": 63.25, "box": [80, 500, 920, 137], "placement": "fixed",
             "over_face": True}]
    return {"version": 5, "runtime_s": 37.9, "title_card_text": "Raw in", "captions": caps,
            "callouts": [{"box": [400, 200, 300, 100]}], "source": {"fps": 30.0},
            "render": {"width": 1080, "height": 1920, "duration_s": 37.93, "title": None,
                       "loudness_ebur128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.6}}, **kw}


def test_srt_vtt_and_transcript():
    cues = X.caption_cues(edl())
    assert X.srt(cues).splitlines()[:4] == ["1", "00:00:00,013 --> 00:00:01,313", "Hi. I'm Billy.", ""]
    assert "00:01:01.500 --> 00:01:03.250\nFour Meta models do all" in X.vtt(cues) and X.vtt(cues).startswith("WEBVTT\n\n")
    assert "[01:01.50] Four Meta models do all" in X.transcript(edl(), cues)


def test_title_ready_needs_the_same_words(tmp_path):
    e = edl(render={"title": {"seconds": 1.5}}, title_card_text="Raw in")
    assert not X.title_ready(tmp_path, e)
    (tmp_path / "title").mkdir()
    (tmp_path / "title" / "title_card.png").write_bytes(b"x")
    (tmp_path / "title" / "request.json").write_text(json.dumps({"prompt": 'the words "Other"'}))
    assert not X.title_ready(tmp_path, e)  # a later version changed the words: the clean copy goes without the card
    (tmp_path / "title" / "request.json").write_text(json.dumps({"prompt": 'the words "Raw in"'}))
    assert X.title_ready(tmp_path, e)


def test_preflight(tmp_path):
    (tmp_path / "edl_v5.json").write_text(json.dumps(edl()))
    (tmp_path / "review_v5.json").write_text(json.dumps({"cut": "runs/x/cut_v5.mp4", "review": {"overall": 76, "pass": False},
                                                         "pass_gate": {"reasons": ["caption over the head at 3.00-4.00 s"]}}))
    rows = {r["id"]: r for r in P.checks(tmp_path, 5)}
    assert rows["review"]["status"] == "warn" and "caption over the head" in rows["review"]["detail"]
    assert rows["loudness"]["status"] == "ok" and rows["format"]["status"] == "ok" and rows["duration"]["status"] == "ok"
    # the lower line (bottom 1587) is under TikTok's caption (from 1480) but clear on the right-hand column rule
    assert rows["safe_tiktok"]["status"] == "warn" and "1 of 2 caption lines" in rows["safe_tiktok"]["detail"]
    assert rows["face"]["status"] == "info"  # the line over the face is at the person's fixed position
    long = edl(render={**edl()["render"], "duration_s": 200.0})
    (tmp_path / "edl_v5.json").write_text(json.dumps(long))
    rows = {r["id"]: r for r in P.checks(tmp_path, 5)}
    assert rows["duration"]["status"] == "fail" and "Reels, Shorts" in rows["duration"]["detail"]


def test_right_column_only_counts_low_on_screen():
    z = P.ZONES["TikTok"]
    assert not P.under_ui([100, 300, 950, 100], z)  # wide, but high: the like / share column is lower down
    assert P.under_ui([100, 1200, 950, 100], z)
