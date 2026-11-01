"""EDL builder: paper-edit spans (turn id + verbatim words) -> in/out points on Voice word boundaries.

A span is verbatim when its normalised tokens appear contiguously in the turn's words. When they do not, the
closest contiguous block is used (difflib) and the span is flagged so the review step can see it.
"""
from __future__ import annotations

import copy
import difflib
import re
from dataclasses import asdict, dataclass

from .edges import Audio, Words, cut_edges, tighten_edges
from .voice import VoiceResult, Word, artefact_doubles

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


MAX_MISHEARINGS = 6


def mishearings(items: list[dict], spans: list["Span"]) -> list[dict]:
    """The planner's likely_mishearings (C3) as glossary suggestions: kept only when `heard` is a run of a kept span's
    words (a caption shows it, and the glossary matches it the same way: normalised tokens), `written` says something
    else, no repeats, at most MAX_MISHEARINGS. `heard` becomes the span's own words, end punctuation off (what the
    glossary edit stores). Never applied here: the captions stay verbatim until a person accepts one."""
    out: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for it in items or []:
        h = norm_tokens(str(it.get("heard") or ""))
        written = " ".join(str(it.get("written") or "").split()).strip()
        if not h or not written or norm_tokens(written) == h or tuple(h) in seen:
            continue
        for s in spans:
            toks = [(w, norm(w)) for w in s.words.split()]
            toks = [t for t in toks if t[1]]
            at = next((i for i in range(len(toks) - len(h) + 1) if [n for _, n in toks[i:i + len(h)]] == h), None)
            if at is not None:
                heard = " ".join(w for w, _ in toks[at:at + len(h)]).rstrip(".,!?;:")
                out.append({"heard": heard, "written": written.rstrip(".,!?;:"), "why": str(it.get("why") or "").strip(),
                            "span": s.id})
                seen.add(tuple(h))
                break
        if len(out) >= MAX_MISHEARINGS:
            break
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
    strength: int = 0  # the planner's 1-5 (fit_length drops the weakest body span first), 0 = not given


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


# a fuzzy match must find at least this share of the quote's words in order, else it is unplaced; measured 31/10 on
# 4500 planner-like paraphrases of real turns (runs 001, 012-016): every quote with one or two words dropped or
# changed keeps >= 0.6 (13 of 900 with three changes fall under), quotes from another turn of the same run reach it
# 2 times in 750 (p99 0.44)
LOCATE_SHARE = 0.6
LOCATE_SLACK = 0.5  # ... inside a window at most this much longer than the quote (Voice's doubles, dropped fillers)


def locate(span_text: str, turn_words: list[Word], after: int = -1,
           taken: list[tuple[int, int]] | None = None) -> tuple[int, int, bool, float] | None:
    """Return (first index, last index, exact, ratio) of the span inside the turn's words, None when the quote is not
    there. A quote said more than once (a retake: "this is the part that matters ... let me say that again, this is
    the part that matters") resolves to the last take after word `after` (the end of the plan's previous span in
    this turn), else the last take, never one overlapping `taken`. A fuzzy match is the densest window at most
    LOCATE_SLACK longer than the quote; under LOCATE_SHARE of the quote's words found there it is unplaced (run 013:
    a slightly-off quote became an 88-word span at ratio 0.108, audit 31/10)."""
    want = norm_tokens(span_text)
    have = [norm(w.w) for w in turn_words]
    if not want or not have:
        return None
    n = len(want)
    taken = taken or []

    def pick(hits: list[tuple[int, int]]) -> tuple[int, int] | None:
        free = [h for h in hits if not any(h[0] <= b and a <= h[1] for a, b in taken)] or hits
        later = [h for h in free if h[0] > after]
        return (later or free)[-1] if free else None

    # the planner reads the transcript without Voice's short duplicate words (voice.shown_words): match there first
    # and map back to the turn's own word indices, so a quote opening on "200" starts on the real "200", not on its
    # 80 ms copy (heli-02 18.02 / 18.38 s); an older plan that quoted the double matches on every word below
    drop = artefact_doubles(turn_words)
    if drop:
        pos = [k for k in range(len(turn_words)) if k not in drop]
        shown = [have[k] for k in pos]
        if hit := pick([(pos[i], pos[i + n - 1]) for i in range(len(shown) - n + 1) if shown[i:i + n] == want]):
            return hit[0], hit[1], True, 1.0
    if hit := pick([(i, i + n - 1) for i in range(len(have) - n + 1) if have[i:i + n] == want]):
        return hit[0], hit[1], True, 1.0
    cap = n + max(2, int(round(LOCATE_SLACK * n)))
    wanted = set(want)
    cands: dict[tuple[int, int], int] = {}  # (first, last) -> quote words found in order inside it
    for i in range(len(have)):
        if have[i] not in wanted:
            continue
        blocks = [b for b in difflib.SequenceMatcher(a=have[i:i + cap], b=want, autojunk=False).get_matching_blocks()
                  if b.size]
        found = 0
        for blk in blocks:  # every run of blocks from the first: a far trailing match may be a stray "and"
            found += blk.size
            key = (i + blocks[0].a, i + blk.a + blk.size - 1)
            cands[key] = max(cands.get(key, 0), found)
    if not cands:
        return None
    # the window that best matches the quote (difflib's ratio on that window alone), so a stray common word a few
    # words on does not stretch it; ties go to the retake rule
    score = {k: 2 * f / (k[1] - k[0] + 1 + n) for k, f in cands.items()}
    top = max(score.values())
    first, last = pick(sorted(k for k, v in score.items() if v == top))
    if cands[(first, last)] < LOCATE_SHARE * n:
        return None
    # keep the window the size of the request around the matched words
    last = min(len(have) - 1, max(last, first + n - 1))
    ratio = difflib.SequenceMatcher(a=have[first:last + 1], b=want, autojunk=False).ratio()
    return first, last, False, round(ratio, 3)


