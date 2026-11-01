"""First-cut benchmark: a fresh plan, v1 and the review loop on APFS clones of finished run folders.

Each base run folder is cloned (`cp -c -R`, the base is never touched), every version past v1 and every review is
removed, and `montivo run --resume --replan --review` makes a new plan from the stored Voice, watch and tracks, renders
v1 and runs the loop. The table: first review score and pass, the loop's best, cost (ledger delta) and wall time.

    .venv/bin/python tools/first_cut_bench.py --tag b0 fc-012 fx-013 fc-015 ...    (run from the repo root holding runs/)
    .venv/bin/python tools/first_cut_bench.py --tag b0 --table                     (the table of a finished round)

Spends Meta credits: about $0.10-0.45 a clip (plan, plan repair, reviews; Voice on the cut).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RUNS = Path("runs")
KEEP = {"audio.wav", "ingest.json", "ledger.json", "proxy.mp4", "run.json", "run.log", "source_hflip.mp4",
        "source_upright.mp4", "speakers.json", "track", "voice", "watch.json", "plan.json", "edl_v1.json", "spark",
        "summary.json"}


def short(base: str) -> str:
    return re.sub(r"^(fc|fx)-", "", base)


def clone(base: str, tag: str) -> Path:
    src, dst = RUNS / base, RUNS / f"fcb-{tag}-{short(base)}"
    if dst.exists():
        raise SystemExit(f"{dst} exists: pick another --tag")
    subprocess.run(["cp", "-c", "-R", str(src), str(dst)], check=True)
    for p in dst.iterdir():
        if p.name not in KEEP:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in (dst / "spark").glob("review*"):
        p.unlink()
    return dst


def run_one(base: str, tag: str, montivo: str, passes: int = 3) -> dict:
    out = clone(base, tag)
    rj = json.loads((out / "run.json").read_text())
    cost0 = json.loads((out / "ledger.json").read_text()).get("total_cost_usd", 0.0)
    cmd = [montivo, "run", rj["footage"], "--brief", rj["brief"], "--out", str(out), "--target", str(rj.get("target_s", 40)),
           "--resume", "--replan", "--review", "--review-passes", str(passes)]
    if rj.get("hflip"):
        cmd.append("--hflip")
    t0 = time.time()
    with open(f"{out}.stdout.log", "w") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    wall = time.time() - t0
    cost1 = json.loads((out / "ledger.json").read_text()).get("total_cost_usd", 0.0)
    (out / "bench.json").write_text(json.dumps({"base": base, "rc": rc, "wall_s": round(wall, 1),
                                                "cost_usd": round(cost1 - cost0, 4)}, indent=1))
    return row(out)


def row(out: Path) -> dict:
    b = json.loads((out / "bench.json").read_text()) if (out / "bench.json").is_file() else {}
    r1 = json.loads((out / "review_v1.json").read_text()) if (out / "review_v1.json").is_file() else {}
    loop = json.loads((out / "review" / "loop.json").read_text()) if (out / "review" / "loop.json").is_file() else {}
    passes = loop.get("passes", [])
    v1 = r1.get("review", r1)
    edl = json.loads((out / "edl_v1.json").read_text()) if (out / "edl_v1.json").is_file() else {}
    return {"run": out.name, "v1": passes[0]["overall"] if passes else v1.get("overall"),
            "v1_pass": passes[0]["pass"] if passes else v1.get("pass"),
            "scores": " ".join(f"{p['overall']}{'P' if p['pass'] else ''}" for p in passes),
            "best": (loop.get("best") or {}).get("overall"), "stop": loop.get("stop"),
            "runtime": edl.get("runtime_s"), "cost": b.get("cost_usd"), "wall": b.get("wall_s"), "rc": b.get("rc")}


def table(rows: list[dict]) -> str:
    head = "| run | v1 | v1 pass | loop | stop | v1 runtime | cost | wall |\n|---|---|---|---|---|---|---|---|"
    return "\n".join([head] + [f"| {r['run']} | {r['v1']} | {'yes' if r['v1_pass'] else 'no'} | {r['scores']} | {r['stop']} "
                               f"| {r['runtime']} | ${r['cost']} | {r['wall']} s |" for r in rows])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bases", nargs="*")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--passes", type=int, default=3, help="reviews in the loop (1 = the first review only, ~$0.05 a clip)")
    ap.add_argument("--table", action="store_true", help="print the table of the round --tag and stop")
    ap.add_argument("--montivo", default=str(Path(sys.executable).parent / "montivo"))
    a = ap.parse_args()
    if a.table:
        print(table([row(p) for p in sorted(RUNS.glob(f"fcb-{a.tag}-*")) if p.is_dir()]))
        return
    with ThreadPoolExecutor(a.jobs) as pool:
        rows = list(pool.map(lambda b: run_one(b, a.tag, a.montivo, a.passes), a.bases))
    print(table(rows))


if __name__ == "__main__":
    main()
