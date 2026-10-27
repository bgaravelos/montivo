"""Offline: the Open Weight planner path (Muse Spark Open on Baseten, OpenAI-compatible chat completions). The HTTP
call is replaced; what is checked is the request body, the Responses-shape adapter, the ledger and the guards."""
import json

import pytest

from montivo.config import OPEN_MODEL, SPARK_MODEL, is_open_model, spark_cost
from montivo.engine import spark as spark_mod
from montivo.engine.api import chat_as_response
from montivo.ledger import Ledger

PLAN = {"paper_edit": [], "title_card_text": "Four Meta models", "estimated_runtime_s": 40.0}
CHAT = {"id": "chatcmpl-1", "model": OPEN_MODEL, "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(PLAN)}}],
        "usage": {"prompt_tokens": 9000, "completion_tokens": 1500, "prompt_tokens_details": {"cached_tokens": 1000}}}


def test_chat_completion_reads_as_a_response():
    r = chat_as_response(CHAT)
    assert r["status"] == "completed" and r["id"] == "chatcmpl-1"
    assert spark_mod.output_text(r) == json.dumps(PLAN)
    assert r["usage"] == {"input_tokens": 9000, "output_tokens": 1500, "input_tokens_details": {"cached_tokens": 1000}}
    assert chat_as_response({**CHAT, "choices": [{**CHAT["choices"][0], "finish_reason": "length"}]})["status"] == "incomplete"


def test_open_model_ids_and_price():
    assert is_open_model(OPEN_MODEL) and is_open_model("meta-models/muse-spark-open") and not is_open_model(SPARK_MODEL)
    usage = chat_as_response(CHAT)["usage"]
    assert spark_cost(usage, OPEN_MODEL) == pytest.approx((8000 * 1.25 + 1000 * 0.15 + 1500 * 4.25) / 1e6)


def test_plan_call_goes_to_baseten_as_chat(tmp_path, monkeypatch):
    sent = {}

    def fake_chat(body):
        sent.update(body)
        return CHAT

    monkeypatch.setattr(spark_mod, "chat_create", fake_chat)
    monkeypatch.setattr(spark_mod, "responses_create", lambda body: pytest.fail("the Meta Responses API was called"))
    ledger = Ledger(tmp_path / "ledger.json")
    sp = spark_mod.Spark(tmp_path, ledger, model=OPEN_MODEL, log=lambda m: None)
    call = sp.plan("a 40 s cut", "[turn 1 | 0.00-3.00s | speaker A] hello", {"speaker_map": []}, 40.0, words_per_s=2.4)
    assert call.parsed == PLAN and call.status == "completed"
    assert sent["model"] == OPEN_MODEL and sent["max_tokens"] == 16000 and "input" not in sent
    assert sent["reasoning_effort"] == "low"  # the default effort spends the whole budget on reasoning
    assert sent["messages"][0]["role"] == "user" and "TRANSCRIPT (authoritative)" in sent["messages"][0]["content"]
    fmt = sent["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True and fmt["json_schema"]["name"] == "plan"
    assert fmt["json_schema"]["schema"] == spark_mod.schemas.PLAN
    entry = json.loads((tmp_path / "ledger.json").read_text())["entries"][-1]
    assert entry["model"] == OPEN_MODEL and entry["stage"] == "spark.plan" and entry["tokens_cached"] == 1000
    assert json.loads((tmp_path / "spark" / "plan_response.json").read_text())["object"] == "chat.completion"


def test_open_model_refuses_video(tmp_path):
    sp = spark_mod.Spark(tmp_path, Ledger(tmp_path / "ledger.json"), model=OPEN_MODEL, log=lambda m: None)
    with pytest.raises(SystemExit, match="text only"):
        sp.call("plan", "p", {"type": "object"}, file_id="file-1")


def test_missing_baseten_key_stops_before_any_call(monkeypatch):
    from montivo.engine.api import chat_create
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="BASETEN_API_KEY"):
        chat_create({"model": OPEN_MODEL})