OVER_TARGET = 1.10  # a plan running more than 10 % over the target loses its weakest body span (fit_length)
UNDER_TARGET = 0.80  # never below this share of the target by a drop


def fit_length(edl: dict, target_s: float | None) -> dict | None:
    """The planner overshoots (heli-03 48.8 s and heli-04 46.6 s on a 40 s target, a brief of 45 s at most): while the
    cut runs over OVER_TARGET x the target, or over the brief's own maximum (a length brief_check), drop the weakest
    `body` span (the planner's lowest strength, then the one that leaves the runtime nearest the target), never the
    hook or the payoff, never below UNDER_TARGET x the target. Returns the record kept as EDL `length_fit`, None when
    nothing was dropped."""
    if not target_s or target_s <= 0:
        return None
    limit = OVER_TARGET * target_s
    for c in edl.get("brief_checks") or []:
        if c.get("kind") == "length" and (c.get("max_s") or -1) > 0:
            limit = min(limit, float(c["max_s"]) + LENGTH_SLACK_S)
    spans = edl["spans"]

    def runtime() -> float:
        return round(sum(s["t_out"] - s["t_in"] for s in spans), 2)

    before, dropped = runtime(), []
    while runtime() > limit:
        rt = runtime()
        cands = [s for s in spans if s["role"] == "body"
                 and rt - (s["t_out"] - s["t_in"]) >= UNDER_TARGET * min(target_s, limit)]
        if not cands:
            break
        weak = min(cands, key=lambda s: (int(s.get("strength") or 3),
                                         abs(rt - (s["t_out"] - s["t_in"]) - target_s)))
        spans.remove(weak)
        dropped.append({"id": weak["id"], "words": weak["words"], "duration": round(weak["t_out"] - weak["t_in"], 2),
                        **({"strength": weak["strength"]} if weak.get("strength") else {})})
    if not dropped:
        return None
    for k, s in enumerate(spans):
        s["order"] = k
        s["contiguous_with_previous"] = bool(k and s["turn_id"] == spans[k - 1]["turn_id"]
                                             and s["word_from"] == spans[k - 1]["word_to"] + 1)
    edl["runtime_s"] = runtime()
    return {"limit_s": round(limit, 2), "before_s": before, "after_s": edl["runtime_s"], "dropped": dropped}


