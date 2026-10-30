"""C1 + A4 (30/10): the planner writes the brief as a checklist (opening, ending, keep, drop, length, other); the
kept words are checked against it at $0 after the plan (one repair call when something fails) and at every review
(a fact for the reviewer, so brief_covered is judged on the measured list)."""
from montivo.engine import edl as E
from montivo.engine.review import facts_text

SPANS = [{"id": "s1", "words": "Hi. I'm Billy. I run a small digital marketing agency in Athens"},
         {"id": "s2", "words": "meta meta models do all the work News voice hears every word."},
         {"id": "s3", "words": "Soundtracks me so when I move like right now the vertical crop follows me"},
         {"id": "s4", "words": "raw footage in a reviewed cut out"}]
CHECKS = [
    {"item": "Open on 'Hi. I'm Billy.'", "kind": "opening", "quote": "Hi. I'm Billy.", "min_s": -1, "max_s": -1},
    {"item": "End on 'Raw footage in, a reviewed cut out'", "kind": "ending", "quote": "raw footage in a reviewed cut out",
     "min_s": -1, "max_s": -1},
    {"item": "Keep the four Meta models line", "kind": "keep", "quote": "four meta models do all the work",
     "min_s": -1, "max_s": -1},
    {"item": "Drop the false start", "kind": "drop", "quote": "no wait sorry let me say that again", "min_s": -1, "max_s": -1},
    {"item": "35 to 45 s", "kind": "length", "quote": "", "min_s": 35, "max_s": 45},
    {"item": "Make it fun", "kind": "other", "quote": "", "min_s": -1, "max_s": -1},
]


def test_check_brief_measures_each_item_on_the_kept_words():
    edl = {"spans": SPANS, "brief_checks": CHECKS, "runtime_s": 38.0}
    st = {c["kind"]: c["status"] for c in E.check_brief(edl)}
    # "meta meta models" (Voice's "Four Meta") still matches the keep quote; nothing of the false start is kept
    assert st == {"opening": "met", "ending": "met", "keep": "met", "drop": "met", "length": "met", "other": "open"}
    late = {"spans": [SPANS[1], SPANS[0], SPANS[3]], "brief_checks": CHECKS, "runtime_s": 50.0}
    st = {c["kind"]: c["status"] for c in E.check_brief(late)}
    assert st["opening"] == "missed" and st["length"] == "missed" and st["ending"] == "met"


def test_plan_check_names_what_to_fix():
    ok = {"spans": SPANS, "brief_checks": CHECKS, "runtime_s": 38.0, "unplaced": []}
    assert E.plan_check(ok, 40.0) == []
    bad = {"spans": [SPANS[1], SPANS[0], SPANS[3]], "brief_checks": CHECKS, "runtime_s": 50.0, "unplaced": []}
    problems = E.plan_check(bad, 40.0)
    assert any(p.startswith("opening: the cut opens on") for p in problems)
    assert any(p.startswith("length: the cut runs 50.0 s") for p in problems)
    # no length in the brief: the target band decides (28 s on a 40 s target is short, as 017's test plan was)
    assert E.plan_check({"spans": SPANS, "brief_checks": [], "runtime_s": 28.0, "unplaced": []}, 40.0) == [
        "length: the cut runs 28.0 s, the target is 40 to 48 s"]


def test_a_repeated_take_is_found_but_not_a_voice_double():
    edl = {"spans": [{"id": "s1", "words": "we had enough time to film it"},
                     {"id": "s2", "words": "so we had enough time to film it again"}]}
    assert E.repeated_takes(edl) == ['"we had enough time" is kept twice (s1 and s2)']
    assert E.repeated_takes({"spans": [{"id": "s1", "words": "agent agent agent agent the editor"}]}) == []


def test_the_reviewer_gets_the_checklist():
    edl = {"spans": [SPANS[1], SPANS[0], SPANS[3]], "brief_checks": CHECKS, "runtime_s": 38.0}
    caps = {"lines": 0, "over_face": [], "min_clearance_px": None}
    text = facts_text([], caps, 38.0, brief_checks=E.check_brief(edl, 38.0))
    assert "met: " in text and "missed: \"Open on 'Hi. I'm Billy.'\"" in text and "yours to judge: \"Make it fun\"" in text
