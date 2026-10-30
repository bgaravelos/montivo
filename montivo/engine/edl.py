"""EDL builder: paper-edit spans (turn id + verbatim words) -> in/out points on Voice word boundaries.

A span is verbatim when its normalised tokens appear contiguously in the turn's words. When they do not, the
closest contiguous block is used (difflib) and the span is flagged so the review step can see it.
"""
from __future__ import annotations

import copy
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


# words too common to tell one line from another when a missing brief item is matched against the kept words
_FILLER = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "is", "it", "its", "it's", "i",
           "me", "my", "we", "so", "that", "this", "when", "like", "about", "keep", "line", "take", "second", "first"}


def brief_missing(items: list[str], spans: list["Span"]) -> list[str]:
    """The planner's brief_not_in_footage, cleaned. An item whose words are mostly in the kept spans is dropped: the
    planner reads Voice's transcript, so a line Voice misheard ("SAM tracks me" as "Soundtracks me", "Four Meta" as
    "meta meta") looked missing to it on run 015 although it kept that very line. Told to the reviewer as missing,
    such an item would hide a real drop."""
    kept = {t for s in spans for t in norm_tokens(s.words)}
    out = []
    for x in (i.strip() for i in items or []):
        content = [t for t in norm_tokens(x) if t not in _FILLER]
        if x and (not content or sum(t in kept for t in content) / len(content) < 0.5):
            out.append(x)
    return out


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
        "title_style": "header",  # the title over the opening speech, no silent card before the hook (title_style)
        "caption_style_note": plan.get("caption_style_note", ""),
        "reframe_note": plan.get("reframe_note", ""),
        # what the brief asks for and the footage does not have, as the planner found it: said once in the chat and
        # handed to the reviewer as a fact about the source, so it does not come back as a brief_covered major
        "brief_missing": brief_missing(plan.get("brief_not_in_footage") or [], spans),
        # the brief as a checklist (C1): measured against the kept words at every review (check_brief)
        "brief_checks": [{k: c.get(k) for k in ("item", "kind", "quote", "min_s", "max_s")}
                         for c in plan.get("brief_checks") or []],
        "speaker_map": watch.get("speaker_map", []),
        "tracks": tracks,
        "reframe_concept": reframe_concept,  # the SAM concept the 9:16 crop follows (falls back to person)
        "glossary": {},  # this run's caption name fixes {"heard": "written"} and highlighted names (captions.py)
        "highlight": [],
        # caption lines break where a phrase allows (captions.phrase_breaks); an EDL without it keeps the greedy lines
        "look": {"line_breaks": "phrase"},
        "captions": [],  # filled by the caption step (evening 3)
        "reframe": [],  # filled by the reframe step (evening 2)
    }
    if audio is not None and edl["spans"]:
        tighten_edges(edl, Words([asdict(w) for w in voice.words]), audio)
    return edl


# --------------------------------------------------------------------------- the brief as a checklist (C1, A4)

QUOTE_MATCH = 0.8  # a quote counts as kept when a run of the kept words matches it this well (difflib ratio)
DROP_MATCH = 0.6  # a drop item counts as left in when a run of the kept words matches it this well
LENGTH_SLACK_S = 0.5
REPEAT_N = 4  # the same REPEAT_N words kept twice is a repeated take


def _best_window(quote: list[str], kept: list[str]) -> float:
    """How well the best run of `kept` (the quote's length, a word either side) matches `quote` (difflib ratio)."""
    import difflib
    if not quote or not kept:
        return 0.0
    n = len(quote)
    best = 0.0
    for size in (n - 1, n, n + 1):
        if size <= 0:
            continue
        for i in range(0, max(1, len(kept) - size + 1)):
            r = difflib.SequenceMatcher(None, quote, kept[i:i + size], autojunk=False).ratio()
            if r > best:
                best = r
    return best


def check_brief(edl: dict, duration_s: float | None = None) -> list[dict]:
    """The planner's brief_checks measured on this EDL's kept words: each item with status met / missed / open
    ("other" and items without a quote are the reviewer's), and what was found. Opening and ending compare the
    quote with the first / last words of the cut, keep and drop look for it anywhere in the kept words (fuzzy:
    Voice's doubled words and small mishearings still match), length uses `duration_s` (the cut, title card
    included) or the runtime."""
    spans = edl.get("spans") or []
    kept = [t for s in spans for t in norm_tokens(s.get("words") or "")]
    runtime = float(duration_s if duration_s is not None else edl.get("runtime_s") or 0.0)
    out = []
    for c in edl.get("brief_checks") or []:
        kind, quote = c.get("kind"), norm_tokens(c.get("quote") or "")
        status, found = "open", ""
        if kind == "length" and (c.get("min_s", -1) >= 0 or c.get("max_s", -1) >= 0):
            lo, hi = c.get("min_s", -1), c.get("max_s", -1)
            ok = (lo < 0 or runtime >= lo - LENGTH_SLACK_S) and (hi < 0 or runtime <= hi + LENGTH_SLACK_S)
            status, found = ("met" if ok else "missed"), f"{runtime:.1f} s"
        elif kind in ("opening", "ending") and quote and kept:
            part = kept[:len(quote) + 1] if kind == "opening" else kept[-(len(quote) + 1):]
            r = _best_window(quote, part)
            status = "met" if r >= QUOTE_MATCH else "missed"
            found = " ".join(kept[:len(quote)] if kind == "opening" else kept[-len(quote):])
        elif kind == "keep" and quote:
            r = _best_window(quote, kept)
            status, found = ("met" if r >= QUOTE_MATCH else "missed"), f"best match {r:.0%}"
        elif kind == "drop" and quote:
            r = _best_window(quote, kept)
            status, found = ("met" if r < DROP_MATCH else "missed"), f"best match {r:.0%}"
        out.append({**c, "status": status, "found": found})
    return out


