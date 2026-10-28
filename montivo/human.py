"""The human actions in the loop, as an engine entry point the UI server runs as a subprocess:

    python -m montivo.human drop runs/012 --span s3 [--from 2] [--review 2]
    python -m montivo.human trim runs/012 --span s3 --edge out --word 41 [--from 2] [--review 2]
    python -m montivo.human replan runs/012 --instruction "drop the agency line" [--from 2] [--review 2]
    python -m montivo.human punch runs/012 --on|--off [--from 2] [--review 2]
    python -m montivo.human punch runs/012 --skip "emphasis:Montivo@47.120" [--from 2] [--review 2]
    python -m montivo.human caption runs/015 --at 18.5 --text "Four Meta models do all" [--at ... --text ...]
    python -m montivo.human text runs/015 [--callout s5="Four Meta models"] [--callout s6=] [--title "..."] [--title-off]
    python -m montivo.human reorder runs/015 --span s4 (--before s2 | --end)
    python -m montivo.human cut runs/015 --words s2:16-16 [--words s3:0-2 ...]
    python -m montivo.human pin runs/014 --speaker A --head 3 (or --head none to drop the pin)
    python -m montivo.human edges runs/015
    python -m montivo.human style runs/015 [--look '{"preset": "box", "position": "fixed", "top": 1180}'] [--callout s5=120,300] [--callout s5=auto]
    python -m montivo.human music runs/015 --track chill_lofi [--level -20] [--duck -12] [--voice 0] (or --off)

Each writes the next edl_vN.json (N = one past the highest version in the run folder, so no version is ever
overwritten) with a `patches` entry marked `source: "human"`, renders cut_vN.mp4 and, with --review, runs the blind
review loop from it. `drop` and `trim` go through the reviewer's own patch path (`apply_fixes`) and call no model
unless --review is given; `replan` is one Spark plan call (about $0.05) with the instruction added to the brief.
`punch` turns the punch-in zooms (engine/punch.py) on or off, or removes one of them by its key; $0, ffmpeg only.
`caption` gives the caption line on screen at a cut second the words actually spoken (the reviewer's caption_text
patch); $0, ffmpeg only. `text` rewrites or removes callouts and turns the title card off or on ($0) or gives it new
words (one Muse Image call, $0.01: the words are part of the generated image). `reorder` moves a clip (the reviewer's
reorder patch) and tightens the cuts that now meet; `pin` makes the crop follow one head for a speaker (EDL
`reframe_speaker_ids`, for clips where the speaker map got it wrong, like run 014). Both $0. `cut` deletes the words
of a transcript selection (turn word indices per span) in one version: whole clips are dropped, words at a clip's edge
trimmed like a timeline drag, words inside a clip split it in two; $0. `edges` runs the edge pass again from the
Voice word boundaries with today's rules, for a version made before a rule changed; $0. `style` sets the look of the
captions and callouts (graphics.normalize_look: a preset, font, colours, words per line, the caption position auto or
fixed) and pins callouts where a person dragged them on the canvas (span `callout_xy`), or back to auto; $0.
`music` puts a bundled music bed under the speech, ducked while anyone talks (engine/music.py, EDL `music`), changes
its track or level, or takes it out; $0.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

from .engine.api import dump_json
from .engine.edges import Audio, Words, reset_edges, tighten_edges
from .engine.edl import build_edl, carry_run_words, edl_summary, normalize_roles
from .engine.graphics import Look, normalize_look
from .engine.music import DUCK_DB as MUSIC_DUCK_DB, LEVEL_DB as MUSIC_LEVEL_DB, normalize_music
from .engine.punch import parse_key
from .engine.review import (REACH_S, Fix, _caption_at, _src_time, apply_fixes, cut_map, finalize, kept_elsewhere,
                            refresh_span, review_loop)
from .engine.voice import VoiceResult
from .ledger import Ledger
from .run import RunLog, render_run

VERSIONED = re.compile(r"(?:edl|cut|review)_v(\d+)\.(?:json|mp4)")


def next_version(run_dir: Path) -> int:
    return max((int(m.group(1)) for p in run_dir.iterdir() if (m := VERSIONED.fullmatch(p.name))), default=0) + 1


def latest_edl(run_dir: Path) -> int:
    ns = [int(m.group(1)) for p in run_dir.glob("edl_v*.json") if (m := re.fullmatch(r"edl_v(\d+)\.json", p.name))]
    if not ns:
        raise SystemExit(f"no edl_vN.json in {run_dir}")
    return max(ns)


def _words(run_dir: Path) -> Words:
    return Words(json.loads((run_dir / "voice" / "voice.json").read_text())["words"])


DEFER: list[int] | None = None  # montivo.agent chains several edits: each one's version is kept here, unrendered


def _finish(run_dir: Path, edl_path: Path, n: int, passes: int, budget: float, ledger: Ledger, log) -> dict:
    """Render cut_vN.mp4 from the new EDL, then the review loop from it when passes > 0."""
    if DEFER is not None:
        DEFER.append(n)
        return {"version": n, "edl": edl_path.name, "deferred": True}
    render_run(run_dir, edl_path=edl_path, out_name=f"cut_v{n}.mp4", ledger=ledger, log=log)
    loop = None
    if passes > 0:
        loop = review_loop(run_dir, edl_path, passes=passes, budget_usd=budget, ledger=ledger, log=log)
    log(f"human: v{n} ready ({edl_path.name}, cut_v{n}.mp4{', review: ' + loop['stop'] if loop else ''})")
    return {"version": n, "edl": edl_path.name, "cut": f"cut_v{n}.mp4", "review": loop}


def drop(run_dir: Path, span_id: str, from_v: int | None = None, passes: int = 0, budget: float = 0.15) -> dict:
    """Drop one span of edl_v{from} -> edl_vN: the reviewer's drop_span patch, then the two edges that now meet are
    tightened to the speech again (they used to face the dropped span)."""
    log = RunLog(run_dir / "run.log")
    from_v = from_v or latest_edl(run_dir)
    edl = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    ids = [s["id"] for s in edl["spans"]]
    if span_id not in ids:
        raise SystemExit(f"{span_id} is not a span of edl_v{from_v} ({', '.join(ids)})")
    if len(ids) == 1:
        raise SystemExit("cannot drop the only span")
    n = next_version(run_dir)
    log(f"montivo drop: {span_id} from edl_v{from_v} -> edl_v{n}")
    words, audio = _words(run_dir), Audio.load(run_dir)
    k = ids.index(span_id)
    neighbours = {(ids[k - 1], "out")} if k > 0 else set()
    if k + 1 < len(ids):
        neighbours.add((ids[k + 1], "in"))
    fix = Fix(op="drop_span", target=span_id, issue=-1, severity="human", t_s=0.0)
    new, plog = apply_fixes(edl, [fix], words, audio)
    if audio is not None and neighbours:
        for m in tighten_edges(new, words, audio, only=neighbours)["moves"]:
            plog[0]["note"] += f"; {m['span']} {m['edge']} edge tightened to the speech {m['from']:.3f} -> {m['to']:.3f}"
        for s in new["spans"]:
            refresh_span(s, words)
    new["version"] = n
    new.setdefault("patches", []).append({"from": from_v, "review": None, "source": "human", "action": "drop",
                                          "fixes": plog})
    log(f"patch v{from_v} -> v{n}: drop_span {span_id}: {plog[0]['note']} (human)")
    edl_path = run_dir / f"edl_v{n}.json"
    dump_json(edl_path, new)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def trim_fix(edl: dict, words: Words, span_id: str, edge: str, word: int) -> Fix:
    """The reviewer's own edge op for a human drag on the timeline: `word` is the turn word index the dragged edge
    should land on (the span's new word_to for edge out, word_from for edge in). Inward = trim_span at a source time
    picked so trim_at keeps exactly up to that word; outward = extend_span by the length of the added words. Both then
    take the reviewer's path: a sentence end within two words, the quietest point nearby, the edge pass."""
    s = next((x for x in edl["spans"] if x["id"] == span_id), None)
    if s is None:
        raise SystemExit(f"{span_id} is not a span of this EDL ({', '.join(x['id'] for x in edl['spans'])})")
    if edge not in ("in", "out"):
        raise SystemExit("edge is in or out")
    tw = words.turn(s["turn_id"])
    a, b = s["word_from"], s["word_to"]
    if not 0 <= word < len(tw):
        raise SystemExit(f"word {word} is outside turn {s['turn_id']} (0..{len(tw) - 1})")
    if (edge == "out" and word == b) or (edge == "in" and word == a):
        raise SystemExit("the edge is already on that word")
    if (edge == "out" and word < a + 1) or (edge == "in" and word > b - 1):
        raise SystemExit("a clip keeps at least two words: delete it instead")
    mk = lambda op, **args: Fix(op=op, target=span_id, issue=-1, severity="human", t_s=0.0, args=args)  # noqa: E731
    if edge == "out" and word < b:  # trim_at keeps the words that start before src_t - TRIM_SLACK_S, reach stops at word
        return mk("trim_span", edge="out", src_t=round(tw[word + 1]["t_start"] + REACH_S - 0.001, 3))
    if edge == "in" and word > a:  # ... the words that end after src_t + TRIM_SLACK_S, reach starts at word
        return mk("trim_span", edge="in", src_t=round(tw[word - 1]["t_end"] - REACH_S + 0.001, 3))
    added = range(b + 1, word + 1) if edge == "out" else range(word, a)
    if set(added) & kept_elsewhere(edl, s):
        raise SystemExit("those words are already in another clip")
    seconds = sum(tw[i]["t_end"] - tw[i]["t_start"] for i in added) - 0.001
    return mk("extend_span", edge=edge, seconds=round(seconds if edge == "out" else -seconds, 3))


def trim(run_dir: Path, span_id: str, edge: str, word: int, from_v: int | None = None, passes: int = 0,
         budget: float = 0.15) -> dict:
    """Move one edge of one span of edl_v{from} to a word boundary -> edl_vN, through apply_fixes like a review fix."""
    log = RunLog(run_dir / "run.log")
    from_v = from_v or latest_edl(run_dir)
    edl = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    words, audio = _words(run_dir), Audio.load(run_dir)
    fix = trim_fix(edl, words, span_id, edge, word)
    n = next_version(run_dir)
    log(f"montivo trim: {span_id} {edge} edge to word {word} in edl_v{from_v} -> edl_v{n}")
    new, plog = apply_fixes(edl, [fix], words, audio)
    hold_trimmed_edges(edl, new, [fix], plog, words, audio)
    new["version"] = n
    new.setdefault("patches", []).append({"from": from_v, "review": None, "source": "human", "action": "trim",
                                          "fixes": plog})
    log(f"patch v{from_v} -> v{n}: {fix.op} {span_id} {edge}: {plog[0]['note']} (human)")
    edl_path = run_dir / f"edl_v{n}.json"
    dump_json(edl_path, new)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def caption_fixes(edl: dict, edits: list[tuple[float, str]]) -> list[Fix]:
    """One caption_text Fix per (cut second, text): the caption line on screen at that second (edl_vN's rendered
    `captions`) gets the words actually spoken, the reviewer's own caption_text patch. The timing of the line stays."""
    if not edl.get("captions"):
        raise SystemExit("this EDL has no rendered captions (render it first)")
    cmap = cut_map(edl)
    fixes, seen = [], set()
    for t, text in edits:
        text = " ".join(text.split())
        if not text:
            raise SystemExit("the caption text is empty")
        cap = _caption_at(edl, t, within=0.0)
        if cap is None:
            raise SystemExit(f"no caption line on screen at {t:.2f} s")
        if cap["id"] in seen:
            raise SystemExit(f"two edits for the line at {t:.2f} s ({cap['text']!r})")
        seen.add(cap["id"])
        if text == cap["text"]:
            raise SystemExit(f"the line at {t:.2f} s already reads {text!r}")
        sid = cap["spans"][0]
        src_t = round(_src_time(edl, cmap, sid, cap["words"][0]["t_in"]) + 0.01, 3)
        fixes.append(Fix(op="caption_text", target=f"{sid}@{src_t:.2f}", issue=-1, severity="human", t_s=t,
                         args={"span": sid, "src_t": src_t, "text": text, "was": cap["text"]}))
    return fixes


def carry_caption_text(prev: dict, new: dict) -> list[dict]:
    """The retexted caption lines of a rendered EDL (a human's or the reviewer's caption_text) as overrides for a
    re-plan: the text goes to the new span over the same source time, with the words Voice heard on that line
    (`voice`), so the renderer applies it only where the new plan breaks a line the same way (captions.override_for)."""
    texts = [o for o in prev.get("caption_overrides") or [] if o.get("text")]
    out = []
    for c in prev.get("captions") or []:
        heard = next((w["voice"] for w in c.get("words") or [] if w.get("voice")), None)
        ov = next((o for o in reversed(texts) if o["span"] in c["spans"] and o["text"] == c["text"]), None)
        if not heard or ov is None:
            continue
        src_t = float(ov["src_t"])
        s = next((s for s in new["spans"] if s["t_in"] <= src_t < s["t_out"]), None)
        if s is not None:
            out.append({"span": s["id"], "src_t": src_t, "text": c["text"], "voice": heard, "why": ov.get("why")})
    return out


def caption(run_dir: Path, edits: list[tuple[float, str]], from_v: int | None = None, passes: int = 0,
            budget: float = 0.15) -> dict:
    """Retext caption lines of edl_v{from} -> edl_vN, rendered: for the words Voice misheard (run 015: "meta meta
    models" for "Four Meta models"). $0 without --review."""
    log = RunLog(run_dir / "run.log")
    from_v = from_v or latest_edl(run_dir)
    edl = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    fixes = caption_fixes(edl, edits)
    n = next_version(run_dir)
    log(f"montivo caption: {len(fixes)} line(s) in edl_v{from_v} -> edl_v{n}")
    new, plog = apply_fixes(edl, fixes, _words(run_dir))
    new["version"] = n
    new.setdefault("patches", []).append({"from": from_v, "review": None, "source": "human", "action": "caption",
                                          "fixes": plog})
    for f in fixes:
        log(f"patch v{from_v} -> v{n}: caption_text at {f.t_s:.2f} s: {f.args['was']!r} -> {f.args['text']!r} (human)")
    edl_path = run_dir / f"edl_v{n}.json"
    dump_json(edl_path, new)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def punch(run_dir: Path, on: bool | None = None, skip: str | None = None, from_v: int | None = None, passes: int = 0,
          budget: float = 0.15) -> dict:
    """Punch-ins on or off (`on`), or one punch-in removed (`skip`, a key from edl_v{from}'s `punch_ins`) ->
    edl_vN, rendered. The EDL's `punch` and `punch_skip` travel with every later version (review patches deep-copy the
    EDL, replan carries them), so a removed punch-in stays removed and the next render plans the rest again."""
    if (on is None) == (skip is None):
        raise SystemExit("give --on, --off or --skip KEY")
    log = RunLog(run_dir / "run.log")
    from_v = from_v or latest_edl(run_dir)
    edl = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    new = copy.deepcopy(edl)
    if skip is not None:
        try:
            parse_key(skip)
        except ValueError as e:
            raise SystemExit(str(e))
        entry = next((e for e in edl.get("punch_ins") or [] if e.get("key") == skip), None)
        if not edl.get("punch") or entry is None:
            have = ", ".join(e["key"] for e in edl.get("punch_ins") or [] if e.get("key")) or "none"
            raise SystemExit(f"{skip} is not a punch-in of edl_v{from_v} (punch-ins: {have})")
        new["punch_skip"] = list(edl.get("punch_skip") or []) + [skip]
        what = entry.get("word") or f"the jump cut into {entry.get('span')}"
        fix = {"op": "punch_skip", "target": skip, "issue": -1, "severity": "human",
               "note": f"punch-in removed: {what} at {entry['t_in']:.2f} s (x{entry['zoom']})"}
    else:
        if bool(edl.get("punch")) == on:
            raise SystemExit(f"punch-ins are already {'on' if on else 'off'} in edl_v{from_v}")
        new["punch"] = on
        n_skip = len(edl.get("punch_skip") or [])
        fix = {"op": "punch_on" if on else "punch_off", "target": "cut", "issue": -1, "severity": "human",
               "note": f"punch-ins {'on' if on else 'off'}" + (f" ({n_skip} removed by hand stay removed)" if n_skip else "")}
    n = next_version(run_dir)
    log(f"montivo punch: {fix['op']} {fix['target']} in edl_v{from_v} -> edl_v{n}")
    for k in ("captions", "callouts", "reframe", "punch_ins"):
        new[k] = []
    new.pop("render", None)
    new["version"] = n
    new.setdefault("patches", []).append({"from": from_v, "review": None, "source": "human", "action": "punch",
                                          "fixes": [fix]})
    log(f"patch v{from_v} -> v{n}: {fix['op']} {fix['target']}: {fix['note']} (human)")
    edl_path = run_dir / f"edl_v{n}.json"
    dump_json(edl_path, new)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def _new_version(run_dir: Path, from_v: int | None) -> tuple[int, dict, int]:
    from_v = from_v or latest_edl(run_dir)
    return from_v, json.loads((run_dir / f"edl_v{from_v}.json").read_text()), next_version(run_dir)


def _save(run_dir: Path, new: dict, n: int, from_v: int, action: str, fixes: list[dict], log) -> Path:
    new["version"] = n
    new.setdefault("patches", []).append({"from": from_v, "review": None, "source": "human", "action": action,
                                          "fixes": fixes})
    for f in fixes:
        log(f"patch v{from_v} -> v{n}: {f['op']} {f['target']}: {f['note']} (human)")
    edl_path = run_dir / f"edl_v{n}.json"
    dump_json(edl_path, new)
    return edl_path


def text_fixes(edl: dict, callouts: dict[str, str], title: str | None, title_card: bool | None) -> list[dict]:
    """The patch entries of a text edit: callout_text per span ("" removes it), title_text, title_off / title_on.
    Raises SystemExit for a span that is not in the EDL or an edit that changes nothing."""
    by_id = {s["id"]: s for s in edl["spans"]}
    out = []
    for sid, text in callouts.items():
        if sid not in by_id:
            raise SystemExit(f"{sid} is not a span of this EDL ({', '.join(by_id)})")
        text = " ".join(text.split())
        was = (by_id[sid].get("callout") or "").strip()
        if text == was:
            continue
        out.append({"op": "callout_text", "target": sid, "issue": -1, "severity": "human", "args": {"text": text, "was": was},
                    "note": f"callout {was!r} -> {text!r}" if text and was else f"callout {text!r} added" if text
                    else f"callout {was!r} removed"})
    if title is not None:
        title = " ".join(title.split())
        if not title:
            raise SystemExit("the title is empty: use --title-off to drop the card")
        if title != (edl.get("title_card_text") or ""):
            out.append({"op": "title_text", "target": "title", "issue": -1, "severity": "human",
                        "args": {"text": title, "was": edl.get("title_card_text")},
                        "note": f"title card {edl.get('title_card_text')!r} -> {title!r} (new Muse Image card, $0.01)"})
    if title_card is not None and title_card != (edl.get("title_card", True) is not False):
        out.append({"op": "title_on" if title_card else "title_off", "target": "title", "issue": -1, "severity": "human",
                    "note": "title card back on" if title_card else "title card off"})
    if not out:
        raise SystemExit("nothing to change: the texts are already these")
    return out


def text(run_dir: Path, callouts: dict[str, str] | None = None, title: str | None = None, title_card: bool | None = None,
         from_v: int | None = None, passes: int = 0, budget: float = 0.15) -> dict:
    """Callout and title card text of edl_v{from} -> edl_vN, rendered."""
    log = RunLog(run_dir / "run.log")
    from_v, edl, n = _new_version(run_dir, from_v)
    fixes = text_fixes(edl, callouts or {}, title, title_card)
    log(f"montivo text: {len(fixes)} change(s) in edl_v{from_v} -> edl_v{n}")
    new, _ = apply_fixes(edl, [], _words(run_dir))  # a clean copy: render fields cleared, order and runtime as they were
    by_id = {s["id"]: s for s in new["spans"]}
    for f in fixes:
        if f["op"] == "callout_text":
            by_id[f["target"]]["callout"] = f["args"]["text"]
        elif f["op"] == "title_text":
            new["title_card_text"] = f["args"]["text"]
            new["title_card"] = True
        else:
            new["title_card"] = f["op"] == "title_on"
    edl_path = _save(run_dir, new, n, from_v, "text", fixes, log)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def look_note(was: Look, now: Look) -> str:
    """What changed between two looks, for the patch note: "preset marker -> box, font poppins, top 1300 -> 1180"."""
    a, b = was.to_json(), now.to_json()
    return ", ".join(f"{k} {a[k]!r} -> {b[k]!r}" for k in b if a[k] != b[k])


def style_fixes(edl: dict, look: dict | None, callouts: dict[str, list[float] | None]) -> tuple[dict | None, list[dict]]:
    """The patch entries of a style edit: one `look` entry (the look normalized from its preset, every key checked) and
    a `callout_pin` per span ([x, y] in the 1080x1920 frame, the pill's top-left) or `callout_auto` (None). Raises
    SystemExit for a bad look, a span without a callout or an edit that changes nothing."""
    out: list[dict] = []
    new_look = None
    if look is not None:
        try:
            now = normalize_look(look)
            was = normalize_look(edl.get("look"))
        except ValueError as e:
            raise SystemExit(f"look: {e}")
        if note := look_note(was, now):
            new_look = now.to_json()
            out.append({"op": "look", "target": "captions", "issue": -1, "severity": "human", "args": {"look": new_look},
                        "note": f"look: {note}"})
    by_id = {s["id"]: s for s in edl["spans"]}
    for sid, xy in callouts.items():
        s = by_id.get(sid)
        if s is None or not (s.get("callout") or "").strip():
            have = ", ".join(x["id"] for x in edl["spans"] if (x.get("callout") or "").strip()) or "none"
            raise SystemExit(f"{sid} has no callout in this EDL (callouts on: {have})")
        if xy is None:
            if not s.get("callout_xy"):
                continue
            out.append({"op": "callout_auto", "target": sid, "issue": -1, "severity": "human",
                        "note": f"callout {s['callout']!r} back to its automatic place"})
            continue
        xy = [round(float(xy[0])), round(float(xy[1]))]
        if not (0 <= xy[0] <= 1080 and 0 <= xy[1] <= 1920):
            raise SystemExit(f"callout position {xy} is outside the 1080x1920 frame")
        if s.get("callout_xy") == xy:
            continue
        out.append({"op": "callout_pin", "target": sid, "issue": -1, "severity": "human", "args": {"xy": xy},
                    "note": f"callout {s['callout']!r} pinned at x {xy[0]}, y {xy[1]}"})
    if not out:
        raise SystemExit("nothing to change: the look and the callout places are already these")
    return new_look, out


def style(run_dir: Path, look: dict | None = None, callouts: dict[str, list[float] | None] | None = None,
          from_v: int | None = None, passes: int = 0, budget: float = 0.15, prompt: str | None = None) -> dict:
    """The look of the captions and callouts and the callout places of edl_v{from} -> edl_vN, rendered, $0. The EDL's
    `look` and every span's `callout_xy` travel with later versions (review patches deep-copy the EDL, replan carries
    them), and the review loop keeps a fixed caption position (review.gate_check, resolve)."""
    log = RunLog(run_dir / "run.log")
    from_v, edl, n = _new_version(run_dir, from_v)
    ledger = Ledger.resume(run_dir / "ledger.json")
    why = None
    if prompt:
        from .engine.look import suggest
        log(f"montivo style: the look for {prompt!r} (one Spark call)")
        try:
            look, why, corrected = suggest(run_dir, edl, prompt, ledger, log)
        except ValueError as e:
            raise SystemExit(f"Spark's look did not check out: {e}")
        if corrected:
            log(f"style: corrected Spark's answer: {'; '.join(corrected)}")
    new_look, fixes = style_fixes(edl, look, callouts or {})
    if why and fixes[0]["op"] == "look":
        fixes[0]["note"] += f" ({why})"
        fixes[0]["args"]["prompt"] = prompt
    log(f"montivo style: {len(fixes)} change(s) in edl_v{from_v} -> edl_v{n}")
    new, _ = apply_fixes(edl, [], _words(run_dir))
    if new_look is not None:
        new["look"] = new_look
    by_id = {s["id"]: s for s in new["spans"]}
    for f in fixes:
        if f["op"] == "callout_pin":
            by_id[f["target"]]["callout_xy"] = f["args"]["xy"]
        elif f["op"] == "callout_auto":
            by_id[f["target"]].pop("callout_xy", None)
    edl_path = _save(run_dir, new, n, from_v, "style", fixes, log)
    if prompt:
        new["patches"][-1]["instruction"] = prompt
        dump_json(edl_path, new)
    return _finish(run_dir, edl_path, n, passes, budget, ledger, log)


def music_fix(edl: dict, track: str | None, level_db: float | None = None, duck_db: float | None = None,
              voice_db: float | None = None) -> tuple[dict | None, dict]:
    """The EDL's next `music` and the patch entry: track None = off; a level, duck or voice gain left out keeps the
    current one (else the defaults; voice 0 dB). Raises SystemExit for an unknown track, a value out of range or an
    edit that changes nothing."""
    was = edl.get("music") or None
    if track is None:
        if level_db is not None or duck_db is not None or voice_db is not None:
            raise SystemExit("--off takes no level")
        if not was:
            raise SystemExit("there is no music to take out")
        return None, {"op": "music_off", "target": "audio", "issue": -1, "severity": "human",
                      "note": f"music {was['track']} off"}
    want = {"track": track,
            "level_db": level_db if level_db is not None else (was or {}).get("level_db", MUSIC_LEVEL_DB),
            "duck_db": duck_db if duck_db is not None else (was or {}).get("duck_db", MUSIC_DUCK_DB),
            "voice_db": voice_db if voice_db is not None else (was or {}).get("voice_db", 0.0)}
    try:
        now = normalize_music(want)
    except ValueError as e:
        raise SystemExit(str(e))
    try:
        was = normalize_music(was)
    except ValueError:
        pass  # a hand-written music entry the bundled tracks no longer know: replaced as it is
    if was and was == now:
        raise SystemExit(f"the music is already {now['track']} at {now['level_db']:g} LUFS, ducked {now['duck_db']:g} dB"
                         + (f", voice {now['voice_db']:+g} dB" if now.get("voice_db") else ""))
    val = lambda m, k: m.get(k, 0.0 if k == "voice_db" else None)  # noqa: E731 (voice_db is only kept when not 0)
    changed = ", ".join(f"{k} {val(was, k)!r} -> {val(now, k)!r}" for k in ("track", "level_db", "duck_db", "voice_db")
                        if val(was, k) != val(now, k)) if was else None
    note = f"music {changed}" if changed else (f"music {now['track']} under the speech at {now['level_db']:g} LUFS, "
                                              f"ducked {now['duck_db']:g} dB while anyone speaks")
    return now, {"op": "music", "target": "audio", "issue": -1, "severity": "human", "args": {"music": now}, "note": note}


def music(run_dir: Path, track: str | None, level_db: float | None = None, duck_db: float | None = None,
          from_v: int | None = None, passes: int = 0, budget: float = 0.15, voice_db: float | None = None) -> dict:
    """A music bed under edl_v{from}'s speech, another track or level, or none -> edl_vN, rendered, $0 (ffmpeg only).
    The EDL's `music` travels with later versions like `look` (review patches deep-copy the EDL, replan carries it)."""
    log = RunLog(run_dir / "run.log")
    from_v, edl, n = _new_version(run_dir, from_v)
    now, fix = music_fix(edl, track, level_db, duck_db, voice_db)
    log(f"montivo music: {fix['note']} in edl_v{from_v} -> edl_v{n}")
    new, _ = apply_fixes(edl, [], _words(run_dir))
    if now:
        new["music"] = now
    else:
        new.pop("music", None)
    edl_path = _save(run_dir, new, n, from_v, "music", [fix], log)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def carry_callout_places(prev: dict, new: dict) -> list[str]:
    """A re-plan keeps a pinned callout where a person put it when the new plan has the same callout text."""
    pins = {(s.get("callout") or "").strip().lower(): s["callout_xy"] for s in prev["spans"] if s.get("callout_xy")}
    out = []
    for s in new["spans"]:
        key = (s.get("callout") or "").strip().lower()
        if key and key in pins:
            s["callout_xy"] = list(pins[key])
            out.append(s["callout"])
    return out


def roles_by_position(spans: list[dict]) -> list[str]:
    """edl.normalize_roles on EDL span dicts: after a move, the first clip is the hook and the last the payoff."""
    ns = [SimpleNamespace(id=s["id"], role=s["role"], planner_role=s.get("planner_role")) for s in spans]
    notes = normalize_roles(ns)
    for s, x in zip(spans, ns):
        s["role"] = x.role
        if x.planner_role:
            s["planner_role"] = x.planner_role
    return notes


def reorder(run_dir: Path, span_id: str, before: str | None, from_v: int | None = None, passes: int = 0,
            budget: float = 0.15) -> dict:
    """Move one clip of edl_v{from} before another (or to the end, before=None) -> edl_vN: the reviewer's reorder
    patch; the cuts that now meet (around the gap it left and around its new place) are tightened to the speech."""
    log = RunLog(run_dir / "run.log")
    from_v, edl, n = _new_version(run_dir, from_v)
    ids = [s["id"] for s in edl["spans"]]
    if span_id not in ids or (before is not None and before not in ids):
        raise SystemExit(f"{span_id if span_id not in ids else before} is not a span of edl_v{from_v} ({', '.join(ids)})")
    k = ids.index(span_id)
    rest = [i for i in ids if i != span_id]
    at = rest.index(before) if before else len(rest)
    order = rest[:at] + [span_id] + rest[at:]
    if order == ids:
        raise SystemExit(f"{span_id} is already there")
    log(f"montivo reorder: {span_id} {'before ' + before if before else 'to the end'} in edl_v{from_v} -> edl_v{n}")
    words, audio = _words(run_dir), Audio.load(run_dir)
    fix = Fix(op="reorder", target=span_id, issue=-1, severity="human", t_s=0.0, args={"before": before})
    new, plog = apply_fixes(edl, [fix], words, audio)
    moved = order.index(span_id)
    meet = {(span_id, "in"), (span_id, "out")}
    for i, e in ((k - 1, "out"), (k + 1, "in")):  # the old neighbours now meet each other
        if 0 <= i < len(ids):
            meet.add((ids[i], e))
    for i, e in ((moved - 1, "out"), (moved + 1, "in")):  # the new neighbours
        if 0 <= i < len(order):
            meet.add((order[i], e))
    if audio is not None:
        for m in tighten_edges(new, words, audio, only=meet)["moves"]:
            plog[0]["note"] += f"; {m['span']} {m['edge']} edge tightened to the speech {m['from']:.3f} -> {m['to']:.3f}"
        for s in new["spans"]:
            refresh_span(s, words)
    roles = roles_by_position(new["spans"])
    if roles:
        plog[0]["note"] += f"; roles by position: {', '.join(roles)}"
    edl_path = _save(run_dir, new, n, from_v, "reorder", plog, log)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def cut_plan(edl: dict, words: Words, ranges: list[tuple[str, int, int]]) -> tuple[list[Fix], dict[str, list[tuple[int, int]]]]:
    """A transcript selection ((span id, first, last turn word index deleted) per range) as the ops that remove it: a
    whole clip -> drop_span, words at a clip's start or end -> trim_fix (the timeline drag's own op: reviewer rules, the
    edge may settle on a sentence end within two words), words inside a clip -> a split into the clip's kept word runs.
    Returns (the drop and trim fixes, {span id: its kept runs} for the splits)."""
    by_id = {s["id"]: s for s in edl["spans"]}
    gone: dict[str, set[int]] = {}
    for sid, a, b in ranges:
        s = by_id.get(sid)
        if s is None:
            raise SystemExit(f"{sid} is not a span of this EDL ({', '.join(by_id)})")
        if not s["word_from"] <= a <= b <= s["word_to"]:
            raise SystemExit(f"words {a}-{b} are not all in {sid} (words {s['word_from']}-{s['word_to']})")
        gone.setdefault(sid, set()).update(range(a, b + 1))
    if not gone:
        raise SystemExit("no words to delete")
    fixes, splits = [], {}
    for sid, rm in gone.items():
        s = by_id[sid]
        runs: list[tuple[int, int]] = []
        for i in range(s["word_from"], s["word_to"] + 1):
            if i in rm:
                continue
            if runs and runs[-1][1] == i - 1:
                runs[-1] = (runs[-1][0], i)
            else:
                runs.append((i, i))
        if not runs:
            fixes.append(Fix(op="drop_span", target=sid, issue=-1, severity="human", t_s=0.0))
        elif len(runs) == 1:
            a, b = runs[0]
            if a == b:
                raise SystemExit(f"{sid} would keep one word: a clip keeps at least two words, delete it instead")
            if a > s["word_from"]:
                fixes.append(trim_fix(edl, words, sid, "in", a))
            if b < s["word_to"]:
                fixes.append(trim_fix(edl, words, sid, "out", b))
        else:
            splits[sid] = runs
    if sum(f.op == "drop_span" for f in fixes) == len(by_id):
        raise SystemExit("that deletes every clip")
    return fixes, splits


SPLIT_SETTLE_S = 0.1  # run 015: Voice put the second "and" 0.25 s late; the and/we dip sat 0.08 s before its "we"


def settle_split(edl: dict, words: Words, audio: Audio, span_id: str, edge: str) -> str:
    """A split edge still inside speech after the edge pass (Voice's boundary between a deleted and a kept word can be
    off by a few hundred ms, and those words abut) moves to the quietest 40 ms within SPLIT_SETTLE_S, when that is
    clearly quieter: the dip between the two words. Returns a note, empty when nothing moved."""
    s = next(x for x in edl["spans"] if x["id"] == span_id)
    key = "t_in" if edge == "in" else "t_out"
    t = s[key]
    now = audio.level(t - 0.02, t + 0.02)
    if now <= audio.threshold_db():
        return ""
    x, db = audio.quietest(t - SPLIT_SETTLE_S, t + SPLIT_SETTLE_S, step=0.01)
    if db > now - 3.0 or abs(x - t) < 0.005 or (x >= s["t_out"] if edge == "in" else x <= s["t_in"]):
        return ""
    s[key] = x
    s.setdefault("edge_tuned", {})[edge] = x
    s["duration"] = round(s["t_out"] - s["t_in"], 3)
    return f"; {span_id} {edge} edge settled on the dip between the words {t:.3f} -> {x:.3f} ({now:.1f} -> {db:.1f} dB)"


def hold_trimmed_edges(edl: dict, new: dict, fixes: list[Fix], plog: list[dict], words: Words, audio: Audio | None,
                       keep_out: dict[int, set[int]] | None = None) -> None:
    """After apply_fixes, a trimmed edge that settled or widened back over speech more than SPLIT_SETTLE_S into the
    words the trim removed (they abut the kept ones in continuous speech: run 015's "checks its | own work" came back
    whole) goes to
    the Voice boundary again, through the edge pass with the removed words (and `keep_out`) blocked, then onto the dip
    between the words. The clip's other edge keeps the place it had (trim_at puts both edges back on Voice boundaries
    and apply_fixes widens both: a trim at the end of run 015's s9 re-opened its start over the "and" deleted before).
    Notes each change on the fix's entry of `plog` (apply_fixes' log)."""
    old = {s["id"]: s for s in edl["spans"]}
    cur = {s["id"]: s for s in new["spans"]}
    held = False
    for f in fixes:
        if f.op != "trim_span" or f.target not in cur:
            continue
        s, was, edge = cur[f.target], old[f.target], f.args["edge"]
        tw = words.turn(s["turn_id"])
        entry = next(e for e in plog if e["op"] == "trim_span" and e["target"] == f.target and e["args"]["edge"] == edge)
        other, okey = ("in", "t_in") if edge == "out" else ("out", "t_out")
        wkey = "word_from" if other == "in" else "word_to"
        if s[wkey] == was[wkey] and abs(s[okey] - was[okey]) >= 0.005:
            entry["note"] += f"; the {other} edge stays where it was {s[okey]:.3f} -> {was[okey]:.3f}"
            s[okey] = was[okey]
            for d in ("edge_tuned", "edges"):
                if other in (was.get(d) or {}):
                    s.setdefault(d, {})[other] = was[d][other]
                else:
                    (s.get(d) or {}).pop(other, None)
            refresh_span(s, words)
            held = True
        if audio is None:
            continue
        if edge == "out":
            gone, line = set(range(s["word_to"] + 1, was["word_to"] + 1)), tw[s["word_to"]]["t_end"]
            past = s["t_out"] - line
        else:
            gone, line = set(range(was["word_from"], s["word_from"])), tw[s["word_from"]]["t_start"]
            past = line - s["t_in"]
        spoken = (audio.speech_runs(line + SPLIT_SETTLE_S, s["t_out"]) if edge == "out"
                  else audio.speech_runs(s["t_in"], line - SPLIT_SETTLE_S))
        if not gone or past <= SPLIT_SETTLE_S or not spoken:  # only a pause past the boundary: the edge may stay there
            continue
        key = "t_out" if edge == "out" else "t_in"
        before = s[key]
        s[key] = line
        for d in ("edge_tuned", "edges"):
            (s.get(d) or {}).pop(edge, None)
        blocked = {t: set(v) for t, v in (keep_out or {}).items()}
        blocked.setdefault(s["turn_id"], set()).update(gone)
        tighten_edges(new, words, audio, only={(s["id"], edge)}, keep_out=blocked)
        settle_split(new, words, audio, s["id"], edge)
        refresh_span(s, words)
        entry["note"] += f"; held out of the removed words {before:.3f} -> {s[key]:.3f}"
        held = True
    if held:
        new["runtime_s"] = round(sum(x["duration"] for x in new["spans"]), 2)


def cut(run_dir: Path, ranges: list[tuple[str, int, int]], from_v: int | None = None, passes: int = 0,
        budget: float = 0.15) -> dict:
    """Delete the words of a transcript selection from edl_v{from} -> one edl_vN: drops and trims through apply_fixes
    like the reviewer's, splits as new spans on the kept words' boundaries; every cut that now meets is tightened to the
    speech, and roles follow the position."""
    log = RunLog(run_dir / "run.log")
    from_v, edl, n = _new_version(run_dir, from_v)
    words, audio = _words(run_dir), Audio.load(run_dir)
    fixes, splits = cut_plan(edl, words, ranges)
    total = len({(sid, i) for sid, a, b in ranges for i in range(a, b + 1)})
    sids = list(dict.fromkeys(sid for sid, _, _ in ranges))
    log(f"montivo cut: {total} word{'s' if total != 1 else ''} of {', '.join(sids)} in edl_v{from_v} -> edl_v{n}")
    ids = [s["id"] for s in edl["spans"]]
    by_id = {s["id"]: s for s in edl["spans"]}
    deleted: dict[int, set[int]] = {}  # no edge may settle or widen back into the words the selection deleted
    for sid, a, b in ranges:
        deleted.setdefault(by_id[sid]["turn_id"], set()).update(range(a, b + 1))
    new, plog = apply_fixes(edl, fixes, words, audio)
    hold_trimmed_edges(edl, new, fixes, plog, words, audio, keep_out=deleted)
    meet: set[tuple[str, str]] = set()
    dropped = {f.target for f in fixes if f.op == "drop_span"}
    alive = [k for k, sid in enumerate(ids) if sid not in dropped]
    for k, j in zip(alive, alive[1:]):  # the clips that now meet across a dropped run
        if j > k + 1:
            meet |= {(ids[k], "out"), (ids[j], "in")}
    if dropped and alive[0] > 0:
        meet.add((ids[alive[0]], "in"))
    if dropped and alive[-1] < len(ids) - 1:
        meet.add((ids[alive[-1]], "out"))
    taken = [int(m.group(1)) for x in [*edl["spans"], *({"id": k.split(":", 1)[1]} for k in edl.get("punch_skip") or [])]
             if (m := re.fullmatch(r"s(\d+)", x["id"]))]
    next_id = max(taken, default=0) + 1
    split_edges: set[tuple[str, str]] = set()
    for sid, runs in splits.items():
        k = next(i for i, x in enumerate(new["spans"]) if x["id"] == sid)
        s = new["spans"][k]
        tw = words.turn(s["turn_id"])
        pieces = []
        for j, (a, b) in enumerate(runs):
            p = copy.deepcopy(s)
            if j:  # the callout stays on the first piece, the place it was timed from
                p["id"], next_id = f"s{next_id}", next_id + 1
                p["callout"] = ""
                p.pop("callout_shift_s", None)
                p.pop("callout_xy", None)
            tuned = p.get("edge_tuned") or {}
            if a != s["word_from"]:
                p["t_in"] = tw[a]["t_start"]
                tuned.pop("in", None)
                split_edges.add((p["id"], "in"))
            if b != s["word_to"]:
                p["t_out"] = tw[b]["t_end"]
                tuned.pop("out", None)
                split_edges.add((p["id"], "out"))
            if not tuned:
                p.pop("edge_tuned", None)
            p["word_from"], p["word_to"] = a, b
            refresh_span(p, words)
            pieces.append(p)
        new["spans"][k:k + 1] = pieces
        ovs = new.get("caption_overrides") or []  # an override goes with the piece over its source time, or goes
        for o in [o for o in ovs if o.get("span") == sid]:
            home = next((p for p in pieces if p["t_in"] - 0.05 <= float(o["src_t"]) < p["t_out"]), None)
            if home is None:
                ovs.remove(o)
            else:
                o["span"] = home["id"]
        cutw = [tw[i]["w"] for i in range(s["word_from"], s["word_to"] + 1) if not any(a <= i <= b for a, b in runs)]
        plog.append({"op": "split_span", "target": sid, "issue": -1, "severity": "human", "t_s": 0.0,
                     "args": {"runs": [list(r) for r in runs], "ids": [p["id"] for p in pieces]},
                     "note": f"{sid} split around {len(cutw)} deleted word{'s' if len(cutw) != 1 else ''} "
                             f"({' '.join(cutw)[:60]!r}): " + " | ".join(f"{p['id']} {p['words'][:40]}" for p in pieces)})
    if splits:
        finalize(new, words)
    meet |= split_edges
    if audio is not None and meet:
        for m in tighten_edges(new, words, audio, only=meet, keep_out=deleted)["moves"]:
            plog[-1]["note"] += f"; {m['span']} {m['edge']} edge tightened to the speech {m['from']:.3f} -> {m['to']:.3f}"
        for sid, edge in sorted(split_edges):
            plog[-1]["note"] += settle_split(new, words, audio, sid, edge)
        for s in new["spans"]:
            refresh_span(s, words)
        new["runtime_s"] = round(sum(s["duration"] for s in new["spans"]), 2)
    roles = roles_by_position(new["spans"])
    if roles:
        plog[-1]["note"] += f"; roles by position: {', '.join(roles)}"
    edl_path = _save(run_dir, new, n, from_v, "cut", plog, log)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def head_ids(run_dir: Path) -> list[str]:
    p = run_dir / "track" / "head" / "tracks.json"
    return sorted((json.loads(p.read_text()).get("objects") or {}) if p.is_file() else {}, key=lambda x: int(x) if x.isdigit() else 0)


def pin(run_dir: Path, speaker: str, head: str | None, from_v: int | None = None, passes: int = 0,
        budget: float = 0.15) -> dict:
    """The crop follows head `head` whenever `speaker` talks (EDL `reframe_speaker_ids`, render.load_speaker_ids);
    head None drops that speaker's pin, back to the speaker map. -> edl_vN, rendered, $0."""
    log = RunLog(run_dir / "run.log")
    from_v, edl, n = _new_version(run_dir, from_v)
    voice = json.loads((run_dir / "voice" / "voice.json").read_text())
    speakers = [str(x) for x in voice.get("speakers") or []]
    if speaker not in speakers:
        raise SystemExit(f"{speaker} is not a Voice speaker of this run ({', '.join(speakers) or 'none'})")
    if (edl.get("reframe_concept") or "head") != "head":
        raise SystemExit("this cut's crop follows the person track, not heads: a pin has nothing to follow")
    heads = head_ids(run_dir)
    if head is not None and head not in heads:
        raise SystemExit(f"head {head} is not in track/head/tracks.json ({', '.join(heads) or 'no head track'})")
    pins = dict(edl.get("reframe_speaker_ids") or {})
    if pins.get(speaker) == head or (head is None and speaker not in pins):
        raise SystemExit(f"speaker {speaker} is already {'on head ' + head if head else 'unpinned'}")
    log(f"montivo pin: speaker {speaker} -> {'head ' + head if head else 'the speaker map'} in edl_v{from_v} -> edl_v{n}")
    new, _ = apply_fixes(edl, [], _words(run_dir))
    if head is None:
        pins.pop(speaker, None)
    else:
        pins[speaker] = head
    new["reframe_speaker_ids"] = pins
    if not pins:
        new.pop("reframe_speaker_ids")
    fix = {"op": "pin_speaker", "target": speaker, "issue": -1, "severity": "human", "args": {"head": head},
           "note": f"the crop follows head {head} when {speaker} talks" if head else f"{speaker} follows the speaker map again"}
    edl_path = _save(run_dir, new, n, from_v, "pin", [fix], log)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def edges(run_dir: Path, from_v: int | None = None, passes: int = 0, budget: float = 0.15) -> dict:
    """The edge pass (engine/edges.py) again with today's rules on every edge it moved before and nothing moved since
    (edges.reset_edges): run 015's v1-v4 open "Okay, hi." from before the dropped-word rule. -> edl_vN, rendered, $0."""
    log = RunLog(run_dir / "run.log")
    from_v, edl, n = _new_version(run_dir, from_v)
    words, audio = _words(run_dir), Audio.load(run_dir)
    if audio is None:
        raise SystemExit(f"{run_dir / 'audio.wav'} not found (ingest writes it)")
    new, _ = apply_fixes(edl, [], words)
    was = {s["id"]: (s["t_in"], s["t_out"]) for s in new["spans"]}
    tighten_edges(new, words, audio, only=reset_edges(new, words))
    fixes = []
    for s in new["spans"]:
        for edge, k in (("in", 0), ("out", 1)):
            a, b = was[s["id"]][k], (s["t_in"], s["t_out"])[k]
            if abs(a - b) >= 0.005:
                rec = (s.get("edges") or {}).get(edge) or {}
                why = ", a dropped word's sound skipped" if rec.get("fragment") else ""
                fixes.append({"op": "edge_pass", "target": s["id"], "issue": -1, "severity": "human",
                              "args": {"edge": edge, "from": a, "to": b},
                              "note": f"{edge} edge {a:.3f} -> {b:.3f} s (the edge pass again{why})"})
        refresh_span(s, words)
    if not fixes:
        raise SystemExit(f"the edge pass moves nothing in edl_v{from_v} with today's rules")
    log(f"montivo edges: the edge pass again on edl_v{from_v} -> edl_v{n}")
    edl_path = _save(run_dir, new, n, from_v, "edges", fixes, log)
    return _finish(run_dir, edl_path, n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)


def target_from(instruction: str) -> float | None:
    """'make it 30 seconds' -> 30.0: a runtime named in the instruction replaces the run's target."""
    m = re.search(r"(\d{2,3})\s*(?:s\b|sec|second)", instruction, re.I)
    return float(m.group(1)) if m and 10 <= float(m.group(1)) <= 180 else None


def replan(run_dir: Path, instruction: str, from_v: int | None = None, target_s: float | None = None,
           passes: int = 0, budget: float = 0.15, model: str | None = None, reasoning_effort: str | None = None) -> dict:
    """Plan again with the instruction added to the brief and the current paper edit for reference, reusing voice,
    watch and the tracks (no ingest, Voice or SAM spend), -> edl_vN on word boundaries with the edge pass, render.
    With no instruction it is a fresh plan from the run's own brief, blind to the current cut (`montivo plan`, the
    planner A/B); `model` picks the planner (default muse-spark-1.3, config.OPEN_MODEL for Muse Spark Open)."""
    from .config import SPARK_MODEL
    from .engine.spark import Spark
    log = RunLog(run_dir / "run.log")
    model = model or SPARK_MODEL
    from_v = from_v or latest_edl(run_dir)
    prev = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    run = json.loads((run_dir / "run.json").read_text())
    n = next_version(run_dir)
    target = target_s or (target_from(instruction) if instruction else None) or float(run.get("target_s") or 40.0)
    log(f"montivo replan: edl_v{from_v} + \"{instruction or 'fresh plan'}\" -> edl_v{n} (target {target:.0f} s, {model})")
    voice = VoiceResult.from_json(json.loads((run_dir / "voice" / "voice.json").read_text()))
    watch = json.loads((run_dir / "watch.json").read_text())
    fresh = not instruction
    if instruction:
        brief = f"{run['brief']}\n\nEditor's note for this version (it overrides the brief where they conflict): {instruction}"
        plan_brief = f"{brief}\n\nThe current cut (v{from_v}), for reference:\n{edl_summary(prev)}"
    else:
        brief = plan_brief = run["brief"]
        instruction = f"plan again from scratch with {model}"
    ledger = Ledger.resume(run_dir / "ledger.json")
    v1_call = next((e for e in ledger.entries if e.stage == "spark.plan"), None)  # read before this call adds its own
    spark = Spark(run_dir, ledger, model=model, log=log)
    sdir = run_dir / "spark"
    # Spark names its files after the call ("plan"): v1's request and response are copied to plan_v1_* on disk
    # first (they survive a kill), the new ones become plan_vN_* and v1's go back in place
    for part in ("request", "response"):
        p, v1 = sdir / f"plan_{part}.json", sdir / f"plan_v1_{part}.json"
        if p.is_file() and not v1.is_file():
            shutil.copy2(p, v1)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # the UI's stop button: run the finally below
    try:
        planned = spark.plan(plan_brief, voice.transcript_text(), watch, target, words_per_s=voice.speech_rate(),
                             reasoning_effort=reasoning_effort)
    finally:
        for part in ("request", "response"):
            p, v1 = sdir / f"plan_{part}.json", sdir / f"plan_v1_{part}.json"
            if p.is_file() and v1.is_file():
                shutil.move(p, sdir / f"plan_v{n}_{part}.json")
                shutil.copy2(v1, p)
    dump_json(run_dir / f"plan_v{n}.json", planned.parsed)
    new = build_edl(planned.parsed, voice, watch, prev["source"], brief, prev.get("tracks") or {},
                    reframe_concept=prev.get("reframe_concept") or "person", audio=Audio.load(run_dir))
    if not new["spans"]:
        raise SystemExit("the new plan placed no span on the transcript")
    if new["role_fixes"]:
        log(f"roles set by position (one hook first, the payoff last): {', '.join(new['role_fixes'])}")
    if new.get("brief_missing"):
        log(f"plan: not in the footage: {'; '.join(new['brief_missing'])}")
    new["version"] = n
    # the title card stays as it was: same text (no new Muse Image call), and off when the reviewer dropped it
    new["title_card_text"] = prev.get("title_card_text") or new["title_card_text"]
    if prev.get("title_card") is False:
        new["title_card"] = False
    if prev.get("punch"):
        new["punch"] = True
    if prev.get("punch_skip"):
        new["punch_skip"] = list(prev["punch_skip"])
    if prev.get("reframe_speaker_ids"):
        new["reframe_speaker_ids"] = dict(prev["reframe_speaker_ids"])
    if prev.get("look"):
        new["look"] = dict(prev["look"])
    if prev.get("music"):
        new["music"] = dict(prev["music"])
    carry_run_words(prev, new)
    if pinned := carry_callout_places(prev, new):
        log(f"callout places carried from v{from_v}: " + ", ".join(repr(t) for t in pinned))
    carried = carry_caption_text(prev, new)
    if carried:
        new.setdefault("caption_overrides", []).extend(carried)
        log(f"caption texts carried from v{from_v} (where a line reads the same): "
            + ", ".join(repr(o["text"]) for o in carried))
    note =f"replanned: {len(new['spans'])} spans, {new['runtime_s']} s (was {len(prev['spans'])} spans, {prev['runtime_s']} s)"
    new["patches"] = list(prev.get("patches") or []) + [
        {"from": from_v, "review": None, "source": "human", "action": "replan", "instruction": instruction,
         "model": model, "fixes": [{"op": "replan", "target": "plan", "issue": -1, "severity": "human", "note": note}]}]
    log(f"patch v{from_v} -> v{n}: replan: {note} (human)")
    for s in new["spans"]:
        log(f"  {s['id']} {s['role']:6s} {s['t_in']:7.2f}-{s['t_out']:7.2f}s {s['words'][:80]}")
    edl_path = run_dir / f"edl_v{n}.json"
    dump_json(edl_path, new)
    compare = None
    if fresh and (run_dir / "edl_v1.json").is_file():
        v1 = json.loads((run_dir / "edl_v1.json").read_text())
        compare = compare_plans(v1, new, run.get("planner_model") or run.get("model") or SPARK_MODEL, model,
                                v1_call, planned, target)
        dump_json(run_dir / f"plan_compare_v1_v{n}.json", compare)
        a, b = compare["v1"], compare[f"v{n}"]
        log(f"plan A/B: v1 {a['runtime_s']} s / {a['spans']} spans / verbatim {a['verbatim_rate']:.0%} vs "
            f"v{n} {b['runtime_s']} s / {b['spans']} spans / verbatim {b['verbatim_rate']:.0%}, ${planned.cost_usd:.4f}")
    out = _finish(run_dir, edl_path, n, passes, budget, ledger, log)
    return {**out, "compare": compare} if compare else out


def compare_plans(v1: dict, new: dict, v1_model: str, model: str, v1_call, planned, target_s: float) -> dict:
    """The planner A/B side by side (`montivo plan RUN --planner open`): spans, runtime, verbatim rate, unplaced
    passages, the hook and what each plan call cost. `v1_call` is v1's ledger entry (None when the ledger lost it)."""
    def side(e: dict, planner: str, cost: float | None, seconds: float | None) -> dict:
        return {"planner": planner, "spans": len(e["spans"]), "runtime_s": e["runtime_s"],
                "verbatim_rate": e["verbatim_rate"], "unplaced": len(e.get("unplaced") or []),
                "hook": e["spans"][0]["words"][:80] if e["spans"] else "", "plan_cost_usd": cost, "plan_seconds": seconds}
    return {"target_s": target_s,
            "v1": side(v1, v1_model, v1_call.cost_usd if v1_call else None, v1_call.seconds if v1_call else None),
            f"v{new['version']}": side(new, model, planned.cost_usd, planned.seconds)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m montivo.human", description="Human edits: the next EDL version, rendered.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("drop", help="drop one span -> edl_vN -> cut_vN ($0 without --review)")
    d.add_argument("run_dir", type=Path)
    d.add_argument("--span", required=True)
    t = sub.add_parser("trim", help="move one clip edge to a word boundary -> edl_vN -> cut_vN ($0 without --review)")
    t.add_argument("run_dir", type=Path)
    t.add_argument("--span", required=True)
    t.add_argument("--edge", choices=("in", "out"), required=True)
    t.add_argument("--word", type=int, required=True, help="turn word index the edge lands on (new word_from / word_to)")
    r = sub.add_parser("replan", help="plan again with an instruction -> edl_vN -> cut_vN (one Spark plan call)")
    r.add_argument("run_dir", type=Path)
    r.add_argument("--instruction", required=True)
    r.add_argument("--target", type=float, default=None, help="runtime target in seconds (default: from the instruction or run.json)")
    r.add_argument("--model", default=None, help="planner model id (default muse-spark-1.3)")
    pu = sub.add_parser("punch", help="punch-ins on or off, or remove one -> edl_vN -> cut_vN ($0 without --review)")
    pu.add_argument("run_dir", type=Path)
    g = pu.add_mutually_exclusive_group(required=True)
    g.add_argument("--on", dest="on", action="store_const", const=True, default=None)
    g.add_argument("--off", dest="on", action="store_const", const=False)
    g.add_argument("--skip", default=None, metavar="KEY", help="a punch_ins key of the EDL: emphasis:<word>@<s> or jump_cut:<span>")
    c = sub.add_parser("caption", help="retext caption lines -> edl_vN -> cut_vN ($0 without --review)")
    c.add_argument("run_dir", type=Path)
    c.add_argument("--at", type=float, action="append", required=True,
                   help="a cut second inside the line to retext (repeat with --text for more lines)")
    c.add_argument("--text", action="append", required=True, help="the words actually spoken on that line")
    tx = sub.add_parser("text", help="callout and title card text -> edl_vN -> cut_vN ($0, $0.01 for new title words)")
    tx.add_argument("run_dir", type=Path)
    tx.add_argument("--callout", action="append", default=[], metavar="SPAN=TEXT", help="a span's callout (empty removes it)")
    tx.add_argument("--title", default=None, help="new title card words (one Muse Image call)")
    tg = tx.add_mutually_exclusive_group()
    tg.add_argument("--title-off", dest="title_card", action="store_const", const=False, default=None)
    tg.add_argument("--title-on", dest="title_card", action="store_const", const=True)
    ro = sub.add_parser("reorder", help="move one clip -> edl_vN -> cut_vN ($0 without --review)")
    ro.add_argument("run_dir", type=Path)
    ro.add_argument("--span", required=True)
    rg = ro.add_mutually_exclusive_group(required=True)
    rg.add_argument("--before", default=None, help="the span it goes in front of")
    rg.add_argument("--end", action="store_true", help="to the end of the cut")
    pn = sub.add_parser("pin", help="the crop follows one head for a speaker -> edl_vN -> cut_vN ($0)")
    pn.add_argument("run_dir", type=Path)
    pn.add_argument("--speaker", required=True, help="a Voice speaker label (A, B, ...)")
    pn.add_argument("--head", required=True, help="a head id of track/head/tracks.json, or none to drop the pin")
    cu = sub.add_parser("cut", help="delete transcript words (drop, trim or split clips) -> edl_vN -> cut_vN ($0)")
    cu.add_argument("run_dir", type=Path)
    cu.add_argument("--words", action="append", required=True, metavar="SPAN:FROM-TO",
                    help="turn word indices to delete from a span, inclusive (repeat for more ranges)")
    ed = sub.add_parser("edges", help="the edge pass again with today's rules -> edl_vN -> cut_vN ($0 without --review)")
    ed.add_argument("run_dir", type=Path)
    sy = sub.add_parser("style", help="caption look and callout places -> edl_vN -> cut_vN ($0)")
    sy.add_argument("run_dir", type=Path)
    sy.add_argument("--look", default=None, help='the look as JSON: {"preset": "box", "font": "anton", ...}')
    sy.add_argument("--callout", action="append", default=[], metavar="SPAN=X,Y", help="pin a callout (SPAN=auto unpins it)")
    sy.add_argument("--prompt", default=None, help="describe the look in words: one Spark call (about $0.008) picks it")
    mu = sub.add_parser("music", help="a music bed under the speech, ducked while anyone talks -> edl_vN -> cut_vN ($0)")
    mu.add_argument("run_dir", type=Path)
    mg = mu.add_mutually_exclusive_group(required=True)
    mg.add_argument("--track", default=None, help="a track id of montivo/music/tracks.json")
    mg.add_argument("--off", action="store_true", help="take the music out")
    mu.add_argument("--level", type=float, default=None, help="the bed's loudness in LUFS where nobody speaks (default -20)")
    mu.add_argument("--duck", type=float, default=None, help="dB the bed drops while anyone speaks (default -12)")
    mu.add_argument("--voice", type=float, default=None, help="dB on the speech against the bed, -12..6 (default 0)")
    for p in (d, t, r, pu, c, tx, ro, pn, cu, ed, sy, mu):
        p.add_argument("--from", dest="from_v", type=int, default=None, help="EDL version to start from (default: the latest)")
        p.add_argument("--review", type=int, default=0, help="review passes after the render (default 0, none)")
        p.add_argument("--budget", type=float, default=0.15, help="max dollars for the review calls (default 0.15)")
    a = ap.parse_args(argv)
    if not a.run_dir.is_dir():
        ap.error(f"{a.run_dir} is not a run folder")
    if a.cmd == "drop":
        out = drop(a.run_dir, a.span, a.from_v, a.review, a.budget)
    elif a.cmd == "trim":
        out = trim(a.run_dir, a.span, a.edge, a.word, a.from_v, a.review, a.budget)
    elif a.cmd == "punch":
        out = punch(a.run_dir, a.on, a.skip, a.from_v, a.review, a.budget)
    elif a.cmd == "caption":
        if len(a.at) != len(a.text):
            ap.error("give one --text for every --at")
        out = caption(a.run_dir, list(zip(a.at, a.text)), a.from_v, a.review, a.budget)
    elif a.cmd == "text":
        pairs = [x.split("=", 1) for x in a.callout]
        if any(len(x) != 2 for x in pairs):
            ap.error("--callout SPAN=TEXT")
        out = text(a.run_dir, dict(pairs), a.title, a.title_card, a.from_v, a.review, a.budget)
    elif a.cmd == "reorder":
        out = reorder(a.run_dir, a.span, None if a.end else a.before, a.from_v, a.review, a.budget)
    elif a.cmd == "cut":
        ranges = []
        for x in a.words:
            m = re.fullmatch(r"(s\d+):(\d+)-(\d+)", x)
            if not m:
                ap.error("--words SPAN:FROM-TO, like s2:16-18")
            ranges.append((m.group(1), int(m.group(2)), int(m.group(3))))
        out = cut(a.run_dir, ranges, a.from_v, a.review, a.budget)
    elif a.cmd == "edges":
        out = edges(a.run_dir, a.from_v, a.review, a.budget)
    elif a.cmd == "style":
        try:
            look = json.loads(a.look) if a.look else None
        except json.JSONDecodeError as e:
            ap.error(f"--look is not JSON: {e}")
        if look is not None and not isinstance(look, dict):
            ap.error("--look must be a JSON object")
        places: dict[str, list[float] | None] = {}
        for x in a.callout:
            m = re.fullmatch(r"(s\d+)=(?:(auto)|(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?))", x)
            if not m:
                ap.error("--callout SPAN=X,Y or SPAN=auto, like s5=120,300")
            places[m.group(1)] = None if m.group(2) else [float(m.group(3)), float(m.group(4))]
        if a.prompt and look is not None:
            ap.error("give --look or --prompt, not both")
        out = style(a.run_dir, look, places, a.from_v, a.review, a.budget, prompt=a.prompt)
    elif a.cmd == "music":
        out = music(a.run_dir, None if a.off else a.track, a.level, a.duck, a.from_v, a.review, a.budget, a.voice)
    elif a.cmd == "pin":
        out = pin(a.run_dir, a.speaker, None if a.head.lower() == "none" else a.head, a.from_v, a.review, a.budget)
    else:
        out = replan(a.run_dir, a.instruction, a.from_v, a.target, a.review, a.budget, model=a.model)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
