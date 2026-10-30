"""Ask Montivo: the chat's agent. One message -> the edits it asks for -> one new version, rendered once.

    python -m montivo.agent runs/015 --message "put some lo-fi music under it" [--from 5] [--review 1] [--planner open]
    python -m montivo.agent runs/015 --message "make this shorter" --scope '{"clips": ["s3"]}' --preview
    python -m montivo.agent runs/015 --apply PENDING_ID [--review 1]        # run a previewed proposal
    python -m montivo.agent runs/015 --retry PENDING_ID --preview            # "Try something else": route once more

One muse-spark-1.3 call (strict json_schema, no video, about $0.005) reads the message next to the current version
(clips, captions on screen, title card, look, music, punch-ins, the last review's notes) and picks ops from a closed
list. Each op is one of the human edits in montivo/human.py (music, style, punch, caption, text, drop, reorder,
replan), so the agent can do exactly what the editor's own buttons do and nothing else. The server checks every id
first; an op the EDL refuses is skipped with its reason. The ops run with human.DEFER set: each writes its edl_vN
unrendered, the chain is folded into one version (one patch, `action` the op's own when there is one, else "ask",
with an `agent` record: message, reply, ops, router) and that version is rendered once, with the review loop when
asked. A message that asks a question or needs an answer gets a reply and no version.

With --preview (the chat sends it) a structural change (re-plan, drop, reorder, cut words) or ops estimated over
PREVIEW_USD are not run: the routed ops go to ui/pending/<id>.json and the record carries a `pending` card (what
changes in plain words, cost and time) for the editor's Apply or Try something else. A re-plan is not planned for the
card (the plan is the costly part): Apply plans it once. --retry routes the same message again with the turned-down
proposal in the prompt, once and without a review. Cheap edits (music, look, captions, callouts, title, punch-ins) run
at once as before.

--scope is what the editor selected (clips, words of the transcript, a caption line, the playhead): the router reads it
as what "this" means, and `check` refuses an op that names a clip, word or line outside it.

Without MODEL_API_KEY (or with --no-model) a keyword reader stands in for the call: music on or off, punch-ins on or
off, a caption preset by name, the title card on or off, and deleting or moving the clips or words named or selected.
Anything else needs the key.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path

from . import human
from .config import OPEN_MODEL, SPARK_MODEL
from .engine import music as music_mod
from .engine.api import dump_json
from .engine.graphics import PRESET_NAMES, PRESETS
from .engine.review import caption_timeline, cut_map
from .ledger import Ledger
from .run import RunLog

OPS = ["music_on", "music_off", "style", "punch", "caption", "callout", "title_text", "title_on", "title_off", "overlay",
       "drop", "reorder", "cut", "trim", "split", "insert", "pauses", "glossary", "speakers", "pin", "restore", "replan",
       "review", "seek", "select", "open"]
SPAN_OPS = {"caption", "callout", "drop", "reorder", "cut", "trim", "split", "insert", "overlay", "select"}  # they name clips or lines of this version: a re-plan makes new ones
STRUCTURAL = {"replan", "drop", "reorder", "cut", "trim", "insert", "pauses", "restore"}  # what is said or in which order: previewed before it runs
UI_OPS = {"seek", "select", "open"}  # the editor does these (the playhead, a selection, a panel): no version
PANELS = ["media", "captions", "style", "text", "music", "speakers", "versions", "export", "loop"]
HISTORY_TURNS = 8  # chat lines the router reads before the message
UNUSED_WORDS = 900  # words of the recording outside the cut the router reads (about 95 s of speech)
COST_USD = 0.006  # one router call (015 v5 context: about 4K tokens in, 300 out)
PREVIEW_USD = 0.02  # ops estimated over this are previewed too (Billy, 28/10)
# rough prices and times of what an op calls (server/app.py COST: Spark 1.3 plan $0.05 in 35 s, Spark Open $0.012 in
# 7 s, a Muse Image title card $0.01 in 10 s, a look from words $0.008, a blind review $0.035 in about 34 s with its
# prep; a render about 0.5 s per cut second on the Mac, less with the segment cache)
EST = {"plan": 0.05, "plan_open": 0.012, "plan_s": 35, "plan_open_s": 7, "image": 0.01, "image_s": 10, "look": 0.008,
       "look_s": 6, "review": 0.035, "review_s": 34, "render_s_per_s": 0.5}

OP_FIELDS = {
    "op": {"type": "string", "enum": OPS},
    "span": {"type": "string", "description": "clip id (s3) for callout, drop, reorder, cut, trim, split, select; "
                                              "else empty"},
    "word_from": {"type": "integer", "description": "cut: the first word index to delete (the clip's numbered "
                                                    "words); insert: the first word of the passage (its turn's "
                                                    "numbers); select: the first word to select; else -1"},
    "word_to": {"type": "integer", "description": "cut / insert / select: the last word index; else -1"},
    "word": {"type": "integer", "description": "split: the word the second piece starts with; trim: the word the "
                                               "clip now starts with (edge in) or ends with (edge out); else -1"},
    "edge": {"type": "string", "description": "trim: in (the start) or out (the end); else empty"},
    "turn": {"type": "integer", "description": "insert: the turn of the passage (from 'Not in the cut'); else -1"},
    "before": {"type": "string", "description": "reorder: the clip it moves in front of, empty = to the end; insert: "
                                                "the clip it goes in front of, 'end' = at the end, empty = where it "
                                                "was said"},
    "text": {"type": "string", "description": "caption: the new words of the line; callout: its text, empty "
                                              "removes it; title_text: the title card's words; overlay: what the "
                                              "still shows, in English"},
    "at_s": {"type": "number", "description": "caption: a cut second inside that line; overlay: the cut second the "
                                              "still starts; seek: the cut second to show; else -1"},
    "track": {"type": "string", "description": "music_on: a track id from the list"},
    "level_db": {"type": "number", "description": "music_on: bed loudness in LUFS, -30 quiet to -14 loud; 0 keeps "
                                                  "the current or default (-18)"},
    "on": {"type": "boolean", "description": "punch: true turns the punch-in zooms on, false off"},
    "preset": {"type": "string", "description": "style: a preset id, or empty when look_prompt describes it"},
    "look_prompt": {"type": "string", "description": "style: the caption look in words (colours, font, size, "
                                                     "position) when no preset fits; else empty"},
    "instruction": {"type": "string", "description": "replan: the editor's note for the planner, in English"},
    "version": {"type": "integer", "description": "restore: the version to bring back; else -1"},
    "speaker": {"type": "string", "description": "speakers / pin: the Voice speaker label (A, B...); else empty"},
    "name": {"type": "string", "description": "speakers: the person's name; else empty"},
    "head": {"type": "string", "description": "pin: the head id the crop follows, empty = back to the speaker "
                                              "map; else empty"},
    "heard": {"type": "string", "description": "glossary: the word or words as Voice heard them (as the captions "
                                               "show them); else empty"},
    "written": {"type": "string", "description": "glossary: how they are written in every caption; else empty"},
    "panel": {"type": "string", "description": "open: one of " + ", ".join(PANELS) + "; else empty"}}
OP_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(OP_FIELDS), "properties": OP_FIELDS}

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reply", "question", "ops"],
          "properties": {
              "reply": {"type": "string", "description": "one or two sentences to the editor, in the language of "
                                                         "the message: what you are changing, or the answer"},
              "question": {"type": "string", "description": "a question back when the message cannot be done as "
                                                            "asked; else empty"},
              "ops": {"type": "array", "items": OP_SCHEMA}}}

PROMPT = """You are Montivo, an agent-first video editor for short vertical clips. The editor talks to you in the chat \
(English, Greek or both). Turn the message into ops on the current version. You can only use the ops below; each \
is something the editor can do with a button, so if no op does what is asked, say so in the reply and use no op.

