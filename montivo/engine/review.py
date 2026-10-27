"""Review: Spark 1.3 judges its own render blind, the patcher turns its fix ops into the next EDL, the loop re-renders.

The reviewer gets the rendered cut, the brief and the rubric, never the EDL (design rule of 26/10). Two deterministic
facts go with it as text: the silence gaps ffmpeg `silencedetect` finds in the cut, and where every caption box sits
against the SAM head box (from the EDL `captions` entries the render wrote). A third fact was added after kill test (b):
the source audio level right at both edges of every cut, because Spark never heard a word clipped by a cut (0 of 3 runs). The copy Spark watches is the cut scaled
to 720x1280 with a small clock burned in at the top left, because Spark sees about one frame per second with no
timestamps: the clock is the only way its t_s can land within a second of the fault.

The reviewer names times in the cut, not spans. `cut_map` maps every span (and the title card) to cut seconds with the
same merge and frame alignment the render uses, so `plan_fixes` can turn an issue at t_s into a concrete patch:

    drop_span      the span spoken at t_s goes; a window t_s..t_end_s over part of it trims those words at that end;
                   the hook span is never dropped whole (t_s inside the title card: the card goes)
    extend_span    whole Voice words added at one end of the span, never into words another span keeps
    move_cut       the edge of the cut nearest t_s: snapped back onto its Voice word boundary when it is off one, else
                   moved by whole words (|seconds| >= 0.3), else moved to the quietest 40 ms of the source audio
                   within 0.3 s (Voice words abut, so only the audio shows where the gap between two words is)
    swap_hook      the span at target_t_s opens the cut (a target inside the opening span trims its start to there);
                   without a target, a title card in front is dropped
    reorder        the span at t_s moves before the span at target_t_s
    caption_fix    the caption line at t_s: a forced position removed, moved below the head, or its words replaced
    retime_callout the callout near t_s shifted (callout_shift_s) or re-worded
    reframe_bias   the segment at t_s: the crop path shifted 60 source px left or right (render.apply_bias)

Only blocker and major issues are applied, one fix per target, at most MAX_FIXES per pass. Loop guards: at most 3
reviews, a dollar budget checked before every review, the same fix proposed twice stops the loop (the first time did
not cure it), and a pass that scores below the best so far stops it (the best version is the result). Every review lands in review_vN.json and spark/review_vN_*.json; its cost in the ledger as spark.review.
"""
from __future__ import annotations

import copy
import difflib
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from ..config import PRICE, SPARK_MODEL
from ..ledger import Entry, Ledger, Timer
from .api import dump_json, upload_file
from .captions import CAPTION_TOP, HEAD_MARGIN, SAFE_BOTTOM, overlaps
from .edges import Audio, Words, level_db, tighten_edges  # noqa: F401 (level_db re-exported for callers)
from .edl import norm, norm_tokens
from .graphics import font
from .render import frame_align, merge_segments

APPLY_SEVERITY = ("blocker", "major")
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2}
MAX_FIXES = 4  # per pass: a patch the size of a re-plan is a re-plan, not a review fix
MAX_PASSES = 3
BUDGET_USD = 0.30  # review calls per loop (about $0.04 per review of a 55 s cut)
SILENCE_DB = -35.0
SILENCE_MIN_S = 0.4
EDGE_HALF_S = 0.02  # source audio measured over 40 ms centred on a cut edge
EDGE_LOUD_DB = -34.0  # clean edges -37.8 to -53.4 dB, the edge inside "agent" -25.8 dB (26/10, source audio.wav)
DROP_WHOLE_COVER = 0.7  # drop_span drops the whole span only when the issue window covers this much of it
EDGE_SLACK_S = 0.6  # a window this close to a span edge counts as touching it
TRIM_SLACK_S = 0.1  # a word must overlap a trim point by this much to count as under way (Voice edges abut)
REACH_S = 0.05  # a sentence end one word past the trim point counts when that word overlaps it by this much
               # (run 012: "Billy." overlapped by 0.10 s and is kept; run 012-ktb: "So," by 0.01 s and stays out)
VALLEY_RANGE_S = 0.3  # move_cut on an edge already on a Voice boundary: search the source audio this far each side
VALLEY_STEP_S = 0.02  # for the quietest 40 ms (Voice word edges carry up to 0.3 s of slop and words always abut)
WORD_SHIFT_MIN_S = 0.3
BIAS_PX = 60  # reframe_bias step in source px (10 % of a 608 px crop at 1920)
CLOCK_FPS = 10
REVIEW_W, REVIEW_H = 720, 1280


# --------------------------------------------------------------------------- facts

_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def silences_in(media: Path, noise_db: float = SILENCE_DB, min_s: float = SILENCE_MIN_S) -> list[tuple[float, float]]:
    """Silence gaps in a file's audio with ffmpeg silencedetect, as (start, end) cut seconds."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(media), "-map", "0:a:0", "-af",
                           f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"], capture_output=True, text=True)
    return parse_silences(proc.stderr, duration=media_duration(media))


def parse_silences(stderr: str, duration: float | None = None) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            out.append((round(start, 2), round(float(m.group(1)), 2)))
            start = None
    if start is not None and duration is not None:
        out.append((round(start, 2), round(duration, 2)))
    return out


def media_duration(media: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(media)],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def caption_face_facts(edl: dict) -> dict:
    """Every caption line's box against the SAM head box over its interval (both in output px, from the EDL)."""
    lines = edl.get("captions") or []
    over: list[dict] = []
    clearance: list[tuple[float, float, str]] = []
    for c in lines:
        x, y, w, h = c["box"]
        rect = [x, y, x + w, y + h]
        head = c.get("head_box")
        if head is None:
            continue
        if overlaps(rect, head):
            over.append({"t_in": c["t_in"], "t_out": c["t_out"], "text": c["text"], "caption_top": y,
                         "head_box_y": [head[1], head[3]], "overlap_px": round(min(rect[3], head[3]) - max(rect[1], head[1]))})
        else:
            clearance.append((round(y - head[3]), c["t_in"], c["text"]))
    least = min(clearance) if clearance else None
    return {"lines": len(lines), "with_head_box": sum(1 for c in lines if c.get("head_box")), "over_face": over,
            "min_clearance_px": least[0] if least else None, "min_clearance_at": least[1] if least else None}


