"""Captions and callouts: Voice word times on the kept spans -> caption lines placed clear of the head -> overlay specs.

Pipeline, all offline:
1. `span_words`: the Voice words of every EDL span (turn id + word_from..word_to), in cut order.
2. `dedupe_words`: Voice doubles words at random ("its its", "that's that's", "in in"); an adjacent identical word whose
   gap to the previous one is under 150 ms is dropped from the caption (the audio is untouched) and recorded.
3. `apply_glossary`: a small correction list for mishearings of names ("metal models" -> "Meta models",
   "ready viewed" -> "reviewed", "MuseSpark"/"Newspark" -> "Muse Spark", "News Voice" -> "Muse Voice"); every
   replacement is recorded.
4. `group_lines`: 1 to 4 words per line, never across a cut, a pause over 0.45 s or end punctuation, never wider than the
   frame allows; a line that would be up under LINE_MIN_S joins a neighbour of the same phrase when the two fit on one
   line, and `hold_short_lines` keeps any line still that short up LINE_MIN_S, starting the next one later.
5. `build_overlays`: cut times for every line and word, the head boxes (SAM head track, interpolated, mapped through the
   segment's crop path into output pixels) over each line's interval, the caption pushed below or above the head when
   the lower-third default would touch it; the plan's per-span callouts anchored to the SAM person box. Returns the
   EDL `captions` / `callouts` entries and, per segment, the PNG overlays the render burns in.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .edl import norm
from .graphics import CaptionStyle, callout_size, draw_callout, draw_caption, line_width
from .reframe import id_at

OUT_W, OUT_H = 1080, 1920
CAPTION_TOP = 1300  # default caption top in the 1080x1920 frame (lower third, above the Reels/TikTok UI)
SAFE_TOP = 220  # platform UI at the top
SAFE_BOTTOM = 1620  # platform UI (caption, buttons) at the bottom
HEAD_MARGIN = 36
DEDUPE_GAP_S = 0.15
LINE_MAX_WORDS = 4
LINE_BREAK_GAP_S = 0.45
LINE_HOLD_S = 0.5  # a line stays up until the next one when the gap is shorter than this
LINE_TAIL_S = 0.3
LINE_MIN_S = 0.4  # no caption line shows for less ("Hi." showed 0.15 s on run 012 after the edge pass)
CALLOUT_LEAD_S = 0.3
CALLOUT_MIN_S, CALLOUT_MAX_S = 2.0, 3.0
CALLOUT_FADE_S = 0.2

GLOSSARY: dict[str, str] = {
    "metal models": "Meta models",
    "ready viewed": "reviewed",
    "musespark": "Muse Spark",
    "newspark": "Muse Spark",
    "news spark": "Muse Spark",
    "news voice": "Muse Voice",
}
HIGHLIGHT = {"montivo", "muse", "spark", "voice", "sam", "meta"}
_END = re.compile(r"[.!?,;:]$")


@dataclass
class CWord:
    w: str
    t_in: float  # source seconds
    t_out: float
    span: str
    src: str = ""  # what Voice said when a correction changed it

    def to_json(self) -> dict:
        return {"w": self.w, "t_in": round(self.t_in, 3), "t_out": round(self.t_out, 3)}


@dataclass
class Line:
    words: list[CWord]
    segment: int
    t_in: float = 0.0  # cut seconds (final file)
    t_out: float = 0.0
    word_times: list[tuple[float, float]] = field(default_factory=list)  # cut seconds per word

    @property
    def text(self) -> str:
        return " ".join(w.w for w in self.words)


# --------------------------------------------------------------------------- words

def span_words(spans: list[dict], words: list[dict]) -> dict[str, list[CWord]]:
    """Voice words of each span, keyed by span id, their times clipped to the span's edges (the edge pass moves an
    edge into the first or last word: Voice puts the pause before a passage inside its first word)."""
    by_turn: dict[int, list[dict]] = {}
    for w in words:
        by_turn.setdefault(int(w["turn_id"]), []).append(w)
    for tw in by_turn.values():
        tw.sort(key=lambda w: w["i"])
    out: dict[str, list[CWord]] = {}
    for s in spans:
        tw = by_turn.get(int(s["turn_id"]), [])
        a, b = float(s.get("t_in", 0.0)), float(s.get("t_out", math.inf))
        out[s["id"]] = [CWord(w=w["w"], t_in=min(b, max(a, float(w["t_start"]))),
                              t_out=max(a, min(b, float(w["t_end"]))), span=s["id"])
                        for w in tw if s["word_from"] <= w["i"] <= s["word_to"]]
    return out


def dedupe_words(words: list[CWord], max_gap_s: float = DEDUPE_GAP_S) -> tuple[list[CWord], list[dict]]:
    """Drop a word identical (case and punctuation aside) to the previous one when the gap between them is under
    max_gap_s; the kept word stretches over both."""
    out: list[CWord] = []
    fixes: list[dict] = []
    for w in words:
        prev = out[-1] if out else None
        if prev and norm(prev.w) and norm(prev.w) == norm(w.w) and w.t_in - prev.t_out < max_gap_s:
            fixes.append({"kind": "dedupe", "from": f"{prev.w} {w.w}", "to": prev.w, "src_t": round(prev.t_in, 3), "span": w.span})
            prev.t_out = max(prev.t_out, w.t_out)
            continue
        out.append(CWord(w=w.w, t_in=w.t_in, t_out=w.t_out, span=w.span, src=w.src))
    return out, fixes


def apply_glossary(words: list[CWord], glossary: dict[str, str] | None = None) -> tuple[list[CWord], list[dict]]:
    """Replace known mishearings (matched on normalised tokens, longest phrase first). The replacement words share the
    time of the words they replace, split by character count."""
    gl = {tuple(norm(t) for t in k.split()): v for k, v in (glossary if glossary is not None else GLOSSARY).items()}
    longest = max((len(k) for k in gl), default=0)
    out: list[CWord] = []
    fixes: list[dict] = []
    i = 0
    while i < len(words):
        hit = None
        for n in range(min(longest, len(words) - i), 0, -1):
            key = tuple(norm(w.w) for w in words[i:i + n])
            if key in gl:
                hit = (n, gl[key])
                break
        if not hit:
            out.append(words[i])
            i += 1
            continue
        n, repl = hit
        old = words[i:i + n]
        src = " ".join(w.w for w in old)
        trail = old[-1].w[len(old[-1].w.rstrip(".,!?;:")):]  # keep the punctuation Voice put on the last word
        new = repl.split()
        new[-1] += trail
        t0, t1 = old[0].t_in, old[-1].t_out
        total = sum(len(x) for x in new) or 1
        t = t0
        for x in new:
            d = (t1 - t0) * len(x) / total
            out.append(CWord(w=x, t_in=round(t, 3), t_out=round(t + d, 3), span=old[0].span, src=src))
            t += d
        fixes.append({"kind": "glossary", "from": src, "to": " ".join(new), "src_t": round(t0, 3), "span": old[0].span})
        i += n
    return out, fixes


def group_lines(words: list[CWord], style: CaptionStyle, max_words: int = LINE_MAX_WORDS,
                break_gap_s: float = LINE_BREAK_GAP_S, min_tail_s: float = 0.8) -> list[list[CWord]]:
    """1 to max_words words per line; a new line on a pause, after end punctuation, at a span boundary (the planner
    splits a long turn into spans at sentence ends, and Voice gives no punctuation inside a turn) or when the line would
    get too wide. A name the glossary produced ("Muse Voice") is never split across two lines, a short last line
    borrows words from the line before so it does not flash by, and a line that would be up under min_line_s joins a
    neighbour of the same phrase when they fit together (merge_short_lines)."""
    lines: list[list[CWord]] = []
    cur: list[CWord] = []
    for w in words:
        hard = bool(cur) and (w.t_in - cur[-1].t_out > break_gap_s or bool(_END.search(cur[-1].w))
                              or w.span != cur[-1].span)
        soft = bool(cur) and (len(cur) >= max_words or line_width([x.w for x in cur + [w]], style) > style.max_w)
        if hard or soft:
            carry: list[CWord] = []
            if soft and not hard:
                while len(cur) > 1 and cur[-1].src and cur[-1].src == w.src:
                    carry.insert(0, cur.pop())
            lines.append(cur)
            cur = carry
        cur.append(w)
    if cur:
        lines.append(cur)
    # a short line (1 word, or under min_tail_s) takes the last word of the line before when they are one phrase
    # (no pause, no end punctuation, same span) and that line keeps 2+ words
    for j in range(1, len(lines)):
        for _ in range(2):
            last, prev = lines[j], lines[j - 1]
            moved = prev[-1]
            short = len(last) == 1 or last[-1].t_out - last[0].t_in < min_tail_s
            if (short and len(prev) >= 3 and len(last) < max_words and last[0].t_in - moved.t_out <= break_gap_s
                    and moved.span == last[0].span
                    and not _END.search(moved.w) and not (moved.src and moved.src == prev[-2].src)
                    and line_width([moved.w] + [x.w for x in last], style) <= style.max_w):
                last.insert(0, prev.pop())
            else:
                break
    return merge_short_lines(lines, style, max_words, break_gap_s)


def shown_s(lines: list[list[CWord]], j: int) -> float:
    """How long line j will be up, as build_overlays times it: until the next line when the gap is under LINE_HOLD_S,
    else until its last word ends (plus LINE_TAIL_S for the last line)."""
    ln = lines[j]
    if j + 1 < len(lines) and lines[j + 1][0].t_in - ln[-1].t_out < LINE_HOLD_S:
        return lines[j + 1][0].t_in - ln[0].t_in
    return ln[-1].t_out - ln[0].t_in + (LINE_TAIL_S if j == len(lines) - 1 else 0.0)


def merge_short_lines(lines: list[list[CWord]], style: CaptionStyle, max_words: int = LINE_MAX_WORDS,
                      break_gap_s: float = LINE_BREAK_GAP_S, min_line_s: float = LINE_MIN_S) -> list[list[CWord]]:
    """A line that would be up under min_line_s joins the line before, else the line after, when the two are one
    phrase (same span, no pause over break_gap_s, no end punctuation between them) and fit (max_words, frame width)."""
    def joinable(a: list[CWord], b: list[CWord]) -> bool:
        return (a[-1].span == b[0].span and b[0].t_in - a[-1].t_out <= break_gap_s and not _END.search(a[-1].w)
                and len(a) + len(b) <= max_words and line_width([x.w for x in a + b], style) <= style.max_w)

    lines = [list(ln) for ln in lines]
    j = 0
    while j < len(lines):
        if shown_s(lines, j) < min_line_s:
            if j > 0 and joinable(lines[j - 1], lines[j]):
                lines[j - 1] += lines.pop(j)
                j -= 1
                continue
            if j + 1 < len(lines) and joinable(lines[j], lines[j + 1]):
                lines[j] += lines.pop(j + 1)
                continue
        j += 1
    return lines


def hold_short_lines(lines: list[Line], seg_end: float, min_s: float = LINE_MIN_S) -> list[float]:
    """Cut-time pass after the hold and tail: a line still up under min_s stays up min_s (never past seg_end, and never
    leaving the next line under min_s), the next line starts that much later; every word's time is then clamped inside
    its line so the EDL entries and the word highlights agree. Returns the seconds each line was held over."""
    held = [0.0] * len(lines)
    for k, a in enumerate(lines):
        if a.t_out - a.t_in >= min_s - 1e-6:
            continue
        b = lines[k + 1] if k + 1 < len(lines) else None
        end = min(seg_end, a.t_in + min_s)
        if b is not None:
            end = min(end, max(a.t_out, b.t_out - min_s))
            if end > b.t_in:
                b.t_in = round(end, 3)
        if end > a.t_out:
            held[k] = round(end - a.t_out, 3)
            a.t_out = round(end, 3)
    for ln in lines:
        ln.word_times = [(min(max(wa, ln.t_in), ln.t_out), min(max(wb, ln.t_in), ln.t_out)) for wa, wb in ln.word_times]
    return held


# --------------------------------------------------------------------------- boxes

def interp_box(recs: list[dict], t: float, max_gap_s: float = 0.6) -> list[float] | None:
    """Linear interpolation of one object's box at time t; None outside its records (beyond max_gap_s)."""
    if not recs or t < recs[0]["t"] - max_gap_s or t > recs[-1]["t"] + max_gap_s:
        return None
    lo, hi = 0, len(recs) - 1
    if t <= recs[0]["t"]:
        return [float(v) for v in recs[0]["box"]]
    if t >= recs[-1]["t"]:
        return [float(v) for v in recs[-1]["box"]]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if recs[mid]["t"] <= t:
            lo = mid
        else:
            hi = mid
    a, b = recs[lo], recs[hi]
    if b["t"] - a["t"] > 2 * max_gap_s:
        return None
    f = 0.0 if b["t"] == a["t"] else (t - a["t"]) / (b["t"] - a["t"])
    return [a["box"][k] + (b["box"][k] - a["box"][k]) * f for k in range(4)]


