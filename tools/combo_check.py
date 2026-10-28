#!/usr/bin/env python3
"""Combination matrix (debugging day 1, 29/10): chains of human edits on one EDL, run through the CLI
(`python -m montivo.human ...`) on a throwaway APFS clone of a finished run folder, with the invariants checked after
every version. Every op has its own tests; nobody had checked what they do to each other's fields.

After every version:
  files      edl_vN.json and cut_vN.mp4 both exist (never one without the other), and an op that refuses writes neither
  duration   cut_vN.mp4 lasts the EDL's spans (+ the title card) within one frame
  loudness   -14 LUFS +-0.5, true peak <= -1 dBTP (ffmpeg ebur128 on the file)
  words      every kept word once (no two clips share a word or a stretch of audio), the words the op deleted gone, no
             word the op did not add appears, no clip's audio covers most of a word outside its own range
  fields     what the op did not touch stays as it was: music, look (line_breaks too), overlays, punch, punch_skip,
             reframe_speaker_ids, caption_overrides, glossary, highlight, title card, the clips' callouts and places
  segcache   the same EDL rendered again with MONTIVO_SEGCACHE=0 gives the same bytes
At a chain's end (music and overlays on): `python -m montivo.export` at 9:16, 4:5 and 1:1 renders, at the frame size.

$0: MODEL_API_KEY is unset for every command; an overlay gets a placeholder picture instead of Muse Image (the only
shim: broll.generate is replaced, everything else is the CLI as the server calls it).

    .venv/bin/python tools/combo_check.py                  # every chain on a clone of runs/016 (from its newest EDL)
    .venv/bin/python tools/combo_check.py --chains A,C --keep
Exit 1 when an invariant fails; the report is printed and written to <work>/report.json.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")
FPS_TOL = 1 / 30 + 0.002
WATCHED = ("music", "look", "overlays", "punch", "punch_skip", "reframe_speaker_ids", "caption_overrides", "glossary",
           "highlight", "title_card", "title_card_text", "target")
STRUCTURAL = {"drop", "trim_in", "trim_out", "cut", "split", "insert", "pauses", "reorder", "edges"}
MAY_CHANGE = {  # watched fields each op may change (structural ops: caption_overrides only for words that left)
    "caption": {"caption_overrides"}, "text": {"title_card", "title_card_text"}, "title_off": {"title_card"},
    "title_on": {"title_card"}, "style_preset": {"look"}, "style_fixed": {"look"}, "style_pin": set(),
    "music_on": {"music"}, "music_faders": {"music"}, "music_off": {"music"}, "punch_on": {"punch"},
    "punch_off": {"punch"}, "punch_skip": {"punch_skip"}, "pin": {"reframe_speaker_ids"},
    "pin_none": {"reframe_speaker_ids"}, "overlay_add": {"overlays"}, "overlay_set": {"overlays"},
    "overlay_remove": {"overlays"}}


def env(cache: bool = True) -> dict:
    e = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "BASETEN_API_KEY")}
    e["PYTHONPATH"] = str(ROOT)
    if not cache:
        e["MONTIVO_SEGCACHE"] = "0"
    return e


def sh(cmd: list[str], cache: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env=env(cache), capture_output=True, text=True)


# ---------------------------------------------------------------- run folder state

def versions(run: Path) -> list[int]:
    return sorted(int(m.group(1)) for p in run.glob("edl_v*.json") if (m := re.fullmatch(r"edl_v(\d+)\.json", p.name)))


def cuts(run: Path) -> list[int]:
    return sorted(int(m.group(1)) for p in run.glob("cut_v*.mp4") if (m := re.fullmatch(r"cut_v(\d+)\.mp4", p.name)))


def load(run: Path, n: int) -> dict:
    return json.loads((run / f"edl_v{n}.json").read_text())


def voice_words(run: Path) -> list[dict]:
    return json.loads((run / "voice" / "voice.json").read_text())["words"]


def kept(edl: dict) -> list[tuple[int, int]]:
    """(turn, word index) of every word the EDL keeps, in cut order, duplicates kept (they are the fault)."""
    return [(s["turn_id"], i) for s in edl["spans"] for i in range(s["word_from"], s["word_to"] + 1)]


def unused_runs(edl: dict, words: list[dict]) -> list[tuple[int, int, int]]:
    """(turn, first, last) of every run of words no clip keeps."""
    have = set(kept(edl))
    out: list[tuple[int, int, int]] = []
    for w in words:
        key = (w["turn_id"], w["i"])
        if key in have:
            continue
        if out and out[-1][0] == key[0] and out[-1][2] == key[1] - 1:
            out[-1] = (key[0], out[-1][1], key[1])
        else:
            out.append((key[0], key[1], key[1]))
    return out


# ---------------------------------------------------------------- measurements

def probe_duration(p: Path) -> float:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration,width,height",
            "-of", "json", str(p)])
    return float(json.loads(r.stdout)["streams"][0]["duration"])


def probe_size(p: Path) -> tuple[int, int]:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json",
            str(p)])
    s = json.loads(r.stdout)["streams"][0]
    return s["width"], s["height"]


def loudness(p: Path) -> tuple[float, float]:
    r = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-map", "0:a:0", "-af", "ebur128=peak=true",
            "-f", "null", "-"])
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", tail).group(1))
    return i, tp


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def expected_duration(run: Path, edl: dict) -> float:
    title = (edl.get("render") or {}).get("title") or {}
    t = float(title.get("seconds") or 0.0) if isinstance(title, dict) else 0.0
    return sum(s["t_out"] - s["t_in"] for s in edl["spans"]) + t


# ---------------------------------------------------------------- steps

@dataclass
class Step:
    name: str
    argv: list[str]                      # after `python -m montivo.human`
    gone: set = field(default_factory=set)   # (turn, i) the op must remove
    added: set = field(default_factory=set)  # (turn, i) the op may add
    slack: int = 0                       # words past `gone` the op may also take (trim settles on a sentence end)
    restore_of: int | None = None
    shim: bool = False


def mid_body(edl: dict) -> dict:
    spans = edl["spans"]
    body = spans[1:-1] or spans
    return body[len(body) // 2]


def longest(edl: dict, skip_hook: bool = True) -> dict:
    spans = edl["spans"][1:] if skip_hook and len(edl["spans"]) > 1 else edl["spans"]
    return max(spans, key=lambda s: s["word_to"] - s["word_from"])


def caption_mid(edl: dict) -> dict | None:
    caps = edl.get("captions") or []
    return caps[len(caps) // 2] if caps else None


def make_step(name: str, run: Path, edl: dict, words: list[dict], chain_versions: list[int]) -> Step | None:
    r = str(run)
    spans = edl["spans"]
    if name == "drop":
        s = mid_body(edl)
        return Step(name, ["drop", r, "--span", s["id"]], gone={(s["turn_id"], i) for i in range(s["word_from"], s["word_to"] + 1)})
    if name in ("trim_in", "trim_out"):
        s = longest(edl)
        if s["word_to"] - s["word_from"] < 5:
            return None
        if name == "trim_in":
            w = s["word_from"] + 2
            return Step(name, ["trim", r, "--span", s["id"], "--edge", "in", "--word", str(w)],
                        gone={(s["turn_id"], i) for i in range(s["word_from"], w)}, slack=2)
        w = s["word_to"] - 2
        return Step(name, ["trim", r, "--span", s["id"], "--edge", "out", "--word", str(w)],
                    gone={(s["turn_id"], i) for i in range(w + 1, s["word_to"] + 1)}, slack=2)
    if name == "cut":
        s = longest(edl)
        if s["word_to"] - s["word_from"] < 6:
            return None
        a = s["word_from"] + 2
        return Step(name, ["cut", r, "--words", f"{s['id']}:{a}-{a + 1}"], gone={(s["turn_id"], a), (s["turn_id"], a + 1)})
    if name == "split":
        s = longest(edl)
        w = (s["word_from"] + s["word_to"] + 1) // 2
        return Step(name, ["split", r, "--span", s["id"], "--word", str(w)])
    if name == "insert":
        runs = [x for x in unused_runs(edl, words) if x[2] - x[1] >= 1]
        if not runs:
            return None
        t, a, b = max(runs, key=lambda x: x[1])  # the latest passage (016: "and then Spark watches on ...")
        b = min(b, a + 3)
        return Step(name, ["insert", r, "--words", f"{t}:{a}-{b}"], added={(t, i) for i in range(a, b + 1)})
    if name == "pauses":
        return Step(name, ["pauses", r])
    if name == "reorder":
        if len(spans) < 4:
            return None
        return Step(name, ["reorder", r, "--span", spans[-2]["id"], "--before", spans[1]["id"]])
    if name == "edges":
        return Step(name, ["edges", r])
    if name == "caption":
        c = caption_mid(edl)
        if c is None:
            return None
        t = (c["t_in"] + c["t_out"]) / 2
        return Step(name, ["caption", r, "--at", f"{t:.3f}", "--text", c["text"].upper()])
    if name == "text":
        s = mid_body(edl)
        return Step(name, ["text", r, "--callout", f"{s['id']}=COMBO CALLOUT"])
    if name == "title_off":
        return Step(name, ["text", r, "--title-off"]) if edl.get("title_card", True) is not False else None
    if name == "title_on":
        return Step(name, ["text", r, "--title-on"]) if edl.get("title_card", True) is False else None
    if name == "style_preset":
        return Step(name, ["style", r, "--look", json.dumps({"preset": "box"})])
    if name == "style_fixed":
        return Step(name, ["style", r, "--look", json.dumps({"position": "fixed", "top": 1180})])
    if name == "style_pin":
        s = next((s for s in spans if s.get("callout")), None)
        return Step(name, ["style", r, "--callout", f"{s['id']}=140,360"]) if s else None
    if name == "music_on":
        return Step(name, ["music", r, "--track", "chill_lofi"])
    if name == "music_faders":
        tr = (edl.get("music") or {}).get("track") or "chill_lofi"
        return Step(name, ["music", r, "--track", tr, "--level", "-16", "--voice", "3"])
    if name == "music_off":
        return Step(name, ["music", r, "--off"]) if edl.get("music") else None
    if name == "punch_on":
        return Step(name, ["punch", r, "--on"]) if not edl.get("punch") else None
    if name == "punch_off":
        return Step(name, ["punch", r, "--off"]) if edl.get("punch") else None
    if name == "punch_skip":
        e = next((e for e in edl.get("punch_ins") or [] if e.get("key")), None)
        return Step(name, ["punch", r, "--skip", e["key"]]) if edl.get("punch") and e else None
    if name in ("pin", "pin_none"):
        heads = sorted(json.loads((run / "track" / "head" / "tracks.json").read_text()).get("objects") or {})
        speaker = spans[0].get("speaker") or "A"
        if name == "pin_none":
            return Step(name, ["pin", r, "--speaker", speaker, "--head", "none"]) \
                if (edl.get("reframe_speaker_ids") or {}).get(speaker) else None
        return Step(name, ["pin", r, "--speaker", speaker, "--head", heads[-1]]) if heads else None
    if name == "overlay_add":
        at = round(sum(s["t_out"] - s["t_in"] for s in spans) * 0.4, 2)
        return Step(name, ["overlay", r, "--prompt", "a laptop showing a video timeline", "--at", str(at),
                           "--placement", "full"], shim=True)
    if name == "overlay_set":
        o = (edl.get("overlays") or [None])[0]
        return Step(name, ["overlay", r, "--set", o["id"], "--placement", "upper", "--dur", "3"]) if o else None
    if name == "overlay_remove":
        o = (edl.get("overlays") or [None])[-1]
        return Step(name, ["overlay", r, "--remove", o["id"]]) if o else None
    if name.startswith("restore"):  # restore@K: the chain's K-th version (0 = the base, -2 = the one before last)
        k = int(name.split("@")[1])
        v = chain_versions[k]
        return Step("restore", ["restore", r, "--from", str(v)], restore_of=v)
    raise ValueError(name)


CHAINS = {
    "A": ["music_on", "overlay_add", "split", "reorder", "restore@-2", "music_faders"],
    "B": ["reorder", "split", "overlay_add", "music_on", "caption", "restore@-2", "music_faders"],
    "C": ["style_preset", "text", "style_pin", "cut", "insert", "drop", "edges", "title_on"],
    "D": ["punch_off", "pin", "trim_in", "trim_out", "pauses", "punch_on", "punch_skip", "caption", "pin_none"],
    "E": ["caption", "cut", "insert", "overlay_add", "overlay_set", "style_fixed", "drop", "music_on", "music_off",
          "overlay_remove"],
    "F": ["insert", "pauses", "split", "cut", "drop", "restore@1", "edges", "title_off"],
}
EXPORT_AFTER = {"A", "B"}  # chains that end with music and an overlay on


# ---------------------------------------------------------------- checks

def check_words(prev: dict, new: dict, step: Step, words: list[dict], restored: dict | None) -> list[str]:
    bad = []
    k = kept(new)
    if len(k) != len(set(k)):
        dup = sorted({x for x in k if k.count(x) > 1})
        bad.append(f"words kept twice: {dup[:8]}")
    have, had = set(k), set(kept(prev))
    if restored is not None:
        if have != set(kept(restored)):
            bad.append("restore: the word set differs from the restored version's")
    elif step.name not in STRUCTURAL:
        if have != had:
            bad.append(f"a {step.name} edit changed the words: -{sorted(had - have)[:6]} +{sorted(have - had)[:6]}")
    else:
        if step.gone & have:
            bad.append(f"deleted words still kept: {sorted(step.gone & have)[:8]}")
        if extra := have - had - step.added:
            bad.append(f"words the op did not add: {sorted(extra)[:8]}")
        if step.added - have:
            bad.append(f"inserted words missing: {sorted(step.added - have)[:8]}")
        lost = had - have - step.gone
        if len(lost) > step.slack:
            bad.append(f"words lost the op did not delete: {sorted(lost)[:8]}")
    # audio: no two clips overlap in the source, no clip covers most of a word outside its own range
    iv = sorted((s["t_in"], s["t_out"], s["id"]) for s in new["spans"])
    for (a0, a1, ia), (b0, b1, ib) in zip(iv, iv[1:]):
        if b0 < a1 - 0.005:
            bad.append(f"{ia} and {ib} share {a1 - b0:.3f} s of source audio")
    for s in new["spans"]:
        for w in words:
            if w["turn_id"] == s["turn_id"] and s["word_from"] <= w["i"] <= s["word_to"]:
                continue
            d = w["t_end"] - w["t_start"]
            ov = min(s["t_out"], w["t_end"]) - max(s["t_in"], w["t_start"])
            if d > 0 and ov > 0.5 * d and ov > 0.08:
                bad.append(f"{s['id']} plays {ov:.2f} s of word {w['i']} {w['w']!r} outside its range")
    return bad


def callout_map(edl: dict) -> dict:
    return {s["id"]: (s.get("callout") or "", s.get("callout_xy")) for s in edl["spans"]}


def check_fields(prev: dict, new: dict, step: Step, restored: dict | None, words: list[dict]) -> list[str]:
    bad = []
    if restored is not None:
        for f in WATCHED:
            if restored.get(f) != new.get(f):
                bad.append(f"restore: {f} differs from v{step.restore_of}: {json.dumps(restored.get(f))[:80]} -> "
                           f"{json.dumps(new.get(f))[:80]}")
        return bad
    may = MAY_CHANGE.get(step.name, set())
    for f in WATCHED:
        if f in may:
            continue
        a, b = prev.get(f), new.get(f)
        if f == "look" and step.name not in MAY_CHANGE and a is None and b == {"line_breaks": "greedy"}:
            continue
        if f == "caption_overrides" and step.name in STRUCTURAL:
            # an override stays while the word it sits on stays in a clip (same text); deleting that word takes it
            have = set(kept(new))
            for o in a or []:
                t = float(o["src_t"])
                w = next((w for w in words if w["t_start"] <= t < w["t_end"]), None)
                if w and (w["turn_id"], w["i"]) in have and not any(
                        abs(float(x["src_t"]) - t) < 1e-6 and x.get("text") == o.get("text") for x in b or []):
                    bad.append(f"caption override {o.get('text')!r} @ {t} dropped although its words stay")
            continue
        if f == "punch_skip" and step.name in STRUCTURAL:
            continue  # a jump cut's key can go with its clip
        if a != b:
            bad.append(f"{f} changed: {json.dumps(a)[:100]} -> {json.dumps(b)[:100]}")
    lb = lambda e: (e.get("look") or {}).get("line_breaks") or "greedy"  # noqa: E731 (no look = the greedy lines)
    if lb(prev) != lb(new):
        bad.append(f"look.line_breaks {lb(prev)} -> {lb(new)}")
    pc, nc = callout_map(prev), callout_map(new)
    if step.name in STRUCTURAL:
        lost = [t for sid, (t, _) in pc.items() if t and sid in nc and nc[sid][0] != t]
        if lost:
            bad.append(f"callouts changed on clips that stayed: {lost}")
    elif step.name not in ("text", "style_pin"):
        if pc != nc:
            bad.append(f"callouts or their places changed: {pc} -> {nc}")
    return bad


def check_version(run: Path, n: int, prev: dict, step: Step, words: list[dict], restored: dict | None,
                  segcache: bool) -> dict:
    out = {"version": n, "op": step.name, "argv": step.argv[0:1] + step.argv[2:], "fail": []}
    fail = out["fail"]
    edl_p, cut_p = run / f"edl_v{n}.json", run / f"cut_v{n}.mp4"
    if not edl_p.is_file() or not cut_p.is_file():
        fail.append(f"files: edl {edl_p.is_file()}, cut {cut_p.is_file()}")
        return out
    new = load(run, n)
    dur, want = probe_duration(cut_p), expected_duration(run, new)
    out["duration"] = [round(dur, 3), round(want, 3)]
    # each segment covers its spans in whole frames (in edge down, out edge up), audio cut on the same frames: up to
    # two frames per segment over the spans' exact seconds; the file must match its own frames within one
    segs = (new.get("render") or {}).get("segments") or []
    framed = sum(s["duration"] for s in segs) + expected_duration(run, {"spans": [], "render": new.get("render")})
    if abs(dur - want) > FPS_TOL * 2 * max(1, len(segs)) or (segs and abs(dur - framed) > FPS_TOL):
        fail.append(f"duration {dur:.3f} s vs the EDL's {want:.3f} s ({framed:.3f} s in segment frames)")
    i, tp = loudness(cut_p)
    out["loudness"] = [i, tp]
    if abs(i + 14) > 0.5 or tp > -1.0:
        fail.append(f"loudness {i} LUFS / {tp} dBTP")
    fail += check_words(prev, new, step, words, restored)
    fail += check_fields(prev, new, step, restored, words)
    if segcache:
        tmp = run / f"nocache_v{n}.json"
        shutil.copy2(edl_p, tmp)
        r = sh([PY, "-m", "montivo.cli", "render", str(run), "--edl", str(tmp), "--out", f"nocache_v{n}.mp4"],
               cache=False)
        nc = run / f"nocache_v{n}.mp4"
        if r.returncode or not nc.is_file():
            fail.append(f"segcache: the uncached render failed: {r.stderr[-300:]}")
        elif md5(nc) != md5(cut_p):
            fail.append("segcache: cached and uncached renders differ")
        out["segcache_same"] = nc.is_file() and md5(nc) == md5(cut_p)
        for p in (tmp, nc, run / "render" / f"nocache_v{n}.speech.m4a", run / "render" / f"nocache_v{n}.music.m4a"):
            p.unlink(missing_ok=True)
    return out


def check_exports(run: Path, n: int) -> list[dict]:
    res = []
    for aspect, size in (("9:16", (1080, 1920)), ("4:5", (1080, 1350)), ("1:1", (1080, 1080))):
        d = run / "exports" / aspect.replace(":", "x")
        r = sh([PY, "-m", "montivo.export", str(run), "--version", str(n), "--out", str(d), "--include", "video,clean",
                "--aspect", aspect])
        item = {"aspect": aspect, "fail": []}
        vids = sorted(d.glob("*.mp4")) if d.is_dir() else []
        if r.returncode or len(vids) < 2:
            item["fail"].append(f"export failed ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
        for v in vids:
            if probe_size(v) != size:
                item["fail"].append(f"{v.name} is {probe_size(v)}, not {size}")
            i, tp = loudness(v)
            if abs(i + 14) > 0.5 or tp > -1.0:
                item["fail"].append(f"{v.name} loudness {i} / {tp}")
        item["files"] = [v.name for v in vids]
        res.append(item)
    return res


# ---------------------------------------------------------------- chains

def shim(argv: list[str]) -> int:
    """`combo_check.py --shim ARGS`: montivo.human with a placeholder picture for broll.generate (no Muse Image)."""
    from PIL import Image, ImageDraw
    from montivo import human
    from montivo.engine import broll

    def fake(run_dir, oid, prompt, style, placement, ledger, log=print):
        w, h = map(int, broll.SIZE[placement].split("x"))
        img = Image.new("RGB", (w, h), (20, 20, 20))
        ImageDraw.Draw(img).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(255, 198, 41))
        out = run_dir / "broll"
        out.mkdir(exist_ok=True)
        img.save(out / f"{oid}.png")
        return out / f"{oid}.png"
    broll.generate = fake
    return human.main(argv)


def run_chain(base: Path, from_v: int | None, work: Path, name: str, ops: list[str], segcache: bool) -> dict:
    run = work / name
    if run.exists():
        shutil.rmtree(run)
    subprocess.run(["cp", "-c", "-R", str(base), str(run)], check=True)
    vs = versions(run)
    start = from_v or vs[-1]
    for v in vs:  # only the start version and the ones before it: a chain's versions follow it
        if v > start:
            for p in (run / f"edl_v{v}.json", run / f"cut_v{v}.mp4", run / f"review_v{v}.json"):
                p.unlink(missing_ok=True)
    words = voice_words(run)
    chain_versions = [start]
    rep = {"chain": name, "ops": ops, "steps": [], "exports": []}
    print(f"\n== chain {name} on {run} from v{start}: {' -> '.join(ops)}", flush=True)
    for op in ops:
        prev_n = versions(run)[-1]
        prev = load(run, prev_n)
        step = make_step(op, run, prev, words, chain_versions)
        if step is None:
            rep["steps"].append({"op": op, "skipped": "not applicable here"})
            print(f"  {op}: not applicable", flush=True)
            continue
        cmd = [PY, str(Path(__file__).resolve()), "--shim", *step.argv] if step.shim else \
            [PY, "-m", "montivo.human", *step.argv]
        r = sh(cmd)
        after = versions(run)
        if r.returncode:
            msg = (r.stderr.strip().splitlines() or ["?"])[-1]
            item = {"op": op, "refused": msg[:300], "fail": []}
            if after != versions(run) or after[-1] != prev_n or set(cuts(run)) - set(after):
                item["fail"].append("a refused op left files behind")
            if not msg or "Traceback" in r.stderr:
                item["fail"].append(f"crashed: {r.stderr[-800:]}")
            rep["steps"].append(item)
            print(f"  {op}: refused: {msg[:140]}" + (f"  FAIL {item['fail']}" if item["fail"] else ""), flush=True)
            continue
        n = after[-1]
        if n != prev_n + 1:
            rep["steps"].append({"op": op, "fail": [f"expected v{prev_n + 1}, newest is v{n}"]})
            continue
        restored = load(run, step.restore_of) if step.restore_of is not None else None
        item = check_version(run, n, prev, step, words, restored, segcache)
        chain_versions.append(n)
        rep["steps"].append(item)
        print(f"  v{n} {op}: {'ok' if not item['fail'] else 'FAIL'} dur {item.get('duration')} "
              f"loud {item.get('loudness')}" + "".join(f"\n      - {f}" for f in item["fail"]), flush=True)
    if name in EXPORT_AFTER:
        n = versions(run)[-1]
        e = load(run, n)
        if not (e.get("music") and e.get("overlays")):
            rep["exports"].append({"fail": [f"v{n} has no music or no overlay: the export check needs both"]})
        else:
            rep["exports"] = check_exports(run, n)
        for x in rep["exports"]:
            print(f"  export {x.get('aspect')}: {'ok' if not x['fail'] else 'FAIL ' + str(x['fail'])}", flush=True)
    return rep


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--shim"]:
        return shim(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", type=Path, default=ROOT / "runs" / "016")
    ap.add_argument("--from", dest="from_v", type=int, default=None, help="base version (default: its newest EDL)")
    ap.add_argument("--chains", default=",".join(CHAINS))
    ap.add_argument("--work", type=Path, default=ROOT / "runs" / "_combo")
    ap.add_argument("--no-segcache", action="store_true", help="skip the uncached re-render of every version")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway clones")
    a = ap.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    reports = []
    for name in a.chains.split(","):
        reports.append(run_chain(a.base.resolve(), a.from_v, a.work, name, CHAINS[name], not a.no_segcache))
    fails = [(r["chain"], s.get("version"), s["op"] if "op" in s else "export", f)
             for r in reports for s in [*r["steps"], *r["exports"]] for f in s.get("fail", [])]
    (a.work / "report.json").write_text(json.dumps(reports, indent=2))
    print(f"\n{len(fails)} failure(s)" + "".join(f"\n  {c} v{v} {op}: {f}" for c, v, op, f in fails))
    if not a.keep:
        for name in a.chains.split(","):
            shutil.rmtree(a.work / name, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
