#!/usr/bin/env python3
"""Chat benchmark: does Ask Montivo's router pick the edits a message asks for?

Ask Montivo routes every chat message with one model call (montivo/agent.py `route`) and runs what it picked. This
scores the routing alone, no edit runs and nothing renders: a fixed set of messages in English and Greek on three
finished runs (015 Billy's demo clip v5, 012 Billy v2, 014 four astronauts v1), some with a selection or the chat's
last turns, each with the edits a person would expect. A case passes when the router's ops, read as intents, are one
of the accepted sets and the arguments it names (clip, word range, turn, track, cut second...) match; a question case
passes with no ops and a reply that says what is asked.

Intents are the editor's own edits, so old and new routers score on one scale: the router's op names map onto them
(music_on -> music, callout / title_* -> text...). An intent the router has no op for cannot pass: that is what the
benchmark measures before and after a change.

The runs are cloned once to runs/chatbench/<base> (APFS clones, git-ignored) so the router's request files and ledger
never touch the originals. Each router call costs about $0.005 (Spark 1.3) or $0.002 (Spark Open).

    .venv/bin/python tools/chat_bench.py --label before                 # Spark 1.3, every case
    .venv/bin/python tools/chat_bench.py --label before-open --model open
    .venv/bin/python tools/chat_bench.py --label after --cases 5 9 12   # some cases
    .venv/bin/python tools/chat_bench.py --compare before after         # stored results side by side, $0
"""
from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from montivo import agent  # noqa: E402
from montivo.config import OPEN_MODEL, SPARK_MODEL  # noqa: E402
from montivo.ledger import Ledger  # noqa: E402

RUNS = ROOT / "runs"
OUT = RUNS / "chatbench"
BASES = {"015": 5, "012": 2, "014": 1}

# the router's op names (old and new) as the editor's intents
INTENT = {"music_on": "music", "music_off": "music", "callout": "text", "title_text": "text", "title_on": "text",
          "title_off": "text"}

UPBEAT = {"funky_jam", "midnight_cruiser", "ukulele", "raspberry_jam", "apple_cider"}