def largest_at(track: dict | None, t: float) -> list[list[float]]:
    """The largest box at time t (the speaker, not a prop the person concept also picked up), as a one-item list."""
    bs = boxes_at(track, t)
    return [max(bs, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))] if bs else []


def clamp_frame(box: list[float] | None) -> list[float] | None:
    if box is None:
        return None
    return [max(0.0, box[0]), max(0.0, box[1]), min(float(OUT_W), box[2]), min(float(OUT_H), box[3])]


def boxes_at(track: dict | None, t: float) -> list[list[float]]:
    """Every object's box at source time t, in SOURCE pixels (tracks are in proxy pixels)."""
    if not track or not track.get("objects"):
        return []
    return [b for b in (interp_box(recs, t) for recs in track["objects"].values()) if b is not None]


def to_output(box: list[float], crop_x: float, crop_w: int, crop_h: int) -> list[float]:
    sx, sy = OUT_W / crop_w, OUT_H / crop_h
    return [(box[0] - crop_x) * sx, box[1] * sy, (box[2] - crop_x) * sx, box[3] * sy]


def union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def overlaps(a: list[float], b: list[float] | None, margin: float = 0.0) -> bool:
    if b is None:
        return False
    return a[0] < b[2] + margin and b[0] - margin < a[2] and a[1] < b[3] + margin and b[1] - margin < a[3]


