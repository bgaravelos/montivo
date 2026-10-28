"""Offline: engine hygiene (28/10). Segment colour tags and overlay colours on a lavfi render, the cut written atomically,
the back-off on 429 / 5xx (Retry-After) and the one retry on an unparsable or incomplete Spark answer, a Voice chunk
run again after its stream broke, a SAM concept asked again when nothing came, and the run's own caption words
(glossary / highlight) instead of the demo's for every clip."""
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from PIL import Image

from montivo.engine import api
from montivo.engine.captions import DEMO_HIGHLIGHT, Overlay, highlight_words, run_glossary
from montivo.engine.edl import carry_run_words
from montivo.engine.reframe import SegmentPath
from montivo.engine.render import Segment, mux, render_audio, render_segment_video, render_title_video, source_color
from montivo.ledger import Ledger

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
MARKER = (0xFF, 0xC6, 0x29)


def probe_color(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=pix_fmt,color_range,color_space,color_primaries,color_transfer", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def pixel(video: Path, t: float, xy: tuple[int, int], tmp: Path) -> tuple[int, int, int]:
    png = tmp / f"px_{t:.2f}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(png)], check=True)
    return Image.open(png).convert("RGB").getpixel(xy)


@pytest.fixture
def full_range_source(tmp_path):
    """A 1920x1080 30 fps clip stored full range (yuvj420p / pc, BT.709), like Billy's DJI footage, with a tone."""
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=3", "-vf", "scale=out_range=pc:out_color_matrix=bt709",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuvj420p", "-color_range", "pc",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", str(src)],
                   check=True)
    return src


@needs_ffmpeg
def test_segments_and_title_are_limited_range_bt709_and_the_marker_stays_marker(tmp_path, full_range_source):
    assert source_color(full_range_source) == {"range": "pc", "matrix": "bt709"}
    rd = tmp_path / "render"
    rd.mkdir()
    swatch = rd / "swatch.png"
    Image.new("RGBA", (200, 200), MARKER + (255,)).save(swatch)
    seg = Segment(index=0, t_in=0.0, t_out=2.0, span_ids=["s1"], frames=60, fps=30.0)
    path = SegmentPath(t_in=0.0, t_out=2.0, track_id=None, crop_w=608, crop_h=1080, xs=[656] * 60, fps=30.0)
    segf = render_segment_video(full_range_source, seg, path, rd, [Overlay(swatch, 100, 100, 0.0, 2.0),
                                                                   Overlay(swatch, 500, 100, 0.0, 2.0, fade_s=0.2)],
                                color=source_color(full_range_source))
    title, frames = render_title_video(swatch, 30.0, rd, seconds=1.0, fade_s=0.2)
    for f in (segf, title):
        c = probe_color(f)
        assert c == {"pix_fmt": "yuv420p", "color_range": "tv", "color_space": "bt709", "color_primaries": "bt709",
                     "color_transfer": "bt709"}, f.name
    # the overlays went to YUV with BT.709, so the marker comes back as drawn (BT.601 gave #FFC122)
    for xy in ((200, 200), (600, 200)):
        assert all(abs(a - b) <= 4 for a, b in zip(pixel(segf, 1.0, xy, tmp_path), MARKER)), xy
    assert all(abs(a - b) <= 4 for a, b in zip(pixel(title, 0.2, (540, 960), tmp_path), MARKER))
    # the cut joins title + segment with one set of tags, written as .part and renamed
    audio, _, _ = render_audio(full_range_source, [seg], rd, lead_s=frames / 30.0, loudnorm=False)
    out = mux([title, segf], audio, tmp_path / "cut_v1.mp4", rd)
    assert probe_color(out)["color_range"] == "tv" and probe_color(out)["color_space"] == "bt709"
    assert not list(tmp_path.glob("*.part"))


