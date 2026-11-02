"""Offline: review.review_render, the step every review runs. A tiny synthetic run folder (test_review's make_edl, its
Voice words, a 5.1 s lavfi cut) with the HTTP layer of Spark faked, the upload stubbed and Voice on the cut stubbed:
the review copy is made, the measured facts are assembled (silences, cut edge levels, caption timeline, edge words
heard, the caption-vs-heard diff), a version with a music bed is measured on its speech stem, the pass gate lands in
review_vN.json and the ledger carries the call's cost. No network, no key."""
import json
import subprocess

import pytest

import montivo.engine.spark as S
from montivo.config import OPEN_MODEL, SPARK_MODEL
from montivo.engine import review as R
from montivo.engine.schemas import CRITERIA
from montivo.ledger import Entry, Ledger
from tests.test_edges import SynthAudio
from tests.test_review import make_edl, voice_words

CLEAN = [(10.05, 11.15), (11.85, 12.65), (30.65, 32.05)]  # the source speech, every cut edge in a pause
LOUD = [(10.05, 11.15), (11.85, 13.2), (30.65, 32.05)]  # s2's out edge (12.7 s, the cut at 3.6 s) inside speech
CUT_S = 5.1  # title 1.5 + s1 1.2 + s2 0.9 + s3 1.5
VOICE_USD = 0.0021


def _media(path, audio="sine=frequency=440", seconds=CUT_S, video=True):
    v = ["-f", "lavfi", "-i", f"testsrc=size=108x192:rate=30:duration={seconds}"] if video else []
    a = ["-f", "lavfi", "-i", f"{audio}:duration={seconds}" if "sine" in audio else f"{audio}"]
    out = ["-c:v", "libx264", "-pix_fmt", "yuv420p"] if video else []
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *v, *a, "-t", str(seconds), *out, "-c:a", "aac",
                    str(path)], check=True)


def _review(overall=90, passed=True, issues=()):
    return {"pass": passed, "overall": overall, "scores": {c: 9 for c in CRITERIA}, "issues": list(issues),
            "summary": "fine"}


def _response(answer, tokens_in=4000, tokens_out=900):
    return {"id": "resp_rv", "status": "completed", "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}


def _chat(answer):
    return {"id": "chatcmpl-rv", "object": "chat.completion", "model": OPEN_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(answer)}}],
            "usage": {"prompt_tokens": 4000, "completion_tokens": 900}}


@pytest.fixture
def run(tmp_path, monkeypatch):
    """A run folder with edl_v1 (title card, one caption line over the head box, one clear of it), voice.json and
    cut_v1.mp4; Audio.load gives synthetic source audio, the upload and Voice on the cut are stubbed and record
    what they got."""
    d = tmp_path / "runs" / "rr"
    (d / "voice").mkdir(parents=True)
    (d / "voice" / "voice.json").write_text(json.dumps({"words": voice_words()}))
    edl, _ = make_edl()
    edl["captions"] = [
        {"id": "c1", "t_in": 1.5, "t_out": 2.7, "text": "w00 w01 w02 w03", "spans": ["s1"],
         "box": [100, 1300, 880, 120], "head_box": [300, 300, 780, 800]},
        {"id": "c2", "t_in": 2.7, "t_out": 3.6, "text": "w06 w07 w08", "spans": ["s2"],
         "box": [100, 600, 880, 120], "head_box": [300, 300, 780, 800], "placement": "auto"}]
    cut = d / "cut_v1.mp4"
    _media(cut)
    seen = {"upload": [], "heard": [], "speech": CLEAN, "drop_heard": set()}
    monkeypatch.setattr(R.Audio, "load", staticmethod(lambda run_dir: SynthAudio(seen["speech"])))

    def upload(path, mime):
        seen["upload"].append(path)
        return {"id": "file-rv1"}

    def heard(media, out_dir, ledger, log=print):
        seen["heard"].append(media)
        ledger.add(Entry(stage="review.heard", model="muse-voice-transcribe-1.0", seconds=0.1, cost_usd=VOICE_USD))
        kept = R.kept_words(edl, R.Words(voice_words()))
        return [{"w": w["w"], "t_start": w["t_start"], "t_end": w["t_end"]} for w in kept
                if w["w"] not in seen["drop_heard"]]

    monkeypatch.setattr(R, "upload_file", upload)
    monkeypatch.setattr(R, "heard_words", heard)
    return d, edl, cut, seen


