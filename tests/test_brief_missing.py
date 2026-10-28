"""A3: what the brief asks for and the footage does not have is named once by the planner, kept in the EDL and handed
to the reviewer as a fact about the source (run 015's brief asks for a second take that was never said)."""
from montivo.engine import review as R
from montivo.engine import schemas
from montivo.engine.edl import build_edl

from tests.test_edl import _plan, _role_voice


def test_the_plan_schema_asks_for_it_strictly():
    assert "brief_not_in_footage" in schemas.PLAN["required"]
    assert schemas.PLAN["properties"]["brief_not_in_footage"]["items"] == {"type": "string"}


def test_build_edl_keeps_the_planners_list_clean():
    plan = dict(_plan("hook", "body", "body", "payoff"), brief_not_in_footage=[" 'this is the part that matters' (second take) ", ""])
    edl = build_edl(plan, _role_voice(), {}, {"fps": 30.0}, "b", {})
    assert edl["brief_missing"] == ["'this is the part that matters' (second take)"]
    assert build_edl(_plan("hook", "payoff"), _role_voice(), {}, {"fps": 30.0}, "b", {})["brief_missing"] == []


def test_the_reviewer_hears_it_as_a_fact_about_the_source():
    caps = {"lines": 0, "over_face": [], "min_clearance_px": None}
    facts = R.facts_text([], caps, 30.0, not_in_source=["this is the part that matters"])
    last = facts.splitlines()[-1]
    assert last.startswith("3. The source footage was searched") and "\"this is the part that matters\"" in last
    assert "brief_covered on the rest" in last
    assert "searched" not in R.facts_text([], caps, 30.0, not_in_source=[])


def test_a_line_voice_misheard_but_the_plan_kept_is_not_missing():
    # run 015, plan v6 on Spark 1.3: it kept "Soundtracks me so ..." and "meta meta models ..." and still listed them
    from types import SimpleNamespace
    from montivo.engine.edl import brief_missing
    kept = [SimpleNamespace(words=w) for w in ("meta meta models do all the work News voice hears every word.",
                                               "Soundtracks me so when I move like right now the vertical crop follows me")]
    items = ["the four Meta models", "SAM tracks me so the vertical crop follows me when I move, like right now",
             "the second 'this is the part that matters'"]
    assert brief_missing(items, kept) == ["the second 'this is the part that matters'"]
