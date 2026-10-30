"""Hook A/B: two versions that differ only in the line that opens the cut, judged by the blind reviewer.

The first 1.5 s decide whether a viewer stays, and the planner's opener is not always the strongest line (README,
"Planner fill"). So the agent makes the challenger and lets the reviewer choose, blind as always:

    A = edl_vN as it is (its review is reused when cut_vN was already reviewed, else it is reviewed now)
    B = edl_vM, M the next free version: A with one swap_hook patch, the challenger span moved to the front and every
        other span in A's order (the reviewer's own op and patch path, so the edge pass, the title card and the
        captions behave exactly as after a review fix)
    render cut_vM, review it, then the pairwise judge (review.pairwise, C2) sees both cuts in one call, twice with
    the two swapped: B is kept only when both calls pick it, a split or a tie keeps A, a change needs evidence

The challenger is picked deterministically, first match wins:

1. the line the reviewer itself asked to open with: the target_t_s of a swap_hook issue in this run's reviews, newest
   review first, mapped through the cut map of the version that review saw (the reviewer named it, blind to the plan);
2. another span the planner marked `hook` (it saw two openers and placed one);
3. the planner's `payoff` span, the last one if an old EDL has several (the punchline as a cold open, a common
   short-form opening);
4. the first span after the opener that starts a new passage (not contiguous with the span before it: a cut that
   opens mid-sentence is no hook) and is not a call to action.

A span already compared against this same A (an existing review/hook_ab_vA_*.json) is not tried twice, like the
loop's same-fix guard. Guards: the dollar budget is checked for every review the A/B still needs before the first one
is made; nothing is overwritten (B is the next version); the old opener stays in the cut as a body span (the hook is
never dropped). Both reviews stay for the record and for the next fixes, but they no longer decide: the reviewer varies
by 3-4 points (SD) on an identical cut and both real A/Bs were decided on scores 7 points apart (`by_score` keeps what
the two scores would have said). `decisive` = both calls of the judge agreed. Without the judge (an open-model
reviewer, tests without compare_fn, no budget left for it) the higher (pass, overall) decides as before. Both reviews,
the challenger, why it was picked, the scores, the judge's two calls, the winner and the reason land in
review/hook_ab_vA_vB.json and in B's patch entry (`result`).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from ..config import is_open_model
from ..ledger import Ledger
from .api import dump_json
from .edges import Audio, Words
from .music import follow_cut
from .review import (Fix, apply_fixes, cut_map, estimate_compare_cost, estimate_cost, pairwise, plan_fixes,
                     review_render, span_at)

NOISE_POINTS = 10  # the same cut reviewed twice scored 72 and 83 (README, kill test (b))
REVIEWED = re.compile(r"review_v(\d+)\.json")


def _json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def reviewer_targets(run_dir: Path) -> list[tuple[str, str]]:
    """(span id, why) for every swap_hook target in this run's reviews, newest review first."""
    out: list[tuple[str, str]] = []
    reviews = sorted((int(m.group(1)), p) for p in run_dir.glob("review_v*.json") if (m := REVIEWED.fullmatch(p.name)))
    for n, p in reversed(reviews):
        rv, edl = _json(p), _json(run_dir / f"edl_v{n}.json")
        if not rv or not edl or not edl.get("spans"):
            continue
        cmap = cut_map(edl)
        for i in (rv.get("review") or {}).get("issues") or []:
            tgt = float((i.get("params") or {}).get("target_t_s", -1))
            if i.get("fix_op") == "swap_hook" and tgt >= 0 and (c := span_at(cmap, tgt)) is not None:
                out.append((c.span, f"the reviewer asked to open with the line at {tgt:.1f} s of cut_v{n} "
                                    f"({i.get('severity')}: {i.get('what', '')[:80]})"))
    return out


def challengers(edl: dict, run_dir: Path) -> list[tuple[str, str]]:
    """Every candidate opener in the order of the module docstring, the current opener left out, no repeats."""
    spans = edl["spans"]
    if len(spans) < 2:
        return []
    ids = {s["id"] for s in spans[1:]}
    cands = [c for c in reviewer_targets(run_dir) if c[0] in ids]
    cands += [(s["id"], "the planner marked it a hook too") for s in spans[1:]
              if "hook" in (s["role"], s.get("planner_role"))]
    # the last payoff: an EDL built before normalize_roles may carry several (012-open v3 marked every body span)
    cands += [(s["id"], "the planner's payoff, as a cold open") for s in spans[1:] if s["role"] == "payoff"][-1:]
    cands += [(s["id"], "the first new passage after the opener") for s in spans[1:]
              if not s.get("contiguous_with_previous") and s["role"] != "cta"][:1]
    seen: set[str] = set()
    return [c for c in cands if not (c[0] in seen or seen.add(c[0]))]


