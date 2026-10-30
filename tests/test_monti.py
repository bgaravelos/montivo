"""Offline tests for Monti, the help gecko (montivo/monti.py, POST /api/monti): the guide parses into sections whose
targets exist, the schema is closed over those targets, the answer drops a target the page does not have, the $0
keyword fallback finds the right place in English and Greek and hands an edit request to Ask Montivo, and the call
lands in <root>/monti/ledger.json, which the hosted budget counts."""
import json

import pytest
from fastapi.testclient import TestClient

import montivo.engine.spark as S
import montivo.server.app as A
from montivo import monti
from montivo.server import hosted as H


def fake_spark(monkeypatch, answer: dict, usage=None, seen=None):
    def create(body):
        if seen is not None:
            seen.append(body)
        return {"id": "resp_monti", "status": "completed",
                "usage": usage or {"input_tokens": 7000, "output_tokens": 300},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}
    monkeypatch.setattr(S, "responses_create", create)


MUSIC = {"reply": "Open Music in the rail (key 6).", "steps": ["Press ▶ on a track", "Apply"], "target": "rail.music",
         "open_panel": "music", "handoff_to_ask": "", "suggestions": ["How loud is the music?"], "section": "Music"}


@pytest.fixture
def env(monkeypatch):
    for k in ("MONTIVO_PASSWORD", "MONTIVO_BUDGET_USD", "MONTIVO_ONE_JOB", "MONTIVO_JUDGE_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "test")
    return monkeypatch


def test_every_guide_section_has_a_real_target_and_an_answer():
    secs = monti.sections()
    assert len(secs) >= 40
    for s in secs:
        assert s.target == "none" or s.target in monti.TARGETS, s.title
        assert s.panel in ("", *monti.PANELS), s.title
        assert s.lead and s.words, s.title
    titles = {s.title for s in secs}
    for must in ("Music", "Captions: fix a word or line", "The pass gate", "Live and Rendered", "Export",
                 "Open weight: Muse Spark Open", "Hosted demo rules", "Ask Montivo (the chat that edits)"):
        assert must in titles


def test_the_schema_is_closed_over_the_targets_and_panels():
    props = monti.SCHEMA["properties"]
    assert set(props["target"]["enum"]) == {*monti.TARGETS, "none"}
    assert set(props["open_panel"]["enum"]) == {*monti.PANELS, "export", "none"}
    assert set(monti.SCHEMA["required"]) == set(props)


def test_the_web_build_carries_every_target():
    """Show me points at [data-monti=<id>]: every target id must be on an element in web/src."""
    from pathlib import Path
    src = "".join(p.read_text() for p in Path(__file__).parents[1].joinpath("web", "src").glob("*.tsx"))
    missing = [t for t in monti.TARGETS if not t.startswith("rail.") and f'data-monti="{t}"' not in src
               and f'monti="{t}"' not in src]
    assert not missing, missing
    assert "data-monti={`rail.${x.id}`}" in src  # the rail buttons, one per panel in Editor.tsx PANELS
    for p in monti.PANELS:
        assert f'{{ id: "{p}", label:' in src, p


@pytest.mark.parametrize("q,section,target", [
    ("where do I add music?", "Music", "rail.music"),
    ("πού βάζω μουσική;", "Music", "rail.music"),
    ("how do I fix a caption?", "Captions: fix a word or line", "rail.captions"),
    ("πώς διορθώνω έναν υπότιτλο;", "Captions: fix a word or line", "rail.captions"),
    ("what does the gate mean?", "The pass gate", "insp"),
    ("how do I export 4:5?", "Feed 4:5 and Square 1:1", "export.dest"),
    ("how do I export a 4:5 version?", "Feed 4:5 and Square 1:1", "export.dest"),
    ("where is undo", "Versions, undo and redo", "top.undo"),
    ("how do I split a clip", "Split a clip", "tl.tools"),
    ("which model plans the cut?", "Models per stage", "top.more"),
])
def test_the_fallback_finds_the_section(q, section, target):
    a = monti.fallback(q, "editor")
    assert a["section"] == section and a["target"] == target and not a["handoff"]
    assert a["reply"] and a["suggestions"]


@pytest.mark.parametrize("q", ["add lofi and bigger captions", "βάλε μουσική και κάνε τα captions πιο μεγάλα",
                               "make it 30 seconds", "κόψε την εισαγωγή"])
def test_an_edit_request_is_handed_to_ask_montivo(q):
    a = monti.fallback(q, "editor")
    assert a["handoff"] == q and a["target"] == "chat.composer" and a["open_panel"] == "assistant"
    assert a["needs"] == "assistant"


def test_a_how_question_is_not_a_hand_off():
    assert not monti.fallback("how do I add music?")["handoff"]
    assert not monti.is_edit("πώς βάζω μουσική;")


def test_the_answer_drops_a_place_the_page_does_not_have():
    a = monti.answer("Open a project first.", target="rail.music", open_panel="music", page="home")
    assert a["target"] is None and a["open_panel"] is None
    a = monti.answer("New project, top right.", target="home.new", page="home")
    assert a["target"] == "home.new" and a["target_key"] == "N"
    a = monti.answer("x", target="home.new", page="editor")
    assert a["target"] is None
    a = monti.answer("Destination.", target="export.dest", page="export")  # the Export page lives over the editor
    assert a["target"] == "export.dest" and a["open_panel"] == "export"
    assert monti.answer("x", target="nope", open_panel="nope")["target"] is None


def test_the_route_calls_spark_once_and_logs_the_cost(tmp_path, env):
    seen = []
    fake_spark(env, MUSIC, seen=seen)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/monti", json={"message": "where is music?", "context": {"page": "editor", "panel": "captions",
                                                                            "version": 5, "live": True}})
    assert r.status_code == 200
    a = r.json()
    assert a["source"] == "spark" and a["target"] == "rail.music" and a["open_panel"] == "music"
    assert a["cost_usd"] == pytest.approx((7000 * 1.25 + 300 * 4.25) / 1e6, abs=1e-4)
    body = seen[0]
    assert body["model"] == "muse-spark-1.3" and body["reasoning"] == {"effort": "low"}
    assert body["text"]["format"]["strict"] is True
    prompt = body["input"][0]["content"][0]["text"]
    assert prompt.index("## Music") < prompt.index("where is music?")  # the guide is the cached prefix
    assert "open panel: captions" in prompt and "viewer: Live" in prompt
    led = json.loads((tmp_path / "monti" / "ledger.json").read_text())
    assert led["entries"][0]["stage"] == "spark.monti" and led["total_cost_usd"] > 0
    q = [json.loads(x) for x in (tmp_path / "monti" / "questions.jsonl").read_text().splitlines()]
    assert q[0]["q"] == "where is music?" and q[0]["target"] == "rail.music"


