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
    },
    "required": ["summary", "visual_shots", "speaker_map", "trackable_concepts", "b_roll_candidates"],
}

PLAN = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "paper_edit": {"type": "array",
                       "description": "ordered spans to keep; each span is inside one turn and quotes the exact words kept",
                       "items": {"type": "object", "additionalProperties": False, "properties": {
                           "turn_id": {"type": "integer"},
                           "words": {"type": "string", "description": "verbatim contiguous words copied from that turn's transcript, including disfluencies"},
                           "role": {"type": "string", "enum": ["hook", "body", "payoff", "cta"]},
                           "why": {"type": "string"},
                           "callout": {"type": "string", "description": "optional 2 to 4 word on-screen callout for this span, empty if none"}},
                           "required": ["turn_id", "words", "role", "why", "callout"]}},
        "estimated_runtime_s": {"type": "number"},
        "title_card_text": {"type": "string"},
        "caption_style_note": {"type": "string"},
        "reframe_note": {"type": "string", "description": "who the camera should follow and when, in one sentence"},
    },
    "required": ["paper_edit", "estimated_runtime_s", "title_card_text", "caption_style_note", "reframe_note"],
}

CRITERIA = ["hook_in_1_5s", "brief_covered", "no_cut_mid_word", "captions_legible", "captions_match_speech",
            "subject_framed", "callouts_anchored", "pacing", "ending"]
FIX_OPS = ["move_cut", "drop_span", "extend_span", "reframe_bias", "caption_fix", "retime_callout", "swap_hook", "reorder", "none"]

REVIEW = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "pass": {"type": "boolean"},
        "overall": {"type": "integer", "minimum": 0, "maximum": 100},
        "scores": {"type": "object", "additionalProperties": False,
                   "properties": {c: {"type": "integer", "minimum": 0, "maximum": 10} for c in CRITERIA},
                   "required": CRITERIA},
        "issues": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "t_s": {"type": "number"}, "criterion": {"type": "string", "enum": CRITERIA},
            "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
            "what": {"type": "string"},
            "fix_op": {"type": "string", "enum": FIX_OPS},
            "span_id": {"type": "string", "description": "the EDL span id the fix applies to, or empty"},
            "fix_args": {"type": "string"}},
            "required": ["t_s", "criterion", "severity", "what", "fix_op", "span_id", "fix_args"]}},
        "summary": {"type": "string"},
    },
    "required": ["pass", "overall", "scores", "issues", "summary"],
}
