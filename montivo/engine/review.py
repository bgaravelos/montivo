"""Review: Spark 1.3 judges its own render blind, the patcher turns its fix ops into the next EDL, the loop re-renders.

The reviewer gets the rendered cut, the brief and the rubric, never the EDL (design rule of 26/10). Two deterministic
facts go with it as text: the silence gaps ffmpeg `silencedetect` finds in the cut, and where every caption box sits
against the SAM head box (from the EDL `captions` entries the render wrote). A third fact was added after kill test (b):
the source audio level right at both edges of every cut, because Spark never heard a word clipped by a cut (0 of 3 runs).
A fourth is the caption timeline, every line's text and cut times, because Spark sees about one frame per second: a
0.4 s line ("Hi." opening run 012-open's cut_v3) fell between its frames and was flagged as missing. The copy Spark watches is the cut scaled
to 720x1280 with a small clock burned in at the top left, because Spark sees about one frame per second with no
timestamps: the clock is the only way its t_s can land within a second of the fault. A fifth, optional fact is the
words heard in the cut's own audio (Muse Voice on the cut, `heard_words`): Muse Spark Open sees frames but hears nothing
(27/10, Baseten), so a review on it gets the speech as text, and its copy goes inline instead of through the Files API.

After the reviewer benchmark (27/10, runs/bench) two deterministic checks go to every reviewer, from the same Voice
transcript of the cut: the kept words at every cut edge that are not heard where speech still runs at the edge
(`edge_words_unheard`), and the stretches where the followed head leaves the 9:16 crop (`head_frame_facts`, the crop
path re-planned as the render plans it); Spark 1.3 also gets the caption-vs-heard diff, without the heard phrases.
A pass gate (`gate_check`) then overrides the reviewer: a cut does not pass while a loud cut edge, an unheard edge word,
a forced caption over the head or the head out of frame for 0.5 s is measured, and each of those becomes a major issue
that `plan_fixes` patches even when the reviewer missed it (`review.pass` is the gated pass, `reviewer_pass` its own).
The gate also holds the pass while the reviewer itself reports a blocker or major issue (`close_gate`, Billy's rule of
27/10): its prompt passes a cut at 80 with no blocker, and 36 % of the bench's faulty cuts passed with a major open.
brief_covered does not count, because a brief can ask for a line nobody said (run 015).

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
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from ..config import PRICE, SPARK_MODEL, is_open_model
from ..ledger import Entry, Ledger, Timer
from .api import dump_json, upload_file
from .captions import CAPTION_TOP, DEDUPE_GAP_S, HEAD_MARGIN, SAFE_BOTTOM, overlaps
from .edges import Audio, Words, level_db, tighten_edges  # noqa: F401 (level_db re-exported for callers)
from .edl import norm, norm_tokens
from .graphics import font
from .ingest import make_wav
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
                         "placement": c.get("placement"),
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


def checked_captions(edl: dict) -> set[str]:
    """Ids of the caption lines a person retexted against the recording (a `caption_overrides` entry with why "human":
    the editor's Captions panel or `montivo.human caption`). Their words outrank what a speech model hears in the cut:
    run 015's "Four Meta models do all", which Voice hears as "meta models do all", held v5's pass as a reviewer major
    built on the caption-vs-heard fact (28/10)."""
    human = [o for o in edl.get("caption_overrides") or [] if o.get("why") == "human" and o.get("text")]
    if not human or not edl.get("captions"):
        return set()
    cmap = {c.span: c for c in cut_map(edl)}
    spans = {s["id"]: s for s in edl["spans"]}
    out: set[str] = set()
    for o in human:
        c = cmap.get(o.get("span"))
        if c is None:
            continue
        cap = _caption_at(edl, c.cut_in + float(o["src_t"]) - spans[c.span]["t_in"], within=0.0)
        if cap is not None and o["span"] in cap["spans"]:
            out.add(cap["id"])
    return out


def caption_timeline(edl: dict) -> list[dict]:
    """Every caption line as it is on screen: text and cut seconds (from the EDL `captions` entries the render wrote);
    `checked` on a line a person retexted (checked_captions)."""
    checked = checked_captions(edl)
    return [{"t_in": c["t_in"], "t_out": c["t_out"], "text": c["text"], **({"checked": True} if c.get("id") in checked
                                                                          else {})}
            for c in edl.get("captions") or []]


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
               edges: list[dict] | None = None, timeline: list[dict] | None = None,
               heard: list[dict] | None = None, edge_words: dict | None = None, head: dict | None = None,
               diff_heard: list[dict] | None = None) -> str:
    """The facts handed to the reviewer, numbered. `heard` adds the heard phrases and the caption diff (a reviewer that
    cannot hear); `diff_heard` adds only the caption diff (Spark 1.3: in the benchmark the phrases cost it the weak
    hook, 4/4 -> 2/4, while the diff brought caption text 4/6 -> 6/6)."""
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
    if edge_words is not None:
        miss = edge_words["unheard"]
        line = (f"{len(out) + 1}. The words this cut keeps on both sides of every cut ({edge_words['edge_words']} words "
                f"at {edge_words['cuts']} cuts) against what a speech model hears in the cut's own audio (the same model "
                "transcribed the original, so a word missing here was cut off at the edge, not misheard): ")
        if miss:
            line += ("; ".join(f"\"{m['w']}\" ({'end of the passage before' if m['edge'] == 'out' else 'start of the passage after'} "
                               f"the cut at {m['cut_t']:.2f} s) " + (f"heard as \"{m['heard']}\"" if m["heard"] else "not heard")
                               for m in miss)
                     + ". A word that is not heard there is clipped by the cut.")
        else:
            line += "every one of them is heard."
        out.append(line)
    if head is not None:
        long_ = [r for r in head["out_of_frame"] if r["seconds"] >= HEAD_OUT_MIN_S]
        line = (f"{len(out) + 1}. The speaker's head (the SAM head box the crop follows) against the 9:16 frame, "
                f"checked 10 times a second: ")
        if long_:
            line += ("out of frame (more than " f"{HEAD_OUT_FRAC:.0%} of the head outside) at "
                     + "; ".join(f"{r['t_in']:.2f}-{r['t_out']:.2f} s ({r['seconds']:.1f} s, up to {r['max_out']:.0%} "
                                 f"outside, on the {r['side']})" for r in long_) + ".")
        else:
            line += f"inside the frame throughout (at most {head['max_out']:.0%} of its width outside)."
        out.append(line)
    if timeline:
        out.append(f"{len(out) + 1}. Caption lines exactly as they are on screen, cut seconds: "
                   + "; ".join(f"{c['t_in']:.2f}-{c['t_out']:.2f} s \"{c['text']}\"" for c in timeline)
                   + ". You see about one frame per second, so a line up for less than a second can fall between "
                     "your frames: a line in this list is on screen even when you did not see it.")
    if heard is not None:
        phrases = heard_phrases(heard)
        out.append(f"{len(out) + 1}. Words heard in this cut's own audio (a speech model transcribed the cut, cut seconds; "
                   "what is said and where the speech pauses): "
                   + ("; ".join(f"{h['t_in']:.2f}-{h['t_out']:.2f} s \"{h['text']}\"" for h in phrases) or "no speech")
                   + ". That speech model mishears names and similar-sounding words and can add a stray word at a cut.")
    diff_words = heard if heard is not None else diff_heard
    if diff_words is not None and timeline:
        diffs = caption_heard_diff(timeline, diff_words)
        heard_what = "the heard words" if heard is not None else "the words a speech model hears in the cut's own audio"
        checked = [c for c in timeline if c.get("checked")]
        out.append(f"{len(out) + 1}. Where the caption words and {heard_what} differ (the two aligned word by word): "
                   + ("; ".join(f"{d['t']:.2f} s caption \"{d['caption']}\" / heard \"{d['heard'] or '(nothing)'}\""
                                for d in diffs) or "nowhere")
                   + ". The captions were checked by hand where the speech model got a name wrong, so a pair that "
                     "sounds alike (\"Muse\" / \"news\") is the speech model's mistake, not the caption's: a caption "
                     "is wrong only where its word sounds nothing like what was heard."
                   + (" A person checked these lines against the recording, so they are right as written and are left "
                      "out of the comparison: " + "; ".join(f"{c['t_in']:.2f} s \"{c['text']}\"" for c in checked) + "."
                      if checked else ""))
    return "\n".join(out)


def _tok(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def caption_heard_diff(timeline: list[dict], heard: list[dict]) -> list[dict]:
    """Caption words against the heard words, aligned as two word sequences (difflib), every replaced or unheard caption
    word with its cut time. No similarity threshold: whether a pair sounds alike is left to the reviewer, which knows
    "Muse" / "news" rhyme and "Google" / "voice" do not (character ratios rank those two pairs the other way round).
    Lines a person checked (`checked`) take part in the alignment (so the heard words under them stay paired with
    them) but their differences are not reported."""
    cap = [(_tok(w), c["t_in"], bool(c.get("checked"))) for c in timeline for w in c["text"].split() if _tok(w)]
    hw = [_tok(w["w"]) for w in sorted(heard, key=lambda w: w["t_start"]) if _tok(w["w"])]
    sm = difflib.SequenceMatcher(None, [c[0] for c in cap], hw, autojunk=False)
    out = []
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "replace" and a2 - a1 == b2 - b1:  # as many words on both sides: pair them one to one
            out += [{"t": cap[a][1], "caption": cap[a][0], "heard": hw[b]} for a, b in zip(range(a1, a2), range(b1, b2))
                    if not cap[a][2]]
        elif op in ("replace", "delete"):
            mine = [c for c in cap[a1:a2] if not c[2]]
            if mine:
                out.append({"t": mine[0][1], "caption": " ".join(c[0] for c in mine), "heard": " ".join(hw[b1:b2])})
    return out


HEARD_GAP_S = 0.3  # a pause this long starts a new phrase in the heard-words fact
HEARD_MAX_WORDS = 8


def heard_words(cut: Path, out_dir: Path, ledger: Ledger, log=print) -> list[dict]:
    """What a viewer hears: Muse Voice on the cut's own audio, the words with cut times (about $0.002 for 40 s).
    Cached in out_dir/voice.json. On run 015 it heard "news voice", "soundtracks", "Newspark" again: the same
    mishearings the captions were corrected for by hand, hence the caution in the fact."""
    from .voice import VoiceResult, transcribe  # voice imports nothing from review; kept local like the cost helpers

    out_dir.mkdir(parents=True, exist_ok=True)
    cached = out_dir / "voice.json"
    if cached.is_file():
        res = VoiceResult.from_json(json.loads(cached.read_text()))
    else:
        wav = make_wav(cut, out_dir / "cut.wav")
        res = transcribe(wav, out_dir, ledger, log=log)
    return dedupe_heard([{"w": w.w, "t_start": w.t_start, "t_end": w.t_end} for w in res.words])


def dedupe_heard(words: list[dict], max_gap_s: float = DEDUPE_GAP_S) -> list[dict]:
    """Voice doubles words at random ("run run", "I I"), the artifact the captions drop (captions.dedupe_words): a
    word identical to the one before it within max_gap_s goes. Without this the open reviewer read every doubled word
    on run 015 as a repeated take (four major pacing issues on a clean cut, 27/10)."""
    out: list[dict] = []
    for w in sorted(words, key=lambda w: w["t_start"]):
        if out and _tok(w["w"]) == _tok(out[-1]["w"]) and w["t_start"] - out[-1]["t_end"] < max_gap_s:
            continue
        out.append(w)
    return out


def heard_phrases(words: list[dict], gap_s: float = HEARD_GAP_S, max_words: int = HEARD_MAX_WORDS) -> list[dict]:
    phrases: list[dict] = []
    cur: list[dict] = []
    for w in sorted(words, key=lambda w: w["t_start"]):
        if cur and (w["t_start"] - cur[-1]["t_end"] >= gap_s or len(cur) >= max_words):
            phrases.append(cur)
            cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    return [{"t_in": round(p[0]["t_start"], 2), "t_out": round(p[-1]["t_end"], 2), "text": " ".join(w["w"] for w in p)}
            for p in phrases]


# --------------------------------------------------------------------------- deterministic checks and the pass gate
# The reviewer benchmark (27/10, runs/bench) measured what Spark 1.3 misses: a word clipped at a cut (2 of 6; the 015
# cut 45 % into "Montivo" measured -42.7 dB, under the edge-level line), a head pushed out of the crop on a wide shot
# (4 of 6) and a wrong caption word (4 of 6); 30 % of faulty cuts still passed. These checks measure the first two
# without a model, and the gate keeps a cut from passing while one of them (or the loud-edge and caption-over-head
# facts above) reports a fault, whatever the reviewer said.

EDGE_WORDS = 2  # kept words checked on each side of a cut
HEARD_SIMILAR = 0.75  # a heard word this close (difflib ratio) to the kept one is the same word heard differently
HEAD_OUT_FRAC = 0.4  # a head with more than this share of its width outside the crop is out of frame (run 013's
                     # lean reached 31 % for 0.6 s on its kept cut; the planted framing faults 100 %)
HEAD_OUT_MIN_S = 0.5  # the gate blocks an out-of-frame stretch at least this long
PASS_IGNORES = ("brief_covered",)  # reviewer majors that do not hold the pass: 015's brief asks for a line never said
HEAD_SAMPLE_FRAMES = 3  # the head is checked every 3rd frame (10 per second at 30 fps)


def kept_words(edl: dict, words: Words) -> list[dict]:
    """The Voice words the EDL keeps, in cut order with cut seconds (from the source Voice transcript, the words the
    planner chose), each tagged with the cut edge it sits at: `edge` "out" for the last EDGE_WORDS before a join or the
    end, "in" for the first EDGE_WORDS after a join or at the start, None inside a passage."""
    cmap = cut_map(edl)
    edge_of: dict[tuple[str, str], float] = {}  # (span, "in"/"out") -> the cut point's time
    for t, before, after in cuts(cmap):
        if before is not None:
            edge_of[(before.span, "out")] = t
        if after is not None:
            edge_of[(after.span, "in")] = t
    out: list[dict] = []
    for c in cmap:
        s = edl["spans"][c.index]
        tw = words.turn(s["turn_id"])
        idx = list(range(s["word_from"], min(s["word_to"], len(tw) - 1) + 1))
        for n, i in enumerate(idx):
            w = tw[i]
            a = min(c.cut_out, max(c.cut_in, c.cut_in + w["t_start"] - s["t_in"]))
            b = min(c.cut_out, max(c.cut_in, c.cut_in + w["t_end"] - s["t_in"]))
            edge = cut_t = None
            if (c.span, "out") in edge_of and n >= len(idx) - EDGE_WORDS:
                edge, cut_t = "out", edge_of[(c.span, "out")]
            elif (c.span, "in") in edge_of and n < EDGE_WORDS:
                edge, cut_t = "in", edge_of[(c.span, "in")]
            out.append({"w": w["w"], "t_start": round(a, 3), "t_end": round(b, 3), "span": c.span, "edge": edge,
                        "cut_t": round(cut_t, 3) if cut_t is not None else None})
    return out


def _same_word(kept: str, heard: str) -> bool:
    """The heard word is the kept one heard (differently): close spelling, or a word that contains it or starts like it
    and is at least as long. A clipped word comes back shorter or not at all; run 012 heard "Montivo" as "montevideo"
    and run 015 "news" + "Spark" as "newspark" with the whole word there."""
    if not heard:
        return False
    return (kept == heard or difflib.SequenceMatcher(None, kept, heard).ratio() >= HEARD_SIMILAR or kept in heard
            or (len(heard) >= len(kept) and heard[:3] == kept[:3]))


def edge_words_unheard(kept: list[dict], heard: list[dict], speech_at=None) -> dict:
    """Kept words at the cut edges that Voice does not hear in the cut. Both word lists come from the same speech
    model (the source transcript and the cut's own), so a name it mishears it mostly mishears the same way twice; a
    word missing from the cut's transcript, or heard as a different word, may have been cut off at the edge. The two
    lists are aligned as word sequences (difflib), doubles dropped on both sides first (dedupe_heard).
    `speech_at(cut_t, edge)` -> (source dB at that edge, speech going on there): a word is only cut off where speech
    still runs at the edge, so a missing word at an edge in silence is the speech model's slip (run 012-open: "then"
    heard in one transcript of the same audio and not in another, its edge at -49.5 dB under the clip's -45.1 dB
    threshold) and goes to `quiet`; the 015 cut inside "Montivo" sat at -42.7 dB, over that clip's -50.4 dB."""
    kept = dedupe_heard(kept)
    hw = [w for w in dedupe_heard(heard) if _tok(w["w"])]
    kt = [_tok(w["w"]) for w in kept]
    ht = [_tok(w["w"]) for w in hw]
    got: list[str | None] = [None] * len(kept)
    sm = difflib.SequenceMatcher(None, kt, ht, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and a2 - a1 == b2 - b1):
            for a, b in zip(range(a1, a2), range(b1, b2)):
                got[a] = ht[b]
        elif op == "replace":  # unequal blocks: each kept word takes the heard word nearest in time
            for a in range(a1, a2):
                mid = (kept[a]["t_start"] + kept[a]["t_end"]) / 2
                b = min(range(b1, b2), key=lambda b: abs((hw[b]["t_start"] + hw[b]["t_end"]) / 2 - mid))
                if abs((hw[b]["t_start"] + hw[b]["t_end"]) / 2 - mid) < 0.6:
                    got[a] = ht[b]
    at_edges = [k for k, w in enumerate(kept) if w["edge"] and kt[k]]
    ok = {k: _same_word(kt[k], got[k] or "") for k in at_edges}
    missing: list[dict] = []
    quiet: list[dict] = []
    for k in at_edges:
        # only words the cut can have clipped: those with no heard word between them and the cut (014: Voice drops a
        # quick "smoked" in 3 of 6 transcripts of the same audio while "turkey", the last word before the cut, is heard)
        nearer = [j for j in at_edges if kept[j]["cut_t"] == kept[k]["cut_t"] and kept[j]["edge"] == kept[k]["edge"]
                  and (j > k if kept[k]["edge"] == "out" else j < k)]
        if ok[k] or any(ok[j] for j in nearer):
            continue
        db, speech = speech_at(kept[k]["cut_t"], kept[k]["edge"]) if speech_at else (None, True)
        (missing if speech else quiet).append(
            {"w": kept[k]["w"], "t": kept[k]["t_start"], "cut_t": kept[k]["cut_t"], "edge": kept[k]["edge"],
             "span": kept[k]["span"], "heard": got[k] or "", "edge_db": db})
    return {"edge_words": len(at_edges), "cuts": len({kept[k]["cut_t"] for k in at_edges}), "unheard": missing,
            "quiet": quiet}


def edge_speech(edl: dict, audio: Audio):
    """speech_at for edge_words_unheard: the source level over 40 ms centred on that edge of the cut, and whether it is
    over the clip's speech threshold (edges.Audio, floor + 6 dB)."""
    at: dict[tuple[float, str], float] = {}
    for t, before, after in cuts(cut_map(edl)):
        if before is not None:
            e = edl["spans"][before.index]["t_out"]
            at[(round(t, 3), "out")] = audio.level(e - EDGE_HALF_S, e + EDGE_HALF_S)
        if after is not None:
            i = edl["spans"][after.index]["t_in"]
            at[(round(t, 3), "in")] = audio.level(i - EDGE_HALF_S, i + EDGE_HALF_S)
    thr = audio.threshold_db()

    def speech_at(cut_t: float, edge: str) -> tuple[float | None, bool]:
        db = at.get((round(cut_t, 3), edge))
        return (db, True) if db is None else (round(db, 1), db > thr)
    return speech_at


def crop_paths(run_dir: Path, edl: dict) -> tuple[str | None, list, list]:
    """The crop path of every rendered segment, planned exactly as render.render plans it (same track, speaker map,
    pins and reframe bias): (concept, segments, paths). Deterministic and offline."""
    from .reframe import plan_segment
    from .render import apply_bias, follow_pieces, load_reframe_track, load_speaker_ids, segment_bias
    concept, track = load_reframe_track(run_dir, edl)
    speaker_ids = load_speaker_ids(run_dir, concept, edl)
    segs = frame_align(merge_segments(edl["spans"]), float(edl["source"]["fps"]))
    paths = []
    for seg in segs:
        path = plan_segment(seg.t_in, seg.t_out, edl["source"], track, span_ids=seg.span_ids,
                            follow=follow_pieces(seg, edl["spans"], speaker_ids))
        path.xs = path.xs[:seg.frames] + [path.xs[-1]] * max(0, seg.frames - len(path.xs))
        bias = segment_bias(edl["spans"], seg.span_ids)
        if bias:
            apply_bias(path, bias, int(edl["source"]["width"]))
        paths.append(path)
    return concept, segs, paths


def head_frame_facts(run_dir: Path, edl: dict) -> dict | None:
    """Where the followed head leaves the 9:16 crop: the SAM head box of the head the crop follows (the path's track id,
    per speaker piece; the largest head when the crop follows the person box) against the crop window of every
    sampled frame. Stretches with more than HEAD_OUT_FRAC of the head's width outside, in cut seconds, with the side
    it leaves on and the shift (source px, median over the stretch) that centres it again: a fix that only brought it
    back inside left run 012's planted fault at the frame edge (bias 420 -> 120). None without a head track.
    Punch-in zooms are not modelled (they stay clear of a head near the top by design, punch.py)."""
    from .captions import interp_box, largest_at
    from .reframe import id_at
    from .render import load_track
    head = load_track(run_dir, edl, "head")
    if not head or not head.get("objects"):
        return None
    concept, segs, paths = crop_paths(run_dir, edl)
    fx = float(edl["source"]["width"]) / float(head["source"]["width"])
    fy = float(edl["source"]["height"]) / float(head["source"]["height"])
    objs = {oid: [{"t": r["t"], "box": [r["box"][0] * fx, r["box"][1] * fy, r["box"][2] * fx, r["box"][3] * fy]}
                  for r in recs] for oid, recs in head["objects"].items()}
    t0 = title_seconds(edl)
    samples: list[dict] = []
    for seg, path in zip(segs, paths):
        step = HEAD_SAMPLE_FRAMES / seg.fps
        for k in range(0, len(path.xs), HEAD_SAMPLE_FRAMES):
            src = seg.t_in + k / seg.fps
            if concept == "head":
                hid = id_at(path, src)
                box = interp_box(objs[hid], src) if hid in objs else None
            else:
                box = (largest_at({"objects": objs}, src) or [None])[0]
            if box is None or box[2] - box[0] <= 1:
                continue
            x0, x1 = path.xs[k], path.xs[k] + path.crop_w
            width = box[2] - box[0]
            left, right = max(0.0, x0 - box[0]), max(0.0, box[2] - x1)
            frac = min(1.0, (left + right) / width)
            shift = (box[0] + box[2]) / 2 - (x0 + path.crop_w / 2)
            samples.append({"t": round(t0 + k / seg.fps, 2), "step": step, "frac": frac,
                            "side": "left" if left >= right else "right", "shift": shift, "segment": seg.index})
        t0 += seg.duration
    out_runs: list[dict] = []
    for s in samples:
        if s["frac"] <= HEAD_OUT_FRAC:
            continue
        cur = out_runs[-1] if out_runs else None
        if cur and cur["segment"] == s["segment"] and cur["side"] == s["side"] and s["t"] - cur["t_out"] <= 1.5 * s["step"]:
            cur["t_out"] = round(s["t"] + s["step"], 2)
            cur["fracs"].append(s["frac"])
            cur["shifts"].append(s["shift"])
        else:
            out_runs.append({"t_in": s["t"], "t_out": round(s["t"] + s["step"], 2), "side": s["side"],
                             "segment": s["segment"], "fracs": [s["frac"]], "shifts": [s["shift"]]})
    stretches = []
    for r in out_runs:
        shifts = sorted(r.pop("shifts"))
        fr = r.pop("fracs")
        stretches.append({**r, "seconds": round(r["t_out"] - r["t_in"], 2), "max_out": round(max(fr), 2),
                          "shift_px": int(round(shifts[len(shifts) // 2] / 2)) * 2})
    return {"concept": concept, "samples": len(samples), "max_out": round(max((s["frac"] for s in samples), default=0.0), 2),
            "out_of_frame": stretches}


def gate_check(caps: dict, edges: list[dict] | None, edge_words: dict | None, head: dict | None) -> dict:
    """The pass gate: every fault a deterministic check reports, as a reason and as an issue in the reviewer's form
    (severity major, so plan_fixes turns it into a patch even when the reviewer called it minor or missed it).
    Faults: a cut edge inside speech (the level fact), a kept edge word not heard where speech runs at the edge, a
    caption over the head that the renderer could have placed clear of it (forced there), the head out of frame for
    HEAD_OUT_MIN_S or more."""
    reasons: list[str] = []
    issues: list[dict] = []

    def issue(t: float, t_end: float, criterion: str, op: str, what: str, check: str, **params) -> None:
        p = {"edge": "none", "seconds": 0, "target_t_s": -1, "text": "", "direction": "none", **params}
        issues.append({"t_s": round(t, 2), "t_end_s": round(t_end, 2), "criterion": criterion, "severity": "major",
                       "what": what, "fix_op": op, "params": p, "fix_args": "", "check": check})

    for e in edges or []:
        if not e["loud"]:
            continue
        sides = [s for s, db in (("out", e["end_db"]), ("in", e["start_db"])) if db is not None and db > e["loud_db"]]
        reasons.append(f"cut at {e['t']:.2f} s inside speech ({' and '.join(sides) or 'out'} edge over {e['loud_db']:.1f} dB)")
        for side in sides or ["out"]:
            issue(e["t"], e["t"], "no_cut_mid_word", "move_cut", f"speech still going at the {side} edge of the cut",
                  "loud_edge", edge=side)
    for m in (edge_words or {}).get("unheard") or []:
        heard = f"heard as \"{m['heard']}\"" if m["heard"] else "not heard"
        reasons.append(f"\"{m['w']}\" at the cut at {m['cut_t']:.2f} s {heard}")
        issue(m["cut_t"], m["cut_t"], "no_cut_mid_word", "move_cut", f"kept word \"{m['w']}\" {heard} in the cut",
              "edge_word", edge=m["edge"])
    for o in caps.get("over_face") or []:
        if o.get("placement") == "over_head":
            continue  # the renderer found no clear place (run 013: the head fills the frame), a patch cannot move it
        reasons.append(f"caption over the head at {o['t_in']:.2f}-{o['t_out']:.2f} s")
        issue((o["t_in"] + o["t_out"]) / 2, (o["t_in"] + o["t_out"]) / 2, "captions_legible", "caption_fix",
              f"caption \"{o['text']}\" over the head", "caption_face")
    for r in (head or {}).get("out_of_frame") or []:
        if r["seconds"] < HEAD_OUT_MIN_S:
            continue
        reasons.append(f"head out of frame {r['t_in']:.2f}-{r['t_out']:.2f} s ({r['max_out']:.0%} outside, {r['side']})")
        issue((r["t_in"] + r["t_out"]) / 2, r["t_out"], "subject_framed", "reframe_bias",
              f"head leaves the crop on the {r['side']}", "head_frame", direction=r["side"], px=abs(r["shift_px"]))
    return {"blocked": bool(reasons), "reasons": reasons, "issues": issues}


def close_gate(review: dict, gate: dict) -> dict:
    """The gated pass: the reviewer's own pass, no measured fault (gate_check) and no blocker or major issue of the
    reviewer still open (PASS_IGNORES aside, and captions_match_speech on a line a person checked: the gate's
    `checked_captions`, cut seconds). Sets review["pass"] (keeps review["reviewer_pass"]) and returns the gate
    with `checks` (the measured reasons), `open_majors` and `reasons` = both. Idempotent, so the bench re-scores stored
    reviews with it; a gate written before the rule has only measured reasons."""
    checks = list(gate.get("checks", gate.get("reasons") or []))
    checked = gate.get("checked_captions") or []

    def on_checked_line(i: dict) -> bool:  # a person's caption outranks the reviewer's ear (checked_captions)
        return i["criterion"] == "captions_match_speech" and any(a - 0.3 <= i["t_s"] <= b + 0.3 for a, b in checked)

    majors = [{"t_s": i["t_s"], "criterion": i["criterion"], "severity": i["severity"]}
              for i in sorted(review["issues"], key=lambda i: i["t_s"])
              if i["severity"] in APPLY_SEVERITY and i["criterion"] not in PASS_IGNORES and not on_checked_line(i)]
    reasons = checks + [f"reviewer's {m['severity']} still open: {m['criterion']} at {m['t_s']:.2f} s" for m in majors]
    review.setdefault("reviewer_pass", bool(review["pass"]))
    review["pass"] = bool(review["reviewer_pass"]) and not reasons
    return {**gate, "blocked": bool(reasons), "reasons": reasons, "checks": checks, "open_majors": majors}


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
    check: str = ""  # the deterministic check behind it (gate_check), empty for a reviewer issue

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
        if text and norm_tokens(text) != norm_tokens(cap["text"]) and cap.get("id") in checked_captions(edl):
            return None, f"a person checked the line {cap['text']!r}: its words stay"
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
        return mk("reframe_bias", cs.span, dir=d, **({"px": int(p["px"])} if p.get("px") else {})), ""

    return None, f"unknown fix_op {op}"


EDGE_OPS = ("move_cut", "extend_span", "trim_span")


def claims(fix: Fix) -> set[str]:
    """What a fix occupies in one pass: one edge of a span for the edge ops (the opening span's start can be trimmed
    while its end is snapped), the whole target otherwise. Overlapping claims conflict."""
    if fix.op in EDGE_OPS:
        return {f"{fix.target}:{fix.args['edge']}"}
    return {f"{fix.target}:in", f"{fix.target}:out", f"{fix.target}:*"}


def plan_fixes(review: dict, edl: dict, words: Words, audio: Audio | None = None,
               gate: dict | None = None) -> tuple[list[Fix], list[dict]]:
    """Blocker and major issues -> at most MAX_FIXES fixes, worst first, one per target (per edge for edge ops); the
    rest with the reason. `gate` (gate_check) adds the checks' issues after the reviewer's (issue indices past
    the reviewer's count are the gate's); at equal severity a check's fix goes first, since it is measured."""
    cmap = cut_map(edl)
    issues = list(review.get("issues") or []) + list((gate or {}).get("issues") or [])
    order = sorted(range(len(issues)),
                   key=lambda k: (SEVERITY_RANK.get(issues[k]["severity"], 3), not issues[k].get("check"), issues[k]["t_s"]))
    fixes: list[Fix] = []
    skipped: list[dict] = []
    taken: set[str] = set()
    for k in order:
        issue = issues[k]
        if issue["severity"] not in APPLY_SEVERITY:
            skipped.append({"issue": k, "reason": f"severity {issue['severity']}"})
            continue
        fix, why = resolve(issue, k, edl, words, cmap, audio)
        if fix is not None and issue.get("check"):
            fix.check = issue["check"]
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


def conflict(edl: dict, f: Fix) -> str:
    """Why a structural fix no longer fits the EDL the earlier fixes of the same pass made, "" when it does: each was
    resolved against the reviewed EDL (Codex review F05: drop s2 then "move s3 before s2", or two drops leaving only
    the hook)."""
    if f.op not in STRUCTURAL:
        return ""
    ids = {x["id"]: x for x in edl["spans"]}
    if f.target not in ids:
        return f"{f.target} was dropped by an earlier fix in this pass"
    if f.op == "reorder" and f.args.get("before") and f.args["before"] not in ids:
        return f"{f.args['before']} was dropped by an earlier fix in this pass"
    if f.op == "drop_span" and len(ids) <= 2:
        return "would leave fewer than 2 spans"
    if f.op == "drop_span" and ids[f.target]["role"] == "hook":
        return "the hook span is never dropped whole"
    return ""


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
        if why := conflict(new, f):
            log.append({**asdict(f), "fingerprint": f.fingerprint, "note": f"not applied: {why}", "skipped": True})
            continue
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
        elif f.op == "reorder":  # before None: to the end (a human drag past the last clip)
            rest = [x for x in new["spans"] if x["id"] != f.target]
            at = next(i for i, x in enumerate(rest) if x["id"] == f.args["before"]) if f.args.get("before") else len(rest)
            new["spans"] = rest[:at] + [s] + rest[at:]
            note = f"{f.target} moved before {f.args['before']}" if f.args.get("before") else f"{f.target} moved to the end"
        elif f.op == "caption_unforce":
            overrides[:] = [o for o in overrides if not (o.get("span") == f.args["span"] and o.get("top") is not None
                                                          and abs(float(o["src_t"]) - f.args["src_t"]) < 0.01)]
            note = "forced caption position removed"
        elif f.op == "caption_move":
            overrides.append({"span": f.args["span"], "src_t": f.args["src_t"], "top": f.args["top"], "why": "review"})
            note = f"caption moved to y {f.args['top']}"
        elif f.op == "caption_text":
            overrides.append({"span": f.args["span"], "src_t": f.args["src_t"], "text": f.args["text"],
                              "why": "human" if f.severity == "human" else "review"})
            note = f"caption text -> {f.args['text']!r}"
        elif f.op == "retime_callout":
            if f.args["seconds"]:
                s["callout_shift_s"] = round(float(s.get("callout_shift_s") or 0.0) + f.args["seconds"], 2)
            if f.args["text"]:
                s["callout"] = f.args["text"]
            note = f"callout shift {s.get('callout_shift_s', 0)} s, text {s['callout']!r}"
        elif f.op == "reframe_bias":
            # the render takes a segment's mean bias (render.segment_bias): every span of the segment moves, else a
            # segment of three spans moved a third of the step
            step = int(f.args.get("px") or BIAS_PX) * (1 if f.args["dir"] == "right" else -1)
            seg = next((g.span_ids for g in merge_segments(new["spans"]) if f.target in g.span_ids), [f.target])
            for x in new["spans"]:
                if x["id"] in seg:
                    x["reframe_bias_px"] = int(x.get("reframe_bias_px") or 0) + step
            note = f"crop bias {s['reframe_bias_px']:+d} px" + (f" ({len(seg)} spans of the segment)" if len(seg) > 1 else "")
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
                  clock: bool = True, log=print, name: str | None = None, heard: bool | None = None,
                  heard_dir: Path | None = None) -> dict:
    """Review cut_vN.mp4 blind: facts, checks, review copy, upload, Spark, pass gate. Writes review_vN.json and returns
    it. Every reviewer gets the deterministic checks (kept edge words heard, head in frame) and the caption-vs-heard
    diff, from Muse Voice on the cut (cached in `heard_dir`, default review/<name>/heard, about $0.002 per 40 s).
    A Muse Spark Open reviewer (spark.model) gets the copy inline and, since it cannot hear, the heard phrases too;
    `heard` True or False forces that fact either way. `review.pass` is the gated pass (the reviewer's own in
    `review.reviewer_pass`); `pass_gate` holds the reasons and the issues plan_fixes applies."""
    name = name or f"review_v{version}"
    rdir = run_dir / "review" / name
    open_model = is_open_model(spark.model)
    heard = open_model if heard is None else heard
    log(f"review: {name}: facts, review copy and upload ...")  # the UI's progress: prep starts here, not at the facts
    with Timer() as t:
        duration = media_duration(cut)
        sil = silences_in(cut)
        caps = caption_face_facts(edl)
        audio = Audio.load(run_dir)
        edges = cut_edge_levels(edl, audio) if audio else None
        timeline = caption_timeline(edl)
        words_heard = heard_words(cut, heard_dir or rdir / "heard", ledger, log=log)
        vpath = run_dir / "voice" / "voice.json"
        edge_words = (edge_words_unheard(kept_words(edl, Words(json.loads(vpath.read_text())["words"])), words_heard,
                                         edge_speech(edl, audio) if audio else None)
                      if vpath.is_file() else None)
        head = head_frame_facts(run_dir, edl)
        facts = facts_text(sil, caps, duration, edges, timeline, words_heard if heard else None, edge_words=edge_words,
                           head=head, diff_heard=words_heard)
        gate = gate_check(caps, edges, edge_words, head)
        gate["checked_captions"] = [[c["t_in"], c["t_out"]] for c in timeline if c.get("checked")]
        copy_path = review_copy(cut, rdir, clock=clock)
        up = {"id": None} if open_model else upload_file(copy_path, "video/mp4")
    ledger.add(Entry(stage="review.prep", model="ffmpeg" if open_model else "ffmpeg+files", seconds=t.seconds, cost_usd=0.0,
                     note=f"{name}: {copy_path.name} {copy_path.stat().st_size / 1e6:.1f} MB, file {up.get('id')}, "
                          f"{len(sil)} silences, {len(caps['over_face'])} captions over the face, "
                          f"{sum(e['loud'] for e in edges or [])} loud cuts, "
                          f"{len((edge_words or {}).get('unheard') or [])} edge words not heard, "
                          f"{sum(1 for r in (head or {}).get('out_of_frame') or [] if r['seconds'] >= HEAD_OUT_MIN_S)} "
                          f"head-out stretches"))
    log(f"review: {name} facts:\n{facts}")
    call = spark.review(up["id"], brief or edl["brief"], facts, duration, name=name,
                        video=copy_path if open_model else None, hears=not open_model)
    rv = call.parsed
    rv["reviewer_pass"] = bool(rv["pass"])
    gate = close_gate(rv, gate)
    log(f"review: {name} overall {rv['overall']} pass {rv['pass']} ({len(rv['issues'])} issues), ${call.cost_usd:.4f}"
        + (f"; gate blocks the reviewer's pass: {'; '.join(gate['reasons'])}" if gate["blocked"] and rv["reviewer_pass"]
           else f"; gate: {'; '.join(gate['reasons'])}" if gate["blocked"] else ""))
    for i in sorted(rv["issues"], key=lambda i: i["t_s"]):
        log(f"  {i['t_s']:6.2f}s {i['severity']:7s} {i['criterion']:22s} {i['fix_op']:14s} {i['what'][:90]}")
    out = {"name": name, "version": version, "cut": cut.as_posix(), "review_copy": copy_path.as_posix(),
           "file_id": up.get("id"), "clock": clock, "duration_s": duration,
           "reviewer": spark.model,
           "facts": {"silences": sil, "captions": caps, "cut_edges": edges, "caption_timeline": timeline,
                     "heard": words_heard, "edge_words": edge_words, "head_frame": head},
           "facts_text": facts, "review": rv, "pass_gate": gate, "cost_usd": call.cost_usd, "seconds": call.seconds,
           "response_id": call.response_id}
    dump_json(run_dir / f"{name}.json", out)
    return out


# --------------------------------------------------------------------------- the loop

def rank(h: dict) -> tuple[bool, bool, float]:
    """How the loop compares versions: a version that passes beats one that does not (the loop never stops on a pass
    and keeps a failed version), then one no measured check blocks beats one a check blocks, then the reviewer's score
    (an open reviewer major already costs score, so it does not rank twice; old loop.json has no `checks`)."""
    return (bool(h.get("pass")), not h.get("checks", h.get("gate")), h["overall"])


def edl_version(path: Path, edl: dict) -> int:
    m = re.search(r"_v(\d+)\.json$", path.name)
    return int(m.group(1)) if m else int(edl.get("version", 1))


def review_loop(run_dir: Path, edl_path: Path | None = None, passes: int = MAX_PASSES, budget_usd: float = BUDGET_USD,
                ledger: Ledger | None = None, log=print, clock: bool = True, apply: bool = True, spark=None,
                render_fn=None, review_fn=None) -> dict:
    """review cut_vN -> patch -> edl_vN+1 -> render cut_vN+1 -> review ..., until the cut passes, `passes` reviews are
    done, the budget would be exceeded, no fix applies, or a fix repeats. Writes review/loop.json."""
    from ..human import next_version  # human.py imports the engine; this one only reads file names
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
        gate = rv.get("pass_gate")
        fixes, skipped = plan_fixes(rv["review"], edl, words, audio, gate)
        entry = {"version": n, "edl": edl_path.name, "cut": cut.name, "overall": rv["review"]["overall"],
                 "pass": rv["review"]["pass"], "reviewer_pass": rv["review"].get("reviewer_pass", rv["review"]["pass"]),
                 "gate": (gate or {}).get("reasons") or [], "checks": (gate or {}).get("checks", (gate or {}).get("reasons")) or [],
                 "scores": rv["review"]["scores"], "issues": len(rv["review"]["issues"]),
                 "cost_usd": rv["cost_usd"], "fixes": [{**asdict(f), "fingerprint": f.fingerprint} for f in fixes],
                 "skipped": skipped}
        history.append(entry)
        rv["fixes"], rv["skipped"] = entry["fixes"], skipped
        dump_json(run_dir / f"{rv['name']}.json", rv)
        if rv["review"]["pass"]:
            stop = "pass"
            break
        best_before = max(history[:-1], key=rank) if len(history) > 1 else None
        if best_before and rank(entry) < rank(best_before):
            stop = (f"regression: v{n} scored {entry['overall']}{' (gated)' if entry['gate'] else ''} < "
                    f"v{best_before['version']} {best_before['overall']}, the best stays v{best_before['version']}")
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
        new, patch_log = apply_fixes(edl, fixes, words, audio)
        if all(p.get("skipped") for p in patch_log):
            stop = "no applicable fix"
            break
        for p in patch_log:
            if not p.get("skipped"):
                seen[p["fingerprint"]] = n
        new.setdefault("patches", []).append({"from": n, "review": rv["name"], "fixes": patch_log})
        nxt = next_version(run_dir)  # past every version in the folder: reviewing v1 of v1-v5 makes v6, never a new v2
        for p in patch_log:
            log(f"patch v{n} -> v{nxt}: {p['op']} {p['target']}: {p['note']}")
        entry["applied"] = patch_log
        n = nxt
        edl_path = run_dir / f"edl_v{n}.json"
        dump_json(edl_path, new)
        edl = new
    best = max(history, key=rank) if history else None
    summary = {"stop": stop, "passes": history, "reviews": len(history), "review_cost_usd": round(spent, 4),
               "best": {"version": best["version"], "cut": best["cut"], "overall": best["overall"]} if best else None,
               "last_edl": edl_path.name, "budget_usd": budget_usd}
    prior = run_dir / "review" / "loop.json"
    if prior.is_file():  # a later review (the editor's "Review this version") keeps the agent loop's history
        shutil.copy2(prior, prior.with_name(f"loop_{time.strftime('%Y%m%d-%H%M%S', time.localtime(prior.stat().st_mtime))}.json"))
    dump_json(prior, summary)
    log(f"review loop: {len(history)} reviews, stop: {stop}; scores "
        + " -> ".join(f"v{h['version']} {h['overall']}" for h in history) + f"; ${spent:.4f}")
    return summary
