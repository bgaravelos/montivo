"""Offline: the stages that run on Muse Spark Open when BASETEN_API_KEY is set (engine/stagemodel.py, measured with
tools/open_stages_bench.py and tools/monti_bench.py on 1/11), their fall back to Spark 1.3 when the open call fails or
the key is missing, and the stages that stay on Spark 1.3. The HTTP layer is faked: no network."""
import json

import pytest

import montivo.engine.spark as S
from montivo import monti
from montivo.config import OPEN_MODEL, SPARK_MODEL
from montivo.engine import look as L
from montivo.engine import stagemodel
from montivo.ledger import Ledger

MUSIC = {"reply": "Open Music in the rail (key 6).", "steps": ["Press ▶ on a track"], "target": "rail.music",
         "open_panel": "music", "handoff_to_ask": "", "suggestions": [], "section": "Music"}


def chat(answer: dict) -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": OPEN_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(answer)}}],
            "usage": {"prompt_tokens": 9000, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 8800}}}


def response(answer: dict) -> dict:
    return {"id": "resp_1", "status": "completed", "usage": {"input_tokens": 9000, "output_tokens": 300},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "meta")
    monkeypatch.setenv("BASETEN_API_KEY", "baseten")
    monkeypatch.delenv("MONTIVO_OPEN_STAGES", raising=False)
    return monkeypatch


def test_only_the_measured_stages_move_and_only_with_the_key(keys):
    assert set(stagemodel.OPEN_STAGES) == {"monti", "look"}
    assert stagemodel.model_for("monti") == OPEN_MODEL and stagemodel.model_for("look") == OPEN_MODEL
    for stage in ("titles", "post", "cover_design", "ask", "review", "watch", "plan_repair"):  # measured worse, or must see and hear
        assert stagemodel.model_for(stage) == SPARK_MODEL
    keys.setenv("MONTIVO_OPEN_STAGES", "0")
    assert stagemodel.model_for("monti") == SPARK_MODEL
    keys.delenv("MONTIVO_OPEN_STAGES")
    keys.delenv("BASETEN_API_KEY")
    assert stagemodel.model_for("monti") == SPARK_MODEL  # the hosted demo without a Baseten key stays on 1.3


def test_an_open_stage_goes_to_baseten_and_says_so(tmp_path, keys):
    sent = []
    keys.setattr(S, "chat_create", lambda body: sent.append(body) or chat(MUSIC))
    keys.setattr(S, "responses_create", lambda body: pytest.fail("Spark 1.3 was called"))
    led = Ledger(tmp_path / "ledger.json")
    c = stagemodel.call("monti", tmp_path, led, "monti", "where is music?", monti.SCHEMA, log=lambda m: None,
                        max_output_tokens=3000, reasoning_effort="low", stage="spark.monti", schema_name="monti")
    assert c.model == OPEN_MODEL and c.parsed == MUSIC and sent[0]["model"] == OPEN_MODEL
    e = led.entries[-1]
    assert e.model == OPEN_MODEL and e.stage == "spark.monti" and e.tokens_cached == 8800


def test_a_failed_open_call_falls_back_to_spark_13(tmp_path, keys):
    said = []

    def down(body):
        raise RuntimeError("chat completions 503: overloaded")
    keys.setattr(S, "chat_create", down)
    keys.setattr(S, "responses_create", lambda body: response(MUSIC))
    led = Ledger(tmp_path / "ledger.json")
    c = stagemodel.call("monti", tmp_path, led, "monti", "where is music?", monti.SCHEMA, log=said.append,
                        reasoning_effort="low", stage="spark.monti", schema_name="monti")
    assert c.model == SPARK_MODEL and c.parsed == MUSIC
    assert [e.model for e in led.entries] == [SPARK_MODEL]  # the failed request spent nothing
    assert any("failed" in m and SPARK_MODEL in m for m in said)


def test_no_fallback_when_the_model_is_forced_or_there_is_no_meta_key(tmp_path, keys):
    def down(body):
        raise RuntimeError("chat completions 503")
    keys.setattr(S, "chat_create", down)
    keys.setattr(S, "responses_create", lambda body: pytest.fail("no fallback expected"))
    led = Ledger(tmp_path / "ledger.json")
    with pytest.raises(RuntimeError):
        stagemodel.call("monti", tmp_path, led, "monti", "q", monti.SCHEMA, log=lambda m: None, model=OPEN_MODEL)
    keys.delenv("MODEL_API_KEY")
    with pytest.raises(RuntimeError):
        stagemodel.call("monti", tmp_path, led, "monti", "q", monti.SCHEMA, log=lambda m: None)


def test_monti_answers_on_spark_open_with_the_baseten_key(tmp_path, keys):
    keys.setattr(S, "chat_create", lambda body: chat(MUSIC))
    keys.setattr(S, "responses_create", lambda body: pytest.fail("Spark 1.3 was called"))
    out = monti.ask(tmp_path, "where is music?", {"page": "editor"})
    assert out["model"] == OPEN_MODEL and out["target"] == "rail.music"
    led = json.loads((tmp_path / "monti" / "ledger.json").read_text())
    assert led["entries"][0]["model"] == OPEN_MODEL


def test_monti_stays_on_spark_13_without_the_baseten_key(tmp_path, keys):
    keys.delenv("BASETEN_API_KEY")
    keys.setattr(S, "chat_create", lambda body: pytest.fail("Baseten was called"))
    keys.setattr(S, "responses_create", lambda body: response(MUSIC))
    assert monti.ask(tmp_path, "where is music?", {"page": "editor"})["model"] == SPARK_MODEL


def test_a_look_from_words_runs_on_spark_open(tmp_path, keys):
    from montivo.engine.graphics import Look
    answer = {**Look().to_json(), "size": 110, "why": "bigger"}
    answer.pop("highlight_off")
    sent = []
    keys.setattr(S, "chat_create", lambda body: sent.append(body) or chat(answer))
    keys.setattr(S, "responses_create", lambda body: pytest.fail("Spark 1.3 was called"))
    keys.setattr(L, "backdrop", lambda *a, **k: None)
    look, why, fixed = L.suggest(tmp_path, {"captions": []}, "make the captions bigger", Ledger(tmp_path / "l.json"))
    assert look["size"] == 110 and why == "bigger" and not fixed
    assert sent[0]["model"] == OPEN_MODEL and sent[0]["response_format"]["json_schema"]["name"] == "caption_look"


def test_title_hooks_and_post_text_stay_on_spark_13(tmp_path, keys):
    from montivo.engine import titles
    keys.setattr(S, "chat_create", lambda body: pytest.fail("Baseten was called"))
    keys.setattr(S, "responses_create", lambda body: response({"titles": [{"text": "Why stop at the first cut?",
                                                                           "why": "a question"}]}))
    edl = {"spans": [{"words": "why stop at the first cut"}], "title_card_text": "Montivo"}
    assert titles.suggest(tmp_path, edl, "brief", Ledger(tmp_path / "l.json"), log=lambda m: None)[0]["text"] == \
        "Why stop at the first cut?"
