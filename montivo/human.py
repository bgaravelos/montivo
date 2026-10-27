"""The human actions in the loop, as an engine entry point the UI server runs as a subprocess:

    python -m montivo.human drop runs/012 --span s3 [--from 2] [--review 2]
    python -m montivo.human trim runs/012 --span s3 --edge out --word 41 [--from 2] [--review 2]
    python -m montivo.human replan runs/012 --instruction "drop the agency line" [--from 2] [--review 2]
    python -m montivo.human punch runs/012 --on|--off [--from 2] [--review 2]
    python -m montivo.human punch runs/012 --skip "emphasis:Montivo@47.120" [--from 2] [--review 2]

Each writes the next edl_vN.json (N = one past the highest version in the run folder, so no version is ever
overwritten) with a `patches` entry marked `source: "human"`, renders cut_vN.mp4 and, with --review, runs the blind
review loop from it. `drop` and `trim` go through the reviewer's own patch path (`apply_fixes`) and call no model
unless --review is given; `replan` is one Spark plan call (about $0.05) with the instruction added to the brief.
`punch` turns the punch-in zooms (engine/punch.py) on or off, or removes one of them by its key; $0, ffmpeg only.
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

from .engine.api import dump_json
from .engine.edges import Audio, Words, tighten_edges
from .engine.edl import build_edl, edl_summary
from .engine.punch import parse_key
from .engine.review import REACH_S, Fix, apply_fixes, kept_elsewhere, refresh_span, review_loop
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


def _finish(run_dir: Path, edl_path: Path, n: int, passes: int, budget: float, ledger: Ledger, log) -> dict:
    """Render cut_vN.mp4 from the new EDL, then the review loop from it when passes > 0."""
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
    new["version"] = n
    new.setdefault("patches", []).append({"from": from_v, "review": None, "source": "human", "action": "trim",
                                          "fixes": plog})
    log(f"patch v{from_v} -> v{n}: {fix.op} {span_id} {edge}: {plog[0]['note']} (human)")
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
    note = f"replanned: {len(new['spans'])} spans, {new['runtime_s']} s (was {len(prev['spans'])} spans, {prev['runtime_s']} s)"
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
    for p in (d, t, r, pu):
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
    else:
        out = replan(a.run_dir, a.instruction, a.from_v, a.target, a.review, a.budget, model=a.model)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