Ops (fill the fields the op uses, leave the others empty, -1, 0 or false):
- music_on: a music bed under the speech, ducked while anyone talks (track, level_db). Also to change the track or level.
- music_off: take the music out.
- style: the look of the captions and callouts. preset = one of the presets, or look_prompt = the look in words.
- punch: punch-in zooms on emphasised words and jump cuts (on).
- caption: change the words of one caption line on screen (at_s inside the line, text = the full new line).
- glossary: a word Voice mishears everywhere, fixed in every caption (heard = as the captions show it, written = how \
it is spelled). Use it when the same mishearing is in more than one line, or the editor says "everywhere" / "always".
- callout: the text pill on a clip (span, text; empty text removes it).
- title_text: new words for the title card at the start (a new Muse Image card). title_on / title_off: the card on or off.
- overlay: a B-roll still made by Muse Image over 2.5 s of the cut (at_s = the cut second it starts, text = what the \
picture shows, in English, e.g. "a flow diagram of the four models"). Only when the editor asks for an image, a \
picture, a diagram or B-roll; never on your own.
- drop: delete one clip (span). reorder: move a clip (span) in front of another (before) or to the end.
- cut: delete words inside one clip (span, word_from, word_to: indices of that clip's numbered words below). Each \
piece left keeps at least two words. Use it to shorten a selected clip or remove a phrase, a filler or a retake.
- trim: move one edge of a clip to a word (span, edge in|out, word = the clip's new first word for in, its new last \
word for out; the word may lie outside the clip, in the same turn, to lengthen it).
- split: cut one clip in two in front of a word (span, word); nothing is removed.
- insert: put back a passage the cut does not use (turn, word_from, word_to from "Not in the cut", at least two \
words; before = a clip id to place it in front of, "end", or empty for where it was said).
- pauses: shorten every pause in the cut; no word is removed.
- speakers: give a Voice speaker a name (speaker, name). pin: the vertical crop follows one head while a speaker \
talks (speaker, head; empty head = back to automatic).
- restore: bring an earlier version back as the newest (version). "Undo", "go back", "the one before" mean the \
version before the last change (the chat history and the versions list say which).
- replan: plan the paper edit again (which passages, their order, the length, the opening or the ending) with the \
editor's note in instruction. Use it for a change to what is said or how long the whole cut is; never together with \
caption, callout, drop, reorder, cut, trim, split or insert (the plan makes new clips).
- review: one blind review of the new version by the reviewer (about $0.04). Only when asked for a review or a score.
- seek: move the playhead to a cut second (at_s) to show the editor a moment. select: select a clip (span) or words \
in it (span, word_from, word_to) so the editor sees them. open: open a panel of the editor (panel).

Rules:
- Use only the clip ids, word numbers, turns, versions, speakers, heads, track ids and preset ids listed below. Pick a \
track by its mood when none is named (talking head, upbeat tech demo: something light) and name it in the reply.
- No op for what the version already has (music on that track, punch-ins already on or off, the same preset): \
say so in the reply instead.
- A question about the cut or the footage (what score, why, how long, what was left out, who says what) gets an \
answer in reply from the facts below and no edit op. "Where do I say X" / "show me X": seek to the clip's cut second \
and select those words, and say the time in the reply as m:ss (0:22). A passage that is not in the cut has no cut second: say so \
and offer to insert it.
- Read the chat so far: "no", "another one", "the same for", "undo that", "more", "less" refer to what was just asked \
or done there.
- Prefer doing a sensible default over asking. Ask (question, no ops) only when the message cannot be done safely.
- When the editor selected something, "this", "these", "it" and "here" mean the selection: change only what is \
selected (cut, drop, callout or caption on those clips, words or line) and leave every other clip and setting as it is.
- reply speaks to the editor in the language of the message, plainly, no more than two sentences.

The current version (v{version}):
{context}

Music tracks (id: name, mood): {tracks}
Caption presets (id: name): {presets}
{history}{scope}{note}
The editor's message: "{message}"
"""


def numbered(run_dir: Path, span: dict) -> str:
    """A clip's words with their turn indices ("12 Hi 13 I'm ..."), what a cut op names; "" without Voice's words."""
    try:
        tw = human._words(run_dir).turn(span["turn_id"])
        return " ".join(f"{w['i']} {w['w']}" for w in tw if span["word_from"] <= w["i"] <= span["word_to"])
    except (OSError, KeyError, TypeError, ValueError):
        return ""


def context(run_dir: Path, edl: dict) -> str:
    """The version as the router reads it: runtime and target, every clip with its cut seconds, words and callout, the
    caption lines on screen, title card, look, music, punch-ins and the last review's notes."""
    run = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").is_file() else {}
    lines = [f"runtime {edl.get('runtime_s')} s, target {run.get('target_s', 40)} s, frame "
             f"{(edl.get('target') or {}).get('aspect', '9:16')}, brief: {run.get('brief', '')!r}"]
    try:
        times = {c.span: (c.cut_in, c.cut_out) for c in cut_map(edl)}
    except (KeyError, TypeError, ValueError):
        times = {}
    names, merged = edl.get("speaker_names") or {}, edl.get("speaker_merge") or {}
    if names or merged:  # SP1, SP2: who the speakers are, as the person named them
        lines.append("Speakers: " + "; ".join([f"{k} is {v}" for k, v in names.items()]
                                              + [f"{k} is the same person as {v}" for k, v in merged.items()]))
    lines.append("Clips in order:")
    for s in edl["spans"]:
        t = times.get(s["id"])
        at = f" cut {t[0]:.1f}-{t[1]:.1f} s" if t else ""
        call = f", callout {s['callout']!r}" if (s.get("callout") or "").strip() else ""
        spk = str(merged.get(str(s.get("speaker")), s.get("speaker")))
        who = f" {names.get(spk, spk)}" if s.get("speaker") is not None and (names or merged) else ""
        why = f" (why: {str(s['why'])[:90]})" if s.get("why") else ""
        lines.append(f"- {s['id']} [{s.get('role', 'body')}{who}]{at}: {s.get('words', '')!r}{call}{why}")
        if nw := numbered(run_dir, s):
            lines.append(f"  numbered words: {nw}")
    caps = caption_timeline(edl)
    if caps:
        lines.append("Caption lines on screen (cut seconds): " + "; ".join(
            f"{c['t_in']:.1f}-{c['t_out']:.1f} {c['text']!r}" for c in caps[:60]))
    card = edl.get("title_card", True) is not False and edl.get("title_card_text")
    lines.append(f"Title card: {repr(edl.get('title_card_text')) + ' (on)' if card else 'off'}")
    look = edl.get("look") or {}
    lines.append(f"Caption look: preset {look.get('preset', 'marker')}"
                 + (f", {json.dumps({k: v for k, v in look.items() if k != 'preset'})}" if len(look) > 1 else ""))
    m = edl.get("music")
    lines.append(f"Music: {m['track']} at {m.get('level_db', music_mod.LEVEL_DB)} LUFS" if m else "Music: none")
    lines.append(f"Punch-ins: {'on' if edl.get('punch') else 'off'}")
    rv = _review(run_dir, int(edl.get("version") or 0))
    if rv:
        notes = "; ".join(f"{i.get('severity')} {i.get('criterion')}: {i.get('note') or i.get('fix') or ''}"[:160]
                          for i in (rv.get("issues") or [])[:5])
        lines.append(f"Last blind review: {rv.get('overall')}{' pass' if rv.get('pass') else ''}"
                     + (f"; notes: {notes}" if notes else ""))
    if missing := edl.get("brief_missing"):
        lines.append("Asked for in the brief but never said in the footage: " + "; ".join(map(str, missing)))
    return "\n".join(lines + footage(run_dir, edl) + versions(run_dir, int(edl.get("version") or 0)))


def footage(run_dir: Path, edl: dict) -> list[str]:
    """What the recording holds beyond the cut: every passage the cut does not use (numbered by turn, what insert
    names), what Spark saw when it watched the footage (moments with the cut second when the cut plays them), the
    speakers and the heads the crop can follow. All from files the run already has: $0."""
    out: list[str] = []
    try:
        words = human._words(run_dir)
    except (OSError, KeyError, TypeError, ValueError):
        words = None
    if words is not None:
        kept: dict[int, set[int]] = {}
        for s in edl["spans"]:
            kept.setdefault(s["turn_id"], set()).update(range(s["word_from"], s["word_to"] + 1))
        rows, budget = [], UNUSED_WORDS
        for t, ws in sorted(words.by_turn.items()):
            run: list[dict] = []
            for w in [*ws, None]:
                if w is not None and w["i"] not in kept.get(t, set()):
                    run.append(w)
                    continue
                if len(run) >= 2 and budget > 0:
                    run = run[:budget]
                    budget -= len(run)
                    rows.append(f"- turn {t} (speaker {run[0].get('speaker', '?')}, source "
                                f"{float(run[0]['t_start']):.1f} s): " + " ".join(f"{x['i']} {x['w']}" for x in run))
                run = []
        out.append("Not in the cut (passages of the recording the cut does not use; insert names them by turn and "
                   "word numbers):" if rows else "Not in the cut: nothing, every passage of the recording is used.")
        out += rows
    try:
        watch = json.loads((run_dir / "watch.json").read_text())
    except (OSError, ValueError):
        watch = {}
    if watch:
        try:
            cmap = {c.span: c for c in cut_map(edl)}
        except (KeyError, TypeError, ValueError):
            cmap = {}

        def at(t: float) -> str:
            for s in edl["spans"]:
                c = cmap.get(s["id"])
                if c and s["t_in"] <= t <= s["t_out"]:
                    return f"cut {c.cut_in + t - s['t_in']:.1f} s"
            return "not in the cut"
        out.append(f"What Spark saw in the footage: {watch.get('summary', '')}")
        for m in (watch.get("movement_moments") or [])[:8]:
            out.append(f"- moment: {m.get('what')} (source {float(m['start_s']):.1f} s, {at(float(m['start_s']))})")
        for b in (watch.get("b_roll_candidates") or [])[:4]:
            out.append(f"- b-roll candidate: {b.get('why')} (source {float(b['start_s']):.1f} s)")
        if objs := watch.get("trackable_concepts"):
            out.append("Objects in the frame: " + ", ".join(map(str, objs[:10])))
    try:
        spk = json.loads((run_dir / "speakers.json").read_text())
    except (OSError, ValueError):
        spk = {}
    if len(spk.get("speakers") or {}) > 1 or len(spk.get("heads") or {}) > 1:
        out.append("Speakers and their heads (pin): " + "; ".join(
            f"{k} -> head {', '.join(v.get('ids') or []) or '?'} (watch's guess: {str(v.get('who', ''))[:60]})"
            for k, v in (spk.get("speakers") or {}).items()) + "; heads: " + ", ".join(sorted(spk.get("heads") or {})))
    return out


def versions(run_dir: Path, current: int) -> list[str]:
    """Every version in one line each (score, what made it, the name or star a person gave it): what restore names."""
    try:
        labels = json.loads((run_dir / "ui" / "labels.json").read_text())
    except (OSError, ValueError):
        labels = {}
    rows = []
    for p in sorted(run_dir.glob("edl_v*.json"), key=lambda p: int(p.stem[5:]) if p.stem[5:].isdigit() else 0):
        n = int(p.stem[5:]) if p.stem[5:].isdigit() else None
        if n is None or not (run_dir / f"cut_v{n}.mp4").is_file():
            continue
        try:
            e = json.loads(p.read_text())
        except ValueError:
            continue
        last = (e.get("patches") or [{}])[-1] if e.get("patches") else {}
        made = "the first plan" if not e.get("patches") else (last.get("instruction") or last.get("action") or "")[:70]
        rv = _review(run_dir, n)
        lab = labels.get(str(n)) if isinstance(labels, dict) else None
        name = f" named {lab.get('name')!r}" if isinstance(lab, dict) and lab.get("name") else ""
        star = " starred" if isinstance(lab, dict) and lab.get("star") else ""
        rows.append(f"v{n}{' (current)' if n == current else ''}: {e.get('runtime_s', 0):.1f} s"
                    + (f", review {rv.get('overall')}" if rv else "") + f"{name}{star}; made by: {made}")
    return ["Versions:", *(f"- {r}" for r in rows[-12:])] if rows else []


def history(run_dir: Path, message: str | None = None, n: int = HISTORY_TURNS) -> list[dict]:
    """The chat's last lines (ui/chat.jsonl), oldest first, without the line of the message being routed (the server
    writes it when the job starts)."""
    try:
        lines = [json.loads(x) for x in (run_dir / "ui" / "chat.jsonl").read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    if lines and message and lines[-1].get("role") == "user" and (lines[-1].get("text") or "").strip() == message.strip():
        lines = lines[:-1]
    return [{"role": x.get("role"), "text": str(x.get("text") or "")[:240], "version": x.get("version")}
            for x in lines[-n:] if x.get("role") in ("user", "agent") and x.get("text")]


def history_text(turns: list[dict] | None) -> str:
    if not turns:
        return ""
    return "The chat so far (oldest first):\n" + "\n".join(
        f"- {'Editor' if t.get('role') == 'user' else 'Montivo'}: {t.get('text', '')}"
        + (f" (made v{t['version']})" if t.get("version") and t.get("role") != "user" else "") for t in turns) + "\n"


def _review(run_dir: Path, n: int) -> dict | None:
    p = run_dir / f"review_v{n}.json"
    try:
        return json.loads(p.read_text()).get("review") if p.is_file() else None
    except ValueError:
        return None


def norm_scope(scope: dict | None) -> dict:
    """The selection the composer sends: {"clips": [ids], "words": [{"span", "from", "to", "text"}], "caption":
    {"t_in", "t_out", "text"}, "t": playhead seconds}; anything else dropped."""
    scope = scope if isinstance(scope, dict) else {}
    out: dict = {}
    clips = [str(c) for c in scope.get("clips") or [] if re.fullmatch(r"s\d+", str(c))]
    if clips:
        out["clips"] = list(dict.fromkeys(clips))
    words = []
    for w in scope.get("words") or []:
        try:
            words.append({"span": str(w["span"]), "from": int(w["from"]), "to": int(w["to"]),
                          "text": str(w.get("text") or "")[:200]})
        except (KeyError, TypeError, ValueError):
            continue
    if words:
        out["words"] = words
    c = scope.get("caption")
    if isinstance(c, dict):
        try:
            out["caption"] = {"t_in": float(c["t_in"]), "t_out": float(c["t_out"]), "text": str(c.get("text") or "")[:200]}
        except (KeyError, TypeError, ValueError):
            pass
    if isinstance(scope.get("t"), (int, float)):
        out["t"] = float(scope["t"])
    return out


def scope_text(scope: dict) -> str:
    """The selection as the prompt states it (empty without one)."""
    said = []
    if scope.get("clips"):
        said.append(f"clip{'s' if len(scope['clips']) > 1 else ''} {', '.join(scope['clips'])}")
    for w in scope.get("words") or []:
        said.append(f"the words {w['from']}-{w['to']} of {w['span']} ({w['text']!r})")
    if c := scope.get("caption"):
        said.append(f"the caption line {c['t_in']:.1f}-{c['t_out']:.1f} s ({c['text']!r})")
    if "t" in scope:
        said.append(f"the playhead at {scope['t']:.1f} s of the cut")
    return f"\nThe editor selected: {'; '.join(said)}.\n" if said else ""


def scope_spans(scope: dict) -> set[str]:
    """The clips an op that names a clip may touch: the selected clips and the clips of the selected words."""
    return set(scope.get("clips") or []) | {w["span"] for w in scope.get("words") or []}


def route(run_dir: Path, edl: dict, message: str, ledger: Ledger, log, scope: dict | None = None,
          note: str = "", model: str = SPARK_MODEL, history: list[dict] | None = None) -> tuple[dict, float]:
    """The router call: Spark 1.3 reads the message and the version and answers {reply, question, ops}."""
    from .engine.spark import Spark
    prompt = PROMPT.format(version=edl.get("version"), context=context(run_dir, edl),
                           tracks="; ".join(f"{t['id']}: {t['name']}, {t.get('mood', '')}" for t in music_mod.tracks()),
                           presets=", ".join(f"{k}: {PRESET_NAMES[k]}" for k in PRESETS),
                           history=history_text(history), scope=scope_text(scope or {}), note=note,
                           message=message.replace('"', "'"))
    call = Spark(run_dir, ledger, model=model, log=log).call("ask", prompt, SCHEMA, max_output_tokens=6000,
                                                                    reasoning_effort="low", stage="spark.ask",
                                                                    schema_name="ask_montivo")
    return dict(call.parsed), call.cost_usd


OFF = re.compile(r"\b(off|no|without|remove|stop|kill|mute)\b|βγάλ|χωρίς|σβήσ|κλείσ|αφαίρεσ|σταμάτ|όχι|καθόλου", re.I)
GREEK_PRESETS = {"καραόκε": "karaoke", "κουτί": "box", "μίνιμαλ": "minimal"}
DELETE = re.compile(r"\b(delete|drop|remove|cut)\b|σβήσ|διάγραψ|κόψ|βγάλ", re.I)
THIS = re.compile(r"\b(this|these|that|those|it|them|selected|selection)\b|αυτ|επιλεγμέν", re.I)
MOVE = re.compile(r"\bmove\s+(s\d+)\s+(?:before|in front of)\s+(s\d+)|\bmove\s+(s\d+)\s+to the end", re.I)


def keywords(edl: dict, message: str, scope: dict | None = None) -> dict:
    """The router without a model: the few edits a keyword names safely. Same answer shape as `route`."""
    m = message.lower()
    off = bool(OFF.search(m))
    scope = scope or {}
    ops, said = [], []
    blank = {k: "" for k in ("span", "before", "text", "track", "preset", "look_prompt", "instruction")} | \
        {"at_s": -1, "level_db": 0, "on": False, "word_from": -1, "word_to": -1}
    ids = [s["id"] for s in edl["spans"]]
    named = [x for x in dict.fromkeys(re.findall(r"\bs\d+\b", m)) if x in ids]
    if (mv := MOVE.search(m)) and (mv.group(1) or mv.group(3)) in ids:
        span, before = (mv.group(1), mv.group(2)) if mv.group(1) else (mv.group(3), "")
        ops.append(blank | {"op": "reorder", "span": span, "before": before})
        said.append(f"{span} moved " + (f"before {before}" if before else "to the end"))
    elif DELETE.search(m) and not re.search(r"music|μουσικ|title|τίτλ|punch|zoom", m):
        if scope.get("words") and (THIS.search(m) or not named):
            for w in scope["words"]:
                ops.append(blank | {"op": "cut", "span": w["span"], "word_from": w["from"], "word_to": w["to"]})
                said.append(f"{w['to'] - w['from'] + 1} words of {w['span']} deleted")
        else:
            for sid in named or (scope.get("clips") if THIS.search(m) else []) or []:
                ops.append(blank | {"op": "drop", "span": sid})
                said.append(f"clip {sid} deleted")
    ts = music_mod.tracks()
    named = next((t for t in ts if t["id"].replace("_", " ") in m or t["name"].lower() in m), None)
    if named or re.search(r"music|μουσικ|τραγούδ|soundtrack", m):
        if off and not named:
            ops.append(blank | {"op": "music_off"})
            said.append("music out")
        else:
            pick = named \
                or next((t for t in ts if any(w in m for w in re.findall(r"[a-z]{4,}", t["name"].lower()))), None) \
                or next((t for t in ts if t["id"] == (edl.get("music") or {}).get("track")), None) or ts[0]
            ops.append(blank | {"op": "music_on", "track": pick["id"]})
            said.append(f"music: {pick['name']}")
    if re.search(r"punch|zoom|ζουμ", m):
        ops.append(blank | {"op": "punch", "on": not off})
        said.append(f"punch-ins {'off' if off else 'on'}")
    preset = next((k for k in PRESETS if k.replace("_", " ") in m or PRESET_NAMES[k].lower() in m), None) \
        or next((v for k, v in GREEK_PRESETS.items() if k in m), None)
    if preset:
        ops.append(blank | {"op": "style", "preset": preset})
        said.append(f"captions: {PRESET_NAMES[preset]}")
    if re.search(r"title|τίτλ", m):
        ops.append(blank | {"op": "title_off" if off else "title_on"})
        said.append(f"title card {'off' if off else 'on'}")
    if not ops:
        return {"reply": "Without MODEL_API_KEY I can only do music, punch-ins, a caption preset by name, the title "
                         "card on and off, and delete or move the clips or words you name or select. Set the key for "
                         "anything else.", "question": "", "ops": []}
    return {"reply": "Done: " + ", ".join(said) + ".", "question": "", "ops": ops}


def check(edl: dict, ops: list[dict], scope: dict | None = None) -> tuple[list[dict], list[str]]:
    """The ops in the order they can run, each id checked against the version; the rest skipped with a reason.
    Order: caption lines first (they need this version's rendered captions), then a re-plan, then the edits that
    survive one (text, look, music, punch-ins), then the clip edits. With a selection, an op that names a clip, words
    or a caption line outside it is skipped: "make this shorter" touches only what is selected."""
    ids = {s["id"] for s in edl["spans"]}
    by_id = {s["id"]: s for s in edl["spans"]}
    scope = scope or {}
    allowed = scope_spans(scope)
    picked = {(w["span"], i) for w in scope.get("words") or [] for i in range(w["from"], w["to"] + 1)}
    line = scope.get("caption")
    tracks = {t["id"] for t in music_mod.tracks()}
    replan = any(o.get("op") == "replan" for o in ops)
    kept, skipped = [], []
    for o in ops:
        op = o.get("op")
        if op == "reorder" and o.get("before") == "end":  # Spark Open says "end" where the schema says empty
            o = {**o, "before": ""}
        why = None
        if op not in OPS:
            why = f"unknown op {op!r}"
        elif replan and op in SPAN_OPS:
            why = f"{op} skipped: the re-plan makes new clips, ask again on the new version"
        elif op in ("callout", "drop", "reorder", "cut") and o.get("span") not in ids:
            why = f"{op}: {o.get('span')!r} is not a clip of this version ({', '.join(sorted(ids))})"
        elif op == "reorder" and o.get("before") and o["before"] not in ids:
            why = f"reorder: {o['before']!r} is not a clip of this version"
        elif op in ("callout", "drop", "reorder", "cut") and allowed and o["span"] not in allowed:
            why = f"{op} {o['span']}: outside your selection ({', '.join(sorted(allowed))})"
        elif op == "cut" and not (by_id[o["span"]]["word_from"] <= int(o.get("word_from", -1)) <= int(o.get("word_to", -1))
                                  <= by_id[o["span"]]["word_to"]):
            why = (f"cut: words {o.get('word_from')}-{o.get('word_to')} are not in {o['span']} "
                   f"({by_id[o['span']]['word_from']}-{by_id[o['span']]['word_to']})")
        elif op == "cut" and picked and any((o["span"], i) not in picked for i in range(int(o["word_from"]),
                                                                                        int(o["word_to"]) + 1)):
            why = f"cut {o['span']}: words outside your selection"
        elif op == "caption" and line and not line["t_in"] - 0.05 <= float(o.get("at_s", -1)) <= line["t_out"] + 0.05:
            why = f"caption at {float(o.get('at_s', -1)):.1f} s: outside the selected line ({line['t_in']:.1f}-{line['t_out']:.1f} s)"
        elif op == "music_on" and o.get("track") not in tracks:
            why = f"music: {o.get('track')!r} is not a bundled track"
        elif op == "style" and not o.get("preset") and not (o.get("look_prompt") or "").strip():
            why = "style: no preset and no look described"
        elif op == "style" and o.get("preset") and o["preset"] not in PRESETS:
            why = f"style: {o['preset']!r} is not a preset"
        elif op == "caption" and (float(o.get("at_s", -1)) < 0 or not (o.get("text") or "").strip()):
            why = "caption: no line time or no text"
        elif op == "replan" and not (o.get("instruction") or "").strip():
            why = "replan: no instruction"
        elif op == "replan" and (allowed or picked or line):  # a re-plan rebuilds every clip (Codex review 28/10)
            why = "replan: it changes the whole cut, not just your selection; clear the selection to re-plan"
        elif op == "title_text" and not (o.get("text") or "").strip():
            why = "title: no words"
        elif op == "overlay" and (float(o.get("at_s", -1)) < 0 or not (o.get("text") or "").strip()):
            why = "overlay: no cut second or nothing to show"
        elif op in ("trim", "split", "select") and o.get("span") not in ids:
            why = f"{op}: {o.get('span')!r} is not a clip of this version ({', '.join(sorted(ids))})"
        elif op in ("trim", "split") and allowed and o["span"] not in allowed:
            why = f"{op} {o['span']}: outside your selection ({', '.join(sorted(allowed))})"
        elif op == "trim" and (o.get("edge") not in ("in", "out") or int(o.get("word", -1)) < 0):
            why = "trim: no edge (in or out) or no word"
        elif op == "split" and not by_id[o["span"]]["word_from"] < int(o.get("word", -1)) <= by_id[o["span"]]["word_to"]:
            why = (f"split: word {o.get('word')} is not inside {o['span']} after its first word "
                   f"({by_id[o['span']]['word_from']}-{by_id[o['span']]['word_to']})")
        elif op == "insert" and (int(o.get("turn", -1)) < 0 or int(o.get("word_from", -1)) < 0
                                 or int(o.get("word_to", -1)) <= int(o.get("word_from", -1))):
            why = "insert: no turn or not two words"
        elif op == "insert" and (o.get("before") or "") not in ("", "end") and o["before"] not in ids:
            why = f"insert: {o['before']!r} is not a clip of this version"
        elif op == "insert" and (allowed or picked or line):
            why = "insert: it adds to the cut, not to your selection; clear the selection to put a passage back"
        elif op == "glossary" and not ((o.get("heard") or "").strip() and (o.get("written") or "").strip()):
            why = "glossary: the words as heard and as written are both needed"
        elif op == "speakers" and not ((o.get("speaker") or "").strip() and (o.get("name") or "").strip()):
            why = "speakers: no speaker or no name"
        elif op == "pin" and not (o.get("speaker") or "").strip():
            why = "pin: no speaker"
        elif op == "restore" and (int(o.get("version", -1)) < 1 or int(o["version"]) == int(edl.get("version") or 0)):
            why = f"restore: v{o.get('version')} is {'this version' if int(o.get('version', -1)) >= 1 else 'not a version'}"
        elif op == "seek" and not 0 <= float(o.get("at_s", -1)) <= float(edl.get("runtime_s") or 0) + 0.5:
            why = f"seek: {float(o.get('at_s', -1)):.1f} s is outside the cut"
        elif op == "open" and o.get("panel") not in PANELS:
            why = f"open: {o.get('panel')!r} is not a panel ({', '.join(PANELS)})"
        if why:
            skipped.append(why)
        else:
            kept.append(o)
    if (r := next((o for o in kept if o["op"] == "restore"), None)) and len([o for o in kept if o["op"] not in UI_OPS]) > 1:
        skipped += [f"{o['op']} skipped: restore brings v{r['version']} back as it was; ask again on it"
                    for o in kept if o is not r and o["op"] not in UI_OPS | {"review"}]
        kept = [o for o in kept if o is r or o["op"] in UI_OPS | {"review"}]
    rank = {"caption": 0, "overlay": 0, "glossary": 0, "restore": 1, "replan": 1, "callout": 2, "title_text": 2,
            "title_on": 2, "title_off": 2, "style": 3, "music_on": 4, "music_off": 4, "punch": 5, "speakers": 5,
            "pin": 5, "cut": 6, "drop": 6, "trim": 6, "split": 7, "insert": 7, "reorder": 7, "pauses": 8, "review": 9,
            "seek": 10, "select": 10, "open": 10}
    return sorted(kept, key=lambda o: rank[o["op"]]), skipped


def run_ops(run_dir: Path, from_v: int, ops: list[dict], planner: str | None, log) -> tuple[list[int], list[str], list[str]]:
    """Each op through its human edit, chained from from_v, unrendered (human.DEFER). Returns the versions written,
    what was done (one line per op) and what was skipped."""
    done, skipped = [], []
    written: list[int] = []
    human.DEFER = written
    try:
        def at() -> int:
            return written[-1] if written else from_v

        caps = [(float(o["at_s"]), o["text"]) for o in ops if o["op"] == "caption"]
        texts = {o["span"]: o["text"] for o in ops if o["op"] == "callout"}
        title = next((o["text"] for o in ops if o["op"] == "title_text"), None)
        card = next((o["op"] == "title_on" for o in ops if o["op"] in ("title_on", "title_off")), None)
        steps: list[tuple[str, callable]] = []
        for o in ops:  # the look first, on the rendered version: a sentence is styled against the caption rows and the
            if o["op"] == "style":  # face it reads there (an unrendered edit before it has no captions: no face rule)
                look = {"preset": o["preset"]} if o["preset"] else None
                prompt = None if look else o["look_prompt"]
                steps.append((f"captions: {PRESET_NAMES[o['preset']] if look else o['look_prompt']}",
                              lambda look=look, prompt=prompt: human.style(run_dir, look, from_v=at(), prompt=prompt)))
        if caps:
            steps.append((f"{len(caps)} caption line(s) retexted", lambda: human.caption(run_dir, caps, from_v=at())))
        for o in ops:  # cut seconds of this version: before anything moves the clips
            if o["op"] == "overlay":
                steps.append((f"B-roll at {float(o['at_s']):.1f} s: {o['text']}", lambda o=o: human.overlay(
                    run_dir, prompt=o["text"], at=float(o["at_s"]), from_v=at())))
        for o in ops:
            if o["op"] == "replan":
                steps.append((f"re-planned: {o['instruction']}", lambda o=o: human.replan(
                    run_dir, o["instruction"], from_v=at(), model=planner)))
        if texts or title is not None or card is not None:
            steps.append(("callout and title text", lambda: human.text(run_dir, texts, title, card, from_v=at())))
        for o in ops:
            op = o["op"]
            if op == "music_on":
                lvl = float(o["level_db"]) or None
                steps.append((f"music {o['track']}" + (f" at {lvl:g} LUFS" if lvl else ""),
                              lambda o=o, lvl=lvl: human.music(run_dir, o["track"], level_db=lvl, from_v=at())))
            elif op == "music_off":
                steps.append(("music off", lambda: human.music(run_dir, None, from_v=at())))
            elif op == "punch":
                steps.append((f"punch-ins {'on' if o['on'] else 'off'}",
                              lambda o=o: human.punch(run_dir, on=bool(o["on"]), from_v=at())))
            elif op == "drop":
                steps.append((f"clip {o['span']} deleted", lambda o=o: human.drop(run_dir, o["span"], from_v=at())))
        cuts = [(o["span"], int(o["word_from"]), int(o["word_to"])) for o in ops if o["op"] == "cut"]
        if cuts:  # one cut for every range: a split renames the clip, so a second cut could not find it
            n = sum(b - a + 1 for _, a, b in cuts)
            steps.append((f"{n} word{'s' if n != 1 else ''} deleted in {', '.join(dict.fromkeys(c[0] for c in cuts))}",
                          lambda: human.cut(run_dir, cuts, from_v=at())))
        for o in ops:
            op = o["op"]
            if op == "reorder":
                steps.append((f"clip {o['span']} moved " + (f"before {o['before']}" if o["before"] else "to the end"),
                              lambda o=o: human.reorder(run_dir, o["span"], o["before"] or None, from_v=at())))
        for o in ops:  # the editor's pure edits (human.OPS), each as its CLI edit would run it
            if (what := pure(run_dir, o)) is not None:
                label, name, args = what
                def run(name=name, args=args):
                    if name == "pin":
                        human.pin_check(run_dir, args["speaker"], args["head"])
                    return human._run_edit(run_dir, at(), 0, 0.0, human.OPS[name], **args)
                steps.append((label, run))
        for what, step in steps:
            try:
                step()
                done.append(what)
            except SystemExit as e:  # the edit refused it (nothing to change, a span gone after a drop...)
                skipped.append(f"{what}: {e}")
                log(f"agent: skipped {what}: {e}")
    finally:
        human.DEFER = None
    return written, done, skipped


def pure(run_dir: Path, o: dict) -> tuple[str, str, dict] | None:
    """A router op that is one of the editor's pure edits: (what it does in words, the human.OPS name, its args)."""
    op = o["op"]
    if op == "trim":
        return (f"{o['span']} {'starts' if o['edge'] == 'in' else 'ends'} at word {o['word']}", "trim",
                {"span": o["span"], "edge": o["edge"], "word": int(o["word"])})
    if op == "split":
        return f"{o['span']} split before word {o['word']}", "split", {"span": o["span"], "word": int(o["word"])}
    if op == "insert":
        where = {"": "where it was said", "end": "at the end"}.get(o.get("before") or "", f"before {o.get('before')}")
        return (f"turn {o['turn']} words {o['word_from']}-{o['word_to']} put back {where}", "insert",
                {"turn": int(o["turn"]), "a": int(o["word_from"]), "b": int(o["word_to"]), "before": o.get("before") or None})
    if op == "pauses":
        return "pauses shortened", "pauses", {}
    if op == "glossary":
        return (f"“{o['heard']}” written “{o['written']}” in every caption", "glossary",
                {"add": {o["heard"].strip(): o["written"].strip()}})
    if op == "speakers":
        return f"speaker {o['speaker']} is {o['name']}", "speakers", {"names": {o["speaker"]: o["name"].strip()}}
    if op == "pin":
        head = (o.get("head") or "").strip() or None
        return (f"the crop follows head {head} when {o['speaker']} talks" if head else
                f"the crop follows {o['speaker']} automatically again"), "pin", {"speaker": o["speaker"], "head": head}
    return None


NOT_IN_DRAFT = {"replan", "overlay", "restore"}  # a plan, a Muse Image still, an old version: they make a version


def draft_ops(ops: list[dict]) -> list[dict] | None:
    """The checked ops as the editor's draft ops (montivo/server/draft.py: {"op", "args"} as its routes take them), or
    None when one of them cannot go into a draft (a re-plan, a B-roll still, a restore, a look from words: they make a
    version as before). Caption lines stay cut seconds: the draft resolves them against itself as laid out."""
    if any(o["op"] in NOT_IN_DRAFT or (o["op"] == "style" and not o.get("preset")) for o in ops):
        return None
    out: list[dict] = []
    caps = [{"at": float(o["at_s"]), "text": o["text"]} for o in ops if o["op"] == "caption"]
    if caps:
        out.append({"op": "caption", "args": {"edits": caps}})
    callouts = {o["span"]: o["text"] for o in ops if o["op"] == "callout"}
    title = next((o["text"] for o in ops if o["op"] == "title_text"), None)
    card = next((o["op"] in ("title_on", "title_text") for o in ops if o["op"] in ("title_on", "title_off", "title_text")), None)
    if callouts or title is not None or card is not None:
        out.append({"op": "text", "args": {"callouts": callouts, **({"title": title} if title else {}),
                                           **({"title_card": card} if card is not None else {})}})
    for o in ops:
        op = o["op"]
        if op == "style":
            out.append({"op": "style", "args": {"look": {"preset": o["preset"]}, "callouts": {}}})
        elif op == "music_on":
            out.append({"op": "music", "args": {"track": o["track"], "level_db": float(o["level_db"]) or None}})
        elif op == "music_off":
            out.append({"op": "music", "args": {"track": None}})
        elif op == "punch":
            out.append({"op": "punch", "args": {"on": bool(o["on"])}})
        elif op == "drop":
            out.append({"op": "drop", "args": {"span": o["span"]}})
    cuts = [[o["span"], int(o["word_from"]), int(o["word_to"])] for o in ops if o["op"] == "cut"]
    if cuts:
        out.append({"op": "cut", "args": {"ranges": cuts}})
    for o in ops:
        if o["op"] == "reorder":
            out.append({"op": "reorder", "args": {"span": o["span"], "before": o.get("before") or None}})
        elif (what := pure(None, o)) is not None:
            out.append({"op": what[1], "args": what[2]})
    return out


AGENT_KEYS = ("message", "reply", "router", "ops", "done", "skipped", "pending", "scope", "retry_of", "ui", "draft")


def fold(run_dir: Path, from_v: int, written: list[int], record: dict) -> int:
    """The chain v_a -> v_b -> ... folded into v_a: the last EDL with one patch for the whole message, the in-between
    edl files removed (they were never rendered)."""
    first, last = written[0], written[-1]
    base = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    edl = json.loads((run_dir / f"edl_v{last}.json").read_text())
    mine = edl["patches"][len(base.get("patches") or []):]
    actions = [p.get("action") for p in mine]
    patch = {"from": from_v, "review": None, "source": "human",
             "action": actions[0] if len(set(actions)) == 1 else "ask",
             "instruction": record["message"],
             "fixes": [f for p in mine for f in p.get("fixes") or []],
             "agent": {k: record[k] for k in AGENT_KEYS if k in record}}
    if model := next((p.get("model") for p in mine if p.get("action") == "replan"), None):
        patch["model"] = model
    edl["patches"] = list(base.get("patches") or []) + [patch]
    edl["version"] = first
    dump_json(run_dir / f"edl_v{first}.json", edl)
    for n in written[1:]:
        (run_dir / f"edl_v{n}.json").unlink(missing_ok=True)
    return first


def op_label(o: dict) -> str:
    """One op in plain words, for the preview card's "also" line."""
    op = o["op"]
    track = next((t["name"] for t in music_mod.tracks() if t["id"] == o.get("track")), o.get("track"))
    return {"music_on": f"music: {track}", "music_off": "music out", "punch": f"punch-ins {'on' if o.get('on') else 'off'}",
            "style": f"captions: {PRESET_NAMES.get(o.get('preset'), o.get('look_prompt') or '')}",
            "caption": f"caption line at {float(o.get('at_s', 0)):.1f} s: “{o.get('text', '')}”",
            "callout": f"callout on {o.get('span')}: “{o.get('text', '')}”" if o.get("text") else f"callout off {o.get('span')}",
            "title_text": f"title card: “{o.get('text', '')}”", "title_on": "title card on",
            "title_off": "title card off",
            "overlay": f"B-roll at {float(o.get('at_s', 0)):.1f} s: “{o.get('text', '')}”",
            "glossary": f"“{o.get('heard', '')}” written “{o.get('written', '')}” everywhere",
            "speakers": f"speaker {o.get('speaker')} named {o.get('name')}",
            "pin": f"crop follows head {o.get('head')} for {o.get('speaker')}" if o.get("head") else f"crop automatic for {o.get('speaker')}",
            "split": f"{o.get('span')} split before word {o.get('word')}"}.get(op, op)


def needs_preview(ops: list[dict], planner: str | None = None) -> bool:
    """A structural change, or ops that cost over PREVIEW_USD, wait for the editor's Apply (Billy, 28/10)."""
    return any(o["op"] in STRUCTURAL for o in ops) or estimate(ops, planner, 0, 0.0)[0] > PREVIEW_USD


def estimate(ops: list[dict], planner: str | None, passes: int, runtime_s: float) -> tuple[float, float]:
    """(dollars, seconds) the ops, one render of about runtime_s and `passes` reviews take."""
    kinds = {o["op"] for o in ops}
    usd = EST["review"] * passes
    sec = EST["render_s_per_s"] * runtime_s + EST["review_s"] * passes
    if "replan" in kinds:
        usd += EST["plan_open" if planner else "plan"]
        sec += EST["plan_open_s" if planner else "plan_s"]
    if "title_text" in kinds:
        usd, sec = usd + EST["image"], sec + EST["image_s"]
    n_still = sum(o["op"] == "overlay" for o in ops)
    usd, sec = usd + EST["image"] * n_still, sec + EST["image_s"] * n_still
    if any(o["op"] == "style" and not o.get("preset") for o in ops):
        usd, sec = usd + EST["look"], sec + EST["look_s"]
    return round(usd, 3), round(sec)


def preview_card(run_dir: Path, edl: dict, ops: list[dict], planner: str | None, passes: int) -> dict:
    """What the ops would change, in plain words, before anything runs: the clips before and after, which go or move,
    the words a cut deletes, the re-plan's note and target (not planned yet), the other edits, cost and time."""
    spans = edl["spans"]
    by_id = {s["id"]: s for s in spans}
    runtime = float(edl.get("runtime_s") or sum(s.get("duration", 0.0) for s in spans))
    order = [s["id"] for s in spans]
    lines: list[str] = []
    replan = next((o for o in ops if o["op"] == "replan"), None)
    after_s = runtime
    if replan:
        run = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").is_file() else {}
        target = human.target_from(replan["instruction"]) or run.get("target_s")
        lines.append(f"Plan the edit again: “{replan['instruction']}”")
        lines.append(f"{'Muse Spark Open' if planner else 'Muse Spark 1.3'} plans it when you apply"
                     + (f", aiming at about {float(target):.0f} s" if target else "")
                     + f"; now {len(spans)} clips, {runtime:.1f} s")
        after_s = float(target or runtime)
    gone = [o["span"] for o in ops if o["op"] == "drop"]
    cuts = [o for o in ops if o["op"] == "cut"]
    cut_s, cut_lines = 0.0, []
    try:
        words = human._words(run_dir)
    except (OSError, KeyError, ValueError):
        words = None
    for o in cuts:
        s = by_id[o["span"]]
        a, b = int(o["word_from"]), int(o["word_to"])
        tw = {w["i"]: w for w in words.turn(s["turn_id"])} if words else {}
        if a == s["word_from"] and b == s["word_to"]:
            gone.append(s["id"])
            continue
        if a in tw and b in tw:
            cut_s += max(0.0, float(tw[b]["t_end"]) - float(tw[a]["t_start"]))
        text = " ".join(tw[i]["w"] for i in range(a, b + 1) if i in tw)
        cut_lines.append(f"{s['id']} loses {b - a + 1} word{'s' if b > a else ''}" + (f": “{text[:80]}”" if text else ""))
    moves = []
    for o in ops:
        if o["op"] == "reorder" and o["span"] in order:
            order.remove(o["span"])
            if o.get("before") and o["before"] in order:
                order.insert(order.index(o["before"]), o["span"])
            else:
                order.append(o["span"])
            moves.append(f"{o['span']} moves " + (f"in front of {o['before']}" if o.get("before") else "to the end"))
    if not replan:
        gone = list(dict.fromkeys(gone))
        after_s = max(0.0, runtime - sum(float(by_id[g].get("duration", 0.0)) for g in gone) - cut_s)
        kept = [x for x in order if x not in gone]
        lines.append(f"{len(spans)} → {len(kept)} clips, about {after_s:.0f} s (now {runtime:.1f} s)"
                     if gone else f"{len(kept)} clips, about {after_s:.0f} s (now {runtime:.1f} s)")
        for g in gone:
            lines.append(f"{g} goes: “{(by_id[g].get('words') or '')[:80]}”")
        lines += cut_lines + moves
        if moves:
            lines.append("New order: " + " ".join(kept))
    also = [op_label(o) for o in ops if o["op"] not in STRUCTURAL]
    if also:
        lines.append("Also: " + ", ".join(also))
    usd, sec = estimate(ops, planner, passes, after_s)
    return {"lines": lines, "usd": usd, "seconds": sec, "clips_before": len(spans), "runtime_before": round(runtime, 2),
            "runtime_after": round(after_s, 1), "replan": bool(replan), "review": passes}


PENDING_ID = re.compile(r"^[A-Za-z0-9_-]{4,40}$")


def pending_path(run_dir: Path, pid: str) -> Path:
    if not PENDING_ID.match(pid or ""):
        raise SystemExit(f"{pid!r} is not a proposal id")
    return run_dir / "ui" / "pending" / f"{pid}.json"


def load_pending(run_dir: Path, pid: str, *ok: str) -> dict:
    """A proposal waiting for Apply; SystemExit when it is gone or already settled (applied, or turned down for
    another try)."""
    p = pending_path(run_dir, pid)
    if not p.is_file():
        raise SystemExit(f"no proposal {pid} in this run")
    rec = json.loads(p.read_text())
    if rec.get("status") not in (ok or ("open", "running")):
        raise SystemExit(f"proposal {pid} was already {rec.get('status')}")
    return rec


def set_status(run_dir: Path, rec: dict, status: str) -> None:
    rec["status"] = status
    rec[f"{status}_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    dump_json(pending_path(run_dir, rec["id"]), rec)


def execute(run_dir: Path, from_v: int, ops: list[dict], record: dict, passes: int, planner: str | None,
            budget: float, log) -> dict:
    """Run the checked ops: one folded version, rendered once (with the review loop when asked), or the review loop
    alone when the message only asked for a review."""
    if r := next((o for o in ops if o["op"] == "restore"), None):  # check keeps it alone: a version as it was
        out = human.restore(run_dir, int(r["version"]), passes=passes, budget=budget)
        record["done"], record["version"] = [f"v{r['version']} back as v{out['version']}"], out["version"]
        edl_path = run_dir / f"edl_v{out['version']}.json"
        edl = json.loads(edl_path.read_text())
        edl["patches"][-1]["agent"] = {k: record[k] for k in AGENT_KEYS if k in record}
        edl["patches"][-1]["instruction"] = record["message"]
        dump_json(edl_path, edl)
    elif ops:
        written, done, more = run_ops(run_dir, from_v, ops, planner, log)
        record["done"], record["skipped"] = done, record["skipped"] + more
        if written:
            n = fold(run_dir, from_v, written, record)
            record["version"] = n
            human._finish(run_dir, run_dir / f"edl_v{n}.json", n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)
            if (run_dir / f"cut_v{n}.mp4").is_file():
                record["checks"] = measured(run_dir, n, log)
    elif passes:  # "review it" with nothing to change: the review loop on this version
        from .engine.review import review_loop
        review_loop(run_dir, run_dir / f"edl_v{from_v}.json", passes=passes, budget_usd=budget,
                    ledger=Ledger.resume(run_dir / "ledger.json"), log=log)
        record["reviewed"] = from_v
    return record


def measured(run_dir: Path, n: int, log) -> dict | None:
    """B1: the review gate's $0 checks on the version the chat just made (review.gate_check without Voice on the
    cut): a cut edge inside speech, a caption forced over the head, the head out of frame. What a blind review would
    hold the pass on, said in the chat at once, before anyone pays for a review."""
    from .engine.review import Audio, caption_face_facts, cut_edge_levels, gate_check, head_frame_facts
    try:
        edl = json.loads((run_dir / f"edl_v{n}.json").read_text())
        audio = Audio.load(run_dir)
        gate = gate_check(caption_face_facts(edl), cut_edge_levels(edl, audio) if audio else None, None,
                          head_frame_facts(run_dir, edl))
    except (OSError, KeyError, TypeError, ValueError) as e:
        log(f"agent: checks on v{n} skipped ({e})")
        return None
    out = {"ok": not gate["blocked"], "reasons": gate["reasons"][:4], "more": max(0, len(gate["reasons"]) - 4),
           "audio": audio is not None}
    log(f"agent: checks on v{n}: " + ("clear" if out["ok"] else "; ".join(gate["reasons"])))
    return out


def checked_text(c: dict | None) -> str:
    """The checks in one sentence for the chat."""
    if not c:
        return ""
    if c["ok"]:
        return ("Checked the new cut: no cut lands in speech, captions clear of the face, the head in frame."
                if c.get("audio") else "Checked the new cut: captions clear of the face, the head in frame.")
    return "Checked the new cut: " + "; ".join(c["reasons"]) + (f" (and {c['more']} more)" if c.get("more") else "") \
        + ". A review fix or an edit there would clear it."


def compact(ops: list[dict]) -> list[dict]:
    return [{k: v for k, v in o.items() if v not in ("", -1, None) and not (k == "level_db" and v == 0)
             and not (k == "on" and o["op"] != "punch")} for o in ops]


def ask(run_dir: Path, message: str | None, from_v: int | None = None, passes: int = 0, planner: str | None = None,
        use_model: bool = True, budget: float = 0.15, scope: dict | None = None, preview: bool = False,
        retry_of: str | None = None, draft: bool = False, draft_edl: Path | None = None) -> dict:
    log = RunLog(run_dir / "run.log")
    note = ""
    if retry_of:  # "Try something else": the same message, its proposal in the prompt as turned down; once, no review
        old = load_pending(run_dir, retry_of, "open")
        set_status(run_dir, old, "retried")
        message, scope, from_v, passes = old["message"], old.get("scope"), old["from"], 0
        note = ("\nThe editor turned down your first proposal for this message (" + "; ".join(old["card"]["lines"])
                + "). Propose a different way to do what they asked: other clips, a smaller change, or a question "
                  "back.\n")
    if not (message or "").strip():
        raise SystemExit("the message is empty")
    scope = norm_scope(scope)
    from_v = from_v or human.latest_edl(run_dir)
    edl = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    draft_open = draft and draft_edl is not None and draft_edl.is_file()
    if draft_open:  # the open Live draft: "this" is what the editor plays
        edl = json.loads(draft_edl.read_text())
        from_v = int(edl.get("version") or from_v)
    ledger = Ledger.resume(run_dir / "ledger.json")
    log(f"montivo ask: {message!r} on edl_v{from_v}" + (f", selection {scope_text(scope).strip()}" if scope else "")
        + (f" (another try after {retry_of})" if retry_of else ""))
    router, cost = "keywords", 0.0
    answer = None
    if use_model:
        try:
            answer, cost = route(run_dir, edl, message, ledger, log, scope, note, history=history(run_dir, message))
            router = SPARK_MODEL
        except (SystemExit, Exception) as e:  # a failed or unparsable call: the keyword reader still helps
            log(f"agent: the router call failed ({e}); reading keywords")
    if answer is None:
        answer = keywords(edl, message, scope)
    ops, skipped = check(edl, answer.get("ops") or [], scope)
    asked_review = any(o["op"] == "review" for o in ops)
    ops = [o for o in ops if o["op"] != "review"]
    ui, ops = [o for o in ops if o["op"] in UI_OPS], [o for o in ops if o["op"] not in UI_OPS]
    # the composer's Review toggle reviews what the message changes; a question or a "show me" changes nothing, so
    # it reviews only when the message asks for a review
    passes = max(passes if ops else 0, 1 if asked_review else 0)
    log(f"agent ({router}): {answer.get('reply', '')!r}; ops: {', '.join(o['op'] for o in ops + ui) or 'none'}"
        + (f"; skipped: {'; '.join(skipped)}" if skipped else ""))
    record = {"message": message, "reply": (answer.get("reply") or "").strip(), "question": (answer.get("question") or "").strip(),
              "router": router, "router_cost_usd": round(cost, 5), "from": from_v, "ops": compact(ops),
              "done": [], "skipped": skipped, "version": None}
    if scope:
        record["scope"] = scope
    if ui:  # the playhead, a selection, a panel: the editor does them when the reply lands
        record["ui"] = compact(ui)
    if retry_of:
        record["retry_of"] = retry_of
    if draft and ops and not asked_review and (dops := draft_ops(ops)) is not None:  # the Review toggle reviews the
        # version Make Version renders
        # Live (A5): the editor's own draft is the preview; the server adds these ops to it, nothing renders until
        # the editor's Make Version (Billy: a version is made by the button only)
        record["draft"] = dops
        log(f"agent: {len(dops)} edit(s) for the Live draft: {', '.join(o['op'] for o in dops)}")
        return record
    if draft and ops and draft_open:  # a version from the draft's base would leave the draft's edits out
        record["skipped"] += [f"{', '.join(dict.fromkeys(o['op'] for o in ops))}: this makes a version, and the "
                              "draft's edits are not one yet. Make Version first, then ask again"]
        record["ops"] = []
        return record
    if preview and ops and needs_preview(ops, planner):
        card = preview_card(run_dir, edl, ops, planner, passes)
        pid = time.strftime("%H%M%S-") + secrets.token_hex(3)
        dump_json(pending_path(run_dir, pid), {**record, "id": pid, "status": "open", "created": time.strftime(
            "%Y-%m-%dT%H:%M:%S"), "full_ops": ops, "passes": passes, "planner": planner, "card": card})
        record["pending"] = {"id": pid, **card, "can_retry": not retry_of}
        if router == "keywords":  # its "Done: ..." would read as if it ran
            record["reply"] = "Here is what would change. Nothing runs until you Apply."
        log(f"agent: proposal {pid} waits for Apply: {'; '.join(card['lines'])} (about ${card['usd']:.3f}, {card['seconds']} s)")
        return record
    return execute(run_dir, from_v, ops, record, passes, planner, budget, log)


def apply(run_dir: Path, pid: str, passes: int | None = None, budget: float = 0.15) -> dict:
    """The editor's Apply on a proposal: its ops as checked when it was made, from its version, one rendered version
    (a re-plan plans now, once). Try again after a failure runs it once more; an applied one refuses."""
    log = RunLog(run_dir / "run.log")
    p = load_pending(run_dir, pid)
    newest = human.latest_edl(run_dir)
    if newest > p["from"]:  # its ops run from v{from}: applied now, they would leave out every edit made since
        raise SystemExit(f"the cut changed since this proposal (v{p['from']} -> v{newest}): ask again on the newest version")
    set_status(run_dir, p, "running")
    log(f"montivo ask: apply proposal {pid} ({p['message']!r}) on edl_v{p['from']}")
    record = {k: p[k] for k in ("message", "reply", "question", "router", "from", "ops", "skipped") if k in p}
    record |= {"router_cost_usd": 0.0, "done": [], "version": None, "pending": pid, "applied": True}
    if p.get("scope"):
        record["scope"] = p["scope"]
    out = execute(run_dir, p["from"], p["full_ops"], record, p["passes"] if passes is None else passes,
                  p.get("planner"), budget, log)
    set_status(run_dir, p, "applied")
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m montivo.agent", description=__doc__.split("\n")[0])
    p.add_argument("run_dir", type=Path)
    p.add_argument("--message", default=None)
    p.add_argument("--from", dest="from_v", type=int, default=None)
    p.add_argument("--review", type=int, default=None, help="review passes after the edit (--apply: else the proposal's)")
    p.add_argument("--planner", choices=["spark", "open"], default="spark", help="the planner a re-plan uses")
    p.add_argument("--no-model", action="store_true", help="read keywords instead of calling Spark")
    p.add_argument("--scope", default=None, help='the selection as JSON: {"clips": [...], "words": [...], "caption": {...}}')
    p.add_argument("--preview", action="store_true", help="structural or costly ops wait in ui/pending/ for --apply")
    p.add_argument("--apply", default=None, metavar="ID", help="run a waiting proposal")
    p.add_argument("--retry", default=None, metavar="ID", help="route the proposal's message again, once, no review")
    p.add_argument("--draft", action="store_true", help="Live: edits that fit the editor's draft are returned as draft ops")
    p.add_argument("--draft-edl", type=Path, default=None, help="the open draft's EDL: the version the message is about")
    a = p.parse_args(argv)
    import os
    if a.apply:
        out = apply(a.run_dir, a.apply, a.review)
    else:
        if not a.message and not a.retry:
            p.error("--message, --apply or --retry")
        try:
            scope = json.loads(a.scope) if a.scope else None
        except json.JSONDecodeError as e:
            p.error(f"--scope: {e}")
        out = ask(a.run_dir, a.message, a.from_v, a.review or 0, OPEN_MODEL if a.planner == "open" else None,
                  use_model=not a.no_model and bool(os.environ.get("MODEL_API_KEY")), scope=scope, preview=a.preview,
                  retry_of=a.retry, draft=a.draft, draft_edl=a.draft_edl)
    print(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
