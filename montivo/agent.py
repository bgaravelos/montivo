"""Ask Montivo: the chat's agent. One message -> the edits it asks for -> one new version, rendered once.

    python -m montivo.agent runs/015 --message "put some lo-fi music under it" [--from 5] [--review 1] [--planner open]

One muse-spark-1.3 call (strict json_schema, no video, about $0.005) reads the message next to the current version
(clips, captions on screen, title card, look, music, punch-ins, the last review's notes) and picks ops from a closed
list. Each op is one of the human edits in montivo/human.py (music, style, punch, caption, text, drop, reorder,
replan), so the agent can do exactly what the editor's own buttons do and nothing else. The server checks every id
first; an op the EDL refuses is skipped with its reason. The ops run with human.DEFER set: each writes its edl_vN
unrendered, the chain is folded into one version (one patch, `action` the op's own when there is one, else "ask",
with an `agent` record: message, reply, ops, router) and that version is rendered once, with the review loop when
asked. A message that asks a question or needs an answer gets a reply and no version.

Without MODEL_API_KEY (or with --no-model) a keyword reader stands in for the call: music on or off, punch-ins on or
off, a caption preset by name, the title card on or off. Anything else needs the key.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import human
from .config import OPEN_MODEL, SPARK_MODEL
from .engine import music as music_mod
from .engine.api import dump_json
from .engine.graphics import PRESET_NAMES, PRESETS
from .engine.review import caption_timeline, cut_map
from .ledger import Ledger
from .run import RunLog

OPS = ["music_on", "music_off", "style", "punch", "caption", "callout", "title_text", "title_on", "title_off", "drop",
       "reorder", "replan", "review"]
SPAN_OPS = {"caption", "callout", "drop", "reorder"}  # they name clips or lines of this version: a re-plan makes new ones
COST_USD = 0.006  # one router call (015 v5 context: about 4K tokens in, 300 out)

OP_SCHEMA = {"type": "object", "additionalProperties": False,
             "required": ["op", "span", "before", "text", "at_s", "track", "level_db", "on", "preset", "look_prompt",
                          "instruction"],
             "properties": {
                 "op": {"type": "string", "enum": OPS},
                 "span": {"type": "string", "description": "clip id (s3) for callout, drop, reorder; else empty"},
                 "before": {"type": "string", "description": "reorder: the clip it moves in front of; empty = to the end"},
                 "text": {"type": "string", "description": "caption: the new words of the line; callout: its text, empty "
                                                           "removes it; title_text: the title card's words"},
                 "at_s": {"type": "number", "description": "caption: a cut second inside that line; else -1"},
                 "track": {"type": "string", "description": "music_on: a track id from the list"},
                 "level_db": {"type": "number", "description": "music_on: bed loudness in LUFS, -30 quiet to -14 loud; "
                                                               "0 keeps the current or default (-20)"},
                 "on": {"type": "boolean", "description": "punch: true turns the punch-in zooms on, false off"},
                 "preset": {"type": "string", "description": "style: a preset id, or empty when look_prompt describes it"},
                 "look_prompt": {"type": "string", "description": "style: the caption look in words (colours, font, "
                                                                  "size, position) when no preset fits; else empty"},
                 "instruction": {"type": "string", "description": "replan: the editor's note for the planner, in English"}}}

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reply", "question", "ops"],
          "properties": {
              "reply": {"type": "string", "description": "one or two sentences to the editor, in the language of "
                                                         "the message: what you are changing, or the answer"},
              "question": {"type": "string", "description": "a question back when the message cannot be done as "
                                                            "asked; else empty"},
              "ops": {"type": "array", "items": OP_SCHEMA}}}

PROMPT = """You are Montivo, an agent-first video editor for short vertical clips. The editor talks to you in the chat \
(English, Greek or both). Turn the message into edit ops on the current version. You can only use the ops below; each \
is a button in the editor, so if no op does what is asked, say so in the reply and use no op.

Ops (fill the fields the op uses, leave the others empty, -1, 0 or false):
- music_on: a music bed under the speech, ducked while anyone talks (track, level_db). Also to change the track or level.
- music_off: take the music out.
- style: the look of the captions and callouts. preset = one of the presets, or look_prompt = the look in words.
- punch: punch-in zooms on emphasised words and jump cuts (on).
- caption: change the words of one caption line on screen (at_s inside the line, text = the full new line).
- callout: the text pill on a clip (span, text; empty text removes it).
- title_text: new words for the title card at the start (a new Muse Image card). title_on / title_off: the card on or off.
- drop: delete one clip (span). reorder: move a clip (span) in front of another (before) or to the end.
- replan: plan the paper edit again (which passages, their order, the length, the opening or the ending) with the \
editor's note in instruction. Use it for any change to what is said or how long the cut is; never together with \
caption, callout, drop or reorder (the plan makes new clips).
- review: one blind review of the new version by the reviewer (about $0.04). Only when asked for a review or a score.