def build_edl(plan: dict, voice: VoiceResult, watch: dict, source: dict, brief: str, tracks: dict,
              reframe_concept: str = "head", audio: "Audio | None" = None, target_s: float | None = None) -> dict:
    """The EDL v1 on Voice word boundaries; with the source audio, every cut edge is then tightened to the speech
    onset and offset (edges.py), so v1 does not open in the pause before the first word or hold after the last.
    With `target_s`, a plan that runs long loses its weakest body spans (fit_length)."""
    by_turn: dict[int, list[Word]] = {}
    for w in voice.words:
        by_turn.setdefault(w.turn_id, []).append(w)
    turns = {t.turnId: t for t in voice.turns}

    spans: list[Span] = []
    missing: list[dict] = []
    for k, item in enumerate(plan.get("paper_edit", [])):
        tid = int(item["turn_id"])
        tw = by_turn.get(tid, [])
        placed = [(s.word_from, s.word_to) for s in spans if s.turn_id == tid]
        loc = locate(item["words"], tw, after=placed[-1][1] if placed else -1, taken=placed)
        if loc is None:
            missing.append({"order": k, **item,
                            "reason": "quote not found in the turn" if tw else "turn not found"})
            continue
        a, b, exact, ratio = loc
        t_in, t_out = tw[a].t_start, tw[b].t_end
        spans.append(Span(id=f"s{k + 1}", order=k, turn_id=tid, speaker=turns[tid].speaker if tid in turns else "?",
                          role=item["role"], words=" ".join(w.w for w in tw[a:b + 1]), t_in=t_in, t_out=t_out,
                          duration=round(t_out - t_in, 3), verbatim=exact, match_ratio=ratio, word_from=a, word_to=b,
                          callout=item.get("callout", ""), why=item.get("why", ""),
                          strength=int(item.get("strength") or 0)))

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
        # names and terms Voice probably misheard, as the planner found them (C3): offered in the chat and the
        # Captions panel ("Did you say ...?"), applied only when a person accepts one (the glossary edit)
        "glossary_suggestions": mishearings(plan.get("likely_mishearings") or [], spans),
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
    words = Words([asdict(w) for w in voice.words])
    if audio is not None and edl["spans"]:
        tighten_edges(edl, words, audio)
    before = edge_ids(edl["spans"])
    if fit := fit_length(edl, target_s):
        edl["length_fit"] = fit
        # a dropped span that was joined to its neighbours leaves two new cuts the pass above skipped as joins: they
        # sat on Voice's word boundaries, up to 0.3 s off the speech (audit, 31/10)
        if audio is not None and (new := edge_ids(edl["spans"]) - before):
            more = tighten_edges(edl, words, audio, only=new)
            if (ep := edl.get("edge_pass")) and more["moves"]:
                ep["moves"] += more["moves"]
                ep.update(moved=len(ep["moves"]), widened=sum(m["widened"] for m in ep["moves"]),
                          removed_s=round(sum(abs(m["to"] - m["from"]) for m in ep["moves"]), 2))
    anchor_callouts(edl, words)
    return edl


CALLOUT_SHIFT_MIN_S = 0.5  # a callout whose words come sooner than this keeps the span-start timing


def anchor_callouts(edl: dict, words: Words) -> None:
    """A callout shows when the span says its words, not at the span's start: heli-01's "716 SAVES" came up 7 s before
    "716 saves" was spoken and "NVG HOIST CAPABLE" over the lines about hikers (callouts_anchored 4, two majors at the
    first review). The first content word of the callout the span says sets `callout_shift_s` (a number first: "716"),
    within the room build_overlays needs; a callout whose words are not in the span keeps its time. The first span
    keeps its time when its words come while the title header is up (build_overlays then waits for the header)."""
    from .captions import CALLOUT_LEAD_S, CALLOUT_MIN_S, HEADER_S  # captions imports this module
    for k, s in enumerate(edl["spans"]):
        want = [t for t in norm_tokens(s.get("callout") or "") if t not in _FILLER]
        if not want or s.get("callout_shift_s"):
            continue
        tw = words.turn(s["turn_id"])[s["word_from"]:s["word_to"] + 1]
        said = [(norm(w["w"]), w) for w in tw]
        hit = next((w for t, w in said if t in want and t[:1].isdigit()), None) or \
            next((w for t, w in said if t in want), None)
        if hit is None:
            continue
        at = max(0.0, float(hit["t_start"]) - s["t_in"])
        room = max(0.0, s["t_out"] - s["t_in"] - CALLOUT_LEAD_S - CALLOUT_MIN_S)
        shift = round(min(room, max(0.0, at - CALLOUT_LEAD_S)), 2)
        if shift < CALLOUT_SHIFT_MIN_S or (k == 0 and title_header_on(edl) and at < HEADER_S):
            continue
        s["callout_shift_s"] = shift