# (base, message, accepted intent sets, argument checks, extra)
#   checks: {intent: {field: value | (lo, hi) | callable}} on the first op of that intent;
#   "reply": words of which at least one must be in the reply (a question case: no ops);
#   "scope": the editor's selection; "history": the chat's last turns, oldest first.
CASES: list[dict] = [
    # 015 v5: Billy, one speaker
    {"base": "015", "msg": "put some chill lofi music under it", "ok": [{"music"}], "check": {"music": {"track": "chill_lofi"}}},
    {"base": "015", "msg": "Βάλε μουσική, κάτι χαλαρό", "ok": [{"music"}]},
    {"base": "015", "msg": "βγάλε τα punch-ins", "ok": [{"punch"}], "check": {"punch": {"on": False}}},
    {"base": "015", "msg": "κάνε τα captions karaoke", "ok": [{"style"}], "check": {"style": {"preset": "karaoke"}}},
    {"base": "015", "msg": "delete the clip about my agency", "ok": [{"drop"}, {"cut"}], "check": {"drop": {"span": "s2"}, "cut": {"span": "s2"}}},
    {"base": "015", "msg": "move the hackathon line to the very end", "ok": [{"reorder"}], "check": {"reorder": {"span": "s4", "before": ""}}},
    {"base": "015", "msg": "remove the double 'and' in the agency clip", "ok": [{"cut"}], "check": {"cut": {"span": "s2", "words": (18, 19)}}},
    {"base": "015", "msg": "βάλε πίσω το «Okay. Is it recording?» στην αρχή", "ok": [{"insert"}], "check": {"insert": {"turn": 0, "words": (0, 5)}}},
    {"base": "015", "msg": "τι άφησες έξω από το cut;", "ok": [set()], "reply": ["recording", "say that again", "okay", "retake", "wait"]},
    {"base": "015", "msg": "πού λέω για το SAM;", "ok": [{"seek"}, {"select"}, {"seek", "select"}], "check": {"seek": {"at_s": (21.5, 27.3)}, "select": {"span": "s6"}}},
    {"base": "015", "msg": "what score did this version get?", "ok": [set()], "reply": ["87"]},
    {"base": "015", "msg": "shorten the pauses", "ok": [{"pauses"}]},
    {"base": "015", "msg": "split s3 right before 'it never'", "ok": [{"split"}], "check": {"split": {"span": "s3", "word": 38}}},
    {"base": "015", "msg": "trim s7 so it starts at 'Spark'", "ok": [{"trim"}, {"cut"}], "check": {"trim": {"span": "s7", "word": 30}, "cut": {"span": "s7", "words": (28, 29)}}},
    {"base": "015", "msg": "Voice keeps hearing 'news' instead of 'Muse': write Muse in every caption", "ok": [{"glossary"}]},
    {"base": "015", "msg": "make it about 30 seconds", "ok": [{"replan"}]},
    {"base": "015", "msg": "add a title card that says 'AI that checks its own work'", "ok": [{"text"}]},
    {"base": "015", "msg": "remove the callout on the crop clip", "ok": [{"text"}], "check": {"text": {"span": "s6"}}},
    {"base": "015", "msg": "go back to version 3", "ok": [{"restore"}], "check": {"restore": {"version": 3}}},
    {"base": "015", "msg": "όχι, κάτι πιο ζωντανό", "ok": [{"music"}], "check": {"music": {"track": lambda t: t in UPBEAT}},
     "history": [{"role": "user", "text": "put chill lofi under it"},
                 {"role": "agent", "text": "Chill Lofi under the speech at -18 LUFS.", "version": 6}]},
    {"base": "015", "msg": "undo that", "ok": [{"restore"}], "check": {"restore": {"version": 5}},
     "history": [{"role": "user", "text": "delete s3"}, {"role": "agent", "text": "Clip s3 deleted.", "version": 6}]},
    {"base": "015", "msg": "and the same for the next one", "ok": [{"drop"}], "check": {"drop": {"span": "s4"}},
     "history": [{"role": "user", "text": "delete s3"}, {"role": "agent", "text": "Clip s3 deleted.", "version": 6}]},
    {"base": "015", "msg": "delete this", "ok": [{"drop"}, {"cut"}], "check": {"drop": {"span": "s5"}, "cut": {"span": "s5"}},
     "scope": {"clips": ["s5"]}},
    {"base": "015", "msg": "σβήσε αυτά", "ok": [{"cut"}], "check": {"cut": {"span": "s2", "words": (18, 19)}},
     "scope": {"words": [{"span": "s2", "from": 18, "to": 19, "text": "and and"}]}},
    {"base": "015", "msg": "open the music panel", "ok": [{"open"}]},
    {"base": "015", "msg": "the line at 19 seconds should read 'the work. Muse Voice'", "ok": [{"caption"}], "check": {"caption": {"at_s": (18.8, 20.7)}}},
    {"base": "015", "msg": "run a blind review on this", "ok": [{"review"}]},
    {"base": "015", "msg": "add a B-roll picture of a flow diagram of the four models at 17 s", "ok": [{"overlay"}], "check": {"overlay": {"at_s": (16.0, 18.5)}}},
    # 012 v2: Billy, an older take with a Greek pre-roll
    {"base": "012", "msg": "in s2 cut 'agent agent' down to one 'agent'", "ok": [{"cut"}], "check": {"cut": {"span": "s2", "words": (9, 10)}}},
    {"base": "012", "msg": "put back the line about editing 10 videos a week by hand, right after the hook", "ok": [{"insert"}],
     "check": {"insert": {"turn": 1, "words": (20, 32), "before": "s2"}}},
    {"base": "012", "msg": "drop the 'this is the part that matters' clip", "ok": [{"drop"}], "check": {"drop": {"span": "s7"}}},
    {"base": "012", "msg": "πόσο διαρκεί τώρα;", "ok": [set()], "reply": ["44", "45"]},
    # 014 v1: four astronauts, one handheld mic
    {"base": "014", "msg": "speaker A is Tracy", "ok": [{"speakers"}], "check": {"speakers": {"speaker": "A"}}},
    {"base": "014", "msg": "when B talks, follow head 0", "ok": [{"pin"}], "check": {"pin": {"speaker": "B", "head": "0"}}},
    {"base": "014", "msg": "delete the short repeated 'smoked turkey' clip", "ok": [{"drop"}, {"cut"}], "check": {"drop": {"span": "s6"}}},
    {"base": "014", "msg": "put back 'we're grateful for a nation that is a spacefaring nation'", "ok": [{"insert"}], "check": {"insert": {"turn": 8, "words": (70, 82)}}},
    {"base": "014", "msg": "make it 40 seconds", "ok": [{"replan"}]},
    {"base": "014", "msg": "δείξε μου πού μιλάνε για τα Brussels sprouts", "ok": [{"seek"}, {"select"}, {"seek", "select"}], "check": {"seek": {"at_s": (27.5, 33.1)}, "select": {"span": "s5"}}},
]


def clone(base: str) -> Path:
    """runs/chatbench/<base>: an APFS clone of runs/<base> made once."""
    dst = OUT / base
    if not dst.is_dir():
        OUT.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-c", "-R", str(RUNS / base), str(dst)], check=True)
    return dst


def intents(ops: list[dict]) -> set[str]:
    return {INTENT.get(o.get("op", ""), o.get("op", "")) for o in ops}


def first(ops: list[dict], intent: str) -> dict | None:
    return next((o for o in ops if INTENT.get(o.get("op", ""), o.get("op")) == intent), None)


def overlap(o: dict, lo: int, hi: int) -> bool:
    a, b = o.get("word_from", -1), o.get("word_to", -1)
    return a is not None and b is not None and a >= 0 and b >= a and a <= hi and b >= lo


