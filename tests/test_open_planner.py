"""Offline: the Open Weight planner (Muse Spark Open on Baseten, Chat Completions) and the `montivo plan` A/B; the HTTP
call is replaced, nothing is sent."""
import json
from pathlib import Path

import pytest

from montivo.config import PLANNERS, SPARK_MODEL, SPARK_OPEN_MODEL, is_open, open_api_key, spark_cost
from montivo.engine import spark as spark_mod
from montivo.engine.spark import Spark, chat_result
from montivo.engine.voice import VoiceResult, fold_events
from montivo.ledger import Ledger

REC = Path(__file__).resolve().parent / "fixtures" / "voice_stream_nasa30.json"

# a plan as the host returns it; the A/B test below swaps in a verbatim turn of the recorded NASA stream
PLAN = {"paper_edit": [{"turn_id": 3, "words": "I can hear you loud and clear", "role": "hook", "why": "w", "callout": ""}],
        "estimated_runtime_s": 2.0, "title_card_text": "Open title", "caption_style_note": "", "reframe_note": ""}


def chat_response(content: str, finish: str = "stop") -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": SPARK_OPEN_MODEL,
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 2000, "completion_tokens": 400, "prompt_tokens_details": {"cached_tokens": 1000}}}


@pytest.fixture
def fake_chat(monkeypatch):
    sent: list[dict] = []

    def fake(body, timeout_s=900, retries=1):
        sent.append(body)
        return chat_response(json.dumps(fake.reply))
    fake.reply = PLAN
    monkeypatch.setattr(spark_mod, "chat_create", fake)
    monkeypatch.setattr(spark_mod, "responses_create", lambda *a, **k: pytest.fail("Meta Responses API called"))
    fake.sent = sent
    return fake


def test_planners_and_hosts():
    assert PLANNERS == {"spark": SPARK_MODEL, "open": SPARK_OPEN_MODEL}
    assert is_open(SPARK_OPEN_MODEL) and not is_open(SPARK_MODEL)


def test_open_key_is_required(monkeypatch):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="BASETEN_API_KEY"):
        open_api_key()


def test_open_call_is_a_strict_chat_completion_with_the_same_ledger(tmp_path, fake_chat):
    led = Ledger(tmp_path / "ledger.json")
    call = Spark(tmp_path, led, model=SPARK_OPEN_MODEL, log=lambda *_: None).call(
        "plan_open", "prompt", {"type": "object"}, reasoning_effort="low", stage="spark.plan", schema_name="plan")
    body = fake_chat.sent[0]
    assert body["model"] == SPARK_OPEN_MODEL and body["messages"] == [{"role": "user", "content": "prompt"}]
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "plan", "strict": True, "schema": {"type": "object"}}}
    assert body["reasoning_effort"] == "low" and body["max_tokens"] == 12000 and "input" not in body
    assert call.parsed == PLAN and call.status == "completed"
    usage = {"input_tokens": 2000, "output_tokens": 400, "input_tokens_details": {"cached_tokens": 1000}}
    assert call.cost_usd == round(spark_cost(usage, SPARK_OPEN_MODEL), 4)
    e = led.entries[0]
    assert (e.stage, e.model, e.tokens_in, e.tokens_out, e.tokens_cached) == ("spark.plan", SPARK_OPEN_MODEL, 2000, 400, 1000)
    assert (tmp_path / "spark" / "plan_open_request.json").is_file() and (tmp_path / "spark" / "plan_open_response.json").is_file()


def test_open_model_is_text_only(tmp_path, fake_chat):
    sp = Spark(tmp_path, Ledger(tmp_path / "ledger.json"), model=SPARK_OPEN_MODEL, log=lambda *_: None)
    with pytest.raises(ValueError, match="text only"):
        sp.call("watch", "p", {}, file_id="file-1")


def test_chat_result_length_and_content_parts():
    usage, status, text = chat_result(chat_response("{}", finish="length"))
    assert status == "incomplete" and text == "{}" and usage["input_tokens"] == 2000
    resp = chat_response("")
    resp["choices"][0]["message"]["content"] = [{"type": "text", "text": "{\"a\":"}, {"type": "text", "text": " 1}"}]
    assert chat_result(resp)[2] == '{"a": 1}'
    assert chat_result({})[0] == {"input_tokens": 0, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 0}}


@pytest.mark.skipif(not REC.is_file(), reason="recorded stream not present")
def test_plan_run_ab_on_a_run_folder(tmp_path, fake_chat):
    from montivo.run import plan_run
    turns, words = fold_events(json.loads(REC.read_text())["events"], offset_ms=0)
    voice = VoiceResult(turns=turns, words=words, audio_s=30.0, chunks=[])
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "voice.json").write_text(json.dumps(voice.to_json()))
    (tmp_path / "watch.json").write_text(json.dumps({"summary": "s", "visual_shots": [], "speaker_map": [],
                                                     "movement_moments": []}))
    (tmp_path / "run.json").write_text(json.dumps({"brief": "the answer", "target_s": 20, "model": SPARK_MODEL}))
    first = next(t for t in turns if t.turnId == 1)
    v1 = {"source": {"path": "x.mp4", "width": 1280, "height": 720, "fps": 30.0}, "tracks": {}, "reframe_concept": "head",
          "runtime_s": 1.5, "verbatim_rate": 1.0, "unplaced": [], "title_card_text": "V1 title",
          "spans": [{"id": "s1", "words": first.transcript, "t_in": 0, "t_out": 1.5}]}
    (tmp_path / "edl_v1.json").write_text(json.dumps(v1))
    turn3 = next(t for t in turns if t.turnId == 3).transcript
    fake_chat.reply = {**PLAN, "paper_edit": [{**PLAN["paper_edit"][0], "words": turn3}]}

    out = plan_run(tmp_path, SPARK_OPEN_MODEL, tag="open", render=False, log=lambda *_: None)
    edl = json.loads((tmp_path / "edl_open.json").read_text())
    assert edl["planner"] == SPARK_OPEN_MODEL and edl["spans"][0]["turn_id"] == 3 and edl["verbatim_rate"] == 1.0
    assert edl["title_card_text"] == "V1 title" and edl["title_card_text_planned"] == "Open title"
    assert out["open"]["planner"] == SPARK_OPEN_MODEL and out["v1"]["planner"] == SPARK_MODEL
    assert out["open"]["spans"] == 1 and out["open"]["plan_cost_usd"] > 0 and "render" not in out
    assert (tmp_path / "plan_open.json").is_file() and (tmp_path / "spark" / "plan_open_request.json").is_file()
    prompt = fake_chat.sent[0]["messages"][0]["content"]
    assert "Brief: the answer" in prompt and turn3 in prompt