def _go(d, edl, cut, monkeypatch, answer, model=SPARK_MODEL, **kw):
    ledger = Ledger(d / "ledger.json")
    bodies = []
    if model == SPARK_MODEL:
        monkeypatch.setattr(S, "responses_create", lambda body: bodies.append(body) or _response(answer))
    else:
        monkeypatch.setattr(S, "chat_create", lambda body: bodies.append(body) or _chat(answer))
    spark = S.Spark(d, ledger, model=model, log=lambda *a: None)
    rv = R.review_render(d, edl, cut, 1, ledger, spark, log=lambda *a: None, **kw)
    return rv, ledger, bodies


def test_a_clean_cut_gets_its_copy_facts_and_the_reviewers_pass(run, monkeypatch):
    d, edl, cut, seen = run
    edl["captions"].pop()  # only the line clear of the head
    rv, ledger, bodies = _go(d, edl, cut, monkeypatch, _review())
    # the review copy: 720x1280 with the clock, uploaded once, the clock PNGs cleaned up
    copy = d / "review" / "review_v1" / "cut_v1_review.mp4"
    assert rv["review_copy"] == copy.as_posix() and copy.is_file() and seen["upload"] == [copy]
    assert not (d / "review" / "review_v1" / "clock").exists()
    wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", str(copy)], capture_output=True, text=True).stdout.strip()
    assert wh == f"{R.REVIEW_W},{R.REVIEW_H}"
    assert rv["file_id"] == "file-rv1" and rv["reviewer"] == SPARK_MODEL
    assert rv["duration_s"] == pytest.approx(CUT_S, abs=0.1)
    # the facts: a tone with no gaps, four edges all quiet, the caption timeline, every edge word heard
    f = rv["facts"]
    assert f["silences"] == [] and f["head_frame"] is None
    assert [e["t"] for e in f["cut_edges"]] == [1.5, 2.7, 3.6, 5.1] and not any(e["loud"] for e in f["cut_edges"])
    assert f["caption_timeline"] == [{"t_in": 1.5, "t_out": 2.7, "text": "w00 w01 w02 w03"}]
    assert f["captions"]["over_face"] == [] and f["edge_words"]["unheard"] == []
    assert len(f["heard"]) == 12 and seen["heard"] == [cut]  # no music: Voice listens to the cut itself
    # the facts reach the reviewer's prompt, the video goes by file id (Spark 1.3 hears it, no heard phrases)
    prompt = bodies[0]["input"][0]["content"][0]["text"]
    assert "Silence gaps" in prompt and "w00 w01 w02 w03" in prompt and "every one of them is heard" in prompt
    assert "Words heard in this cut" not in prompt
    assert {"type": "input_file", "file_id": "file-rv1"} in bodies[0]["input"][0]["content"]
    # the gate is clear, the gated pass is the reviewer's
    assert rv["review"]["pass"] is True and rv["review"]["reviewer_pass"] is True
    assert rv["pass_gate"]["blocked"] is False and rv["pass_gate"]["reasons"] == []
    # the file on disk is what came back, the ledger has the prep, Voice and the review call with its cost
    assert json.loads((d / "review_v1.json").read_text()) == json.loads(json.dumps(rv))
    stages = {e.stage: e for e in ledger.entries}
    assert set(stages) == {"review.prep", "review.heard", "spark.review"}
    assert stages["spark.review"].cost_usd == rv["cost_usd"] > 0 and stages["spark.review"].tokens_in == 4000
    assert stages["review.prep"].cost_usd == 0.0
    assert ledger.total_cost() == pytest.approx(rv["cost_usd"] + VOICE_USD, abs=1e-4)
    assert json.loads((d / "ledger.json").read_text())["total_cost_usd"] == ledger.total_cost()


def test_measured_faults_hold_the_reviewers_pass(run, monkeypatch):
    d, edl, cut, seen = run
    seen["speech"] = LOUD
    seen["drop_heard"] = {"w08"}  # the word at the loud out edge is not heard in the cut
    rv, _, _ = _go(d, edl, cut, monkeypatch, _review(overall=91))
    gate = rv["pass_gate"]
    assert rv["review"]["reviewer_pass"] is True and rv["review"]["pass"] is False and gate["blocked"]
    checks = " | ".join(gate["checks"])
    assert "cut at 3.60 s inside speech" in checks
    assert '"w08" at the cut at 3.60 s not heard' in checks
    assert "caption over the head at 2.70-3.60 s" in checks
    assert {i["check"] for i in gate["issues"]} == {"loud_edge", "edge_word", "caption_face"}
    assert all(i["severity"] == "major" for i in gate["issues"])
    assert [e["loud"] for e in rv["facts"]["cut_edges"]] == [False, False, True, False]
    assert len(rv["facts"]["captions"]["over_face"]) == 1
    assert gate["checked_captions"] == [] and gate["open_majors"] == []
    saved = json.loads((d / "review_v1.json").read_text())
    assert saved["pass_gate"]["checks"] == gate["checks"] and saved["review"]["pass"] is False