def check_field(o: dict, field: str, want) -> bool:
    if field == "words":
        return overlap(o, *want)
    if field == "word":  # split / trim: the word the router names (one field or the start of a range)
        got = o.get("word", o.get("word_from"))
        return got == want
    got = o.get(field)
    if field == "span" and o.get("op") in ("text", "callout") and got != want:
        got = o.get("span")
    if callable(want):
        return bool(got) and want(got)
    if isinstance(want, tuple):
        try:
            return want[0] <= float(got) <= want[1]
        except (TypeError, ValueError):
            return False
    if field == "before":
        return (got or "") == want
    if isinstance(want, bool):
        return got is want
    return str(got) == str(want)


def score(case: dict, answer: dict) -> tuple[bool, str]:
    ops = answer.get("ops") or []
    got = intents(ops)
    if got not in case["ok"]:
        return False, f"intents {sorted(got) or '-'} not in {[sorted(s) or '-' for s in case['ok']]}"
    for intent, fields in (case.get("check") or {}).items():
        if intent not in got:
            continue
        o = first(ops, intent)
        for f, want in fields.items():
            if not check_field(o, f, want):
                return False, f"{intent}.{f} = {o.get(f, o.get('word_from'))!r}"
    if case.get("reply"):
        said = (answer.get("reply", "") + " " + answer.get("question", "")).lower()
        if not any(w.lower() in said for w in case["reply"]):
            return False, f"reply lacks {case['reply']}: {said[:80]!r}"
    return True, ""


def run_case(i: int, case: dict, model: str) -> dict:
    run_dir = clone(case["base"])
    v = BASES[case["base"]]
    edl = json.loads((run_dir / f"edl_v{v}.json").read_text())
    ledger = Ledger.resume(run_dir / "ledger.json")
    params = inspect.signature(agent.route).parameters
    kw = {}
    if "model" in params:
        kw["model"] = model
    if "history" in params and case.get("history"):
        kw["history"] = case["history"]
    t0 = time.time()
    try:
        answer, cost = agent.route(run_dir, edl, case["msg"], ledger, lambda *_: None, case.get("scope"), "", **kw)
        err = ""
    except (SystemExit, Exception) as e:
        answer, cost, err = {"reply": "", "ops": []}, 0.0, f"{type(e).__name__}: {e}"
    secs = time.time() - t0
    ok, why = (False, err) if err else score(case, answer)
    return {"i": i, "base": case["base"], "msg": case["msg"], "ok": ok, "why": why, "seconds": round(secs, 2),
            "cost_usd": round(cost, 5), "reply": answer.get("reply", ""), "question": answer.get("question", ""),
            "ops": answer.get("ops") or []}


def table(rows: list[dict]) -> str:
    out = []
    for r in rows:
        ops = ", ".join(INTENT.get(o.get("op"), o.get("op")) for o in r["ops"]) or "-"
        out.append(f"{r['i']:>2} {'PASS' if r['ok'] else 'fail'} {r['seconds']:>5.1f}s  {r['msg'][:58]:<58}  {ops[:30]:<30} {r['why'][:70]}")
    return "\n".join(out)


def summary(rows: list[dict]) -> str:
    n, ok = len(rows), sum(r["ok"] for r in rows)
    secs = sorted(r["seconds"] for r in rows)
    return (f"{ok}/{n} pass ({100 * ok / max(n, 1):.0f} %), median {secs[len(secs) // 2]:.1f} s, "
            f"p90 {secs[int(len(secs) * 0.9)]:.1f} s, ${sum(r['cost_usd'] for r in rows):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="run")
    ap.add_argument("--model", choices=["spark13", "open"], default="spark13")
    ap.add_argument("--cases", type=int, nargs="*", help="case numbers (default: all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="two stored labels side by side ($0)")
    a = ap.parse_args()
    if a.compare:
        A, B = (json.loads((OUT / f"results_{x}.json").read_text())["rows"] for x in a.compare)
        bi = {r["i"]: r for r in B}
        for r in A:
            s = bi.get(r["i"])
            mark = "  " if s is None or s["ok"] == r["ok"] else ("+ " if s["ok"] else "- ")
            print(f"{mark}{r['i']:>2} {'PASS' if r['ok'] else 'fail'} -> {'PASS' if s and s['ok'] else 'fail'}  {r['msg'][:70]}")
        print(f"{a.compare[0]}: {summary(A)}\n{a.compare[1]}: {summary(B)}")
        return
    model = OPEN_MODEL if a.model == "open" else SPARK_MODEL
    pick = [(i, c) for i, c in enumerate(CASES, 1) if not a.cases or i in a.cases]
    for b in {c["base"] for _, c in pick}:
        clone(b)
    with ThreadPoolExecutor(a.workers) as ex:
        rows = sorted(ex.map(lambda ic: run_case(ic[0], ic[1], model), pick), key=lambda r: r["i"])
    print(table(rows))
    print(summary(rows))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"results_{a.label}.json").write_text(json.dumps({"label": a.label, "model": model, "time": time.strftime(
        "%Y-%m-%dT%H:%M:%S"), "summary": summary(rows), "rows": rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