def edge_loud_db(audio: "Audio") -> float:
    """The level over which a cut edge counts as inside speech: EDGE_LOUD_DB, or the clip's own speech threshold (floor
    + 6 dB) when its room is louder, never above EDGE_LOUD_DB + 4 (a clip with no quiet frames has no usable floor)."""
    return max(EDGE_LOUD_DB, min(audio.threshold_db(), EDGE_LOUD_DB + 4.0))


def cut_edge_levels(edl: dict, audio: "Audio") -> list[dict]:
    """Speech level of the SOURCE audio centred on both edges of every cut (where a kept passage ends, where the next
    begins, and the very end), reported at the cut's time. Between two words the level drops (clean edges on runs 012
    and 012-ktb: -37.8 to -53.4 dB); an edge inside a word does not (the cut inside "agent" -25.8 dB, an end 20 ms into
    the next word -31.3 dB). Measured on the source because the render fades 20 ms at every join, and on the edge itself
    because a word that ends right at a tight cut is loud just before it without being clipped. Spark does not hear a
    clipped word (kill test b: 0 of 3), so the levels are handed to it as a fact."""
    out = []
    # a noisy room (run 013: floor -39.6 dB) puts room tone near -34 dB: loud means over its own speech threshold there
    loud_db = edge_loud_db(audio)
    for t, before, after in cuts(cut_map(edl)):
        if before is None:
            continue  # the start of the first span is not a join
        e = edl["spans"][before.index]["t_out"]
        end_db = audio.level(e - EDGE_HALF_S, e + EDGE_HALF_S)
        start_db = None
        if after is not None:
            i = edl["spans"][after.index]["t_in"]
            start_db = audio.level(i - EDGE_HALF_S, i + EDGE_HALF_S)
        loud = max(end_db, start_db if start_db is not None else -120.0) > loud_db
        out.append({"t": round(t, 2), "end_db": end_db, "start_db": start_db, "end": after is None, "loud": loud,
                    "loud_db": loud_db})
    return out


def facts_text(silences: list[tuple[float, float]], caps: dict, duration_s: float,
               edges: list[dict] | None = None) -> str:
    sil = ", ".join(f"{a:.2f}-{b:.2f} s ({b - a:.2f} s)" for a, b in silences) or "none"
    out = [f"1. Silence gaps in the audio (ffmpeg silencedetect, below {SILENCE_DB:.0f} dB for at least {SILENCE_MIN_S} s), "
           f"cut seconds out of {duration_s:.1f}: {sil}."]
    if not caps["lines"]:
        out.append("2. The cut has no captions.")
    else:
        line = (f"2. Caption boxes against the speaker's head box tracked by SAM (1080x1920 frame, y grows downward): "
                f"{caps['lines']} caption lines, {len(caps['over_face'])} overlap the head box")
        if caps["over_face"]:
            line += ": " + "; ".join(f"{o['t_in']:.2f}-{o['t_out']:.2f} s \"{o['text']}\" (caption top y {o['caption_top']}, "
                                     f"head y {o['head_box_y'][0]}-{o['head_box_y'][1]}, {o['overlap_px']} px overlap)"
                                     for o in caps["over_face"])
        if caps["min_clearance_px"] is not None:
            line += (f". Among the others the tightest sits {caps['min_clearance_px']} px below the head "
                     f"(at {caps['min_clearance_at']:.2f} s)")
        out.append(line + ".")
    if edges is not None:
        if not edges:
            out.append("3. The cut has no joins between passages.")
        else:
            def edge(e: dict) -> str:
                if e["end"]:
                    return f"{e['t']:.2f} s (the end) {e['end_db']:.1f} dB"
                return f"{e['t']:.2f} s: passage end {e['end_db']:.1f} dB, next start {e['start_db']:.1f} dB"
            loud = [e for e in edges if e["loud"]]
            line = ("3. Speech level of the original recording exactly at each cut, on both sides of the join: "
                    + "; ".join(edge(e) for e in edges)
                    + f". Between two words the level drops below {edges[0].get('loud_db', EDGE_LOUD_DB):.1f} dB "
                      "in this recording; ")
            if loud:
                line += ("above it speech is still going at the cut, so a word is cut off there: "
                         + ", ".join(f"{e['t']:.2f} s" for e in loud) + ".")
            else:
                line += "every cut here is below it."
            out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- the copy Spark watches

def draw_clock(t: float, out: Path, size: int = 40) -> None:
    f = font(size)
    text = f"{t:4.1f}"
    box = f.getbbox("00.0")
    w, h = box[2] - box[0] + 20, box[3] - box[1] + 16
    img = Image.new("RGBA", (w, h), (0, 0, 0, 200))
    ImageDraw.Draw(img).text((10 - box[0], 8 - box[1]), text, font=f, fill=(255, 255, 255, 255))
    img.save(out)