def test_an_untagged_source_is_read_by_its_size(tmp_path, monkeypatch):
    def fake(stream):
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps({"streams": [stream]}), "")
    monkeypatch.setattr(subprocess, "run", fake({"height": 1080, "pix_fmt": "yuv420p"}))
    assert source_color(tmp_path / "x.mp4") == {"range": "tv", "matrix": "bt709"}
    monkeypatch.setattr(subprocess, "run", fake({"height": 480, "pix_fmt": "yuv420p"}))
    assert source_color(tmp_path / "x.mp4") == {"range": "tv", "matrix": "bt601"}
    monkeypatch.setattr(subprocess, "run", fake({"height": 480, "pix_fmt": "yuv420p", "color_space": "bt470bg"}))
    assert source_color(tmp_path / "x.mp4")["matrix"] == "bt470"


def test_a_failed_mux_leaves_the_old_cut_and_no_part_file(tmp_path):
    old = tmp_path / "cut_v3.mp4"
    old.write_bytes(b"the old cut")
    (tmp_path / "seg.mp4").write_bytes(b"not a video")
    (tmp_path / "audio.m4a").write_bytes(b"not audio")
    with pytest.raises(RuntimeError):
        mux([tmp_path / "seg.mp4"], tmp_path / "audio.m4a", old, tmp_path)
    assert old.read_bytes() == b"the old cut" and not list(tmp_path.glob("*.part"))


def test_dump_json_and_the_ledger_write_whole_files(tmp_path):
    p = tmp_path / "run" / "edl_v2.json"
    api.dump_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1} and oct(p.stat().st_mode & 0o777) == "0o644"
    with pytest.raises(TypeError):
        api.dump_json(p, {"bad": object()})  # a failed write keeps the old file and leaves no temp file
    assert json.loads(p.read_text()) == {"a": 1} and [x.name for x in p.parent.iterdir()] == ["edl_v2.json"]
    from montivo.ledger import Entry
    led = Ledger(tmp_path / "run" / "ledger.json")
    led.add(Entry(stage="render", model="ffmpeg", seconds=1.0, cost_usd=0.0))
    assert json.loads(led.path.read_text())["stages"]["render"]["calls"] == 1
    assert sorted(x.name for x in p.parent.iterdir()) == ["edl_v2.json", "ledger.json"]


# --------------------------------------------------------------------------- retries

@pytest.fixture
def fake_http(monkeypatch):
    """httpx.Client answers from `answers` (a list of httpx.Response, or exceptions to raise), in order; `sleeps`
    records the back-off."""
    state = {"answers": [], "requests": [], "sleeps": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        a = state["answers"].pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(api, "_sleep", state["sleeps"].append)
    monkeypatch.setattr(api, "api_key", lambda: "test-key")
    return state


def test_429_is_retried_after_the_servers_retry_after(fake_http):
    fake_http["answers"] = [httpx.Response(429, headers={"Retry-After": "7"}, text="slow down"),
                            httpx.Response(503, text="busy"),
                            httpx.Response(200, json={"id": "resp_1", "status": "completed"})]
    assert api.responses_create({"model": "m"})["id"] == "resp_1"
    assert fake_http["sleeps"] == [7.0, api.BACKOFF_S[1]] and len(fake_http["requests"]) == 3


def test_retry_after_is_capped_and_a_client_error_is_not_retried(fake_http):
    fake_http["answers"] = [httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200, json={"ok": 1})]
    assert api.responses_create({}) == {"ok": 1} and fake_http["sleeps"] == [api.RETRY_AFTER_MAX_S]
    fake_http["answers"] = [httpx.Response(400, text="bad schema")]
    with pytest.raises(RuntimeError, match="responses 400: bad schema"):
        api.responses_create({})
    fake_http["answers"] = [httpx.Response(429)] * (api.RETRIES + 1)
    with pytest.raises(RuntimeError, match="429"):
        api.responses_create({})


def test_a_stream_is_retried_while_it_opens(fake_http):
    sse = 'data: {"type": "response.output_text.delta", "delta": "x"}\n\ndata: [DONE]\n\n'
    fake_http["answers"] = [httpx.ConnectError("refused"), httpx.Response(429, headers={"Retry-After": "1"}),
                            httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})]
    assert [e["delta"] for e in api.stream_responses({"model": "sam"})] == ["x"]
    assert fake_http["sleeps"] == [api.BACKOFF_S[0], 1.0]


