"""C3: the planner names the words Voice probably misheard (likely_mishearings, same plan call, $0 extra); the EDL keeps
them as glossary_suggestions, never applied: the captions stay verbatim until a person accepts one (the glossary edit),
and an accepted one is no longer offered."""
import json
from types import SimpleNamespace

from montivo import agent
from montivo.engine import captions, schemas
from montivo.engine.edl import build_edl, mishearings
from montivo.engine.spark import Spark
from tests.test_edl import _plan, _role_voice


def test_the_plan_schema_asks_for_them_strictly():
    assert "likely_mishearings" in schemas.PLAN["required"]
    item = schemas.PLAN["properties"]["likely_mishearings"]["items"]
    assert item["required"] == ["heard", "written", "why"] and item["additionalProperties"] is False


def test_the_plan_prompt_asks_for_evidence_only(tmp_path, monkeypatch):
    sent = {}
    sp = Spark(tmp_path, SimpleNamespace(add=lambda e: None), log=lambda m: None)
    monkeypatch.setattr(sp, "call", lambda name, prompt, schema, **kw: sent.update(prompt=prompt, schema=schema))
    sp.plan("brief", "TRANSCRIPT", {}, 40.0)
    p = sent["prompt"]
    assert "likely_mishearings" in p and "Never an ordinary word" in p and "the captions stay as heard" in p
    assert sent["schema"] is schemas.PLAN


def kept(*words):
    return [SimpleNamespace(id=f"s{k + 1}", words=w) for k, w in enumerate(words)]


def test_mishearings_keeps_only_runs_of_kept_words():
    # run 015 / 016: the planner's own reading of Voice's transcript
    spans = kept("meta meta models do all the work. News voice hears every word.",
                 "Soundtracks me, so when I move like right now the vertical crop follows me.")
    items = [{"heard": "Soundtracks me", "written": "SAM tracks me", "why": "the brief: SAM tracks me"},
             {"heard": "news voice", "written": "Muse Voice.", "why": "the model is Muse Voice"},
             {"heard": "meta meta", "written": "four Meta", "why": "brief: four Meta models"},
             {"heard": "soundtracks me", "written": "SAM tracks me", "why": "repeat"},  # the same heard words twice
             {"heard": "Newspark", "written": "Muse Spark", "why": "not in any kept line"},
             {"heard": "every word", "written": "Every word", "why": "only the case changes"},
             {"heard": "", "written": "x", "why": ""}]
    out = mishearings(items, spans)
    assert out == [{"heard": "Soundtracks me", "written": "SAM tracks me", "why": "the brief: SAM tracks me", "span": "s2"},
                   {"heard": "News voice", "written": "Muse Voice", "why": "the model is Muse Voice", "span": "s1"},
                   {"heard": "meta meta", "written": "four Meta", "why": "brief: four Meta models", "span": "s1"}]
    many = [{"heard": f"w{k}", "written": f"W{k}x", "why": ""} for k in range(10)]
    assert len(mishearings(many, kept(" ".join(f"w{k}" for k in range(10))))) == 6


def test_build_edl_stores_them_and_leaves_the_glossary_alone():
    plan = dict(_plan("hook", "body", "body", "payoff"),
                likely_mishearings=[{"heard": "w11 w12", "written": "Muse Spark", "why": "on-screen text"}])
    edl = build_edl(plan, _role_voice(), {}, {"fps": 30.0}, "b", {})
    assert edl["glossary_suggestions"] == [{"heard": "w11 w12", "written": "Muse Spark", "why": "on-screen text", "span": "s3"}]
    assert edl["glossary"] == {}
    assert build_edl(_plan("hook", "payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {})["glossary_suggestions"] == []


def test_an_accepted_suggestion_is_no_longer_offered():
    edl = {"glossary": {}, "glossary_suggestions": [{"heard": "Soundtracks me", "written": "SAM tracks me", "why": "", "span": "s2"},
                                                    {"heard": "meta meta", "written": "four Meta", "why": "", "span": "s1"}]}
    assert [s["heard"] for s in captions.glossary_suggestions(edl)] == ["Soundtracks me", "meta meta"]
    edl["glossary"] = {"soundtracks me": "SAM tracks me"}  # accepted (the glossary edit, normalised match)
    assert [s["heard"] for s in captions.glossary_suggestions(edl)] == ["meta meta"]
    assert captions.glossary_suggestions({"glossary": {}}) == []


def test_the_chat_router_knows_them(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({"brief": "b"}))
    edl = {"spans": [{"id": "s1", "speaker": "A", "words": "Soundtracks me", "role": "hook"}],
           "glossary_suggestions": [{"heard": "Soundtracks me", "written": "SAM tracks me", "why": "", "span": "s1"}]}
    ctx = agent.context(tmp_path, edl)
    assert "Likely Voice mishearings" in ctx and '"Soundtracks me" probably "SAM tracks me"' in ctx


def test_the_version_entry_offers_the_open_ones(tmp_path):
    from montivo.server import runs
    edl = {"spans": [], "glossary": {"meta meta": "four Meta"},
           "glossary_suggestions": [{"heard": "meta meta", "written": "four Meta", "why": "", "span": "s1"},
                                    {"heard": "news voice", "written": "Muse Voice", "why": "", "span": "s1"}]}
    e = runs.version_entry(tmp_path, 1, "edl_v1.json", edl, None, "muse-spark-1.3")
    assert [s["heard"] for s in e["glossary_suggestions"]] == ["news voice"]