def test_a_reviewer_major_holds_the_pass_unless_a_person_checked_that_caption(run, monkeypatch):
    d, edl, cut, _ = run
    edl["captions"].pop()
    edl["captions"][0]["box"] = [100, 1300, 880, 120]
    major = {"t_s": 2.0, "t_end_s": 2.0, "criterion": "captions_match_speech", "severity": "major", "what": "x",
             "fix_op": "caption_fix", "params": {"edge": "none", "seconds": 0, "target_t_s": -1, "text": "w00",
                                                 "direction": "none"}, "fix_args": ""}
    rv, _, _ = _go(d, edl, cut, monkeypatch, _review(issues=[major]))
    assert rv["review"]["pass"] is False and rv["pass_gate"]["checks"] == []
    assert rv["pass_gate"]["open_majors"] == [{"t_s": 2.0, "criterion": "captions_match_speech", "severity": "major"}]
    # a person retexted that line: the reviewer's ear no longer holds the pass
    edl["caption_overrides"] = [{"span": "s1", "src_t": 10.0, "text": "w00 w01 w02 w03", "why": "human"}]
    rv, _, _ = _go(d, edl, cut, monkeypatch, _review(issues=[major]))
    assert rv["facts"]["caption_timeline"][0].get("checked") is True
    assert rv["pass_gate"]["checked_captions"] == [[1.5, 2.7]]
    assert rv["review"]["pass"] is True and rv["pass_gate"]["open_majors"] == []


def test_a_music_version_is_measured_on_its_speech_stem(run, monkeypatch):
    d, edl, cut, seen = run
    edl["captions"].pop()
    (d / "render").mkdir()
    stem = d / "render" / "cut_v1.speech.m4a"
    _media(stem, audio="anullsrc=r=48000:cl=mono", video=False)  # the speech stem: silence under the bed's tone
    edl.setdefault("render", {})["music"] = {"track": "chill_lofi", "name": "Chill Lofi", "level_db": -18,
                                             "duck_db": -6, "speech_stem": stem.as_posix(),
                                             "measured": {"margin_db": 9.0}}  # render_bed's record
    rv, _, bodies = _go(d, edl, cut, monkeypatch, _review())
    assert seen["heard"] == [stem]  # Voice hears the speech without the bed
    assert rv["facts"]["silences"] and rv["facts"]["silences"][0][0] == 0.0  # silencedetect ran on the stem, not the cut
    assert rv["review_copy"].endswith("cut_v1_review.mp4")  # the reviewer still watches the mixed cut
    prompt = bodies[0]["input"][0]["content"][0]["text"]
    assert '"Chill Lofi"' in prompt and "speech sits 9 dB over it" in prompt
    # without the stem on disk the cut itself is measured
    stem.unlink()
    seen["heard"].clear()
    rv, _, _ = _go(d, edl, cut, monkeypatch, _review())
    assert seen["heard"] == [cut] and rv["facts"]["silences"] == []


def test_an_open_reviewer_gets_the_cut_inline_and_the_heard_words(run, monkeypatch):
    d, edl, cut, seen = run
    edl["captions"].pop()
    rv, ledger, bodies = _go(d, edl, cut, monkeypatch, _review(), model=OPEN_MODEL, name="review_v1_open")
    assert seen["upload"] == [] and rv["file_id"] is None and rv["reviewer"] == OPEN_MODEL
    content = bodies[0]["messages"][0]["content"]
    assert content[1]["type"] == "video_url" and content[1]["video_url"]["url"].startswith("data:video/mp4;base64,")
    assert "Words heard in this cut" in content[0]["text"] and "cannot hear its sound" in content[0]["text"]
    assert (d / "review_v1_open.json").is_file() and not (d / "review_v1.json").exists()
    assert {e.stage for e in ledger.entries} == {"review.prep", "review.heard", "spark.review"}
    assert next(e for e in ledger.entries if e.stage == "review.prep").model == "ffmpeg"