def compared(run_dir: Path, a: int) -> set[str]:
    """Challengers already compared against version a."""
    return {r["challenger"] for p in (run_dir / "review").glob(f"hook_ab_v{a}_v*.json") if (r := _json(p))}


def winner(ra: dict, rb: dict) -> tuple[str, str]:
    """'a' or 'b' and why: the higher (pass, overall), a tie keeps A."""
    ka, kb = (bool(ra["pass"]), ra["overall"]), (bool(rb["pass"]), rb["overall"])
    if kb > ka:
        why = "B passes and A does not" if kb[0] and not ka[0] else f"B scores {kb[1]} against A's {ka[1]}"
        return "b", why
    if ka > kb:
        return "a", "A passes and B does not" if ka[0] and not kb[0] else f"A scores {ka[1]} against B's {kb[1]}"
    return "a", f"a tie at {ka[1]}: the opener stays unless the challenger does better"


def hook_ab(run_dir: Path, edl_path: Path | None = None, budget_usd: float = 0.20, ledger: Ledger | None = None,
            log=print, clock: bool = True, spark=None, render_fn=None, review_fn=None, compare_fn=None) -> dict:
    """The hook A/B on edl_vN (default: the newest EDL). Returns the record also written to review/hook_ab_vA_vB.json,
    or {"stop": why} when it does not run (no challenger, budget). render_fn(edl_path, out_name),
    review_fn(edl, cut, n) -> review dict and compare_fn(rv_a, rv_b) -> pairwise record replace the render and the
    Spark calls in tests."""
    from ..human import latest_edl, next_version  # human.py imports the engine; these two only read file names
    from ..run import render_run
    from .spark import Spark
    if edl_path is None:
        edl_path = run_dir / f"edl_v{latest_edl(run_dir)}.json"
    ledger = ledger or Ledger.resume(run_dir / "ledger.json")
    if review_fn is None:
        spark = spark or Spark(run_dir, ledger, log=log)
        review_fn = lambda e, c, n: review_render(run_dir, e, c, n, ledger, spark, clock=clock, log=log)  # noqa: E731
        if compare_fn is None and not is_open_model(spark.model):  # two uploaded videos in one call: Spark 1.3 only
            compare_fn = lambda ra, rb: pairwise(run_dir, ra, rb, spark, edl_a["brief"], log=log)  # noqa: E731
    render_fn = render_fn or (lambda ep, out: render_run(run_dir, edl_path=ep, out_name=out, ledger=ledger, log=log))
    words = Words(json.loads((run_dir / "voice" / "voice.json").read_text())["words"])
    audio = Audio.load(run_dir)

    m = re.fullmatch(r"edl_v(\d+)\.json", edl_path.name)
    if not m:
        raise SystemExit(f"{edl_path.name} is not an edl_vN.json")
    a = int(m.group(1))
    edl_a = json.loads(edl_path.read_text())
    done = compared(run_dir, a)
    cands = [c for c in challengers(edl_a, run_dir) if c[0] not in done]
    if not cands:
        why = "one span only" if len(edl_a["spans"]) < 2 else f"every candidate was already compared against v{a}"
        log(f"hook a/b: v{a}: no challenger ({why})")
        return {"stop": f"no challenger: {why}"}

    cut_a = run_dir / f"cut_v{a}.mp4"
    if not cut_a.is_file() or Path((edl_a.get("render") or {}).get("output", "")).name != cut_a.name:
        log(f"hook a/b: rendering {edl_path.name} -> {cut_a.name}")
        render_fn(edl_path, cut_a.name)
        edl_a = json.loads(edl_path.read_text())
    rv_a = _json(run_dir / f"review_v{a}.json")
    reuse = bool(rv_a and Path(rv_a.get("cut", "")).name == cut_a.name and rv_a.get("review"))
    dur_a = (edl_a.get("render") or {}).get("duration_s") or edl_a["runtime_s"]
    est = estimate_cost(dur_a)
    need = est * (1 if reuse else 2)
    if need > budget_usd:
        stop = f"budget: {'one review' if reuse else 'two reviews'} at about ${est:.3f} each > ${budget_usd:.2f}"
        log(f"hook a/b: v{a}: {stop}")
        return {"stop": stop}
    judge_note = ""
    if compare_fn and need + estimate_compare_cost(dur_a, dur_a) > budget_usd:
        compare_fn = None  # the reviews fit, the judge does not: the scores decide and the record says so
        judge_note = f"no budget for the pairwise judge (~${estimate_compare_cost(dur_a, dur_a):.3f} more)"
        log(f"hook a/b: v{a}: {judge_note}, the two scores decide")
    spent = 0.0
    if not reuse:
        rv_a = review_fn(edl_a, cut_a, a)
        spent += rv_a["cost_usd"]
        fixes, skipped = plan_fixes(rv_a["review"], edl_a, words, audio)
        rv_a["fixes"], rv_a["skipped"] = [{**asdict(f), "fingerprint": f.fingerprint} for f in fixes], skipped
        dump_json(run_dir / f"{rv_a['name']}.json", rv_a)
        # A's own review may name the line it wants first: that becomes the challenger
        cands = [c for c in challengers(edl_a, run_dir) if c[0] not in done] or cands
    cand, why = cands[0]

    b = next_version(run_dir)
    fix = Fix(op="swap_hook", target=cand, issue=-1, severity="hook_ab", t_s=0.0)
    edl_b, plog = apply_fixes(edl_a, [fix], words, audio)
    plog += follow_cut(edl_a, edl_b, severity="hook_ab")
    edl_b["version"] = b
    entry = {"from": a, "review": None, "source": "hook_ab", "action": "hook_ab", "challenger": cand, "why": why,
             "fixes": plog}
    edl_b.setdefault("patches", []).append(entry)
    path_b = run_dir / f"edl_v{b}.json"
    dump_json(path_b, edl_b)
    old = edl_a["spans"][0]
    log(f"hook a/b: v{a} opens on {old['id']} \"{old['words'][:50]}\"; v{b} opens on {cand} "
        f"\"{plog[0]['note'].split(': ', 1)[-1][:50]}\" ({why})")
    cut_b = run_dir / f"cut_v{b}.mp4"
    render_fn(path_b, cut_b.name)
    edl_b = json.loads(path_b.read_text())
    rv_b = review_fn(edl_b, cut_b, b)
    spent += rv_b["cost_usd"]
    fixes, skipped = plan_fixes(rv_b["review"], edl_b, words, audio)
    rv_b["fixes"], rv_b["skipped"] = [{**asdict(f), "fingerprint": f.fingerprint} for f in fixes], skipped
    dump_json(run_dir / f"{rv_b['name']}.json", rv_b)

    ra, rb = rv_a["review"], rv_b["review"]
    by_score = winner(ra, rb)
    side, reason = by_score
    margin = abs(rb["overall"] - ra["overall"])
    decisive = margin >= NOISE_POINTS
    judged = None
    if compare_fn:
        judged = compare_fn(rv_a, rv_b)
        spent += judged["cost_usd"]
        side = judged["winner"]
        reason = f"the pairwise judge: {judged['verdict']}"
        decisive = judged["agree"] and judged["calls"][0]["winner"] != "tie"
    keep = a if side == "a" else b
    hook = "hook_in_1_5s"
    result = {"winner": side, "kept": keep, "edl": f"edl_v{keep}.json", "cut": f"cut_v{keep}.mp4", "reason": reason,
              "margin": margin, "decisive": decisive, "by_score": {"winner": by_score[0], "reason": by_score[1]},
              **({"judge": {k: judged[k] for k in ("winner", "agree", "verdict", "mode", "cost_usd")}
                            | {"calls": [{k: c[k] for k in ("order", "winner", "why", "criteria")} for c in judged["calls"]]}}
                 if judged else {"judge": None, **({"judge_note": judge_note} if judge_note else {})}),
              "a": {"version": a, "opener": old["id"], "overall": ra["overall"], "pass": ra["pass"],
                    "hook": (ra.get("scores") or {}).get(hook), "review": rv_a["name"], "reused": reuse},
              "b": {"version": b, "opener": cand, "overall": rb["overall"], "pass": rb["pass"],
                    "hook": (rb.get("scores") or {}).get(hook), "review": rv_b["name"]}}
    edl_b["patches"][-1]["result"] = result
    dump_json(path_b, edl_b)
    record = {"challenger": cand, "why": why, **result, "cost_usd": round(spent, 4),
              "budget_usd": budget_usd}
    (run_dir / "review").mkdir(exist_ok=True)
    dump_json(run_dir / "review" / f"hook_ab_v{a}_v{b}.json", record)
    log(f"hook a/b: v{a} {ra['overall']}{' pass' if ra['pass'] else ''} (hook {result['a']['hook']}) vs "
        f"v{b} {rb['overall']}{' pass' if rb['pass'] else ''} (hook {result['b']['hook']}): keep v{keep}, {reason}"
        + ("" if judged or result["decisive"] else f" (within the reviewer noise of {NOISE_POINTS} points)")
        + f"; ${spent:.4f}")
    return record
