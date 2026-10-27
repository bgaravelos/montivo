#!/usr/bin/env python3
"""Reviewer benchmark: plant one known fault per cut in finished runs, review every cut blind, count what was caught.

Kill test (b) asked whether the reviewer catches three faults on one cut. This asks how reliable it is: eight fault
types planted one at a time into the kept versions of three runs (012 Billy, 015 Billy's demo clip, 014 four
astronauts), plus the clean cut as a control, each reviewed N times by each reviewer:

  spark13   muse-spark-1.3 on the Meta API, the production review (video with sound, four facts)
  open      Muse Spark Open on Baseten: frames inline, no sound, so it also gets the heard-words fact (Voice on the cut)
  spark13h  muse-spark-1.3 with the heard-words fact too (does the fact help the reviewer that can hear?)

Faults (the window is in cut seconds, computed after the render from the rendered EDL):
  weak_hook     the hook starts 2.5 s or more earlier, on the words (or silence) before it     hook_in_1_5s / swap_hook
  mid_word      a body span ends 45 % into the speech of its longest-sounding last word      no_cut_mid_word / move_cut
  dead_air      a body span runs on into the longest pause after it (up to 2.2 s)             pacing / move_cut, drop_span
  repeat        a body span plays twice in a row                                             pacing / drop_span
  ending        the payoff stops after 40 % of its words                                     ending / extend_span
  caption_text  one word of a caption line replaced by a word nobody said                    captions_match_speech / caption_fix
  caption_face  a caption line forced up over the head                                       captions_legible / caption_fix
  framing       one segment's crop pushed 420 source px so the head leaves the frame         subject_framed / reframe_bias

A fault is caught when an issue of a matching criterion or fix op lies within TOL_S of its window; "major" when that
issue is a blocker or major (only those become patches in the loop). On the clean control every blocker or major issue
is a false alarm. Since the pass gate (review.gate_check, 27/10) the deterministic checks' issues count too, apart:
"caught with the gate" is the reviewer or a gate issue in the window, "pass" is the gated pass, "reviewer pass" the
reviewer's own, and every measured gate reason on a clean cut is a gate false alarm. Stored reviews with a gate are
scored with the current pass rule (review.close_gate: no open reviewer blocker/major either), so re-running the bench
on finished reviews re-scores them at $0.
Results: runs/bench/results.json and results.md; every review in runs/bench/<base>/rv_*.json. `--archive round2` first
moves the selected variants' old rv_* files (every reviewer) to runs/bench/<base>/round2/ and the old results to
runs/bench/round2/, then `--compare round2` scores those old reviews beside the new ones (before / after).

    .venv/bin/python tools/reviewer_bench.py --build-only
    .venv/bin/python tools/reviewer_bench.py --reviewers spark13 open --repeats 2 --budget 8
    .venv/bin/python tools/reviewer_bench.py --reviewers spark13 --variants clean mid_word framing caption_text weak_hook \
        --archive round2 --compare round2 --budget 2
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from montivo.config import OPEN_MODEL, SPARK_MODEL
from montivo.engine import review as R
from montivo.engine.api import dump_json
from montivo.engine.edges import Audio
from montivo.engine.render import merge_segments
from montivo.engine.spark import Spark
from montivo.ledger import Ledger
from montivo.run import RunLog, render_run

BASES = {"012": "edl_v2.json", "015": "edl_v3.json", "014": "edl_v1.json"}
FAULTS = ["weak_hook", "mid_word", "dead_air", "repeat", "ending", "caption_text", "caption_face", "framing"]
MATCH = {
    "weak_hook": ({"hook_in_1_5s"}, {"swap_hook"}),
    "mid_word": ({"no_cut_mid_word"}, {"move_cut", "extend_span"}),
    "dead_air": ({"pacing"}, {"move_cut", "drop_span"}),
    "repeat": ({"pacing", "brief_covered"}, {"drop_span"}),
    "ending": ({"ending"}, {"extend_span"}),
    "caption_text": ({"captions_match_speech"}, {"caption_fix"}),
    "caption_face": ({"captions_legible"}, {"caption_fix"}),
    "framing": ({"subject_framed"}, {"reframe_bias"}),
}
# The clean control counts only where the base version passed its own loop: 014's v1 never went through it (the
# NASA run), every reviewer finds real problems in it, so its clean reviews are reported apart.
PASSED_BASES = ("012", "015")
REVIEWERS = {"spark13": (SPARK_MODEL, False), "open": (OPEN_MODEL, True), "spark13h": (SPARK_MODEL, True)}
TOL_S = 1.0
HOOK_LEAD_S = 2.5
MID_WORD_AT = 0.45
DEAD_AIR_MIN_S = 1.3
DEAD_AIR_MAX_S = 2.2
ENDING_KEEP = 0.4
BIAS_PX = 420
WRONG_WORDS = ["Google", "budget", "weekend", "contract", "holiday", "printer", "Tuesday", "garden"]


# --------------------------------------------------------------------------- building the cuts

def strip_render(edl: dict) -> dict:
    edl = copy.deepcopy(edl)
    for k in ("captions", "callouts", "reframe", "render"):
        edl.pop(k, None)
    edl["title_card"] = False
    return edl


def body_spans(edl: dict) -> list[int]:
    """Indices of the spans between the hook and the payoff (all but the first and last)."""
    return list(range(1, len(edl["spans"]) - 1))


def all_words(words: R.Words) -> list[dict]:
    return sorted((w for tw in words.by_turn.values() for w in tw), key=lambda w: w["t_start"])


def runtime(edl: dict) -> None:
    for s in edl["spans"]:
        s["duration"] = round(s["t_out"] - s["t_in"], 3)
    edl["runtime_s"] = round(sum(s["duration"] for s in edl["spans"]), 2)


def fault_weak_hook(edl: dict, words: R.Words, clean: dict) -> dict:
    s = edl["spans"][0]
    tw = words.turn(s["turn_id"])
    kept = R.kept_elsewhere(edl, s)
    orig = s["t_in"]
    k = s["word_from"]
    while k > 0 and k - 1 not in kept and orig - tw[k - 1]["t_start"] < HOOK_LEAD_S:
        k -= 1
    s["word_from"] = k
    s["t_in"] = tw[k]["t_start"]
    if orig - s["t_in"] < HOOK_LEAD_S:  # not enough words before it: the silence (or room tone) before them
        s["t_in"] = round(max(0.0, orig - HOOK_LEAD_S), 3)
    R.refresh_span(s, words)
    return {"span": s["id"], "lead_s": round(orig - s["t_in"], 3), "words": s["words"]}


def fault_mid_word(edl: dict, words: R.Words, clean: dict, audio: Audio | None = None) -> dict:
    """The cut lands 45 % into the source audio's speech run of a body span's last word: the longest run inside the
    word's Voice window, on the span whose last word sounds longest. Voice can stretch a word over the pause after it
    (012's "now", 0.96 s for 0.44 s of speech), and 45 % of that landed after the spoken word: an inaudible plant.
    Without audio.wav, 45 % of the Voice word (the round-3 plant)."""
    def last(i: int) -> dict:
        s = edl["spans"][i]
        return words.turn(s["turn_id"])[s["word_to"]]

    def run(i: int) -> tuple[float, float]:
        w = last(i)
        if audio is None:
            return w["t_start"], w["t_end"]
        runs = [(max(a, w["t_start"]), min(b, w["t_end"])) for a, b in audio.speech_runs(w["t_start"], w["t_end"])]
        return max(runs, key=lambda r: r[1] - r[0], default=(w["t_start"], w["t_start"]))
    i = max(body_spans(edl), key=lambda i: run(i)[1] - run(i)[0])
    s, w, (a, b) = edl["spans"][i], last(i), run(i)
    if b - a < 0.1:
        raise RuntimeError(f"mid_word: no body span's last word has 0.1 s of speech over {audio.threshold_db()} dB")
    s["t_out"] = round(a + MID_WORD_AT * (b - a), 3)
    meta = {"span": s["id"], "word": w["w"], "src_t": s["t_out"], "speech": [a, b]}
    if audio is not None:
        meta.update(edge_db=round(audio.level(s["t_out"] - R.EDGE_HALF_S, s["t_out"] + R.EDGE_HALF_S), 1),
                    threshold_db=audio.threshold_db())
    return meta


def fault_dead_air(edl: dict, words: R.Words, clean: dict) -> dict | None:
    allw = all_words(words)
    best = None
    for i in body_spans(edl):
        s = edl["spans"][i]
        end = words.turn(s["turn_id"])[s["word_to"]]["t_end"]
        nxt = next((w["t_start"] for w in allw if w["t_start"] >= end + 0.01), None)
        if nxt is None:
            continue
        gap = nxt - end
        if best is None or gap > best[1]:
            best = (i, gap, end)
    if best is None or best[1] < DEAD_AIR_MIN_S:
        return None
    i, gap, end = best
    s = edl["spans"][i]
    old = s["t_out"]
    s["t_out"] = round(end + min(gap - 0.15, DEAD_AIR_MAX_S), 3)
    return {"span": s["id"], "added_s": round(s["t_out"] - old, 3), "gap_s": round(gap, 2)}


def fault_repeat(edl: dict, words: R.Words, clean: dict) -> dict:
    cands = [i for i in body_spans(edl) if len(edl["spans"][i]["words"].split()) >= 6] or body_spans(edl)
    i = cands[len(cands) // 2]
    dup = copy.deepcopy(edl["spans"][i])
    dup["id"] = f"{dup['id']}r"
    dup["contiguous_with_previous"] = False
    edl["spans"].insert(i + 1, dup)
    for k, s in enumerate(edl["spans"]):
        s["order"] = k
    return {"span": edl["spans"][i]["id"], "dup": dup["id"]}


def fault_ending(edl: dict, words: R.Words, clean: dict) -> dict:
    s = edl["spans"][-1]
    n = s["word_to"] - s["word_from"] + 1
    keep = max(2, int(round(n * ENDING_KEEP)))
    s["word_to"] = s["word_from"] + keep - 1
    s["t_out"] = words.turn(s["turn_id"])[s["word_to"]]["t_end"]
    R.refresh_span(s, words)
    return {"span": s["id"], "kept_words": keep, "of": n, "words": s["words"]}


def _caption_pick(clean: dict, nth: int) -> dict:
    """A caption line in the middle of the clean cut with three or more words (nth: 0 first pick, 1 the next one)."""
    lines = [c for c in clean["captions"] if len(c["text"].split()) >= 3] or clean["captions"]
    mid = sorted(lines, key=lambda c: abs(c["t_in"] - clean["runtime_s"] / 2))
    return mid[min(nth, len(mid) - 1)]


def _src_of(clean: dict, line: dict) -> tuple[str, float]:
    cm = {c.span: c for c in R.cut_map(clean)}
    sid = line["spans"][0]
    s = next(x for x in clean["spans"] if x["id"] == sid)
    return sid, round(s["t_in"] + (line["t_in"] - cm[sid].cut_in) + 0.01, 3)


def fault_caption_text(edl: dict, words: R.Words, clean: dict) -> dict:
    line = _caption_pick(clean, 0)
    toks = line["text"].split()
    k = max(range(len(toks)), key=lambda j: len(toks[j].strip(".,!?")))
    wrong = next(w for w in WRONG_WORDS if w.lower() not in line["text"].lower())
    toks[k] = wrong + toks[k][len(toks[k].rstrip(".,!?")):]  # keep the punctuation after the word
    sid, src_t = _src_of(clean, line)
    edl.setdefault("caption_overrides", []).append({"span": sid, "src_t": src_t, "text": " ".join(toks), "why": "bench"})
    return {"span": sid, "src_t": src_t, "was": line["text"], "now": " ".join(toks)}


def fault_caption_face(edl: dict, words: R.Words, clean: dict) -> dict:
    line = _caption_pick(clean, 1)
    head = line.get("head_box") or [0, 700, 1080, 1300]
    top = int((head[1] + head[3]) / 2 - 70)
    sid, src_t = _src_of(clean, line)
    edl.setdefault("caption_overrides", []).append({"span": sid, "src_t": src_t, "top": top, "why": "bench"})
    return {"span": sid, "src_t": src_t, "text": line["text"], "top": top, "head_box": head}


def fault_framing(edl: dict, words: R.Words, clean: dict) -> dict:
    segs = merge_segments(edl["spans"])
    mid = [g for g in segs if g.index not in (0, len(segs) - 1)] or segs
    seg = mid[len(mid) // 2]
    heads = [c["head_box"] for c in clean["captions"] if set(c["spans"]) & set(seg.span_ids) and c.get("head_box")]
    head_x = statistics.mean((h[0] + h[2]) / 2 for h in heads) if heads else 540
    bias = BIAS_PX if head_x < 540 else -BIAS_PX  # move the crop away from the side the head is on
    for s in edl["spans"]:
        if s["id"] in seg.span_ids:
            s["reframe_bias_px"] = bias
    return {"spans": list(seg.span_ids), "bias_px": bias}


FAULT_FN = {"weak_hook": fault_weak_hook, "mid_word": fault_mid_word, "dead_air": fault_dead_air,
            "repeat": fault_repeat, "ending": fault_ending, "caption_text": fault_caption_text,
            "caption_face": fault_caption_face, "framing": fault_framing}


def window(fault: str, meta: dict, edl: dict) -> list[float]:
    """Where the fault is in the rendered cut (cut seconds)."""
    cm = {c.span: c for c in R.cut_map(edl)}
    if fault == "weak_hook":
        c = cm[meta["span"]]
        return [0.0, round(c.cut_in + meta["lead_s"], 2)]
    if fault == "mid_word":
        return [cm[meta["span"]].cut_out] * 2
    if fault == "dead_air":
        c = cm[meta["span"]]
        return [round(c.cut_out - meta["added_s"], 2), c.cut_out]
    if fault == "repeat":  # where a viewer hears it again: the second copy
        return [cm[meta["dup"]].cut_in, cm[meta["dup"]].cut_out]
    if fault == "ending":
        end = cm[meta["span"]].cut_out
        return [round(max(0.0, end - 2.5), 2), end]
    if fault in ("caption_text", "caption_face"):
        for c in edl["captions"]:
            if meta["span"] in c["spans"] and (fault == "caption_face" and c["placement"] == "forced"
                                               or fault == "caption_text" and c["text"] == meta["now"]):
                return [c["t_in"], c["t_out"]]
        raise RuntimeError(f"{fault}: the planted caption is not in the rendered EDL")
    if fault == "framing":
        spans = [cm[s] for s in meta["spans"]]
        return [min(c.cut_in for c in spans), max(c.cut_out for c in spans)]
    raise KeyError(fault)


def setup_base(base: str, out: Path) -> Path:
    src = Path("runs") / base
    out.mkdir(parents=True, exist_ok=True)
    (out / "voice").mkdir(exist_ok=True)
    shutil.copy(src / "voice" / "voice.json", out / "voice" / "voice.json")
    for f in ("audio.wav", "speakers.json"):
        if (src / f).is_file() and not (out / f).exists():
            shutil.copy(src / f, out / f)
    return src / BASES[base]


def build(base: str, root: Path, log) -> dict:
    """Render the clean cut and one cut per fault into root/<base>; returns {variant: {edl, cut, meta, window}}."""
    out = root / base
    edl_src = setup_base(base, out)
    words = R.Words(json.loads((out / "voice" / "voice.json").read_text())["words"])
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    clean_path = out / "edl_clean.json"
    if "clean" not in manifest:
        dump_json(clean_path, strip_render(json.loads(edl_src.read_text())))
        render_run(out, edl_path=clean_path, out_name="cut_clean.mp4", title=False, log=log)
        manifest["clean"] = {"edl": clean_path.as_posix(), "cut": (out / "cut_clean.mp4").as_posix(), "meta": {},
                             "window": None}
        dump_json(manifest_path, manifest)
    clean = json.loads(clean_path.read_text())
    audio = Audio.load(out)
    for fault in FAULTS:
        if fault in manifest:
            continue
        edl = strip_render(clean)
        meta = FAULT_FN[fault](edl, words, clean, **({"audio": audio} if fault == "mid_word" else {}))
        if fault == "weak_hook" and meta["lead_s"] < 1.5:  # the hook would still start inside 1.5 s: not a fault
            meta = None
        if meta is None:
            log(f"bench {base}: {fault} does not apply here (no pause of {DEAD_AIR_MIN_S} s after a body span, "
                "or the hook's source has under 1.5 s before it)")
            manifest[fault] = {"skipped": True}
            dump_json(manifest_path, manifest)
            continue
        runtime(edl)
        edl["bench_fault"] = {"fault": fault, **meta}
        path = out / f"edl_{fault}.json"
        dump_json(path, edl)
        render_run(out, edl_path=path, out_name=f"cut_{fault}.mp4", title=False, log=log)
        rendered = json.loads(path.read_text())
        manifest[fault] = {"edl": path.as_posix(), "cut": (out / f"cut_{fault}.mp4").as_posix(), "meta": meta,
                           "window": window(fault, meta, rendered)}
        log(f"bench {base}: {fault} {json.dumps(meta)} window {manifest[fault]['window']}")
        dump_json(manifest_path, manifest)
    return manifest


# --------------------------------------------------------------------------- scoring

def dist(a: list[float], b: list[float]) -> float:
    return max(0.0, max(a[0], b[0]) - min(a[1], b[1]))


def score(review: dict, fault: str, win: list[float] | None, gate: dict | None = None) -> dict:
    """The reviewer's issues against the fault window; with `gate` (review_vN.json pass_gate) the gate's issues too,
    reported as caught_gate (the reviewer or the gate caught it)."""
    gate_issues = (gate or {}).get("issues") or []
    if fault == "clean":
        major = [i for i in review["issues"] if i["severity"] in R.APPLY_SEVERITY]
        return {"false_alarms": len(major), "false_alarm_criteria": sorted({i["criterion"] for i in major}),
                "gate_false_alarms": len((gate or {}).get("checks", (gate or {}).get("reasons")) or [])}
    out = match(review["issues"], fault, win)
    out["caught_gate"] = out["caught_major"] or match(gate_issues, fault, win)["caught_major"]
    return out


def match(issues: list[dict], fault: str, win: list[float]) -> dict:
    major = [i for i in issues if i["severity"] in R.APPLY_SEVERITY]
    crits, ops = MATCH[fault]
    best = None
    for i in issues:
        if i["criterion"] not in crits and i["fix_op"] not in ops:
            continue
        d = dist([i["t_s"], max(i["t_s"], i["t_end_s"])], win)
        key = (d > TOL_S, i["severity"] not in R.APPLY_SEVERITY, d)
        if best is None or key < best[0]:
            best = (key, {"d": round(d, 2), "t_s": i["t_s"], "t_end_s": i["t_end_s"], "criterion": i["criterion"],
                          "fix_op": i["fix_op"], "severity": i["severity"], "what": i["what"][:160]})
    caught = bool(best and best[1]["d"] <= TOL_S)
    return {"caught": caught, "caught_major": caught and best[1]["severity"] in R.APPLY_SEVERITY,
            "best": best[1] if best else None,
            "other_major": sum(1 for i in major if not (i["criterion"] in crits or i["fix_op"] in ops))}


# --------------------------------------------------------------------------- reviewing

def review_job(root: Path, base: str, variant: str, entry: dict, reviewer: str, k: int, log) -> dict:
    out = root / base
    name = f"rv_{variant}_{reviewer}_{k}"
    path = out / f"{name}.json"
    if path.is_file():
        rv = json.loads(path.read_text())
    else:
        model, heard = REVIEWERS[reviewer]
        ledger = Ledger.resume(out / "ledgers" / f"{name}.json")
        spark = Spark(out, ledger, model=model, log=log)
        edl = json.loads(Path(entry["edl"]).read_text())
        rv = R.review_render(out, edl, Path(entry["cut"]), 1, ledger, spark, log=log, name=name, heard=heard,
                             heard_dir=out / "heard" / variant)
        dump_json(path, rv)
    return score_row(rv, base, variant, entry, reviewer, k)


def score_row(rv: dict, base: str, variant: str, entry: dict, reviewer: str, k: int) -> dict:
    win = None if variant == "clean" else window(variant, entry["meta"], json.loads(Path(entry["edl"]).read_text()))
    gate = rv.get("pass_gate")
    review = copy.deepcopy(rv["review"])
    if gate is not None:  # stored reviews are scored with today's pass rule (close_gate: open reviewer majors too)
        gate = R.close_gate(review, gate)
    sc = score(review, variant, win, gate)
    return {"base": base, "variant": variant, "reviewer": reviewer, "k": k, "overall": review["overall"],
            "pass": review["pass"], "reviewer_pass": review.get("reviewer_pass", review["pass"]),
            "gate": (gate or {}).get("reasons") or [], "cost_usd": rv["cost_usd"], "seconds": rv["seconds"],
            "window": win, **sc}


def archive(root: Path, bases: list[str], variants: list[str] | None, name: str, log) -> None:
    """Move the selected variants' rv_* files (every reviewer) to <base>/<name>/ and results.* to root/<name>/, kept."""
    for b in bases:
        dest = root / b / name
        for f in sorted((root / b).glob("rv_*.json")):
            v = f.stem[3:].rsplit("_", 2)[0]
            if variants is None or v in variants:
                dest.mkdir(exist_ok=True)
                if (dest / f.name).exists():
                    raise SystemExit(f"{dest / f.name} exists: archive {name} already holds this review")
                shutil.move(f, dest / f.name)
        log(f"bench: {b}: old reviews of {variants or 'every variant'} moved to {dest}")
    for f in ("results.json", "results.md"):
        if (root / f).is_file() and not (root / name / f).exists():
            (root / name).mkdir(exist_ok=True)
            shutil.copy2(root / f, root / name / f)


def compare_rows(root: Path, name: str, manifests: dict, rows: list[dict]) -> list[dict]:
    """The archived reviews of the same (base, variant, reviewer, k) as this run's rows, scored the same way; a variant
    rebuilt since (its old entry in <base>/<name>/manifest.json) is scored against its old cut's window."""
    old = []
    for r in rows:
        f = root / r["base"] / name / f"rv_{r['variant']}_{r['reviewer']}_{r['k']}.json"
        if f.is_file():
            mf = root / r["base"] / name / "manifest.json"
            entry = {**manifests[r["base"]], **(json.loads(mf.read_text()) if mf.is_file() else {})}[r["variant"]]
            old.append(score_row(json.loads(f.read_text()), r["base"], r["variant"], entry, r["reviewer"], r["k"]))
    return old


def prewarm_heard(root: Path, base: str, manifest: dict, log) -> None:
    """Voice on every cut once, before the reviews run in parallel (two reviewers must not transcribe one cut twice)."""
    out = root / base
    ledger = Ledger.resume(out / "ledgers" / "heard.json")
    for variant, entry in manifest.items():
        if entry.get("skipped"):
            continue
        R.heard_words(Path(entry["cut"]), out / "heard" / variant, ledger, log=log)


def summarize(rows: list[dict], reviewers: list[str]) -> dict:
    out: dict = {}
    for rev in reviewers:
        rr = [r for r in rows if r["reviewer"] == rev]
        if not rr:
            continue
        faulty = [r for r in rr if r["variant"] != "clean"]
        clean = [r for r in rr if r["variant"] == "clean" and r["base"] in PASSED_BASES]
        raw = [r for r in rr if r["variant"] == "clean" and r["base"] not in PASSED_BASES]
        per = {}
        for f in FAULTS:
            fr = [r for r in faulty if r["variant"] == f]
            if fr:
                per[f] = {"n": len(fr), "caught": sum(r["caught"] for r in fr), "caught_major": sum(r["caught_major"] for r in fr),
                          "caught_gate": sum(r.get("caught_gate", r["caught_major"]) for r in fr),
                          "blocked": sum(not r["pass"] for r in fr),
                          "mean_overall": round(statistics.mean(r["overall"] for r in fr), 1)}
        sd = []
        for key in {(r["base"], r["variant"]) for r in rr}:
            scores = [r["overall"] for r in rr if (r["base"], r["variant"]) == key]
            if len(scores) > 1:
                sd.append(statistics.pstdev(scores))
        out[rev] = {
            "reviews": len(rr), "cost_usd": round(sum(r["cost_usd"] for r in rr), 4),
            "mean_seconds": round(statistics.mean(r["seconds"] for r in rr), 1),
            "detection": round(sum(r["caught"] for r in faulty) / len(faulty), 3) if faulty else None,
            "detection_major": round(sum(r["caught_major"] for r in faulty) / len(faulty), 3) if faulty else None,
            "per_fault": per,
            "clean_mean_overall": round(statistics.mean(r["overall"] for r in clean), 1) if clean else None,
            "clean_pass_rate": round(sum(r["pass"] for r in clean) / len(clean), 3) if clean else None,
            "clean_false_alarms_per_review": round(statistics.mean(r["false_alarms"] for r in clean), 2) if clean else None,
            "faulty_pass_rate": round(sum(r["pass"] for r in faulty) / len(faulty), 3) if faulty else None,
            "faulty_reviewer_pass_rate": (round(sum(r["reviewer_pass"] for r in faulty) / len(faulty), 3)
                                          if faulty else None),
            "detection_gate": (round(sum(r.get("caught_gate", r["caught_major"]) for r in faulty) / len(faulty), 3)
                               if faulty else None),
            "gate_blocked_reviewer_pass": sum(1 for r in faulty if r["reviewer_pass"] and not r["pass"]),
            "clean_gate_false_alarms": sum(r.get("gate_false_alarms", 0) for r in clean),
            "clean_reviewer_pass_rate": round(sum(r["reviewer_pass"] for r in clean) / len(clean), 3) if clean else None,
            "score_sd_same_cut": round(statistics.mean(sd), 1) if sd else None,
            "unreviewed_v1_mean_overall": round(statistics.mean(r["overall"] for r in raw), 1) if raw else None,
        }
    return out


def pct(v) -> str:
    return f"{v:.0%}" if v is not None else "-"


def markdown(summary: dict, rows: list[dict], title: str = "# Reviewer benchmark") -> str:
    revs = list(summary)
    lines = [title, "", "| | " + " | ".join(revs) + " |", "|---|" + "---|" * len(revs)]
    def row(label: str, f) -> None:
        lines.append(f"| {label} | " + " | ".join(f(summary[r]) for r in revs) + " |")
    row("reviews", lambda s: str(s["reviews"]))
    row("faults caught (any severity)", lambda s: f"{s['detection']:.0%}" if s["detection"] is not None else "-")
    row("faults caught as blocker/major", lambda s: f"{s['detection_major']:.0%}" if s["detection_major"] is not None else "-")
    row("faults caught as blocker/major or by the gate", lambda s: pct(s.get("detection_gate")))
    for f in FAULTS:
        row(f"  {f}", lambda s, f=f: (f"{s['per_fault'][f]['caught']}/{s['per_fault'][f]['n']} "
                                     f"({s['per_fault'][f]['caught_major']} major, "
                                     f"{s['per_fault'][f].get('caught_gate', s['per_fault'][f]['caught_major'])} with the gate), "
                                     f"score {s['per_fault'][f]['mean_overall']}")
            if f in s["per_fault"] else "-")
    row("clean (012, 015 kept versions): mean score", lambda s: str(s["clean_mean_overall"]))
    row("clean: pass rate (gated)", lambda s: pct(s["clean_pass_rate"]))
    row("clean: reviewer's own pass rate", lambda s: pct(s.get("clean_reviewer_pass_rate")))
    row("clean: false alarms per review", lambda s: str(s["clean_false_alarms_per_review"]))
    row("clean: gate false alarms (all reviews)", lambda s: str(s.get("clean_gate_false_alarms", "-")))
    row("v1 that never passed a loop (014): mean score", lambda s: str(s["unreviewed_v1_mean_overall"]))
    row("faulty cuts passed (gated)", lambda s: pct(s["faulty_pass_rate"]))
    row("faulty cuts the reviewer passed", lambda s: pct(s.get("faulty_reviewer_pass_rate")))
    row("  of those, blocked by the gate", lambda s: str(s.get("gate_blocked_reviewer_pass", "-")))
    row("score SD, same cut", lambda s: str(s["score_sd_same_cut"]))
    row("seconds per review", lambda s: str(s["mean_seconds"]))
    row("cost", lambda s: f"${s['cost_usd']:.3f}")
    lines += ["", "Misses:", ""]
    for r in sorted(rows, key=lambda r: (r["reviewer"], r["base"], r["variant"], r["k"])):
        if r["variant"] != "clean" and not r["caught"]:
            b = r["best"]
            lines.append(f"- {r['reviewer']} {r['base']} {r['variant']} #{r['k']} (window {r['window']}): "
                         + (f"nearest {b['criterion']}/{b['fix_op']} at {b['t_s']} s, {b['d']} s off" if b else "no matching issue"))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("runs/bench"))
    ap.add_argument("--bases", nargs="+", default=list(BASES))
    ap.add_argument("--reviewers", nargs="+", default=["spark13", "open"], choices=list(REVIEWERS))
    ap.add_argument("--variants", nargs="+", default=None, help="clean and/or fault names (default all)")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--budget", type=float, default=8.0, help="dollars for the review calls of this invocation")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--archive", help="move the selected variants' old reviews to <base>/<ARCHIVE>/ first")
    ap.add_argument("--compare", help="score the reviews archived under this name beside the new ones")
    args = ap.parse_args()
    log = RunLog(args.root / "bench.log")
    manifests = {b: build(b, args.root, log) for b in args.bases}
    if args.build_only:
        return
    if args.archive:
        archive(args.root, args.bases, args.variants, args.archive, log)
    for b in args.bases:  # every review needs the cut's Voice transcript now (edge words, caption diff)
        prewarm_heard(args.root, b, manifests[b], log)
    jobs = [(b, v, e, rev, k) for b in args.bases for v, e in manifests[b].items() if not e.get("skipped")
            and (args.variants is None or v in args.variants)
            for rev in args.reviewers for k in range(1, args.repeats + 1)]
    spent, rows = 0.0, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {}
        for job in jobs:
            done = (args.root / job[0] / f"rv_{job[1]}_{job[3]}_{job[4]}.json").is_file()
            est = 0.0 if done else R.estimate_cost(R.media_duration(Path(job[2]["cut"])))
            if spent + est > args.budget:
                log(f"bench: budget ${args.budget:.2f} reached, {job[0]} {job[1]} {job[3]} #{job[4]} not reviewed")
                continue
            spent += est
            futs[pool.submit(review_job, args.root, *job, log)] = job
        for f in as_completed(futs):
            try:
                rows.append(f.result())
            except Exception as exc:  # one failed call must not lose the others
                log(f"bench: {futs[f][:2]} {futs[f][3]} #{futs[f][4]} failed: {exc}")
    summary = summarize(rows, args.reviewers)
    md = markdown(summary, rows)
    result = {"summary": summary, "rows": rows, "tol_s": TOL_S, "faults": FAULTS}
    if args.compare:
        old = compare_rows(args.root, args.compare, manifests, rows)
        result["compare"] = {"name": args.compare, "summary": summarize(old, args.reviewers), "rows": old}
        md += "\n" + markdown(result["compare"]["summary"], old,
                               title=f"# Before: the reviews archived in {args.compare}")
    dump_json(args.root / "results.json", result)
    (args.root / "results.md").write_text(md)
    log(md)


if __name__ == "__main__":
    main()
