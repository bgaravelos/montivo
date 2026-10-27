"""The two human actions in the loop, as an engine entry point the UI server runs as a subprocess:

    python -m montivo.human drop runs/012 --span s3 [--from 2] [--review 2]
    python -m montivo.human replan runs/012 --instruction "drop the agency line" [--from 2] [--review 2]

Each writes the next edl_vN.json (N = one past the highest version in the run folder, so no version is ever
overwritten) with a `patches` entry marked `source: "human"`, renders cut_vN.mp4 and, with --review, runs the blind
review loop from it. `drop` goes through the reviewer's own patch path (`apply_fixes`) and calls no model unless
--review is given; `replan` is one Spark plan call (about $0.05) with the instruction added to the brief.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import signal
import sys
from pathlib import Path

from .engine.api import dump_json
from .engine.edges import Audio, Words, tighten_edges
from .engine.edl import build_edl, edl_summary
from .engine.review import Fix, apply_fixes, refresh_span, review_loop
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


def target_from(instruction: str) -> float | None:
    """'make it 30 seconds' -> 30.0: a runtime named in the instruction replaces the run's target."""
    m = re.search(r"(\d{2,3})\s*(?:s\b|sec|second)", instruction, re.I)
    return float(m.group(1)) if m and 10 <= float(m.group(1)) <= 180 else None


def replan(run_dir: Path, instruction: str, from_v: int | None = None, target_s: float | None = None,
           passes: int = 0, budget: float = 0.15) -> dict:
    """Plan again with the instruction added to the brief and the current paper edit for reference, reusing voice,
    watch and the tracks (no ingest, Voice or SAM spend), -> edl_vN on word boundaries with the edge pass, render."""
    from .engine.spark import Spark
    log = RunLog(run_dir / "run.log")
    from_v = from_v or latest_edl(run_dir)
    prev = json.loads((run_dir / f"edl_v{from_v}.json").read_text())
    run = json.loads((run_dir / "run.json").read_text())
    n = next_version(run_dir)
    target = target_s or target_from(instruction) or float(run.get("target_s") or 40.0)
    log(f"montivo replan: edl_v{from_v} + \"{instruction}\" -> edl_v{n} (target {target:.0f} s)")
    voice = VoiceResult.from_json(json.loads((run_dir / "voice" / "voice.json").read_text()))
    watch = json.loads((run_dir / "watch.json").read_text())
    brief = f"{run['brief']}\n\nEditor's note for this version (it overrides the brief where they conflict): {instruction}"
    plan_brief = f"{brief}\n\nThe current cut (v{from_v}), for reference:\n{edl_summary(prev)}"
    ledger = Ledger.resume(run_dir / "ledger.json")
    spark = Spark(run_dir, ledger, log=log)
    sdir = run_dir / "spark"
    # Spark names its files after the call ("plan"): v1's request and response are copied to plan_v1_* on disk
    # first (they survive a kill), the new ones become plan_vN_* and v1's go back in place
    for part in ("request", "response"):
        p, v1 = sdir / f"plan_{part}.json", sdir / f"plan_v1_{part}.json"
        if p.is_file() and not v1.is_file():
            shutil.copy2(p, v1)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # the UI's stop button: run the finally below
    try:
        planned = spark.plan(plan_brief, voice.transcript_text(), watch, target, words_per_s=voice.speech_rate())
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
    note = f"replanned: {len(new['spans'])} spans, {new['runtime_s']} s (was {len(prev['spans'])} spans, {prev['runtime_s']} s)"
    new["patches"] = list(prev.get("patches") or []) + [
        {"from": from_v, "review": None, "source": "human", "action": "replan", "instruction": instruction,
         "fixes": [{"op": "replan", "target": "plan", "issue": -1, "severity": "human", "note": note}]}]
    log(f"patch v{from_v} -> v{n}: replan: {note} (human)")
    for s in new["spans"]:
        log(f"  {s['id']} {s['role']:6s} {s['t_in']:7.2f}-{s['t_out']:7.2f}s {s['words'][:80]}")
    edl_path = run_dir / f"edl_v{n}.json"
    dump_json(edl_path, new)
    return _finish(run_dir, edl_path, n, passes, budget, ledger, log)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m montivo.human", description="Human edits: the next EDL version, rendered.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("drop", help="drop one span -> edl_vN -> cut_vN ($0 without --review)")
    d.add_argument("run_dir", type=Path)
    d.add_argument("--span", required=True)
    r = sub.add_parser("replan", help="plan again with an instruction -> edl_vN -> cut_vN (one Spark plan call)")
    r.add_argument("run_dir", type=Path)
    r.add_argument("--instruction", required=True)
    r.add_argument("--target", type=float, default=None, help="runtime target in seconds (default: from the instruction or run.json)")
    for p in (d, r):
        p.add_argument("--from", dest="from_v", type=int, default=None, help="EDL version to start from (default: the latest)")
        p.add_argument("--review", type=int, default=0, help="review passes after the render (default 0, none)")
        p.add_argument("--budget", type=float, default=0.15, help="max dollars for the review calls (default 0.15)")
    a = ap.parse_args(argv)
    if not a.run_dir.is_dir():
        ap.error(f"{a.run_dir} is not a run folder")
    if a.cmd == "drop":
        out = drop(a.run_dir, a.span, a.from_v, a.review, a.budget)
    else:
        out = replan(a.run_dir, a.instruction, a.from_v, a.target, a.review, a.budget)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
