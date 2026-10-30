"""Strict JSON schemas for the three Spark calls. Every property is required and additionalProperties is false,
which is what `text.format: json_schema, strict: true` demands."""
from __future__ import annotations

SHOT_TYPES = ["title_card", "talking_head", "b_roll", "screen", "wide", "closeup", "black", "other"]

WATCH = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "description": "two sentences: what this footage is and who is in it"},
        "visual_shots": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "start_s": {"type": "number"}, "end_s": {"type": "number"},
            "shot_type": {"type": "string", "enum": SHOT_TYPES},
            "visual": {"type": "string"}, "on_screen_text": {"type": "string"},
            "people_visible": {"type": "integer", "minimum": 0},
            "trackable_objects": {"type": "array", "items": {"type": "string"},
                                  "description": "short noun phrases SAM could track, e.g. 'person', 'mission patch'"}},
            "required": ["start_s", "end_s", "shot_type", "visual", "on_screen_text", "people_visible", "trackable_objects"]}},
        "speaker_map": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "speaker": {"type": "string"}, "who": {"type": "string"}, "on_camera": {"type": "boolean"},
            "position": {"type": "string", "enum": ["left", "centre", "right", "off_camera", "varies"]}},
            "required": ["speaker", "who", "on_camera", "position"]}},
        "trackable_concepts": {"type": "array", "items": {"type": "string"},
                               "description": "deduplicated list of concepts worth tracking for callouts, best first, 'person' excluded"},
        "b_roll_candidates": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "start_s": {"type": "number"}, "end_s": {"type": "number"}, "why": {"type": "string"}},
            "required": ["start_s", "end_s", "why"]}},
        "movement_moments": {"type": "array",
                             "description": "moments where the on-camera speaker moves out of centre or handles a prop, with seconds",
                             "items": {"type": "object", "additionalProperties": False, "properties": {
                                 "start_s": {"type": "number"}, "end_s": {"type": "number"},
                                 "what": {"type": "string", "description": "e.g. 'leans left and lifts the laptop'"}},
                                 "required": ["start_s", "end_s", "what"]}},
    },
    "required": ["summary", "visual_shots", "speaker_map", "trackable_concepts", "b_roll_candidates", "movement_moments"],
}

PLAN = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "paper_edit": {"type": "array",
                       "description": "ordered spans to keep; each span is inside one turn and quotes the exact words kept",
                       "items": {"type": "object", "additionalProperties": False, "properties": {
                           "turn_id": {"type": "integer"},
                           "words": {"type": "string", "description": "verbatim contiguous words copied from that turn's transcript, including disfluencies"},
                           "role": {"type": "string", "enum": ["hook", "body", "payoff", "cta"],
                                    "description": "hook = first span only, payoff = last span only (the ending), cta = a closing call to action after it, body = every other span"},
                           "why": {"type": "string"},
                           "callout": {"type": "string", "description": "optional 2 to 4 word on-screen callout for this span, empty if none"}},
                           "required": ["turn_id", "words", "role", "why", "callout"]}},
        "estimated_runtime_s": {"type": "number"},
        "title_card_text": {"type": "string"},
        "caption_style_note": {"type": "string"},
        "reframe_note": {"type": "string", "description": "who the camera should follow and when, in one sentence"},
        "brief_not_in_footage": {"type": "array", "items": {"type": "string"},
                                 "description": "each thing the brief asks to keep or show that the transcript and footage do not "
                                                "contain, in the brief's own words; empty when everything is there"},
        "brief_checks": {"type": "array",
                         "description": "the brief as a checklist, one concrete requirement per item (C1)",
                         "items": {"type": "object", "additionalProperties": False, "properties": {
                             "item": {"type": "string", "description": "the requirement in the brief's own words"},
                             "kind": {"type": "string", "enum": ["opening", "ending", "keep", "drop", "length", "other"]},
                             "quote": {"type": "string", "description": "the transcript's exact words the requirement "
                                       "names (opening, ending, keep, drop), empty when it is not in the transcript or "
                                       "for length / other"},
                             "min_s": {"type": "number", "description": "length: the shortest runtime allowed, else -1"},
                             "max_s": {"type": "number", "description": "length: the longest runtime allowed, else -1"}},
                             "required": ["item", "kind", "quote", "min_s", "max_s"]}},
    },
    "required": ["paper_edit", "estimated_runtime_s", "title_card_text", "caption_style_note", "reframe_note",
                 "brief_not_in_footage", "brief_checks"],
}

