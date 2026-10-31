from montivo.engine import schemas
from montivo.engine.edl import build_edl, locate, norm_tokens
from montivo.engine.voice import Turn, VoiceResult, Word


def words(text, t0=10.0):
    return [Word(w=w, t_start=round(t0 + i * 0.3, 3), t_end=round(t0 + (i + 1) * 0.3, 3), turn_id=1, speaker="B", i=i)
            for i, w in enumerate(text.split())]


def test_exact_match_ignores_punctuation_and_case():
    tw = words("You know it's been interesting as we've we've kind of gone through the the pandemic")
    a, b, exact, ratio = locate("it's been interesting, as we've we've kind of", tw)
    assert (a, b, exact, ratio) == (2, 9, True, 1.0)


def test_fuzzy_match_when_model_cleaned_the_quote():
    tw = words("You know it's been interesting as we've we've kind of gone through the the pandemic")
    a, b, exact, ratio = locate("it's been interesting as we've kind of gone through the pandemic", tw)
    assert exact is False and a == 2 and b >= 12 and ratio > 0.8


def test_norm_tokens():
    assert norm_tokens("Hi, Robert. Can you hear me?") == ["hi", "robert", "can", "you", "hear", "me"]


def _role_voice():
    ws = [Word(w=f"w{t}{k}", t_start=t * 20 + 10 + 0.5 * k, t_end=t * 20 + 10.4 + 0.5 * k, turn_id=t, speaker="A", i=k)
          for t in (0, 1) for k in range(8)]
    return VoiceResult(turns=[Turn(turnId=t, startMs=t * 20000 + 10000, endMs=t * 20000 + 14000, speaker="A", transcript="")
                              for t in (0, 1)], words=ws, audio_s=40.0, chunks=[])


def _plan(*roles):
    spans = [("0", "w00 w01"), ("0", "w03 w04"), ("1", "w11 w12"), ("1", "w15 w16")]
    return {"paper_edit": [{"turn_id": int(t), "words": w, "role": r} for (t, w), r in zip(spans, roles)]}


def test_roles_follow_the_position_whatever_the_planner_wrote():
    # Spark 1.3 on run 012: hook, body..., payoff stays exactly as it was
    edl = build_edl(_plan("hook", "body", "body", "payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {})
    assert [s["role"] for s in edl["spans"]] == ["hook", "body", "body", "payoff"] and edl["role_fixes"] == []
    assert all(s["planner_role"] == "" for s in edl["spans"])
    # Muse Spark Open on run 012-open: every body span labelled payoff
    edl = build_edl(_plan("hook", "payoff", "payoff", "payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {})
    assert [s["role"] for s in edl["spans"]] == ["hook", "body", "body", "payoff"]
    assert edl["role_fixes"] == ["s2 payoff -> body", "s3 payoff -> body"]
    assert [s["planner_role"] for s in edl["spans"]] == ["", "payoff", "payoff", ""]
    # a second hook becomes body (the planner's label is kept), an unlabelled ending becomes the payoff,
    # a trailing call to action stays and a cta in the middle does not
    edl = build_edl(_plan("body", "hook", "cta", "cta"), _role_voice(), {}, {"fps": 30.0}, "b", {})
    assert [s["role"] for s in edl["spans"]] == ["hook", "payoff", "cta", "cta"]
    edl = build_edl(_plan("hook", "cta", "body", "body"), _role_voice(), {}, {"fps": 30.0}, "b", {})
    assert [s["role"] for s in edl["spans"]] == ["hook", "body", "body", "payoff"]
    edl = build_edl(_plan("payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {})
    assert [s["role"] for s in edl["spans"]] == ["hook"]


def _strong(plan, *strengths):
    for item, s in zip(plan["paper_edit"], strengths):
        item["strength"] = s
    return plan


def test_a_plan_over_the_target_loses_its_weakest_body_span():
    # heli-04: 46.6 s on a 40 s target (brief: 45 s at most); every span here is 0.9 s, 3.6 s in all
    edl = build_edl(_strong(_plan("hook", "body", "body", "payoff"), 5, 2, 4, 5), _role_voice(), {}, {"fps": 30.0}, "b",
                    {}, target_s=2.6)
    assert [s["id"] for s in edl["spans"]] == ["s1", "s3", "s4"] and edl["runtime_s"] == 2.7
    assert edl["length_fit"]["dropped"] == [{"id": "s2", "words": "w03 w04", "duration": 0.9, "strength": 2}]
    assert [s["order"] for s in edl["spans"]] == [0, 1, 2]
    # never the hook or the payoff, never under 80 % of the target
    edl = build_edl(_plan("hook", "body", "body", "payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {}, target_s=1.0)
    assert [s["role"] for s in edl["spans"]] == ["hook", "payoff"]
    # within 10 % of the target nothing moves; no target, nothing moves (old callers)
    edl = build_edl(_plan("hook", "body", "body", "payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {}, target_s=3.3)
    assert len(edl["spans"]) == 4 and "length_fit" not in edl
    assert len(build_edl(_plan("hook", "body", "body", "payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {})["spans"]) == 4


def test_the_briefs_maximum_is_a_hard_limit():
    plan = _plan("hook", "body", "body", "payoff")
    plan["brief_checks"] = [{"item": "30-45 s", "kind": "length", "quote": "", "min_s": 1.0, "max_s": 2.8}]
    edl = build_edl(plan, _role_voice(), {}, {"fps": 30.0}, "b", {}, target_s=3.4)  # 3.6 s is under 1.1 x 3.4
    assert edl["runtime_s"] == 2.7 and edl["length_fit"]["limit_s"] == 3.3


def test_the_plan_prompt_asks_for_whole_sentences_and_a_hard_maximum(tmp_path, monkeypatch):
    from montivo.engine.spark import Spark
    from montivo.ledger import Ledger
    seen = {}
    monkeypatch.setattr(Spark, "call", lambda self, name, prompt, *a, **k: seen.setdefault("prompt", prompt))
    Spark(tmp_path, Ledger(tmp_path / "ledger.json")).plan("30 to 45 s", "TRANSCRIPT", {}, 40.0)
    p = seen["prompt"]
    assert "starts where a sentence starts" in p and "ends where a sentence ends" in p
    assert "maximum is a hard limit" in p and "strength" in p
    assert "strength" in schemas.PLAN["properties"]["paper_edit"]["items"]["required"]


def test_watch_gets_more_output_room_on_a_clip_with_many_shots(tmp_path, monkeypatch):
    from montivo.engine import spark as S
    seen = []
    monkeypatch.setattr(S.Spark, "call", lambda self, name, prompt, schema, **k: seen.append(k["max_output_tokens"]))
    sp = S.Spark(tmp_path, __import__("montivo.ledger", fromlist=["Ledger"]).Ledger(tmp_path / "l.json"))
    for shots in (0, 11, 26, 200):
        sp.watch("file-1", "T", "b", 90.0, shots=shots)
    assert seen == [12000, 12400, 18400, 24000]  # heli-04: 26 cuts, 12000 came back incomplete, 18000 worked
