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


def test_a_fuzzy_match_without_most_of_the_quote_is_unplaced():
    # audit 31/10 (run 013): difflib over the whole turn had no floor and no window, a slightly-off quote became an
    # 88-word span at ratio 0.108; under LOCATE_SHARE of the quote's words it is unplaced, the window stays near the
    # quote's length and a stray common word further on does not stretch it
    tw = words("there was a time when I was at University of Michigan studying arsenic in aquifers and and so "
               "the work went on for years and then we moved to Washington where the rest of it happened and it")
    a, b, exact, ratio = locate("there was a time when I was at and that's it", tw)
    assert (a, exact) == (0, False) and b - a + 1 <= 11 and ratio > 0.6
    assert locate("we moved our whole office to a new building last spring", tw) is None
    tw = words("so I always say to people go out and try it the result is never what you expect")
    assert locate("go out and really try it the result is never what you'd expect", tw)[:2] == (6, 17)


def test_a_retake_resolves_to_the_last_take():
    # audit 31/10: the first exact hit was the flubbed take
    tw = words("and this is the part that matters uh no wait let me say that again this is the part that matters")
    assert locate("this is the part that matters", tw) == (15, 20, True, 1.0)
    # after the plan's previous span in the turn: the take that follows it; never a take another span holds
    assert locate("this is the part that matters", tw, after=-1, taken=[(15, 20)]) == (1, 6, True, 1.0)
    assert locate("let me say that again", tw, after=6) == (10, 14, True, 1.0)


def test_build_edl_places_a_retake_after_the_previous_span():
    tw = words("hi I'm Billy this is the part that matters uh no wait let me say that again this is the part "
               "that matters thanks")
    v = VoiceResult(turns=[Turn(turnId=1, startMs=10000, endMs=17000, speaker="B", transcript="")], words=tw,
                    audio_s=20.0, chunks=[])
    plan = {"paper_edit": [{"turn_id": 1, "words": "hi I'm Billy", "role": "hook"},
                           {"turn_id": 1, "words": "this is the part that matters", "role": "body"},
                           {"turn_id": 1, "words": "matters thanks", "role": "payoff"},
                           {"turn_id": 1, "words": "a line nobody said in this turn at all", "role": "cta"}]}
    edl = build_edl(plan, v, {}, {"fps": 30.0}, "b", {})
    assert [(s["word_from"], s["word_to"]) for s in edl["spans"]] == [(0, 2), (17, 22), (22, 23)]
    assert edl["unplaced"][0]["reason"] == "quote not found in the turn"


def test_locate_stays_fast_on_a_long_turn():
    # audit 31/10: artefact_doubles ran once per candidate word, 3 s a span on a 1500-word turn
    import random
    import time
    rnd = random.Random(0)
    vocab = "the a and we it is to of in that this for with you on".split() + [f"w{k}" for k in range(300)]
    tw = [Word(w=rnd.choice(vocab), t_start=k * .4, t_end=(k + 1) * .4, turn_id=0, speaker="A", i=k)
          for k in range(1500)]
    t = time.perf_counter()
    for k in range(10):
        q = [w.w for w in tw[700 + k:720 + k]]
        q[5] = "zzz"
        assert locate(" ".join(q), tw)[0] == 700 + k
    assert time.perf_counter() - t < 1.0


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


def test_the_cuts_a_length_fit_leaves_are_tightened_too():
    """Audit 31/10: three joined spans of one turn, the middle one dropped for length: the new cut was skipped by the
    edge pass (a join then) and stayed on Voice's boundary, in the pause before the speech."""
    from tests.test_edges import SynthAudio
    ws = [Word(w=f"w0{k}", t_start=10 + 0.5 * k, t_end=10.5 + 0.5 * k, turn_id=0, speaker="A", i=k) for k in range(8)]
    voice = VoiceResult(turns=[Turn(turnId=0, startMs=10000, endMs=14000, speaker="A", transcript="")], words=ws,
                        audio_s=40.0, chunks=[])
    plan = {"paper_edit": [{"turn_id": 0, "words": "w00 w01", "role": "hook", "strength": 5},
                           {"turn_id": 0, "words": "w02 w03", "role": "body", "strength": 1},
                           {"turn_id": 0, "words": "w04 w05", "role": "payoff", "strength": 5}]}
    audio = SynthAudio([(10.0, 10.9), (11.0, 11.8), (12.3, 12.95)])  # w04's speech 0.3 s after Voice's boundary
    edl = build_edl(plan, voice, {}, {"fps": 30.0}, "b", {}, audio=audio, target_s=1.9)
    assert [s["id"] for s in edl["spans"]] == ["s1", "s3"]
    s3 = edl["spans"][1]
    assert 12.1 < s3["t_in"] < 12.3  # was 12.0, in the pause
    assert ("s3", "in") in {(m["span"], m["edge"]) for m in edl["edge_pass"]["moves"]}
    assert edl["edge_pass"]["moved"] == len(edl["edge_pass"]["moves"])
    assert edl["runtime_s"] == round(sum(s["t_out"] - s["t_in"] for s in edl["spans"]), 2)