def review_copy(cut: Path, out_dir: Path, clock: bool = True) -> Path:
    """The cut at 720x1280 with a cut-time clock (10 per second) in the top-left corner; the audio unchanged."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{cut.stem}_review.mp4"
    dur = media_duration(cut)
    if clock:
        cdir = out_dir / "clock"
        cdir.mkdir(exist_ok=True)
        n = int(math.ceil(dur * CLOCK_FPS)) + 1
        for k in range(n):
            draw_clock(k / CLOCK_FPS, cdir / f"{k:05d}.png")
        args = ["-i", str(cut), "-framerate", str(CLOCK_FPS), "-i", str(cdir / "%05d.png"), "-filter_complex",
                f"[0:v]scale={REVIEW_W}:{REVIEW_H}:flags=lanczos,setsar=1[b];[b][1:v]overlay=16:16:eof_action=pass,"
                "format=yuv420p[v]", "-map", "[v]", "-map", "0:a:0"]
    else:
        args = ["-i", str(cut), "-vf", f"scale={REVIEW_W}:{REVIEW_H}:flags=lanczos,setsar=1", "-map", "0:v:0", "-map", "0:a:0"]
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "24", "-c:a", "aac", "-b:a", "128k", "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"review copy failed: {proc.stderr[-1500:]}")
    return out


def estimate_cost(duration_s: float) -> float:
    """What one review of a cut this long costs: about 280 input tokens per second of video, about 5K out."""
    p = PRICE[SPARK_MODEL]
    return ((280 * duration_s + 2500) * p["in"] + 5000 * p["out"]) / 1_000_000


# --------------------------------------------------------------------------- where things are in the cut

@dataclass
class CutSpan:
    span: str
    index: int  # position in edl["spans"]
    segment: int
    cut_in: float
    cut_out: float


def title_seconds(edl: dict) -> float:
    title = (edl.get("render") or {}).get("title")
    return float(title["seconds"]) if title else 0.0


def cut_map(edl: dict) -> list[CutSpan]:
    """Cut seconds of every span, with the merge and frame alignment the render uses and the title card in front."""
    fps = float(edl["source"]["fps"])
    segs = frame_align(merge_segments(edl["spans"]), fps)
    by_id = {s["id"]: (k, s) for k, s in enumerate(edl["spans"])}
    t = title_seconds(edl)
    out: list[CutSpan] = []
    for seg in segs:
        for sid in seg.span_ids:
            k, s = by_id[sid]
            a = t + min(seg.duration, max(0.0, s["t_in"] - seg.t_in))
            b = t + min(seg.duration, max(0.0, s["t_out"] - seg.t_in))
            out.append(CutSpan(span=sid, index=k, segment=seg.index, cut_in=round(a, 3), cut_out=round(b, 3)))
        t += seg.duration
    return out


def span_at(cmap: list[CutSpan], t: float) -> CutSpan | None:
    """The span playing at cut second t (the nearest one in a gap or past the end)."""
    if not cmap:
        return None
    for c in cmap:
        if c.cut_in <= t < c.cut_out:
            return c
    return min(cmap, key=lambda c: min(abs(t - c.cut_in), abs(t - c.cut_out)))


def cuts(cmap: list[CutSpan]) -> list[tuple[float, CutSpan | None, CutSpan | None]]:
    """Every cut point in the cut: (cut seconds, span before, span after); the start of the first span and the end of the
    last count too (before / after None)."""
    out: list[tuple[float, CutSpan | None, CutSpan | None]] = []
    prev: CutSpan | None = None
    for c in cmap:
        if prev is None:
            out.append((c.cut_in, None, c))
        elif c.segment != prev.segment:
            out.append((c.cut_in, prev, c))
        prev = c
    if prev is not None:
        out.append((prev.cut_out, prev, None))
    return out


# --------------------------------------------------------------------------- words

def kept_elsewhere(edl: dict, span: dict) -> set[int]:
    return {i for s in edl["spans"] if s["id"] != span["id"] and s["turn_id"] == span["turn_id"]
            for i in range(s["word_from"], s["word_to"] + 1)}


def valley(span: dict, words: Words, edge: str, audio: Audio, blocked: set[int]) -> tuple[float, float, float]:
    """The quietest point within VALLEY_RANGE_S of an edge, never past half the span, never into a word another span
    keeps. Returns (new time, its dB, the current edge's dB)."""
    tw = words.turn(span["turn_id"])
    r = VALLEY_RANGE_S
    if edge == "out":
        t = span["t_out"]
        lo = max(span["t_in"] + (span["t_out"] - span["t_in"]) / 2, t - r)
        hi = t if span["word_to"] + 1 in blocked else t + r
    else:
        t = span["t_in"]
        lo = t if span["word_from"] - 1 in blocked else t - r
        hi = min(span["t_out"] - (span["t_out"] - span["t_in"]) / 2, t + r)
    x, db = audio.quietest(lo, hi, step=VALLEY_STEP_S)
    return x, db, audio.level(t - 0.02, t + 0.02)


def on_boundary(span: dict, words: Words, edge: str) -> bool:
    """The edge sits on its Voice word boundary, or where a valley fix or the edge pass put it (edge_tuned)."""
    tuned = (span.get("edge_tuned") or {}).get(edge)
    if tuned is not None and abs(span["t_out" if edge == "out" else "t_in"] - tuned) < 0.005:
        return True
    tw = words.turn(span["turn_id"])
    if edge == "out":
        return abs(span["t_out"] - tw[span["word_to"]]["t_end"]) < 0.005
    return abs(span["t_in"] - tw[span["word_from"]]["t_start"]) < 0.005


def resnap(span: dict, words: Words, edge: str) -> None:
    tw = words.turn(span["turn_id"])
    if edge == "out":
        span["t_out"] = tw[span["word_to"]]["t_end"]
    else:
        span["t_in"] = tw[span["word_from"]]["t_start"]


def shift_words(span: dict, words: Words, edge: str, seconds: float, blocked: set[int]) -> int:
    """Move one edge of the span by whole words until at least |seconds| moved: seconds > 0 moves the edge later, < 0
    earlier. Never into words another span keeps, never below 2 words. Returns the number of words moved."""
    tw = words.turn(span["turn_id"])
    moved = 0
    done = 0.0
    want = abs(seconds)
    while done < want - 1e-6:
        a, b = span["word_from"], span["word_to"]
        if edge == "out" and seconds > 0:  # extend the end
            if b + 1 >= len(tw) or b + 1 in blocked:
                break
            span["word_to"] = b + 1
            done += tw[b + 1]["t_end"] - tw[b + 1]["t_start"]
        elif edge == "out":  # trim the end
            if b - a < 2:
                break
            span["word_to"] = b - 1
            done += tw[b]["t_end"] - tw[b]["t_start"]
        elif seconds < 0:  # extend the start
            if a - 1 < 0 or a - 1 in blocked:
                break
            span["word_from"] = a - 1
            done += tw[a - 1]["t_end"] - tw[a - 1]["t_start"]
        else:  # trim the start
            if b - a < 2:
                break
            span["word_from"] = a + 1
            done += tw[a]["t_end"] - tw[a]["t_start"]
        moved += 1
    if moved:
        span["t_in"] = tw[span["word_from"]]["t_start"]
        span["t_out"] = tw[span["word_to"]]["t_end"]
    return moved


_SENTENCE_END = re.compile(r"[.!?]$")


def trim_at(span: dict, words: Words, edge: str, src_t: float, min_words: int = 2) -> int:
    """Cut the span at source time src_t: edge out keeps the words that began before it, edge in the words that end after
    it by more than TRIM_SLACK_S (a word already under way is never cut off: Voice stretches a turn's last word to the end of speech, run 012's
    "out." ran 121.9 to 124.14 s); then the edge moves to a sentence end within one word either side. Never fewer than
    min_words. Returns the number of words removed."""
    tw = words.turn(span["turn_id"])
    a, b = span["word_from"], span["word_to"]
    if edge == "out":
        keep = max(min_words, sum(1 for i in range(a, b + 1) if tw[i]["t_start"] < src_t - TRIM_SLACK_S))
        span["word_to"] = min(b, a + keep - 1)
        # the sentence end may lie one word further only when that word overlaps the trim point (left out by the slack)
        reach = max([i for i in range(a, b + 1) if tw[i]["t_start"] < src_t - REACH_S], default=span["word_to"])
        sentence_edge(span, words, "out", hi=max(span["word_to"], reach))
    else:
        keep = max(min_words, sum(1 for i in range(a, b + 1) if tw[i]["t_end"] > src_t + TRIM_SLACK_S))
        span["word_from"] = max(a, b - keep + 1)
        reach = min([i for i in range(a, b + 1) if tw[i]["t_end"] > src_t + REACH_S], default=span["word_from"])
        sentence_edge(span, words, "in", lo=min(span["word_from"], reach))
    span["t_in"] = tw[span["word_from"]]["t_start"]
    span["t_out"] = tw[span["word_to"]]["t_end"]
    return (b - a) - (span["word_to"] - span["word_from"])


def sentence_edge(span: dict, words: Words, edge: str, lo: int | None = None, hi: int | None = None,
                  min_words: int = 2) -> None:
    """Move a word-level edge to a sentence end ("Billy.") when one is the word at the edge, the next one or one or two
    before it. lo..hi bound the new word_to (edge out) or word_from (edge in). Run 012: a trim left "Hi. I'm", an
    extend left "Hi. I'm Billy. I run"."""
    tw = words.turn(span["turn_id"])
    a, b = span["word_from"], span["word_to"]
    if edge == "out":
        lo_, hi_ = max(a + min_words - 1, lo if lo is not None else 0), min(len(tw) - 1, hi if hi is not None else b + 1)
        for j in (b, b + 1, b - 1, b - 2):
            if lo_ <= j <= hi_ and _SENTENCE_END.search(tw[j]["w"]):
                span["word_to"] = j
                break
    else:
        lo_, hi_ = max(1, lo if lo is not None else a - 1), min(b - min_words + 1, hi if hi is not None else len(tw))
        for j in (a, a - 1, a + 1, a + 2):  # j opens the span: the word before it must end a sentence
            if lo_ <= j <= hi_ and _SENTENCE_END.search(tw[j - 1]["w"]):
                span["word_from"] = j
                break
    span["t_in"] = tw[span["word_from"]]["t_start"]
    span["t_out"] = tw[span["word_to"]]["t_end"]


def refresh_span(span: dict, words: Words) -> None:
    tw = words.turn(span["turn_id"])
    span["words"] = " ".join(w["w"] for w in tw[span["word_from"]:span["word_to"] + 1])
    span["duration"] = round(span["t_out"] - span["t_in"], 3)


# --------------------------------------------------------------------------- issues -> fixes

@dataclass
class Fix:
    op: str  # the patch actually applied (may differ from the reviewer's fix_op: drop_title, caption_unforce, ...)
    target: str  # span id, caption key or "title"
    issue: int  # index in review["issues"]
    severity: str
    t_s: float
    args: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        key = ",".join(f"{k}={v}" for k, v in sorted(self.args.items()) if k in ("edge", "dir", "before", "mode", "text"))
        return f"{self.op}:{self.target}:{key}"


def _caption_at(edl: dict, t: float, within: float = 0.6) -> dict | None:
    caps = edl.get("captions") or []
    # strictly inside first: a line that ends exactly where the next starts must not win (kill test b, 15.287 s)
    inside = [c for c in caps if c["t_in"] <= t < c["t_out"]] or [c for c in caps if c["t_in"] - 0.05 <= t <= c["t_out"] + 0.05]
    if inside:
        return inside[-1]
    near = [c for c in caps if min(abs(t - c["t_in"]), abs(t - c["t_out"])) <= within]
    return min(near, key=lambda c: min(abs(t - c["t_in"]), abs(t - c["t_out"]))) if near else None


def _src_time(edl: dict, cmap: list[CutSpan], span_id: str, t_cut: float) -> float:
    c = next(c for c in cmap if c.span == span_id)
    s = edl["spans"][c.index]
    return round(s["t_in"] + (t_cut - c.cut_in), 3)


def resolve(issue: dict, k: int, edl: dict, words: Words, cmap: list[CutSpan],
            audio: Audio | None = None) -> tuple[Fix | None, str]:
    """One review issue -> one concrete Fix on this EDL, or None and why not."""
    op = issue["fix_op"]
    p = issue.get("params") or {}
    t = float(issue["t_s"])
    sev = issue["severity"]
    title_s = title_seconds(edl)
    spans = edl["spans"]

    def mk(op_, target, **args) -> Fix:
        return Fix(op=op_, target=target, issue=k, severity=sev, t_s=t, args=args)

    if op == "none":
        return None, "fix_op none"
    if op in ("drop_span", "swap_hook") and title_s and t < title_s - 0.05:
        # an issue inside the card is about the card, whatever line the reviewer wants first (run 013: "first 1.5 s is
        # silence with a title card", target 27 s, swapped the opener twice and kept the card); a hook still wrong
        # without the card is asked for again in the next pass
        return mk("drop_title", "title"), ""
    cs = span_at(cmap, t)
    if cs is None:
        return None, "no span in the cut"
    span = spans[cs.index]

    if op == "drop_span":
        # the reviewer's window decides how much goes: a window over part of the span trims the words in it at that
        # end (run 012: "backstory at 4.5-7.5 s" sat in the last 3.4 s of the 6.4 s hook span, whose first words the
        # brief requires); only a window over most of the span, or a single moment, drops it whole
        t_end = max(t, float(issue.get("t_end_s", t)))
        a, b = max(t, cs.cut_in), min(t_end, cs.cut_out)
        cover = (b - a) / max(1e-6, cs.cut_out - cs.cut_in)
        if t_end > t and cover < DROP_WHOLE_COVER:
            if b >= cs.cut_out - EDGE_SLACK_S:
                return mk("trim_span", cs.span, edge="out", src_t=_src_time(edl, cmap, cs.span, a)), ""
            if a <= cs.cut_in + EDGE_SLACK_S:
                return mk("trim_span", cs.span, edge="in", src_t=_src_time(edl, cmap, cs.span, b)), ""
            return None, f"window {t:.2f}-{t_end:.2f} is inside {cs.span} ({cover:.0%}): a drop would split it"
        if span["role"] == "hook":
            return None, "the hook span is never dropped whole (trim it, or swap_hook)"
        if len(spans) <= 2:
            return None, "would leave fewer than 2 spans"
        return mk("drop_span", cs.span), ""

    if op == "swap_hook":
        tgt = float(p.get("target_t_s", -1))
        if tgt >= 0:
            ts = span_at(cmap, tgt)
            if ts is not None and ts.index == 0 and title_s and t < title_s - 0.05:
                return mk("drop_title", "title"), ""  # the right line already opens, the card in front delays it
            if ts is not None and ts.index == 0 and tgt - ts.cut_in > 0.5:
                # the strong line is inside the opening span: start the span there
                return mk("trim_span", ts.span, edge="in", src_t=_src_time(edl, cmap, ts.span, tgt - 0.05)), ""
            if ts is None or ts.index == 0:
                return None, "target already opens the cut"
            return mk("swap_hook", ts.span), ""
        hooks = [s for s in spans[1:] if s["role"] == "hook"]
        if hooks:
            return mk("swap_hook", hooks[0]["id"]), ""
        return None, "swap_hook without a target line"

    if op == "reorder":
        tgt = float(p.get("target_t_s", -1))
        ts = span_at(cmap, tgt) if tgt >= 0 else None
        if ts is None or ts.span == cs.span:
            return None, "reorder without a different target line"
        return mk("reorder", cs.span, before=ts.span), ""

    if op == "move_cut":
        near = min(cuts(cmap), key=lambda c: abs(c[0] - t))
        if abs(near[0] - t) > 1.5:
            return None, f"no cut within 1.5 s of {t:.2f} (nearest {near[0]:.2f})"
        _, before, after = near
        edge = p.get("edge") or "none"
        cands = []
        if before is not None:
            cands.append((before, "out"))
        if after is not None:
            cands.append((after, "in"))
        pick = [c for c in cands if c[1] == edge]
        if not pick:
            off = [c for c in cands if not on_boundary(spans[c[0].index], words, c[1])]
            pick = off or [c for c in cands if c[1] == "out"] or cands
        sp, edge = pick[0]
        s = spans[sp.index]
        sec = float(p.get("seconds") or 0.0)
        if not on_boundary(s, words, edge):
            return mk("move_cut", sp.span, edge=edge, mode="snap"), ""
        # a clipped word is fixed by finding the gap, never by removing words (run 012: "-0.3 s" for a loud edge
        # after "Billy." removed "Billy." itself); whole-word moves are for pacing and content
        if abs(sec) >= WORD_SHIFT_MIN_S and issue["criterion"] != "no_cut_mid_word":
            return mk("move_cut", sp.span, edge=edge, mode="words", seconds=sec), ""
        if audio is None:
            return None, "edge on its word boundary and no audio.wav to find a quieter point"
        x, db, now = valley(s, words, edge, audio, kept_elsewhere(edl, s))
        cur = s["t_out" if edge == "out" else "t_in"]
        if abs(x - cur) < 0.005 or db > now - 3.0:  # only a clearly quieter point (3 dB) is worth a re-cut
            return None, f"{edge} edge of {sp.span} is already at the quietest point ({now:.1f} dB, best {db:.1f} dB at {x:.2f})"
        return mk("move_cut", sp.span, edge=edge, mode="valley", to=x, db=db, was_db=now), ""

    if op == "extend_span":
        edge = p.get("edge") if p.get("edge") in ("in", "out") else "out"
        sec = max(WORD_SHIFT_MIN_S, abs(float(p.get("seconds") or 0.0)))
        return mk("extend_span", cs.span, edge=edge, seconds=sec if edge == "out" else -sec), ""

    if op == "caption_fix":
        cap = _caption_at(edl, t)
        if cap is None:
            return None, f"no caption line near {t:.2f}"
        sid = cap["spans"][0]
        src_a = _src_time(edl, cmap, sid, cap["words"][0]["t_in"])
        src_b = _src_time(edl, cmap, sid, cap["words"][-1]["t_out"])
        src_t = round(src_a + 0.01, 3)
        key = f"{sid}@{src_t:.2f}"
        forced = [o for o in edl.get("caption_overrides") or [] if o.get("span") == sid and o.get("top") is not None
                  and src_a - 0.02 <= float(o["src_t"]) <= src_b + 0.02]
        text = (p.get("text") or "").strip()
        if forced:
            return mk("caption_unforce", key, span=sid, src_t=float(forced[0]["src_t"])), ""
        if text and norm_tokens(text) != norm_tokens(cap["text"]):
            have, want = norm_tokens(cap["text"]), norm_tokens(text)
            ratio = difflib.SequenceMatcher(a=have, b=want).ratio()
            if len(want) > len(have) + 2 or ratio < 0.5:
                return None, f"caption text {text!r} does not look like the line {cap['text']!r}"
            return mk("caption_text", key, span=sid, src_t=src_t, text=text), ""
        x, y, w, h = cap["box"]
        head = cap.get("head_box")
        if head and overlaps([x, y, x + w, y + h], head):
            top = int(head[3] + HEAD_MARGIN + 1)
            top = top if top + h <= SAFE_BOTTOM else CAPTION_TOP
            return mk("caption_move", key, span=sid, src_t=src_t, top=top), ""
        return None, "caption_fix with nothing to change (same text, not over the face)"

    if op == "retime_callout":
        calls = [c for c in edl.get("callouts") or [] if min(abs(t - c["t_in"]), abs(t - c["t_out"])) <= 2.0
                 or c["t_in"] <= t <= c["t_out"]]
        if not calls:
            return None, f"no callout near {t:.2f}"
        call = calls[0]
        sec = max(-3.0, min(3.0, float(p.get("seconds") or 0.0)))
        text = (p.get("text") or "").strip()
        if not sec and not text:
            return None, "retime_callout with no shift and no text"
        return mk("retime_callout", call["span"], seconds=sec, text=text), ""

    if op == "reframe_bias":
        d = p.get("direction")
        if d not in ("left", "right"):
            return None, "reframe_bias without a direction"
        return mk("reframe_bias", cs.span, dir=d), ""

    return None, f"unknown fix_op {op}"


EDGE_OPS = ("move_cut", "extend_span", "trim_span")


def claims(fix: Fix) -> set[str]:
    """What a fix occupies in one pass: one edge of a span for the edge ops (the opening span's start can be trimmed
    while its end is snapped), the whole target otherwise. Overlapping claims conflict."""
    if fix.op in EDGE_OPS:
        return {f"{fix.target}:{fix.args['edge']}"}
    return {f"{fix.target}:in", f"{fix.target}:out", f"{fix.target}:*"}


def plan_fixes(review: dict, edl: dict, words: Words, audio: Audio | None = None) -> tuple[list[Fix], list[dict]]:
    """Blocker and major issues -> at most MAX_FIXES fixes, worst first, one per target (per edge for edge ops); the
    rest with the reason."""
    cmap = cut_map(edl)
    order = sorted(range(len(review.get("issues") or [])),
                   key=lambda k: (SEVERITY_RANK.get(review["issues"][k]["severity"], 3), review["issues"][k]["t_s"]))
    fixes: list[Fix] = []
    skipped: list[dict] = []
    taken: set[str] = set()
    for k in order:
        issue = review["issues"][k]
        if issue["severity"] not in APPLY_SEVERITY:
            skipped.append({"issue": k, "reason": f"severity {issue['severity']}"})
            continue
        fix, why = resolve(issue, k, edl, words, cmap, audio)
        if fix is None:
            skipped.append({"issue": k, "reason": why})
        elif claims(fix) & taken:
            skipped.append({"issue": k, "reason": f"{fix.target} already fixed in this pass"})
        elif len(fixes) >= MAX_FIXES:
            skipped.append({"issue": k, "reason": f"more than {MAX_FIXES} fixes in one pass"})
        else:
            fixes.append(fix)
            taken |= claims(fix)
    return fixes, skipped


# --------------------------------------------------------------------------- fixes -> next EDL

STRUCTURAL = ("drop_span", "swap_hook", "reorder")


def settle(span: dict, words: Words, edge: str, audio: Audio | None, blocked: set[int]) -> str:
    """After a word-level edit the new edge sits on a Voice word boundary, which can be up to 0.3 s inside speech (run
    012: the trim to "Hi. I'm Billy." ended at -25.1 dB). Move it to the quietest point nearby when that is clearly
    quieter. Returns a note, empty when nothing moved."""
    if audio is None:
        return ""
    x, db, now = valley(span, words, edge, audio, blocked)
    key = "t_out" if edge == "out" else "t_in"
    if abs(x - span[key]) < 0.005 or db > now - 3.0:
        return ""
    before = span[key]
    span[key] = x
    span.setdefault("edge_tuned", {})[edge] = x
    return f"; edge settled {before:.3f} -> {x:.3f} ({now:.1f} -> {db:.1f} dB)"


def apply_fixes(edl: dict, fixes: list[Fix], words: Words, audio: Audio | None = None) -> tuple[dict, list[dict]]:
    """A new EDL (version + 1) with the fixes applied; edge fixes first, then drops and moves. Edges moved by whole
    words settle on the quietest point nearby and are then tightened to the speech (edges.py), when the source audio
    is there. The render-time fields (captions, callouts, reframe, render) are cleared: the next render fills them.
    Returns (edl, log)."""
    new = copy.deepcopy(edl)
    log: list[dict] = []
    touched: dict[tuple[str, str], int] = {}  # (span, edge) put back on a Voice word boundary -> its log entry
    by_id = {s["id"]: s for s in new["spans"]}
    overrides = new.setdefault("caption_overrides", [])
    for f in sorted(fixes, key=lambda f: f.op in STRUCTURAL):
        s = by_id.get(f.target)
        note = ""
        if f.op == "drop_title":
            new["title_card"] = False
            note = "title card dropped"
        elif f.op == "move_cut":
            edge = f.args["edge"]
            if f.args["mode"] == "snap":
                before = s["t_out"] if edge == "out" else s["t_in"]
                resnap(s, words, edge)
                note = f"{edge} edge snapped to its word boundary {before:.3f} -> {s['t_out' if edge == 'out' else 't_in']:.3f}"
            elif f.args["mode"] == "words":
                n = shift_words(s, words, edge, f.args["seconds"], kept_elsewhere(new, s))
                note = (f"{edge} edge moved by {n} words ({f.args['seconds']:+.2f} s asked)"
                        + settle(s, words, edge, audio, kept_elsewhere(new, s)))
            else:
                key = "t_out" if edge == "out" else "t_in"
                before = s[key]
                s[key] = f.args["to"]
                s.setdefault("edge_tuned", {})[edge] = f.args["to"]
                note = (f"{edge} edge moved to the quietest point {before:.3f} -> {f.args['to']:.3f} "
                        f"({f.args['was_db']:.1f} -> {f.args['db']:.1f} dB)")
        elif f.op == "trim_span":
            n = trim_at(s, words, f.args["edge"], f.args["src_t"])
            note = (f"{n} words trimmed at the {f.args['edge']} end, keeps: "
                    f"{' '.join(w['w'] for w in words.turn(s['turn_id'])[s['word_from']:s['word_to'] + 1])[:60]}"
                    + settle(s, words, f.args["edge"], audio, kept_elsewhere(new, s)))
        elif f.op == "extend_span":
            orig = (s["word_from"], s["word_to"])
            blocked = kept_elsewhere(new, s)
            n = shift_words(s, words, f.args["edge"], f.args["seconds"], blocked)
            if n:  # land on a sentence end, never giving back the words the extension was for
                last = len(words.turn(s["turn_id"])) - 1
                if f.args["edge"] == "out":
                    nxt = s["word_to"] + 1
                    sentence_edge(s, words, "out", lo=orig[1] + 1,
                                  hi=nxt if nxt <= last and nxt not in blocked else s["word_to"])
                else:
                    prv = s["word_from"] - 1
                    sentence_edge(s, words, "in", hi=orig[0] - 1,
                                  lo=prv if prv >= 0 and prv not in blocked else s["word_from"])
            note = f"{n} words added at the {f.args['edge']} end" + settle(s, words, f.args["edge"], audio, kept_elsewhere(new, s))
        elif f.op == "drop_span":
            new["spans"] = [x for x in new["spans"] if x["id"] != f.target]
            overrides[:] = [o for o in overrides if o.get("span") != f.target]
            note = f"{f.target} dropped: {s['words'][:60]}"
        elif f.op == "swap_hook":
            for x in new["spans"]:
                if x["role"] == "hook":
                    x["role"] = "body"
            s["role"] = "hook"
            new["spans"] = [s] + [x for x in new["spans"] if x["id"] != f.target]
            note = f"{f.target} opens the cut: {s['words'][:60]}"
        elif f.op == "reorder":
            rest = [x for x in new["spans"] if x["id"] != f.target]
            at = next(i for i, x in enumerate(rest) if x["id"] == f.args["before"])
            new["spans"] = rest[:at] + [s] + rest[at:]
            note = f"{f.target} moved before {f.args['before']}"
        elif f.op == "caption_unforce":
            overrides[:] = [o for o in overrides if not (o.get("span") == f.args["span"] and o.get("top") is not None
                                                          and abs(float(o["src_t"]) - f.args["src_t"]) < 0.01)]
            note = "forced caption position removed"
        elif f.op == "caption_move":
            overrides.append({"span": f.args["span"], "src_t": f.args["src_t"], "top": f.args["top"], "why": "review"})
            note = f"caption moved to y {f.args['top']}"
        elif f.op == "caption_text":
            overrides.append({"span": f.args["span"], "src_t": f.args["src_t"], "text": f.args["text"], "why": "review"})
            note = f"caption text -> {f.args['text']!r}"
        elif f.op == "retime_callout":
            if f.args["seconds"]:
                s["callout_shift_s"] = round(float(s.get("callout_shift_s") or 0.0) + f.args["seconds"], 2)
            if f.args["text"]:
                s["callout"] = f.args["text"]
            note = f"callout shift {s.get('callout_shift_s', 0)} s, text {s['callout']!r}"
        elif f.op == "reframe_bias":
            s["reframe_bias_px"] = int(s.get("reframe_bias_px") or 0) + (BIAS_PX if f.args["dir"] == "right" else -BIAS_PX)
            note = f"crop bias {s['reframe_bias_px']:+d} px"
        if f.op in ("trim_span", "extend_span") or (f.op == "move_cut" and f.args["mode"] in ("snap", "words")):
            # trim_at / shift_words / resnap put BOTH edges back on Voice boundaries (012-ktb: a trim at the start
            # re-opened the end tightened out of the next word), so both are tightened again
            for e in ("in", "out"):
                touched.setdefault((f.target, e), len(log))
        log.append({**asdict(f), "fingerprint": f.fingerprint, "note": note})
    finalize(new, words)
    if audio is not None and touched:
        for m in tighten_edges(new, words, audio, only=set(touched))["moves"]:
            log[touched[(m["span"], m["edge"])]]["note"] += (f"; edge tightened to the speech {m['from']:.3f} -> "
                                                            f"{m['to']:.3f}")
        for s in new["spans"]:
            refresh_span(s, words)
    return new, log


def finalize(edl: dict, words: Words) -> None:
    """Order, durations, contiguity and runtime after a patch; render-time fields cleared."""
    for k, s in enumerate(edl["spans"]):
        s["order"] = k
        refresh_span(s, words)
        s["contiguous_with_previous"] = False
    for prev, cur in zip(edl["spans"], edl["spans"][1:]):
        if cur["turn_id"] == prev["turn_id"] and cur["word_from"] == prev["word_to"] + 1:
            cur["contiguous_with_previous"] = True
    edl["runtime_s"] = round(sum(s["duration"] for s in edl["spans"]), 2)
    edl["version"] = int(edl.get("version", 1)) + 1
    for k in ("captions", "callouts", "reframe"):
        edl[k] = []
    edl.pop("render", None)


# --------------------------------------------------------------------------- one review

def review_render(run_dir: Path, edl: dict, cut: Path, version: int, ledger: Ledger, spark, brief: str | None = None,
                  clock: bool = True, log=print, name: str | None = None) -> dict:
    """Review cut_vN.mp4 blind: facts, review copy, upload, Spark 1.3. Writes review_vN.json and returns it."""
    name = name or f"review_v{version}"
    rdir = run_dir / "review" / name
    with Timer() as t:
        duration = media_duration(cut)
        sil = silences_in(cut)
        caps = caption_face_facts(edl)
        audio = Audio.load(run_dir)
        edges = cut_edge_levels(edl, audio) if audio else None
        facts = facts_text(sil, caps, duration, edges)
        copy_path = review_copy(cut, rdir, clock=clock)
        up = upload_file(copy_path, "video/mp4")
    ledger.add(Entry(stage="review.prep", model="ffmpeg+files", seconds=t.seconds, cost_usd=0.0,
                     note=f"{name}: {copy_path.name} {copy_path.stat().st_size / 1e6:.1f} MB, file {up.get('id')}, "
                          f"{len(sil)} silences, {len(caps['over_face'])} captions over the face, "
                          f"{sum(e['loud'] for e in edges or [])} loud cuts"))
    log(f"review: {name} facts:\n{facts}")
    call = spark.review(up["id"], brief or edl["brief"], facts, duration, name=name)
    rv = call.parsed
    log(f"review: {name} overall {rv['overall']} pass {rv['pass']} ({len(rv['issues'])} issues), ${call.cost_usd:.4f}")
    for i in sorted(rv["issues"], key=lambda i: i["t_s"]):
        log(f"  {i['t_s']:6.2f}s {i['severity']:7s} {i['criterion']:22s} {i['fix_op']:14s} {i['what'][:90]}")
    out = {"name": name, "version": version, "cut": cut.as_posix(), "review_copy": copy_path.as_posix(),
           "file_id": up.get("id"), "clock": clock, "duration_s": duration, "facts": {"silences": sil, "captions": caps, "cut_edges": edges},
           "facts_text": facts, "review": rv, "cost_usd": call.cost_usd, "seconds": call.seconds,
           "response_id": call.response_id}
    dump_json(run_dir / f"{name}.json", out)
    return out


# --------------------------------------------------------------------------- the loop

def edl_version(path: Path, edl: dict) -> int:
    m = re.search(r"_v(\d+)\.json$", path.name)
    return int(m.group(1)) if m else int(edl.get("version", 1))


def review_loop(run_dir: Path, edl_path: Path | None = None, passes: int = MAX_PASSES, budget_usd: float = BUDGET_USD,
                ledger: Ledger | None = None, log=print, clock: bool = True, apply: bool = True, spark=None,
                render_fn=None, review_fn=None) -> dict:
    """review cut_vN -> patch -> edl_vN+1 -> render cut_vN+1 -> review ..., until the cut passes, `passes` reviews are
    done, the budget would be exceeded, no fix applies, or a fix repeats. Writes review/loop.json."""
    from ..run import render_run  # run.py imports the engine; the loop needs its render entry point
    from .spark import Spark
    edl_path = edl_path or run_dir / "edl_v1.json"
    ledger = ledger or Ledger.resume(run_dir / "ledger.json")
    if review_fn is None:
        spark = spark or Spark(run_dir, ledger, log=log)
        review_fn = lambda edl_, cut_, n_: review_render(run_dir, edl_, cut_, n_, ledger, spark, clock=clock, log=log)
    render_fn = render_fn or (lambda ep, out: render_run(run_dir, edl_path=ep, out_name=out, ledger=ledger, log=log))
    words = Words(json.loads((run_dir / "voice" / "voice.json").read_text())["words"])
    audio = Audio.load(run_dir)
    edl = json.loads(edl_path.read_text())
    n = edl_version(edl_path, edl)
    seen: dict[str, int] = {}
    history: list[dict] = []
    spent = 0.0
    stop = "max passes"
    for k in range(passes):
        cut = run_dir / f"cut_v{n}.mp4"
        rendered = Path((edl.get("render") or {}).get("output", "")).name
        if not cut.is_file() or rendered != cut.name:
            log(f"review: rendering {edl_path.name} -> {cut.name}")
            render_fn(edl_path, cut.name)
            edl = json.loads(edl_path.read_text())
        est = estimate_cost((edl.get("render") or {}).get("duration_s") or edl["runtime_s"])
        if spent + est > budget_usd:
            stop = f"budget: ${spent:.3f} spent + ${est:.3f} for the next review > ${budget_usd:.2f}"
            break
        rv = review_fn(edl, cut, n)
        spent += rv["cost_usd"]
        fixes, skipped = plan_fixes(rv["review"], edl, words, audio)
        entry = {"version": n, "edl": edl_path.name, "cut": cut.name, "overall": rv["review"]["overall"],
                 "pass": rv["review"]["pass"], "scores": rv["review"]["scores"], "issues": len(rv["review"]["issues"]),
                 "cost_usd": rv["cost_usd"], "fixes": [{**asdict(f), "fingerprint": f.fingerprint} for f in fixes],
                 "skipped": skipped}
        history.append(entry)
        rv["fixes"], rv["skipped"] = entry["fixes"], skipped
        dump_json(run_dir / f"{rv['name']}.json", rv)
        if rv["review"]["pass"]:
            stop = "pass"
            break
        best_before = max(history[:-1], key=lambda h: h["overall"]) if len(history) > 1 else None
        if best_before and entry["overall"] < best_before["overall"]:
            stop = (f"regression: v{n} scored {entry['overall']} < v{best_before['version']} {best_before['overall']}, "
                    f"the best stays v{best_before['version']}")
            break
        if k == passes - 1:
            stop = f"max passes ({passes})"
            break
        if not apply:
            stop = "review only (--no-apply)"
            break
        if not fixes:
            stop = "no applicable fix"
            break
        again = [f.fingerprint for f in fixes if f.fingerprint in seen]
        if again:
            stop = f"same fix twice: {', '.join(again)} (first applied in v{seen[again[0]]})"
            break
        for f in fixes:
            seen[f.fingerprint] = n
        new, patch_log = apply_fixes(edl, fixes, words, audio)
        new.setdefault("patches", []).append({"from": n, "review": rv["name"], "fixes": patch_log})
        for p in patch_log:
            log(f"patch v{n} -> v{n + 1}: {p['op']} {p['target']}: {p['note']}")
        entry["applied"] = patch_log
        n += 1
        edl_path = run_dir / f"edl_v{n}.json"
        dump_json(edl_path, new)
        edl = new
    best = max(history, key=lambda h: h["overall"]) if history else None
    summary = {"stop": stop, "passes": history, "reviews": len(history), "review_cost_usd": round(spent, 4),
               "best": {"version": best["version"], "cut": best["cut"], "overall": best["overall"]} if best else None,
               "last_edl": edl_path.name, "budget_usd": budget_usd}
    dump_json(run_dir / "review" / "loop.json", summary)
    log(f"review loop: {len(history)} reviews, stop: {stop}; scores "
        + " -> ".join(f"v{h['version']} {h['overall']}" for h in history) + f"; ${spent:.4f}")
    return summary