def repeated_takes(edl: dict, n: int = REPEAT_N) -> list[str]:
    """The same `n` words kept twice anywhere in the cut (a retake the plan kept both of: d1-tedx "we had enough /
    we've had enough"). One-word doubles ("agent agent") are Voice's and the captions drop them."""
    seen: dict[tuple, str] = {}
    out = []
    for s in edl.get("spans") or []:
        toks = norm_tokens(s.get("words") or "")
        for i in range(len(toks) - n + 1):
            g = tuple(toks[i:i + n])
            if len(set(g)) < 2:
                continue
            if g in seen and seen[g] != f"{s['id']}@{i}":
                out.append(f"\"{' '.join(g)}\" is kept twice ({seen[g].split('@')[0]} and {s['id']})")
                break
            seen.setdefault(g, f"{s['id']}@{i}")
    return list(dict.fromkeys(out))


def plan_check(edl: dict, target_s: float) -> list[str]:
    """A4: what is wrong with a paper edit before anything is rendered: brief items it misses (check_brief), a
    runtime outside the target band when the brief gives no length, a repeated take, planned words not found in
    the transcript. Empty = the plan is kept as it is."""
    out = []
    checks = check_brief(edl)
    for c in checks:
        if c["status"] != "missed":
            continue
        if c["kind"] == "length":
            out.append(f"length: the cut runs {c['found']}, the brief allows {c['min_s']:g} to {c['max_s']:g} s "
                       f"({c['item']})")
        elif c["kind"] in ("opening", "ending"):
            out.append(f"{c['kind']}: the cut {'opens' if c['kind'] == 'opening' else 'ends'} on \"{c['found']}\", the "
                       f"brief wants \"{c['quote']}\" ({c['item']})")
        elif c["kind"] == "keep":
            out.append(f"keep: \"{c['quote']}\" is not in the paper edit ({c['item']})")
        else:
            out.append(f"drop: \"{c['quote']}\" is still in the paper edit ({c['item']})")
    if not any(c["kind"] == "length" and c["status"] != "open" for c in checks) and target_s:
        rt = float(edl.get("runtime_s") or 0.0)
        if rt < 0.8 * target_s or rt > 1.25 * target_s:
            out.append(f"length: the cut runs {rt:.1f} s, the target is {target_s:.0f} to {1.2 * target_s:.0f} s")
    out += [f"repeat: {r}" for r in repeated_takes(edl)]
    out += [f"unplaced: the words \"{m.get('words', '')[:80]}\" are not in turn {m.get('turn_id')} of the transcript"
            for m in edl.get("unplaced") or []]
    return out


def title_on(edl: dict) -> bool:
    """The run's title shows: words exist and nobody turned it off (the Text panel, a kit, the reviewer's swap_hook)."""
    return edl.get("title_card", True) is not False and bool(edl.get("title_card_text"))


def title_style(edl: dict) -> str:
    """"header": the title words over the opening seconds of speech, nothing in front of the first word (new EDLs from
    30/10); "card": the Muse Image card for TITLE_S before the speech (every EDL from before, so their versions render
    as they did). The card failed the hook in every first review (9 of 9 runs: 1.5 s of silence before the hook)."""
    return "header" if edl.get("title_style") == "header" else "card"


def title_card_on(edl: dict) -> bool:
    """The Muse Image card plays in front of the cut (it moves every cut second by its length)."""
    return title_on(edl) and title_style(edl) == "card"


def title_header_on(edl: dict) -> bool:
    return title_on(edl) and title_style(edl) == "header"


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


RUN_WORDS = ("glossary", "highlight")


def carry_run_words(prev: dict, new: dict) -> None:
    """A re-plan keeps the run's caption words (`glossary`, `highlight`) as they were, absent ones too: an EDL from
    before per-run words keeps the demo words (captions.run_glossary) in its re-plans."""
    for k in RUN_WORDS:
        if k in prev:
            new[k] = copy.deepcopy(prev[k])
        else:
            new.pop(k, None)