def place_caption(w: int, h: int, head: list[float] | None, top: int = CAPTION_TOP) -> tuple[int, str]:
    """Top y of a centred caption box: the lower-third default unless it touches the head (plus margin); then just
    below the head when that still clears the bottom UI, else just above it, else as low as the bottom UI allows,
    flagged as overlap (a face that fills the frame, run 013: the mouth sits above the default, the chin at it)."""
    x0 = (OUT_W - w) / 2

    def clear(y: float) -> bool:
        return not overlaps([x0, y, x0 + w, y + h], head, HEAD_MARGIN)

    if clear(top):
        return top, "lower_third"
    below = int(head[3] + HEAD_MARGIN + 1)
    if below + h <= SAFE_BOTTOM and clear(below):
        return below, "below_head"
    above = int(head[1] - HEAD_MARGIN - h - 1)
    if above >= SAFE_TOP and clear(above):
        return above, "above_head"
    return max(top, SAFE_BOTTOM - h), "over_head"


def place_callout(w: int, h: int, person: list[float] | None, head: list[float] | None,
                  avoid: list[list[float]]) -> tuple[int, int, str]:
    """A callout pill anchored to the person box: centred on it horizontally, sitting on top of the box when there is
    room under the top UI, else inside the box just under the head; never over the head, never over a caption."""
    if person is None:
        return (OUT_W - w) // 2, SAFE_TOP, "no_box"
    cx = (person[0] + person[2]) / 2
    x = int(min(OUT_W - 24 - w, max(24, cx - w / 2)))
    cands = [(int(person[1] - 16 - h), "box_top")]
    if head is not None:
        cands.append((int(head[3] + HEAD_MARGIN), "box_below_head"))
    cands.append((int(person[1] + 24), "box_inside_top"))
    for y, where in cands:
        r = [x, y, x + w, y + h]
        if y < SAFE_TOP or y + h > SAFE_BOTTOM:
            continue
        if overlaps(r, head, HEAD_MARGIN / 2) or any(overlaps(r, a, 12) for a in avoid):
            continue
        return x, y, where
    return x, max(SAFE_TOP, cands[-1][0]), "overlap"


