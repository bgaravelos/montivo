#!/usr/bin/env python3
"""Kill test (b), reviewer: a deliberately bad v1 with three known faults, reviewed blind N times.

Faults planted on a finished run's EDL (default runs/012), rendered without a title card into a new run folder:
  1. weak hook   the opening span is the first take, "Okay. So, hi, I'm Billy. I run a small digital company." (turn 0),
                 so the cut opens on about 3 s of "Okay. So," before "hi"
  2. mid-word    span s2 ends inside "agent" (source 50.08 s, the loudest 40 ms of the word by RMS; Voice put the word at
                 50.06-50.62)
  3. caption     the caption line starting "Muse Spark watches" is forced to y 560 of 1920, over the face

Pass: the review catches at least 2 of 3 with a timestamp within 1 s of the fault, in every one of N runs. A fault is
caught when an issue of a matching criterion (or fix op) has its [t_s, t_end_s] within 1 s of the fault's window.

    .venv/bin/python tools/kt_f_review_killtest.py --runs 3 [--no-clock 1] [--tag f3_ --summary killtest_b_f3.json]

Batches on 26/10 night: the first with the two facts of the design (silences, captions vs head), tag "" -> killtest_b.json;
the second with the third fact (audio level before every cut), tag f3_ -> killtest_b_f3.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from montivo.engine import review as R
from montivo.engine.api import dump_json
from montivo.engine.spark import Spark
from montivo.ledger import Ledger
from montivo.run import RunLog, render_run

HOOK_WORDS = (0, 10)  # turn 0: "Okay. So, hi, I'm Billy. I run a small digital company."
HOOK_HI = 2  # index of "hi," in turn 0
MIDWORD_SRC_T = 50.08
MIDWORD_WORD = 9  # "agent" in turn 2
CAPTION_SPAN, CAPTION_SRC_T, CAPTION_TOP = "s4", 63.43, 560

MATCH = {
    "hook": ({"hook_in_1_5s"}, {"swap_hook"}),
    "mid_word": ({"no_cut_mid_word"}, {"move_cut", "extend_span"}),
    "caption": ({"captions_legible", "subject_framed"}, {"caption_fix"}),
}


def build(src: Path, out: Path, log) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "voice").mkdir(exist_ok=True)
    shutil.copy(src / "voice" / "voice.json", out / "voice" / "voice.json")
    if not (out / "audio.wav").exists():
        shutil.copy(src / "audio.wav", out / "audio.wav")  # the loop's move_cut looks for the quietest point in it
    edl = json.loads((src / "check" / "edl_v1_before_review.json").read_text()) \
        if (src / "check" / "edl_v1_before_review.json").is_file() else json.loads((src / "edl_v1.json").read_text())
    words = R.Words(json.loads((src / "voice" / "voice.json").read_text())["words"])
    s1, s2 = edl["spans"][0], edl["spans"][1]
    t0 = words.turn(0)
    s1.update(turn_id=0, word_from=HOOK_WORDS[0], word_to=HOOK_WORDS[1], t_in=t0[HOOK_WORDS[0]]["t_start"],
              t_out=t0[HOOK_WORDS[1]]["t_end"], why="kill test (b): weak first take as the hook")
    s2.update(word_to=MIDWORD_WORD, t_out=MIDWORD_SRC_T, why="kill test (b): ends mid-word")
    for s in (s1, s2):
        s["words"] = " ".join(w["w"] for w in words.turn(s["turn_id"])[s["word_from"]:s["word_to"] + 1])
        s["duration"] = round(s["t_out"] - s["t_in"], 3)
    edl["caption_overrides"] = [{"span": CAPTION_SPAN, "src_t": CAPTION_SRC_T, "top": CAPTION_TOP,
                                 "why": "kill test (b): caption over the face"}]
    edl["title_card"] = False
    edl["runtime_s"] = round(sum(s["duration"] for s in edl["spans"]), 2)
    for k in ("captions", "callouts", "reframe", "render", "patches"):
        edl.pop(k, None)
    edl["kill_test"] = "b"
    path = out / "edl_v1.json"
    dump_json(path, edl)
    render_run(out, edl_path=path, out_name="cut_v1.mp4", title=False, log=log)
    return path


def faults(edl: dict, hi_src_t: float) -> dict:
    """Fault windows in cut seconds: the hook from 0 to where "hi" starts, the mid-word cut point, the forced line."""
    cm = {c.span: c for c in R.cut_map(edl)}
    t_hi = cm["s1"].cut_in + (hi_src_t - edl["spans"][0]["t_in"])
    cap = next(c for c in edl["captions"] if c["placement"] == "forced")
    return {"hook": [0.0, round(t_hi, 2)], "mid_word": [cm["s2"].cut_out, cm["s2"].cut_out],
            "caption": [cap["t_in"], cap["t_out"]], "caption_text": cap["text"]}


def dist(a: list[float], b: list[float]) -> float:
    """Gap between two intervals (0 when they overlap)."""
    return max(0.0, max(a[0], b[0]) - min(a[1], b[1]))


def score(review: dict, fz: dict) -> dict:
    out = {}
    for name, (crits, ops) in MATCH.items():
        best = None
        for i in review["issues"]:
            if i["criterion"] not in crits and i["fix_op"] not in ops:
                continue
            d = dist([i["t_s"], max(i["t_s"], i["t_end_s"])], fz[name])
            if best is None or d < best[0]:
                best = (round(d, 2), i["t_s"], i["t_end_s"], i["criterion"], i["fix_op"], i["severity"])
        out[name] = {"caught": bool(best and best[0] <= 1.0), "best": best}
    out["caught"] = sum(v["caught"] for k, v in out.items() if k in MATCH)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path("runs/012"))
    ap.add_argument("--out", type=Path, default=Path("runs/012-ktb"))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--no-clock", type=int, default=0, help="extra runs without the burned-in clock (control)")
    ap.add_argument("--tag", default="", help="prefix for this batch's review names, e.g. f3_ (earlier batches are kept)")
    ap.add_argument("--summary", default="killtest_b.json", help="summary file name inside --out")
    args = ap.parse_args()
    log = RunLog(args.out / "run.log")
    words = R.Words(json.loads((args.src / "voice" / "voice.json").read_text())["words"])
    edl_path = args.out / "edl_v1.json"
    if not (args.out / "cut_v1.mp4").is_file():
        build(args.src, args.out, log)
    edl = json.loads(edl_path.read_text())
    fz = faults(edl, words.turn(0)[HOOK_HI]["t_start"])
    log(f"kill test (b): faults in cut_v1.mp4 {json.dumps(fz)}")
    ledger = Ledger.resume(args.out / "ledger.json")
    spark = Spark(args.out, ledger, log=log)
    results = []
    plan = [(k + 1, True) for k in range(args.runs)] + [(k + 1, False) for k in range(args.no_clock)]
    for k, clock in plan:
        name = f"review_ktb_{args.tag}{'r' if clock else 'noclock'}{k}"
        if (args.out / f"{name}.json").is_file():
            rv = json.loads((args.out / f"{name}.json").read_text())
        else:
            rv = R.review_render(args.out, edl, args.out / "cut_v1.mp4", 1, ledger, spark, clock=clock, log=log, name=name)
        sc = score(rv["review"], fz)
        results.append({"name": name, "clock": clock, "overall": rv["review"]["overall"], "pass": rv["review"]["pass"],
                        "cost_usd": rv["cost_usd"], **sc})
        log(f"{name}: caught {sc['caught']}/3 " + ", ".join(f"{n} {'Y' if sc[n]['caught'] else 'n'} {sc[n]['best']}" for n in MATCH))
    clocked = [r for r in results if r["clock"]]
    verdict = "PASS" if clocked and all(r["caught"] >= 2 for r in clocked) else "FAIL"
    summary = {"faults": fz, "runs": results, "verdict": verdict, "cost_usd": round(sum(r["cost_usd"] for r in results), 4)}
    dump_json(args.out / args.summary, summary)
    log(f"kill test (b): {verdict} ({', '.join(str(r['caught']) for r in clocked)} of 3 caught per clocked run), "
        f"${summary['cost_usd']:.4f}")


if __name__ == "__main__":
    main()