Rules:
- Use only the clip ids, track ids and preset ids listed below. Pick a track by its mood when none is named \
(talking head, upbeat tech demo: something light) and name it in the reply.
- No op for what the version already has (music on that track, punch-ins already on or off, the same preset): \
say so in the reply instead.
- A question about the cut (what score, why, how long) gets an answer in reply and no ops.
- Prefer doing a sensible default over asking. Ask (question, no ops) only when the message cannot be done safely.
- reply speaks to the editor in the language of the message, plainly, no more than two sentences.

The current version (v{version}):
{context}

Music tracks (id: name, mood): {tracks}
Caption presets (id: name): {presets}

The editor's message: "{message}"
"""


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
    lines.append("Clips in order:")
    for s in edl["spans"]:
        t = times.get(s["id"])
        at = f" cut {t[0]:.1f}-{t[1]:.1f} s" if t else ""
        call = f", callout {s['callout']!r}" if (s.get("callout") or "").strip() else ""
        lines.append(f"- {s['id']} [{s.get('role', 'body')}]{at}: {s.get('words', '')!r}{call}")
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
    lines.append(f"Music: {m['track']} at {m.get('level_db', -20)} LUFS" if m else "Music: none")
    lines.append(f"Punch-ins: {'on' if edl.get('punch') else 'off'}")
    rv = _review(run_dir, int(edl.get("version") or 0))
    if rv:
        notes = "; ".join(f"{i.get('severity')} {i.get('criterion')}: {i.get('note') or i.get('fix') or ''}"[:160]
                          for i in (rv.get("issues") or [])[:5])
        lines.append(f"Last blind review: {rv.get('overall')}{' pass' if rv.get('pass') else ''}"
                     + (f"; notes: {notes}" if notes else ""))
    return "\n".join(lines)


def _review(run_dir: Path, n: int) -> dict | None:
    p = run_dir / f"review_v{n}.json"
    try:
        return json.loads(p.read_text()).get("review") if p.is_file() else None
    except ValueError:
        return None


def route(run_dir: Path, edl: dict, message: str, ledger: Ledger, log) -> tuple[dict, float]:
    """The router call: Spark 1.3 reads the message and the version and answers {reply, question, ops}."""
    from .engine.spark import Spark
    prompt = PROMPT.format(version=edl.get("version"), context=context(run_dir, edl),
                           tracks="; ".join(f"{t['id']}: {t['name']}, {t.get('mood', '')}" for t in music_mod.tracks()),
                           presets=", ".join(f"{k}: {PRESET_NAMES[k]}" for k in PRESETS),
                           message=message.replace('"', "'"))
    call = Spark(run_dir, ledger, model=SPARK_MODEL, log=log).call("ask", prompt, SCHEMA, max_output_tokens=6000,
                                                                    reasoning_effort="low", stage="spark.ask",
                                                                    schema_name="ask_montivo")
    return dict(call.parsed), call.cost_usd


OFF = re.compile(r"\b(off|no|without|remove|stop|kill|mute)\b|βγάλ|χωρίς|σβήσ|κλείσ|αφαίρεσ|σταμάτ|όχι|καθόλου", re.I)
GREEK_PRESETS = {"καραόκε": "karaoke", "κουτί": "box", "μίνιμαλ": "minimal"}


def keywords(edl: dict, message: str) -> dict:
    """The router without a model: the few edits a keyword names safely. Same answer shape as `route`."""
    m = message.lower()
    off = bool(OFF.search(m))
    ops, said = [], []
    blank = {k: "" for k in ("span", "before", "text", "track", "preset", "look_prompt", "instruction")} | \
        {"at_s": -1, "level_db": 0, "on": False}
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
        return {"reply": "Without MODEL_API_KEY I can only do music, punch-ins, a caption preset by name or the title "
                         "card on and off. Set the key for anything else.", "question": "", "ops": []}
    return {"reply": "Done: " + ", ".join(said) + ".", "question": "", "ops": ops}


def check(edl: dict, ops: list[dict]) -> tuple[list[dict], list[str]]:
    """The ops in the order they can run, each id checked against the version; the rest skipped with a reason.
    Order: caption lines first (they need this version's rendered captions), then a re-plan, then the edits that
    survive one (text, look, music, punch-ins), then the clip edits."""
    ids = {s["id"] for s in edl["spans"]}
    tracks = {t["id"] for t in music_mod.tracks()}
    replan = any(o.get("op") == "replan" for o in ops)
    kept, skipped = [], []
    for o in ops:
        op = o.get("op")
        why = None
        if op not in OPS:
            why = f"unknown op {op!r}"
        elif replan and op in SPAN_OPS:
            why = f"{op} skipped: the re-plan makes new clips, ask again on the new version"
        elif op in ("callout", "drop", "reorder") and o.get("span") not in ids:
            why = f"{op}: {o.get('span')!r} is not a clip of this version ({', '.join(sorted(ids))})"
        elif op == "reorder" and o.get("before") and o["before"] not in ids:
            why = f"reorder: {o['before']!r} is not a clip of this version"
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
        elif op == "title_text" and not (o.get("text") or "").strip():
            why = "title: no words"
        if why:
            skipped.append(why)
        else:
            kept.append(o)
    rank = {"caption": 0, "replan": 1, "callout": 2, "title_text": 2, "title_on": 2, "title_off": 2, "style": 3,
            "music_on": 4, "music_off": 4, "punch": 5, "drop": 6, "reorder": 7, "review": 8}
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
        if caps:
            steps.append((f"{len(caps)} caption line(s) retexted", lambda: human.caption(run_dir, caps, from_v=at())))
        for o in ops:
            if o["op"] == "replan":
                steps.append((f"re-planned: {o['instruction']}", lambda o=o: human.replan(
                    run_dir, o["instruction"], from_v=at(), model=planner)))
        if texts or title is not None or card is not None:
            steps.append(("callout and title text", lambda: human.text(run_dir, texts, title, card, from_v=at())))
        for o in ops:
            op = o["op"]
            if op == "style":
                look = {"preset": o["preset"]} if o["preset"] else None
                prompt = None if look else o["look_prompt"]
                steps.append((f"captions: {PRESET_NAMES[o['preset']] if look else o['look_prompt']}",
                              lambda look=look, prompt=prompt: human.style(run_dir, look, from_v=at(), prompt=prompt)))
            elif op == "music_on":
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
            elif op == "reorder":
                steps.append((f"clip {o['span']} moved " + (f"before {o['before']}" if o["before"] else "to the end"),
                              lambda o=o: human.reorder(run_dir, o["span"], o["before"] or None, from_v=at())))
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
             "agent": {k: record[k] for k in ("message", "reply", "router", "ops", "done", "skipped")}}
    if model := next((p.get("model") for p in mine if p.get("action") == "replan"), None):
        patch["model"] = model
    edl["patches"] = list(base.get("patches") or []) + [patch]
    edl["version"] = first
    dump_json(run_dir / f"edl_v{first}.json", edl)
    for n in written[1:]:
        (run_dir / f"edl_v{n}.json").unlink(missing_ok=True)
    return first


def ask(run_dir: Path, message: str, from_v: int | None = None, passes: int = 0, planner: str | None = None,
        use_model: bool = True, budget: float = 0.15) -> dict:
    log = RunLog(run_dir / "run.log")
    from_v = from_v or human.latest_edl(run_dir)
    edl = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    ledger = Ledger.resume(run_dir / "ledger.json")
    log(f"montivo ask: {message!r} on edl_v{from_v}")
    router, cost = "keywords", 0.0
    answer = None
    if use_model:
        try:
            answer, cost = route(run_dir, edl, message, ledger, log)
            router = SPARK_MODEL
        except (SystemExit, Exception) as e:  # a failed or unparsable call: the keyword reader still helps
            log(f"agent: the router call failed ({e}); reading keywords")
    if answer is None:
        answer = keywords(edl, message)
    ops, skipped = check(edl, answer.get("ops") or [])
    passes = max(passes, 1 if any(o["op"] == "review" for o in ops) else 0)
    ops = [o for o in ops if o["op"] != "review"]
    log(f"agent ({router}): {answer.get('reply', '')!r}; ops: {', '.join(o['op'] for o in ops) or 'none'}"
        + (f"; skipped: {'; '.join(skipped)}" if skipped else ""))
    record = {"message": message, "reply": (answer.get("reply") or "").strip(), "question": (answer.get("question") or "").strip(),
              "router": router, "router_cost_usd": round(cost, 5), "from": from_v,
              "ops": [{k: v for k, v in o.items() if v not in ("", -1, None) and not (k == "level_db" and v == 0)
                       and not (k == "on" and o["op"] != "punch")} for o in ops],
              "done": [], "skipped": skipped, "version": None}
    if ops:
        written, done, more = run_ops(run_dir, from_v, ops, planner, log)
        record["done"], record["skipped"] = done, skipped + more
        if written:
            n = fold(run_dir, from_v, written, record)
            record["version"] = n
            human._finish(run_dir, run_dir / f"edl_v{n}.json", n, passes, budget, Ledger.resume(run_dir / "ledger.json"), log)
    elif passes:  # "review it" with nothing to change: the review loop on this version
        from .engine.review import review_loop
        review_loop(run_dir, run_dir / f"edl_v{from_v}.json", passes=passes, budget_usd=budget, ledger=ledger, log=log)
        record["reviewed"] = from_v
    return record


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m montivo.agent", description=__doc__.split("\n")[0])
    p.add_argument("run_dir", type=Path)
    p.add_argument("--message", required=True)
    p.add_argument("--from", dest="from_v", type=int, default=None)
    p.add_argument("--review", type=int, default=0, help="review passes after the edit")
    p.add_argument("--planner", choices=["spark", "open"], default="spark", help="the planner a re-plan uses")
    p.add_argument("--no-model", action="store_true", help="read keywords instead of calling Spark")
    a = p.parse_args(argv)
    import os
    out = ask(a.run_dir, a.message, a.from_v, a.review, OPEN_MODEL if a.planner == "open" else None,
              use_model=not a.no_model and bool(os.environ.get("MODEL_API_KEY")))
    print(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
