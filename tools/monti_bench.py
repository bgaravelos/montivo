#!/usr/bin/env python3
"""Monti benchmark: does the help gecko point at the right place, open the right panel and hand edits to Ask Montivo?

A fixed set of "where is X?" / "how do I Y?" questions in English and Greek (montivo/monti.py `ask`, one Muse Spark 1.3
call each, about $0.013), each with the targets a person would accept, the panel it should open and whether it
is an edit request Monti must hand to Ask Montivo. A case passes when the target is one of the accepted ones (None
where nothing on that page fits), the hand-off matches and a Greek question gets a Greek reply. `--no-model` scores
the $0 keyword fallback on the same cases.

Calls go to <root>/monti/ (default runs/montibench, git-ignored), never to the server's own ledger; results to
<root>/results/<label>.json.

    .venv/bin/python tools/monti_bench.py --label spark            # Muse Spark 1.3, every case (about $0.32)
    .venv/bin/python tools/monti_bench.py --label guide --no-model # the keyword fallback, $0
    .venv/bin/python tools/monti_bench.py --label spark2 --cases 3 7
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from montivo import monti  # noqa: E402

E = {"page": "editor", "panel": "assistant", "version": 5, "live": True}
# (question, context, accepted targets, panel or None (any), hand-off)
CASES: list[tuple[str, dict, set, str | None, bool]] = [
    ("Where do I add music?", E, {"rail.music"}, "music", False),
    ("πού βάζω μουσική;", E, {"rail.music"}, "music", False),
    ("How do I fix a caption?", E, {"rail.captions", "tl.captions"}, "captions", False),
    ("πώς διορθώνω μια λέξη στους υπότιτλους;", E, {"rail.captions", "tl.captions"}, "captions", False),
    ("What does the gate mean?", E, {"insp", "top.score"}, None, False),
    ("γιατί δεν περνάει το review;", E, {"insp", "top.score"}, None, False),
    ("How do I make the captions bigger?", E, {"rail.style"}, "style", False),
    ("How do I export a 4:5 version?", E, {"export.dest", "top.export"}, None, False),
    ("πού είναι το export;", E, {"top.export"}, None, False),
    ("How do I undo?", E, {"top.undo"}, None, False),
    ("What is the difference between Live and Rendered?", E, {"viewer.mode"}, None, False),
    ("How do I split a clip?", E, {"tl.tools", "tl.video"}, None, False),
    ("πώς σβήνω μερικές λέξεις;", E, {"tl.tabs", "tl.tools", "tl.video"}, None, False),
    ("Which stage runs on Muse Spark Open?", E, {"rail.assistant", "chat.composer", "top.more", None}, None, False),
    ("How much does a review cost?", E, {"top.more", "top.score", "insp", None}, None, False),
    ("How do I change which face the crop follows?", E, {"rail.speakers"}, "speakers", False),
    ("Add lofi music and make the captions bigger", E, {"chat.composer"}, "assistant", True),
    ("βάλε μουσική και κόψε την εισαγωγή", E, {"chat.composer"}, "assistant", True),
    ("Make it 30 seconds", E, {"chat.composer"}, "assistant", True),
    ("How do I start a new project?", {"page": "home"}, {"home.new"}, None, False),
    ("πώς ανεβάζω το δικό μου βίντεο;", {"page": "home"}, {"home.new"}, None, False),
    ("Where do I add music?", {"page": "home"}, {None}, None, False),
    ("How do I make a cover?", {"page": "export", "version": 5}, {"export.cover"}, None, False),
    ("Where is the score history of all versions?", E, {"top.loop", "rail.versions"}, None, False),
]


def greek(s: str) -> bool:
    return bool(re.search(r"[α-ωά-ώ]", s, re.I))


def score(case, a: dict) -> dict:
    q, ctx, targets, panel, hand = case
    ok_t = a.get("target") in targets
    ok_p = panel is None or a.get("open_panel") == panel or a.get("needs") == panel
    ok_h = bool(a.get("handoff")) == hand
    ok_l = greek(a.get("reply", "")) == greek(q) or a.get("source") == "guide"  # the fallback's guide text is English
    return {"pass": ok_t and ok_p and ok_h and ok_l, "target": ok_t, "panel": ok_p, "handoff": ok_h, "language": ok_l}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--root", default=str(ROOT / "runs" / "montibench"))
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--cases", type=int, nargs="*")
    ap.add_argument("--workers", type=int, default=1, help="1 keeps the guide prefix cached call after call")
    a = ap.parse_args()
    root = Path(a.root)
    root.mkdir(parents=True, exist_ok=True)
    picked = [(i, c) for i, c in enumerate(CASES) if not a.cases or i in a.cases]

    def one(ic):
        i, c = ic
        t = time.time()
        if a.no_model:
            out = monti.fallback(c[0], c[1]["page"]) | {"source": "guide", "cost_usd": 0.0}
        else:
            out = monti.ask(root, c[0], c[1])
        return {"i": i, "q": c[0], "page": c[1]["page"], "answer": out, "s": round(time.time() - t, 2), **score(c, out)}

    with ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(one, picked))
    for r in rows:
        x = r["answer"]
        flags = "".join(k[0].upper() if not r[k] else "." for k in ("target", "panel", "handoff", "language"))
        print(f"{'PASS' if r['pass'] else 'FAIL'} {flags} #{r['i']:>2} [{r['page']}] {r['q'][:52]:<52} -> "
              f"{x.get('target')} / {x.get('open_panel')}{' / handoff' if x.get('handoff') else ''} "
              f"${x.get('cost_usd', 0):.4f} {r['s']}s | {x.get('reply', '')[:70]}")
    n = sum(r["pass"] for r in rows)
    cost = sum(r["answer"].get("cost_usd", 0) for r in rows)
    secs = sorted(r["s"] for r in rows)
    print(f"\n{a.label}: {n}/{len(rows)} pass, ${cost:.4f}, median {secs[len(secs) // 2]} s")
    (root / "results").mkdir(exist_ok=True)
    (root / "results" / f"{a.label}.json").write_text(json.dumps({"label": a.label, "pass": n, "cases": len(rows),
                                                                    "cost_usd": round(cost, 4), "rows": rows},
                                                                   ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