# --------------------------------------------------------------------------- review patches

def override_for(words: list[CWord], overrides: list[dict]) -> dict | None:
    """The EDL `caption_overrides` entry that targets this line: same span, its source time inside the line's words.
    An override carries `top` (a forced y, used to plant a known fault and removed by caption_fix) and/or `text`
    (the words actually spoken, from the reviewer's caption_fix)."""
    for ov in overrides:
        if any(w.span == ov.get("span") for w in words) and \
                words[0].t_in - 0.01 <= float(ov["src_t"]) <= words[-1].t_out + 0.01:
            return ov
    return None


def retext(words: list[CWord], text: str) -> list[CWord]:
    """The line's words replaced by `text`, sharing the line's time split by character count."""
    new = text.split()
    if not new:
        return words
    t0, t1 = words[0].t_in, words[-1].t_out
    src = " ".join(w.w for w in words)
    total = sum(len(x) for x in new) or 1
    out, t = [], t0
    for x in new:
        d = (t1 - t0) * len(x) / total
        out.append(CWord(w=x, t_in=round(t, 3), t_out=round(t + d, 3), span=words[0].span, src=src))
        t += d
    return out


# --------------------------------------------------------------------------- the stage

@dataclass
class Overlay:
    png: Path
    x: int
    y: int
    t_in: float  # segment-local seconds
    t_out: float
    fade_s: float = 0.0


