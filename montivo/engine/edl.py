"""EDL builder: paper-edit spans (turn id + verbatim words) -> in/out points on Voice word boundaries.

A span is verbatim when its normalised tokens appear contiguously in the turn's words. When they do not, the
closest contiguous block is used (difflib) and the span is flagged so the review step can see it.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass

from .edges import Audio, Words, tighten_edges
from .voice import VoiceResult, Word

_PUNCT = re.compile(r"[^\w'%$€]+", re.U)


def norm(tok: str) -> str:
    return _PUNCT.sub("", tok.lower().replace("’", "'"))


def norm_tokens(text: str) -> list[str]:
    return [n for n in (norm(t) for t in text.split()) if n]


@dataclass
class Span:
    id: str
    order: int
    turn_id: int
    speaker: str
    role: str
    words: str
    t_in: float
    t_out: float
    duration: float
    verbatim: bool
    match_ratio: float
    word_from: int
    word_to: int
    callout: str
    why: str
    contiguous_with_previous: bool = False  # same turn, no gap: render as one continuous cut, no boundary here
    planner_role: str = ""  # the planner's own label when normalize_roles changed it


def normalize_roles(spans: list[Span]) -> list[str]:
    """Roles by position, whatever the planner wrote: the first span is the one `hook`, the last span before any
    trailing `cta` run is the `payoff` (the ending), a `cta` stays only in that trailing run, every other span is
    `body`. Muse Spark Open labelled every body span `payoff` on run 012-open, which made the hook A/B take a middle
    line as the punchline. A changed label keeps the planner's in `planner_role`; returns one note per change."""
    if not spans:
        return []
    end = len(spans)
    while end > 1 and spans[end - 1].role == "cta":
        end -= 1
    notes = []
    for i, s in enumerate(spans):
        want = "hook" if i == 0 else "payoff" if i == end - 1 else "cta" if i >= end else "body"
        if s.role != want:
            notes.append(f"{s.id} {s.role} -> {want}")
            s.planner_role, s.role = s.role, want
    return notes


def locate(span_text: str, turn_words: list[Word]) -> tuple[int, int, bool, float] | None:
    """Return (first index, last index, exact, ratio) of the span inside the turn's words."""
    want = norm_tokens(span_text)
    have = [norm(w.w) for w in turn_words]
    if not want or not have:
        return None
    n = len(want)
    for i in range(0, len(have) - n + 1):
        if have[i:i + n] == want:
            return i, i + n - 1, True, 1.0
    sm = difflib.SequenceMatcher(a=have, b=want, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
    if not blocks:
        return None
    first = min(b.a for b in blocks)
    last = max(b.a + b.size - 1 for b in blocks)
    # keep the window the size of the request around the matched blocks
    last = min(len(have) - 1, max(last, first + n - 1))
    return first, last, False, round(sm.ratio(), 3)


def build_edl(plan: dict, voice: VoiceResult, watch: dict, source: dict, brief: str, tracks: dict,
              reframe_concept: str = "head", audio: "Audio | None" = None) -> dict:
    """The EDL v1 on Voice word boundaries; with the source audio, every cut edge is then tightened to the speech
    onset and offset (edges.py), so v1 does not open in the pause before the first word or hold after the last."""
    by_turn: dict[int, list[Word]] = {}
    for w in voice.words:
        by_turn.setdefault(w.turn_id, []).append(w)
    turns = {t.turnId: t for t in voice.turns}

    spans: list[Span] = []
    missing: list[dict] = []
    for k, item in enumerate(plan.get("paper_edit", [])):
        tid = int(item["turn_id"])
        tw = by_turn.get(tid, [])
        loc = locate(item["words"], tw)
        if loc is None:
            missing.append({"order": k, **item, "reason": "turn not found or no overlap"})
            continue
        a, b, exact, ratio = loc
        t_in, t_out = tw[a].t_start, tw[b].t_end
        spans.append(Span(id=f"s{k + 1}", order=k, turn_id=tid, speaker=turns[tid].speaker if tid in turns else "?",
                          role=item["role"], words=" ".join(w.w for w in tw[a:b + 1]), t_in=t_in, t_out=t_out,
                          duration=round(t_out - t_in, 3), verbatim=exact, match_ratio=ratio, word_from=a, word_to=b,
                          callout=item.get("callout", ""), why=item.get("why", "")))

    role_fixes = normalize_roles(spans)
    for prev, cur in zip(spans, spans[1:]):
        if cur.turn_id == prev.turn_id and cur.word_from == prev.word_to + 1:
            cur.contiguous_with_previous = True
    runtime = round(sum(s.duration for s in spans), 2)
    edl = {
        "version": 1,
        "brief": brief,
        "source": source,
        "target": {"aspect": "9:16", "width": 1080, "height": 1920},
        "runtime_s": runtime,
        "spans": [asdict(s) for s in spans],
        "unplaced": missing,
        "role_fixes": role_fixes,
        "verbatim_rate": round(sum(s.verbatim for s in spans) / len(spans), 3) if spans else 0.0,
        "title_card_text": plan.get("title_card_text", ""),
        "caption_style_note": plan.get("caption_style_note", ""),
        "reframe_note": plan.get("reframe_note", ""),
        "speaker_map": watch.get("speaker_map", []),
        "tracks": tracks,
        "reframe_concept": reframe_concept,  # the SAM concept the 9:16 crop follows (falls back to person)
        "captions": [],  # filled by the caption step (evening 3)
        "reframe": [],  # filled by the reframe step (evening 2)
    }
    if audio is not None and edl["spans"]:
        tighten_edges(edl, Words([asdict(w) for w in voice.words]), audio)
    return edl


def edl_summary(edl: dict) -> str:
    """The current cut for a re-plan's prompt, callouts included: without them the planner dropped both of run 015's
    callouts on "make it about 30 seconds" (015-ui v5), an edit nobody asked for."""
    lines = [f"runtime {edl['runtime_s']} s, {len(edl['spans'])} spans"]
    for s in edl["spans"]:
        call = f" callout \"{s['callout']}\"" if s.get("callout") else ""
        lines.append(f"- {s['id']} [{s['role']}] source {s['t_in']:.2f}-{s['t_out']:.2f}s speaker {s['speaker']}: \"{s['words']}\"{call}")
    if any(s.get("callout") for s in edl["spans"]):
        lines.append("Keep these callouts on the spans that still say those words, unless the editor's note says otherwise.")
    return "\n".join(lines)