CRITERIA = ["hook_in_1_5s", "brief_covered", "no_cut_mid_word", "captions_legible", "captions_match_speech",
            "subject_framed", "callouts_anchored", "pacing", "ending"]
FIX_OPS = ["move_cut", "drop_span", "extend_span", "reframe_bias", "caption_fix", "retime_callout", "swap_hook", "reorder", "none"]

# The reviewer is blind: it never sees the EDL, so an issue names a time in the cut, not a span. review.py maps
# t_s back to the span, cut point, caption line or callout and turns fix_op + params into an EDL patch.
EDGES = ["in", "out", "none"]
DIRECTIONS = ["left", "right", "none"]

REVIEW = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "pass": {"type": "boolean"},
        "overall": {"type": "integer", "minimum": 0, "maximum": 100},
        "scores": {"type": "object", "additionalProperties": False,
                   "properties": {c: {"type": "integer", "minimum": 0, "maximum": 10} for c in CRITERIA},
                   "required": CRITERIA},
        "issues": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "t_s": {"type": "number", "description": "cut seconds where the issue starts, read from the burned-in clock"},
            "t_end_s": {"type": "number", "description": "cut seconds where it ends (same as t_s for a single moment)"},
            "criterion": {"type": "string", "enum": CRITERIA},
            "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
            "what": {"type": "string"},
            "fix_op": {"type": "string", "enum": FIX_OPS},
            "params": {"type": "object", "additionalProperties": False, "properties": {
                "edge": {"type": "string", "enum": EDGES,
                         "description": "move_cut / extend_span: the start (in) or the end (out) of the passage at t_s"},
                "seconds": {"type": "number",
                            "description": "move_cut: + moves the cut later, - earlier; extend_span: seconds to add; "
                                           "retime_callout: + later, - earlier; else 0"},
                "target_t_s": {"type": "number",
                               "description": "swap_hook: cut seconds of the line that should open the video; reorder: "
                                              "cut seconds of the line the passage at t_s should come before; else -1"},
                "text": {"type": "string", "description": "caption_fix: the words actually spoken; retime_callout: new "
                                                         "callout text; else empty"},
                "direction": {"type": "string", "enum": DIRECTIONS,
                              "description": "reframe_bias: which way the camera should move; else none"}},
                "required": ["edge", "seconds", "target_t_s", "text", "direction"]},
            "fix_args": {"type": "string", "description": "the fix in words, one sentence"}},
            "required": ["t_s", "t_end_s", "criterion", "severity", "what", "fix_op", "params", "fix_args"]}},
        "summary": {"type": "string"},
    },
    "required": ["pass", "overall", "scores", "issues", "summary"],
}

# The pairwise judge (C2): one call sees two cuts, Cut A first and Cut B second, and names the better one per criterion
# and overall. review.pairwise asks twice with the two swapped.
SIDES = ["A", "B", "tie"]

COMPARE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "winner": {"type": "string", "enum": SIDES, "description": "the cut you would publish"},
        "why": {"type": "string", "description": "the differences that decided it, with cut times"},
        "criteria": {"type": "object", "additionalProperties": False,
                     "properties": {c: {"type": "string", "enum": SIDES} for c in CRITERIA}, "required": CRITERIA},
    },
    "required": ["winner", "why", "criteria"],
}