def edge_ids(spans: list[dict]) -> set[tuple[str, str]]:
    """The (span id, edge) pairs that are cuts."""
    return {(spans[k]["id"], edge) for k, edge, _ in cut_edges(spans)}


# --------------------------------------------------------------------------- the brief as a checklist (C1, A4)

QUOTE_MATCH = 0.8  # a quote counts as kept when a run of the kept words matches it this well (difflib ratio)
DROP_MATCH = 0.8  # a drop item counts as left in when a run of the kept words matches it this well (0.6 took 012's
# kept second take "this is the part that matters" for the dropped first one, "this is the part I care about")
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


RESTART_WINDOW = 5  # a false start: the same two words again within this many words ...
_PARALLEL = {"and", "but", "or", "then", "because"}  # ... with no conjunction between: "seemed to speak and everyone
# seemed to be looking" and "over there and then over there" (d1-tedx) are said that way on purpose


def restarts(edl: dict, window: int = RESTART_WINDOW) -> list[str]:
    """A false start kept inside one span: the speaker begins a phrase, breaks off and says it again ("we had we've had
    it we had enough", d1-tedx; "a notepad and I took a notepad and I wrote"). The first review called d1-tedx's a
    repeated take (a brief_covered major) and the loop could not drop it: the window sits inside a span. Two different
    words, then the same two within `window` words with at least one word between; one-word doubles are Voice's
    ("all all") and the captions drop them; a repeat after "and" / "then" is a parallel, not a restart. One problem
    per span."""
    out = []
    for s in edl.get("spans") or []:
        toks = norm_tokens(s.get("words") or "")
        hit = None
        for i in range(len(toks) - 1):
            g = toks[i:i + 2]
            if g[0] == g[1] or all(t in _FILLER for t in g):
                continue
            for j in range(i + 3, min(len(toks) - 1, i + window + 1)):
                if toks[j:j + 2] == g and not _PARALLEL & set(toks[i + 2:j]):
                    hit = (i, j)
                    break
            if hit:
                break
        if hit:
            i, j = hit
            out.append(f"{s['id']} keeps a false start: \"{' '.join(toks[i:j + 2])}\" says \"{' '.join(toks[i:i + 2])}\" "
                       f"twice; quote the span from the take the speaker finishes (\"{' '.join(toks[j:j + 4])} ...\"), "
                       "or split it into two spans around the false start")
    return out


OPENING_FILLERS = {"um", "uh", "uhm", "erm", "so", "yeah", "okay", "ok", "well", "and", "like", "anyway"}


def filler_hook(edl: dict) -> list[str]:
    """The hook opens on a filler: heli-01's repaired plan opened on "Yeah so I think the the most important thing"
    (hook_in_1_5s 2, a blocker); the rubric fails filler in the first 1.5 s. Also when the brief names the opening
    line: 016's plan opened "okay hi I'm Billy" for "Open on 'Hi. I'm Billy.'" (Voice heard "okay hi" as one run, the
    reviewer's hook major), unless the brief's own words for the opening start on that word."""
    spans = edl.get("spans") or []
    if not spans:
        return []
    toks = norm_tokens(spans[0].get("words") or "")
    if not toks or toks[0] not in OPENING_FILLERS:
        return []
    if any(c.get("kind") == "opening" and toks[0] in {t.strip("'") for t in norm_tokens(c.get("item") or "")}
           for c in edl.get("brief_checks") or []):
        return []  # the brief's own opening line starts on that word
    lead = " ".join((spans[0]["words"]).split()[:5])
    return [f"{spans[0]['id']} (the hook) opens on a filler: \"{lead} ...\". The first words a viewer hears must be the "
            "line itself: start the hook span after the filler, on a word that begins the claim, or open on another line"]


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
            lo, hi = c.get("min_s", -1), c.get("max_s", -1)  # -1 = no bound on that side
            allows = (f"{lo:g} to {hi:g} s" if lo >= 0 and hi >= 0 else f"at most {hi:g} s" if hi >= 0
                      else f"at least {lo:g} s")
            out.append(f"length: the cut runs {c['found']}, the brief allows {allows} ({c['item']})")
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
