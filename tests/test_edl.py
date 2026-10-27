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