def test_upload_backs_off_too(fake_http, tmp_path):
    f = tmp_path / "proxy.mp4"
    f.write_bytes(b"0" * 10)
    fake_http["answers"] = [httpx.Response(502), httpx.Response(200, json={"id": "file-1"})]
    assert api.upload_file(f, "video/mp4")["id"] == "file-1" and len(fake_http["requests"]) == 2


def test_spark_asks_once_more_on_an_incomplete_answer(tmp_path, monkeypatch):
    from montivo.engine import spark as S
    bodies, answers = [], [
        {"id": "r1", "status": "incomplete", "usage": {"input_tokens": 100, "output_tokens": 1500},
         "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"preset": "bo'}]}]},
        {"id": "r2", "status": "completed", "usage": {"input_tokens": 100, "output_tokens": 300},
         "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"preset": "bold_pop"}'}]}]}]
    monkeypatch.setattr(S, "responses_create", lambda body: (bodies.append(json.loads(json.dumps(body))), answers.pop(0))[1])
    led = Ledger(tmp_path / "ledger.json")
    call = S.Spark(tmp_path, led, log=lambda m: None).call("look", "p", {"type": "object"}, max_output_tokens=1500)
    assert call.parsed == {"preset": "bold_pop"} and call.response_id == "r2"
    assert [b["max_output_tokens"] for b in bodies] == [1500, 2250]
    assert [e.status for e in led.entries] == ["incomplete/parse_error", "completed"]
    assert call.cost_usd == round(sum(e.cost_usd for e in led.entries), 4)
    assert (tmp_path / "spark" / "look_response_try1.json").is_file()
    # a second bad answer raises (one retry only)
    answers[:] = [{"id": "r3", "status": "completed", "output": []}] * 2
    with pytest.raises(RuntimeError, match="could not parse JSON"):
        S.Spark(tmp_path, led, log=lambda m: None).call("look", "p", {"type": "object"})


@needs_ffmpeg
def test_a_voice_chunk_whose_stream_broke_runs_again(tmp_path, monkeypatch):
    from montivo.engine import voice as V
    wav = tmp_path / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:r=24000:d=1", "-ac", "1", str(wav)],
                   check=True)
    calls = []

    def stream(path, request):
        calls.append(path)
        yield {"type": "audioProgress", "audioProcessedMs": 80}
        if len(calls) == 1:
            raise httpx.ReadError("connection reset")
    monkeypatch.setattr(V, "stream_transcribe", stream)
    logs = []
    V.transcribe(wav, tmp_path / "voice", Ledger(tmp_path / "ledger.json"), log=logs.append)
    assert len(calls) == 2 and any("running it again" in m for m in logs)
    events = [json.loads(line) for line in (tmp_path / "voice" / "events.jsonl").read_text().splitlines()]
    assert len(events) == 1  # the broken attempt's events are not kept twice


def test_sam_is_asked_again_only_when_nothing_came(tmp_path, monkeypatch):
    from montivo.engine import track as T
    from montivo.engine.ingest import Probe
    src = T.TrackSource(path=tmp_path / "p.mp4", probe=Probe(960, 540, 5.0, 2.0, 1, True, "h264"), file_id="f",
                        fps=5.0, resampled=False, upload_seconds=0.0)
    line = "<0f>0<|box;x1=1;y1=2;x2=30;y2=40;w=960;h=540|>\n"
    calls = []

    def stream(body, raw_sink=None):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("peer closed")
        yield {"type": "response.output_text.delta", "delta": line}
        yield {"type": "response.completed", "response": {"status": "completed", "usage": {"video_frames_processed": 10}}}
    monkeypatch.setattr(T, "stream_responses", stream)
    ct = T.track_concept(src, "head", tmp_path, Ledger(tmp_path / "ledger.json"), log=lambda m: None)
    assert len(calls) == 2 and ct.status == "completed" and list(ct.objects) == ["0"]

    def broken_after_a_frame(body, raw_sink=None):
        calls.append(1)
        yield {"type": "response.output_text.delta", "delta": line}
        raise httpx.ReadError("reset")
    calls.clear()
    monkeypatch.setattr(T, "stream_responses", broken_after_a_frame)
    ct = T.track_concept(src, "head", tmp_path, Ledger(tmp_path / "ledger.json"), log=lambda m: None)
    assert len(calls) == 1 and ct.status.startswith("stream_error") and list(ct.objects) == ["0"]  # frames kept


# --------------------------------------------------------------------------- the run's caption words

def test_a_new_edl_has_no_demo_words_an_old_one_keeps_them():
    new = {"glossary": {}, "highlight": []}
    assert run_glossary(new) == {} and highlight_words(new) == set()
    old = {}  # an EDL from before per-run words (runs 012-015)
    assert run_glossary(old)["metal models"] == "Meta models" and highlight_words(old) == set(DEMO_HIGHLIGHT)
    # the look adds and removes on top of the run's words
    assert highlight_words({"highlight": ["Acme"], "look": {"highlight_add": ["Widget"]}}) == {"acme", "widget"}
    assert "montivo" not in highlight_words({"look": {"highlight_off": ["montivo"]}})


def test_a_replan_carries_the_runs_words_and_an_old_edls_absence():
    new = {"glossary": {}, "highlight": []}
    carry_run_words({"glossary": {"acme": "ACME"}, "highlight": ["acme"]}, new)
    assert new == {"glossary": {"acme": "ACME"}, "highlight": ["acme"]}
    new = {"glossary": {}, "highlight": []}
    carry_run_words({}, new)  # an old EDL: its re-plan keeps the demo words
    assert new == {}


def test_build_edl_writes_empty_run_words():
    from montivo.engine.edl import build_edl
    from montivo.engine.voice import Turn, VoiceResult, Word
    words = [Word(w=w, t_start=i * 0.4, t_end=i * 0.4 + 0.3, turn_id=0, speaker="A", i=i)
             for i, w in enumerate("my voice is the product".split())]
    voice = VoiceResult(turns=[Turn(turnId=0, startMs=0, endMs=2000, speaker="A", transcript="my voice is the product")],
                        words=words, audio_s=2.0, chunks=[])
    plan = {"paper_edit": [{"turn_id": 0, "words": "my voice is the product", "role": "hook", "why": "", "callout": ""}]}
    edl = build_edl(plan, voice, {}, {"fps": 30.0}, "brief", {})
    assert edl["glossary"] == {} and edl["highlight"] == []


def test_punch_ins_use_the_runs_names_and_the_looks():
    from tests.test_punch import FakeAudio, fixture
    from montivo.engine.punch import plan_punches
    cap = {"segment": 0, "t_in": 2.7, "t_out": 3.9, "box": [200, 1090, 680, 120]}  # keeps "everything" out

    def words_punched(**edl_extra):
        edl, segs, paths, words, track = fixture()
        edl.update(edl_extra)
        _, entries = plan_punches(edl, segs, paths, words, FakeAudio(words), track, captions=[cap])
        return [e.get("word") for e in entries]
    assert "Montivo" in words_punched()  # an old EDL: the demo's names
    assert "Montivo" not in words_punched(highlight=[])  # a new run: "Montivo" is just a word at the median level
    assert "Montivo" not in words_punched(look={"highlight_off": ["montivo"]})  # the look's off, as the captions
    assert "Montivo" in words_punched(highlight=[], look={"highlight_add": ["montivo"]})


@needs_ffmpeg
def test_an_ingest_ffmpeg_failure_reaches_the_run_log(tmp_path):
    from montivo.engine.ingest import make_wav
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video")
    logs = []
    with pytest.raises(subprocess.CalledProcessError) as exc:
        make_wav(bad, tmp_path / "a.wav", log=logs.append)
    assert logs and logs[0].startswith("ingest: wav failed (ffmpeg exit") and "broken.mp4" in logs[0]
    assert "broken.mp4" in str(exc.value)  # the stderr is in the error too