def build_overlays(edl: dict, words: list[dict], segments: list, paths: list, head_track: dict | None,
                   person_track: dict | None, out_dir: Path, title_s: float = 0.0, captions: bool = True,
                   callouts: bool = True, glossary: dict[str, str] | None = None,
                   style: CaptionStyle | None = None, reframe_concept: str | None = None) -> tuple[dict, dict[int, list[Overlay]]]:
    """Caption lines and callouts for the rendered segments.

    `segments` are render.Segment (frame-aligned), `paths` the reframe SegmentPath of each (crop x per source frame).
    Captions and callouts keep clear of one head per segment: the head the crop follows (`reframe_concept` "head" and
    the path's track_id), else the largest head. A union of every head object put all 41 lines of run 013 "over the
    head": SAM also tracked the passers-by in the background, and their union covered the whole frame.
    Returns the EDL fields ({"captions": [...], "callouts": [...], "caption_corrections": [...], ...}) and the PNG
    overlays per segment index in segment-local time.
    """
    style = style or CaptionStyle()
    sw = float(edl["source"]["width"])
    sh = float(edl["source"]["height"])

    def scaled(track):
        if not track:
            return None
        tw, th = float(track["source"]["width"]), float(track["source"]["height"])
        fx, fy = sw / tw, sh / th
        return {"objects": {oid: [{"t": r["t"], "box": [r["box"][0] * fx, r["box"][1] * fy, r["box"][2] * fx, r["box"][3] * fy]}
                                  for r in recs] for oid, recs in track["objects"].items()}}

    head_src, person_src = scaled(head_track), scaled(person_track)
    seg_cut: list[float] = []
    t = title_s
    for seg in segments:
        seg_cut.append(t)
        t += seg.duration

    def union_out(track, i: int, a: float, b: float, pick=None) -> list[float] | None:
        """Union of a track's boxes in output pixels over segment-local [a, b] (every object, or `pick`'s choice)."""
        pick = pick or boxes_at
        seg, path = segments[i], paths[i]
        k0 = max(0, int(a * seg.fps))
        k1 = min(len(path.xs) - 1, int(b * seg.fps))
        out = []
        for k in range(k0, k1 + 1, 3):
            out += [to_output(bx, path.xs[k], path.crop_w, path.crop_h) for bx in pick(track, seg.t_in + k / seg.fps)]
        return union(out)

    def subject_head(i: int):
        """Box picker for the head segment i keeps clear of (see the docstring), in the union_out `pick` form."""
        path = paths[i]
        tid = getattr(path, "track_id", None)
        if reframe_concept == "head" and head_src and tid in head_src["objects"]:
            def followed(track, t):
                # a segment steered by the speaker map follows a different head per piece
                cur = id_at(path, t) if getattr(path, "pieces", None) else tid
                b = interp_box(head_src["objects"][cur], t) if cur in head_src["objects"] else None
                return [b] if b is not None else largest_at(track, t)
            return followed
        return largest_at

    def cut_t(i: int, src: float) -> float:
        seg = segments[i]
        return round(seg_cut[i] + min(seg.duration, max(0.0, src - seg.t_in)), 3)

    overrides = edl.get("caption_overrides") or []
    seg_of_span = {sid: i for i, seg in enumerate(segments) for sid in seg.span_ids}
    per_span = span_words(edl["spans"], words)
    fixes: list[dict] = []
    seg_words: dict[int, list[CWord]] = {}
    for s in edl["spans"]:
        ws, f1 = dedupe_words(per_span.get(s["id"], []))
        ws, f2 = apply_glossary(ws, glossary)
        fixes += f1 + f2
        seg_words.setdefault(seg_of_span[s["id"]], []).extend(ws)

    overlays: dict[int, list[Overlay]] = {i: [] for i in range(len(segments))}
    cap_entries: list[dict] = []
    caption_rects: dict[int, list[tuple[float, float, list[float]]]] = {i: [] for i in range(len(segments))}
    n_line = 0
    if captions:
        for i, ws in sorted(seg_words.items()):
            seg = segments[i]
            lines = [Line(words=lw, segment=i) for lw in group_lines(ws, style)]
            forced: dict[int, dict] = {}
            for k, ln in enumerate(lines):
                ov = override_for(ln.words, overrides)
                if ov is None:
                    continue
                if ov.get("text"):
                    ln.words = retext(ln.words, ov["text"])
                if ov.get("top") is not None:
                    forced[k] = ov
            for ln in lines:
                ln.word_times = [(cut_t(i, w.t_in), cut_t(i, w.t_out)) for w in ln.words]
                ln.t_in, ln.t_out = ln.word_times[0][0], ln.word_times[-1][1]
            for a, b in zip(lines, lines[1:]):
                if b.t_in - a.t_out < LINE_HOLD_S:
                    a.t_out = b.t_in
            if lines:
                lines[-1].t_out = min(seg_cut[i] + seg.duration, lines[-1].t_out + LINE_TAIL_S)
            held = hold_short_lines(lines, round(seg_cut[i] + seg.duration, 3))
            for k, ln in enumerate(lines):
                n_line += 1
                hold_s = held[k]
                la, lb = ln.t_in - seg_cut[i], ln.t_out - seg_cut[i]
                if lb - la <= 0:
                    continue
                head = union_out(head_src, i, la, lb, subject_head(i))
                texts = [w.w for w in ln.words]
                hl = [norm(w).replace("'s", "") in HIGHLIGHT for w in texts]
                w_px, h_px = line_width(texts, style), style.height()
                y, where = place_caption(w_px, h_px, head)
                if k in forced:
                    y, where = int(forced[k]["top"]), "forced"
                x = (OUT_W - w_px) // 2
                caption_rects[i].append((la, lb, [x, y, x + w_px, y + h_px]))
                for k in range(len(texts)):
                    png = out_dir / f"cap_{n_line:03d}_{k + 1}.png"
                    draw_caption(texts, k, hl, style, png)
                    ka = la if k == 0 else ln.word_times[k][0] - seg_cut[i]
                    kb = lb if k == len(texts) - 1 else ln.word_times[k + 1][0] - seg_cut[i]
                    if kb > ka:
                        overlays[i].append(Overlay(png=png, x=x, y=y, t_in=round(ka, 4), t_out=round(kb, 4)))
                cap_entries.append({
                    "id": f"c{n_line}", "segment": i, "spans": sorted({w.span for w in ln.words}), "text": ln.text,
                    "t_in": ln.t_in, "t_out": ln.t_out,
                    "words": [{"w": w.w, "t_in": a, "t_out": b, **({"voice": w.src} if w.src else {})}
                              for w, (a, b) in zip(ln.words, ln.word_times)],
                    "box": [x, y, w_px, h_px], "placement": where,
                    "head_box": [round(v) for v in head] if head else None,
                    "over_face": where == "over_head" or overlaps([x, y, x + w_px, y + h_px], head),
                    **({"held_s": hold_s} if hold_s else {}),
                    "corrections": [f for f in fixes if f["span"] in {w.span for w in ln.words}
                                    and ln.words[0].t_in - 0.01 <= f["src_t"] <= ln.words[-1].t_out + 0.01],
                })

    call_entries: list[dict] = []
    if callouts:
        for s in edl["spans"]:
            text = (s.get("callout") or "").strip()
            if not text:
                continue
            i = seg_of_span[s["id"]]
            seg = segments[i]
            a = max(0.0, max(0.0, s["t_in"] - seg.t_in) + CALLOUT_LEAD_S + float(s.get("callout_shift_s") or 0.0))
            dur = min(CALLOUT_MAX_S, max(CALLOUT_MIN_S, s["duration"] - 2 * CALLOUT_LEAD_S))
            b = min(seg.duration, a + dur)
            if b - a < 0.5:
                continue
            person = clamp_frame(union_out(person_src, i, a, b, largest_at))
            head = union_out(head_src, i, a, b, subject_head(i))
            avoid = [r for (ra, rb, r) in caption_rects[i] if ra < b and rb > a]
            w_px, h_px = callout_size(text)
            x, y, where = place_callout(w_px, h_px, person, head, avoid)
            png = out_dir / f"callout_{s['id']}.png"
            draw_callout(text, png)
            overlays[i].append(Overlay(png=png, x=x, y=y, t_in=round(a, 4), t_out=round(b, 4), fade_s=CALLOUT_FADE_S))
            call_entries.append({"span": s["id"], "text": text, "t_in": round(seg_cut[i] + a, 3), "t_out": round(seg_cut[i] + b, 3),
                                 "box": [x, y, w_px, h_px], "placement": where,
                                 "person_box": [round(v) for v in person] if person else None,
                                 "head_box": [round(v) for v in head] if head else None})

    fields = {"captions": cap_entries, "callouts": call_entries, "caption_corrections": fixes,
              "caption_style": {"font": style.size, "max_words": LINE_MAX_WORDS, "default_top": CAPTION_TOP,
                                "min_line_s": LINE_MIN_S,
                                "dedupe_gap_s": DEDUPE_GAP_S, "glossary": glossary if glossary is not None else GLOSSARY}}
    return fields, overlays