def test_the_route_without_a_key_answers_from_the_guide(tmp_path, env):
    env.delenv("MODEL_API_KEY")
    c = TestClient(A.create_app(tmp_path))
    a = c.post("/api/monti", json={"message": "πού βάζω μουσική;"}).json()
    assert a["source"] == "guide" and a["cost_usd"] == 0 and a["target"] == "rail.music" and "KEY" in a["why"]
    assert not (tmp_path / "monti" / "ledger.json").exists()
    assert c.post("/api/monti", json={"message": " "}).status_code == 400


def test_a_failed_call_still_answers(tmp_path, env):
    def boom(body):
        raise RuntimeError("HTTP 500")
    env.setattr(S, "responses_create", boom)
    a = TestClient(A.create_app(tmp_path)).post("/api/monti", json={"message": "how do I split a clip"}).json()
    assert a["source"] == "guide" and a["target"] == "tl.tools" and "failed" in a["why"]


def test_a_model_edit_answer_points_at_the_composer(tmp_path, env):
    fake_spark(env, {**MUSIC, "reply": "That's an edit.", "steps": [], "target": "none", "open_panel": "none",
                     "handoff_to_ask": "add lofi"})
    a = TestClient(A.create_app(tmp_path)).post("/api/monti", json={"message": "add lofi"}).json()
    assert a["handoff"] == "add lofi" and a["target"] == "chat.composer" and a["open_panel"] == "assistant"


def test_the_hosted_budget_counts_monti_and_stops_the_model_when_spent(tmp_path, env):
    (tmp_path / "runs").mkdir()
    env.setenv("MONTIVO_BUDGET_USD", "1")
    fake_spark(env, MUSIC, usage={"input_tokens": 100000, "output_tokens": 20000})  # $0.21 a question
    c = TestClient(A.create_app(tmp_path))
    b = H.Budget(tmp_path, 1.0)
    assert b.spent() == 0
    for _ in range(4):
        assert c.post("/api/monti", json={"message": "where is music?"}).json()["source"] == "spark"
    assert H.Budget(tmp_path, 1.0).spent() == pytest.approx(4 * 0.21, abs=0.01)
    a = c.post("/api/monti", json={"message": "where is music?"}).json()  # $0.16 left < one more: still under it
    assert a["source"] == "spark"
    a = c.post("/api/monti", json={"message": "where is music?"}).json()
    assert a["source"] == "guide" and "budget" in a["why"] and a["budget"]["left"] == 0


def test_the_hosted_hourly_cap_per_judge(tmp_path, env):
    (tmp_path / "runs").mkdir()
    env.setenv("MONTIVO_BUDGET_USD", "15")
    env.setattr(A, "MONTI_PER_HOUR", 2)
    fake_spark(env, MUSIC)
    c = TestClient(A.create_app(tmp_path))
    got = [c.post("/api/monti", json={"message": "music?"}).json()["source"] for _ in range(3)]
    assert got == ["spark", "spark", "guide"]


def test_no_em_dashes_in_an_answer():
    a = monti.answer("Open a project first — music lives in Music — key 6.", steps=["**Pick** a track"])
    assert a["reply"] == "Open a project first: music lives in Music, key 6." and a["steps"] == ["Pick a track"]
