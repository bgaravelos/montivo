#!/usr/bin/env python3
"""Open-weight stage benchmark: which text-and-picture calls can run on Muse Spark Open at Spark 1.3's quality?

Four calls that need no sound, each run on the same inputs with both models:

- titles: "Suggest 3 hooks" for the title card (engine/titles.py `suggest`)
- post:   the publish pack, post text for Reels / TikTok / Shorts / LinkedIn (export.py `post_text`)
- look:   a caption look from words (engine/look.py `suggest`), on fixed requests with checkable expectations
- cover:  the cover agent's pick from the numbered contact sheet: the still, headlines, template
          (engine/thumb.py STILL_PROMPT; moments here are fixed fractions of the cut, so no review copy is needed)

Each case is scored twice. Measured checks, the same for both models: three usable title lines within the limits and
no number or name the cut does not say; post text within each platform's limits before the settle step had to cut it,
hashtag counts in range, no number the cut does not say; a look that meets the request's expectations without
corrections; a cover pick with a valid still, headlines of 2 to 5 words whose highlight is one of their words.
And a blind pairwise judge (`--judge A B`): both models' answers to one case side by side, shown twice in swapped
order, a side wins only when it wins in both orders (as review.pairwise does); the judge runs on Spark 1.3 and again
on Spark Open, so a judge that prefers its own family shows up as a disagreement. The cover judge sees the two
picked stills.

Run folders are APFS clones in runs/openbench/<label>/<base> (git-ignored), so the request files and ledgers never
touch the originals; results go to runs/openbench/results/<label>.json and judge verdicts to judge_<A>_<B>.json.

    .venv/bin/python tools/open_stages_bench.py run --label spark --model spark
    .venv/bin/python tools/open_stages_bench.py run --label open --model open
    .venv/bin/python tools/open_stages_bench.py judge spark open
    .venv/bin/python tools/open_stages_bench.py report spark open            # the table, $0
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from montivo.config import OPEN_MODEL, SPARK_MODEL  # noqa: E402
from montivo.ledger import Ledger  # noqa: E402

RUNS = ROOT / "runs"
OUT = RUNS / "openbench"
RES = OUT / "results"
MODELS = {"spark": SPARK_MODEL, "open": OPEN_MODEL}

# base run -> the version the editor opens on
BASES = {"015": 5, "012": 2, "014": 1, "017": 13, "018": 7, "013": 3, "d1-tedx": 5, "s5-selfie-0111": 1}
LOOK_BASES = ("015", "014")
COVER_FRACTIONS = (0.2, 0.5, 0.8)
STAGES = ("titles", "post", "look", "cover")  # "repair" runs only when asked: --stages repair


def _hex(v) -> tuple[int, int, int] | None:
    v = str(v or "")
    return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)) if re.match(r"^#[0-9A-Fa-f]{6}", v) else None


def bright(v) -> bool:
    c = _hex(v)
    return bool(c) and sum(c) / 3 > 170


def dark(v) -> bool:
    c = _hex(v)
    return bool(c) and sum(c) / 3 < 70


def yellowish(v) -> bool:
    c = _hex(v)
    return bool(c) and c[0] > 180 and c[1] > 150 and c[2] < 120


def reddish(v) -> bool:
    c = _hex(v)
    return bool(c) and c[0] > 170 and c[1] < 110 and c[2] < 110


# (request, expectations on the settled look: name -> predicate); every request also needs a valid look
LOOK_CASES: list[tuple[str, dict]] = [
    ("make the captions bigger", {"size up": lambda lk, cur: lk["size"] > cur["size"]}),
    ("smaller captions, a calmer look", {"size down": lambda lk, cur: lk["size"] < cur["size"]}),
    ("one or two words at a time, punchy, all caps", {"max_words <= 2": lambda lk, cur: lk["max_words"] <= 2,
                                                       "upper": lambda lk, cur: lk["case"] == "upper"}),
    ("put the captions at the top of the frame", {"top <= 500": lambda lk, cur: lk["top"] <= 500}),
    ("white text on a black box behind each line", {"box": lambda lk, cur: dark(lk["box"]),
                                                     "white text": lambda lk, cur: bright(lk["text"])}),
    ("karaoke style, words light up in red as I say them",
     {"karaoke": lambda lk, cur: lk["active"] == "karaoke", "red": lambda lk, cur: reddish(lk["active_bg"])}),
    ("no highlighting of names, keep it plain", {"no highlight": lambda lk, cur: lk["highlight"] == ""}),
    ("an elegant serif look like a documentary", {"serif": lambda lk, cur: lk["font"] == "dm-serif"}),
    ("βάλε κίτρινα γράμματα με μαύρο περίγραμμα", {"yellow text": lambda lk, cur: yellowish(lk["text"]),
                                                  "black stroke": lambda lk, cur: dark(lk["stroke"]) and lk["stroke_w"] > 0}),
    ("κάνε τους υπότιτλους πιο μικρούς και χωρίς περίγραμμα, με σκιά",
     {"size down": lambda lk, cur: lk["size"] < cur["size"], "no stroke": lambda lk, cur: lk["stroke_w"] == 0,
      "shadow": lambda lk, cur: lk["shadow"] is True}),
    ("TikTok style: Montserrat, big, the spoken word on a yellow pill",
     {"montserrat": lambda lk, cur: lk["font"] == "montserrat", "pill": lambda lk, cur: lk["active"] == "pill",
      "yellow pill": lambda lk, cur: yellowish(lk["active_bg"])}),
    ("keep everything but turn off the active word", {"active none": lambda lk, cur: lk["active"] == "none",
                                                       "font kept": lambda lk, cur: lk["font"] == cur["font"]}),
]

STOP = set("""a an the and or but of to in on at for with from by is are was were be been it its this that these those
i i'm im we you your our my me us he she they them his her their what why how when who which not no yes so as if
then than just can do does did done have has had will would should could about into over up down out all any more
most very really""".split())


def clone(label: str, base: str) -> Path:
    dst = OUT / label / base
    if not dst.is_dir():
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-c", "-R", str(RUNS / base), str(dst)], check=True)
    return dst


def ledger_for(label: str, stage: str, case: str) -> Ledger:
    p = OUT / label / "_ledgers" / f"{stage}_{case}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.unlink(missing_ok=True)
    return Ledger(p)


def spent(led: Ledger) -> tuple[float, float]:
    return round(sum(e.cost_usd for e in led.entries), 5), round(sum(e.seconds for e in led.entries), 2)


def cut_text(edl: dict) -> str:
    return " ".join(str(s.get("words") or "") for s in edl.get("spans") or [])


def run_json(run_dir: Path) -> dict:
    p = run_dir / "run.json"
    return json.loads(p.read_text()) if p.is_file() else {}


NUMBER_WORDS = "zero one two three four five six seven eight nine ten eleven twelve".split()


def ungrounded(text: str, source: str) -> list[str]:
    """Numbers and mid-line capitalised names in `text` that `source` never says (case-insensitive; "4" is grounded
    by "four")."""
    src = source.lower()
    src_digits = set(re.findall(r"\d+", src)) | {str(i) for i, w in enumerate(NUMBER_WORDS) if re.search(rf"\b{w}\b", src)}
    out = []
    for i, tok in enumerate(re.findall(r"[A-Za-zΑ-Ωα-ωά-ώ0-9][\w'’.-]*", text)):
        t = tok.strip(".’'")
        if re.fullmatch(r"\d+(\.\d+)?", t):
            if t.split(".")[0] not in src_digits:
                out.append(t)
        elif i > 0 and t[:1].isupper() and len(t) > 2 and t.lower() not in STOP and t.lower() not in src:
            out.append(t)
    return out


# ------------------------------------------------------------------------------------------------------ the stages

def stage_titles(label: str, model: str, base: str, v: int) -> dict:
    from montivo.engine import titles
    rd = clone(label, base)
    edl = json.loads((rd / f"edl_v{v}.json").read_text())
    brief = str(run_json(rd).get("brief") or "")
    led = ledger_for(label, "titles", base)
    err, out = None, []
    try:
        out = titles.suggest(rd, edl, brief, led, log=lambda *_: None, model=model)
    except Exception as e:  # noqa: BLE001 - a failed call is a measured failure
        err = str(e)[:200]
    cost, secs = spent(led)
    src = cut_text(edl) + " " + brief
    checks = {"three": len(out) == 3,
              "limits": all(len(t["text"]) <= titles.MAX_CHARS and len(t["text"].split()) <= 6 for t in out),
              "grounded": all(not ungrounded(t["text"], src) for t in out)}
    return {"case": base, "output": out, "error": err, "cost": cost, "s": secs, "checks": checks,
            "pass": not err and all(checks.values()),
            "ungrounded": sorted({w for t in out for w in ungrounded(t["text"], src)})}


def stage_post(label: str, model: str, base: str, v: int) -> dict:
    from montivo import export
    rd = clone(label, base)
    edl = json.loads((rd / f"edl_v{v}.json").read_text())
    brief = str(run_json(rd).get("brief") or "")
    led = ledger_for(label, "post", base)
    err, rec = None, {}
    try:
        rec = export.post_text(rd, v, led, log=lambda *_: None, model=model)
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]
    cost, secs = spent(led)
    raw = {}
    resp = rd / "spark" / f"post_v{v}_response.json"
    if resp.is_file():
        from montivo.engine.api import chat_as_response, output_text
        r = json.loads(resp.read_text())
        try:
            raw = json.loads(output_text(chat_as_response(r) if "choices" in r else r))
        except (ValueError, KeyError):
            raw = {}
    review = rd / f"review_v{v}.json"
    score = str((json.loads(review.read_text()).get("review") or {}).get("overall", "")) if review.is_file() else ""
    src = " ".join([cut_text(edl), brief, score, str(edl.get("title_card_text") or "")])
    lim = export.LIMITS
    trunc = [k for k in export.PLATFORMS if len(str((raw.get(k) or {}).get("description") or "")) > lim[k]["description"]
             or len(str((raw.get(k) or {}).get("title") or "")) > max(lim[k]["title"], 0 if lim[k]["title"] else 10 ** 6)]
    tags_ok = all(1 <= len((raw.get(k) or {}).get("hashtags") or []) <= lim[k]["tags"] + (0 if k != "shorts" else 0)
                  for k in export.PLATFORMS)
    plats = rec.get("platforms") or {}
    bad = sorted({w for k in plats for w in ungrounded(plats[k]["title"] + " " + plats[k]["description"], src)
                  if w[:1].isdigit()})
    checks = {"four": len(plats) == 4 and all(plats[k]["description"].strip() for k in plats),
              "no_truncation": not trunc, "tag_counts": tags_ok, "numbers_grounded": not bad,
              "shorts_title": bool(plats.get("shorts", {}).get("title"))}
    return {"case": base, "output": plats, "error": err, "cost": cost, "s": secs, "checks": checks,
            "pass": not err and all(checks.values()), "truncated": trunc, "ungrounded_numbers": bad,
            "names_not_in_cut": sorted({w for k in plats for w in ungrounded(plats[k]["description"], src)
                                        if not w[:1].isdigit()})[:12]}


def stage_look(label: str, model: str, base: str, v: int, i: int) -> dict:
    from montivo.engine import look
    from montivo.engine.graphics import normalize_look
    rd = clone(label, base)
    edl = json.loads((rd / f"edl_v{v}.json").read_text())
    req, exp = LOOK_CASES[i]
    cur = normalize_look(edl.get("look")).to_json()
    led = ledger_for(label, "look", f"{base}_{i}")
    err, lk, why, fixed = None, {}, "", []
    try:
        lk, why, fixed = look.suggest(rd, edl, req, led, log=lambda *_: None, model=model)
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]
    cost, secs = spent(led)
    met = {k: bool(lk) and bool(f(lk, cur)) for k, f in exp.items()}
    return {"case": f"{base}#{i}", "request": req, "output": lk, "why": why, "fixed": fixed, "error": err,
            "cost": cost, "s": secs, "checks": {**met, "no_corrections": not fixed},
            "pass": not err and all(met.values()), "clean": not err and all(met.values()) and not fixed}


def stage_cover(label: str, model: str, base: str, v: int) -> dict:
    from montivo.engine import thumb
    from montivo.engine.spark import Spark
    rd = clone(label, base)
    edl = json.loads((rd / f"edl_v{v}.json").read_text())
    r = edl.get("render") or {}
    dur = float(r.get("duration_s") or edl.get("runtime_s") or 0.0)
    skip = float((r.get("title") or {}).get("seconds") or 0.0)
    moments = [{"t_s": round(skip + (dur - skip) * f, 2)} for f in COVER_FRACTIONS]
    work = rd / "cover" / "bench"
    work.mkdir(parents=True, exist_ok=True)
    cands = thumb.candidates(rd, edl, moments, skip + 0.05, max(skip, dur - 0.3))
    sheet = work / "candidates.jpg"
    if not sheet.is_file():
        thumb.contact_sheet(rd, edl, cands, sheet)
    brief = str(run_json(rd).get("brief") or "")[:600]
    led = ledger_for(label, "cover", base)
    err, p = None, {}
    try:
        call = Spark(rd, led, model=model, log=lambda *_: None).call(
            f"cover_design_v{v}", thumb.STILL_PROMPT.format(k=3, transcript=thumb.transcript_of(edl), brief=brief or "(none)"),
            thumb.STILL_SCHEMA, images=[sheet], max_output_tokens=4000, reasoning_effort="low",
            stage="spark.cover_design")
        p = call.parsed
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]
    cost, secs = spent(led)
    heads = [h for h in p.get("headlines") or [] if str(h.get("text") or "").strip()]
    by_n = {c["n"]: c for c in cands}
    checks = {"still_valid": int(p.get("still") or 0) in by_n, "three": len(heads) == 3,
              "words_2_5": all(2 <= len(thumb.words_of(h["text"])) <= 5 for h in heads),
              "highlight_in": all(thumb.hi_index(thumb.words_of(h["text"]), h["highlight"]) is not None for h in heads)}
    return {"case": base, "output": {**p, "t_s": (by_n.get(int(p.get("still") or 0)) or {}).get("t_s")},
            "sheet": str(sheet), "error": err, "cost": cost, "s": secs, "checks": checks,
            "pass": not err and all(checks.values())}


REPAIR_RUNS = ("017", "018", "fc-012", "fc-d1-nasa", "fc-heli-01", "s6-twocam-0111")
REPAIR_REPS = 2


def stage_repair(label: str, model: str, base: str, rep: int) -> dict:
    """The plan check's repair call (run.py, A4) replayed: the exact repair prompt a run sent, on `model`, built into
    an EDL on today's code and checked by edl.plan_check; fewer problems left than the first plan had = repaired."""
    from montivo.engine import schemas
    from montivo.engine.edges import Audio
    from montivo.engine.edl import build_edl, plan_check
    from montivo.engine.spark import Spark
    from montivo.engine.voice import VoiceResult
    rd = clone(label, base)
    run = run_json(rd)
    v1 = json.loads((rd / "edl_v1.json").read_text())
    req = json.loads((RUNS / base / "spark" / "plan_repair_request.json").read_text())
    prompt = req["input"][0]["content"][0]["text"] if "input" in req else req["messages"][0]["content"]
    target = float(run.get("target_s") or 40.0)
    first = (v1.get("plan_check") or {}).get("first") or []
    led = ledger_for(label, "repair", f"{base}_{rep}")
    err, left, edl = None, None, {}
    try:
        call = Spark(rd, led, model=model, log=lambda *_: None).call(
            f"plan_repair_bench{rep}", prompt, schemas.PLAN, max_output_tokens=16000, reasoning_effort="low",
            stage="spark.plan", schema_name="plan")
        voice = VoiceResult.from_json(json.loads((rd / "voice" / "voice.json").read_text()))
        watch = json.loads((rd / "watch.json").read_text())
        edl = build_edl(call.parsed, voice, watch, v1["source"], run.get("brief") or "", v1.get("tracks") or {},
                        reframe_concept=v1.get("reframe_concept") or "person", audio=Audio.load(rd), target_s=target)
        left = plan_check(edl, target)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:200]}"
    cost, secs = spent(led)
    checks = {"placed": bool(edl.get("spans")), "repaired": left is not None and len(left) < len(first),
              "clean": left == []}
    return {"case": f"{base}#{rep}", "first": first, "left": left, "spans": len(edl.get("spans") or []),
            "runtime_s": edl.get("runtime_s"), "error": err, "cost": cost, "s": secs, "checks": checks,
            "pass": not err and checks["repaired"]}


def jobs(stages: list[str]) -> list[tuple]:
    out = []
    for st in stages:
        if st == "repair":
            out += [(st, b, r) for b in REPAIR_RUNS for r in range(REPAIR_REPS)]
        elif st == "look":
            out += [(st, b, BASES[b], i) for b in LOOK_BASES for i in range(len(LOOK_CASES))]
        else:
            out += [(st, b, v) for b, v in BASES.items()]
    return out


def cmd_run(a) -> None:
    model = MODELS[a.model]
    picked = [j for j in jobs(a.stages) if not a.bases or j[1] in a.bases]
    for b in {j[1] for j in picked}:
        clone(a.label, b)
    fns = {"titles": stage_titles, "post": stage_post, "look": stage_look, "cover": stage_cover,
           "repair": stage_repair}

    def one(j):
        st, *args = j
        t = time.time()
        r = fns[st](a.label, model, *args)
        print(f"{'PASS' if r['pass'] else 'FAIL'} {st:<6} {r['case']:<16} ${r['cost']:.4f} {r['s']:>5}s "
              f"{' '.join(k for k, ok in r['checks'].items() if not ok)} {r.get('error') or ''}", flush=True)
        return {"stage": st, "wall": round(time.time() - t, 2), **r}

    # one worker per base: calls in one run folder reuse request file names
    by_base: dict[str, list] = {}
    for j in picked:
        by_base.setdefault(j[1], []).append(j)
    with ThreadPoolExecutor(min(8, len(by_base))) as ex:
        rows = [r for rs in ex.map(lambda js: [one(j) for j in js], by_base.values()) for r in rs]
    RES.mkdir(parents=True, exist_ok=True)
    path = RES / f"{a.label}.json"
    old = json.loads(path.read_text())["rows"] if path.is_file() else []
    keep = [r for r in old if (r["stage"], r["case"]) not in {(x["stage"], x["case"]) for x in rows}]
    path.write_text(json.dumps({"label": a.label, "model": model, "rows": keep + rows}, ensure_ascii=False, indent=1))
    print(f"\n{a.label} ({model}): {sum(r['pass'] for r in rows)}/{len(rows)} pass, "
          f"${sum(r['cost'] for r in rows):.4f}")


# ------------------------------------------------------------------------------------------------------ the judge

JUDGE_PROMPT = """You are a senior social-media editor. Two assistants answered the same task for the same short
vertical video. Pick the answer you would actually publish. Judge: does it say only what the video says (no invented
claims, numbers or names), would it make a viewer stop and watch, does it follow the task's rules, is it well written.
The order the answers come in says nothing about which is better. tie only when you truly cannot choose.

The task: {task}
The video's words, in order: {words}
The creator's brief: {brief}

Answer A:
{a}

Answer B:
{b}"""

JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["winner", "invented_a", "invented_b", "why"],
                "properties": {"winner": {"type": "string", "enum": ["A", "B", "tie"]},
                               "invented_a": {"type": "boolean"}, "invented_b": {"type": "boolean"},
                               "why": {"type": "string"}}}

STILL_JUDGE = """Image 1 and image 2 are two candidate cover stills from the same vertical talking-head video (the
image people see in the profile grid). Which one is the better cover: eyes open and towards the camera, a good
expression (not caught mid-word), sharp? The order says nothing. tie only when you truly cannot choose."""
STILL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["winner", "why"],
                "properties": {"winner": {"type": "string", "enum": ["1", "2", "tie"]}, "why": {"type": "string"}}}

TASKS = {"titles": "three title-card lines (max 6 words each) for the first second of the video",
         "post": "the post text for Instagram Reels, TikTok, YouTube Shorts and LinkedIn that goes with the video",
         "cover": "three cover headlines of 2 to 5 words (a promise or curiosity hook, not a caption line), one word "
                  "highlighted each"}


def show(stage: str, out) -> str:
    if stage == "titles":
        return "\n".join(f"- {t['text']}" for t in out or [])
    if stage == "post":
        return "\n\n".join(f"[{k}] {p.get('text', '')}" for k, p in (out or {}).items())
    if stage == "cover":
        return "\n".join(f"- {h['text']} (highlight: {h['highlight']})" for h in (out or {}).get("headlines") or [])
    return json.dumps(out)


def cmd_judge(a) -> None:
    from montivo.engine.spark import Spark
    ra = {(r["stage"], r["case"]): r for r in json.loads((RES / f"{a.a}.json").read_text())["rows"]}
    rb = {(r["stage"], r["case"]): r for r in json.loads((RES / f"{a.b}.json").read_text())["rows"]}
    keys = [k for k in ra if k in rb and k[0] in ("titles", "post", "cover") and not ra[k]["error"] and not rb[k]["error"]
            and (not a.stages or k[0] in a.stages)]
    jdir = OUT / "_judge"
    jdir.mkdir(parents=True, exist_ok=True)
    path = RES / f"judge_{a.a}_{a.b}.json"
    done = json.loads(path.read_text())["rows"] if path.is_file() else []
    have = {(d["stage"], d["case"], d["judge"], d.get("kind", "text")) for d in done}

    def ask(judge: str, prompt: str, schema: dict, name: str, images=None) -> tuple[dict, float]:
        led = Ledger(jdir / f"{name}.ledger.json")
        c = Spark(jdir, led, model=MODELS[judge], log=lambda *_: None).call(
            name, prompt, schema, images=images, max_output_tokens=3000, reasoning_effort="low", stage="bench.judge")
        return c.parsed, c.cost_usd

    def one(job):
        k, judge, kind = job
        st, case = k
        base = case.split("#")[0]
        rd = RUNS / base
        edl = json.loads((rd / f"edl_v{BASES[base]}.json").read_text())
        words, brief = cut_text(edl)[:3000], str(run_json(rd).get("brief") or "")[:600]
        cost, votes, notes = 0.0, [], []
        for order in (0, 1):
            x, y = (ra[k], rb[k]) if order == 0 else (rb[k], ra[k])
            tag = f"{st}_{case.replace('#', '_')}_{judge}_{kind}_{order}"
            if kind == "still":
                sx, sy = x["output"].get("t_s"), y["output"].get("t_s")
                if sx is None or sy is None or abs(sx - sy) < 0.05:
                    return {"stage": st, "case": case, "judge": judge, "kind": kind, "winner": "tie",
                            "note": "same still", "cost": 0.0}
                from montivo.engine.look import frame_at
                ims = []
                for n, t in enumerate((sx, sy)):
                    p = jdir / f"{tag}_{n}.jpg"
                    frame_at(RUNS / base, edl, t).convert("RGB").resize((540, 960)).save(p, quality=88)
                    ims.append(p)
                p, c = ask(judge, STILL_JUDGE, STILL_SCHEMA, tag, images=ims)
                w = {"1": "A", "2": "B"}.get(p["winner"], "tie")
            else:
                p, c = ask(judge, JUDGE_PROMPT.format(task=TASKS[st], words=words, brief=brief, a=show(st, x["output"]),
                                                      b=show(st, y["output"])), JUDGE_SCHEMA, tag)
                w = p["winner"]
                notes.append({"invented_first": p["invented_a"], "invented_second": p["invented_b"]})
            cost += c
            # back to the run labels: A = a.a, B = a.b
            votes.append(w if order == 0 or w == "tie" else {"A": "B", "B": "A"}[w])
        win = votes[0] if votes[0] == votes[1] else "tie"
        inv = {}
        if notes:
            inv = {a.a: [notes[0]["invented_first"], notes[1]["invented_second"]],
                   a.b: [notes[0]["invented_second"], notes[1]["invented_first"]]}
        return {"stage": st, "case": case, "judge": judge, "kind": kind, "votes": votes,
                "winner": {"A": a.a, "B": a.b}.get(win, "tie"), "invented": inv, "cost": round(cost, 5)}

    todo = [(k, j, kind) for k in keys for j in a.judges for kind in (("text", "still") if k[0] == "cover" else ("text",))
            if (k[0], k[1], j, kind) not in have]
    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(one, todo))
    for r in rows:
        print(f"{r['stage']:<6} {r['case']:<16} {r['judge']:<5} {r['kind']:<5} -> {r['winner']} {r.get('votes', '')}")
    path.write_text(json.dumps({"a": a.a, "b": a.b, "rows": done + rows}, ensure_ascii=False, indent=1))
    print(f"judge cost ${sum(r['cost'] for r in rows):.4f}")


# ------------------------------------------------------------------------------------------------------ the report

def cmd_rescore(a) -> None:
    """The grounding checks again on stored answers ($0), after a change to `ungrounded`."""
    for lab in a.labels:
        path = RES / f"{lab}.json"
        d = json.loads(path.read_text())
        for r in d["rows"]:
            if r["stage"] not in ("titles", "post") or r["error"]:
                continue
            base = r["case"]
            rd = RUNS / base
            edl = json.loads((rd / f"edl_v{BASES[base]}.json").read_text())
            brief = str(run_json(rd).get("brief") or "")
            if r["stage"] == "titles":
                src = cut_text(edl) + " " + brief
                bad = sorted({w for t in r["output"] for w in ungrounded(t["text"], src)})
                r["ungrounded"], r["checks"]["grounded"] = bad, not bad
            else:
                rv = rd / f"review_v{BASES[base]}.json"
                score = str((json.loads(rv.read_text()).get("review") or {}).get("overall", "")) if rv.is_file() else ""
                src = " ".join([cut_text(edl), brief, score, str(edl.get("title_card_text") or "")])
                p = r["output"]
                bad = sorted({w for k in p for w in ungrounded(p[k]["title"] + " " + p[k]["description"], src)
                              if w[:1].isdigit()})
                r["ungrounded_numbers"], r["checks"]["numbers_grounded"] = bad, not bad
            r["pass"] = all(r["checks"].values())
        path.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        print(lab, {st: f"{sum(r['pass'] for r in d['rows'] if r['stage'] == st)}/{sum(r['stage'] == st for r in d['rows'])}"
                    for st in (*STAGES, "repair")})


def cmd_report(a) -> None:
    import statistics as stx
    data = {lab: json.loads((RES / f"{lab}.json").read_text())["rows"] for lab in (a.a, a.b)}
    jp = RES / f"judge_{a.a}_{a.b}.json"
    judge = json.loads(jp.read_text())["rows"] if jp.is_file() else []
    for st in (*STAGES, "repair"):
        print(f"\n## {st}")
        for lab, rows in data.items():
            rs = [r for r in rows if r["stage"] == st]
            if not rs:
                continue
            secs = [r["s"] for r in rs if not r["error"]]
            extra = f", clean {sum(r.get('clean', False) for r in rs)}" if st == "look" else ""
            print(f"{lab:<6} checks {sum(r['pass'] for r in rs)}/{len(rs)}{extra}, errors {sum(bool(r['error']) for r in rs)}, "
                  f"${sum(r['cost'] for r in rs):.4f} total, ${sum(r['cost'] for r in rs) / len(rs):.4f}/call, "
                  f"median {stx.median(secs) if secs else 0:.1f}s")
        for j in ("spark", "open"):
            for kind in ("text", "still"):
                js = [r for r in judge if r["stage"] == st and r["judge"] == j and r["kind"] == kind]
                if js:
                    cnt = {lab: sum(r["winner"] == lab for r in js) for lab in (a.a, a.b)}
                    inv = {lab: sum(sum(bool(x) for x in (r.get("invented") or {}).get(lab, [])) for r in js)
                           for lab in (a.a, a.b)}
                    print(f"judge {j} ({kind}): {cnt[a.a]} {a.a} / {cnt[a.b]} {a.b} / {len(js) - sum(cnt.values())} tie"
                          + (f"; invented flags {inv}" if kind == "text" else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--model", choices=list(MODELS), required=True)
    r.add_argument("--stages", nargs="*", default=list(STAGES), help=f"{', '.join(STAGES)}, repair")
    r.add_argument("--bases", nargs="*")
    j = sub.add_parser("judge")
    j.add_argument("a")
    j.add_argument("b")
    j.add_argument("--judges", nargs="*", default=["spark", "open"])
    j.add_argument("--stages", nargs="*")
    q = sub.add_parser("rescore")
    q.add_argument("labels", nargs="+")
    p = sub.add_parser("report")
    p.add_argument("a")
    p.add_argument("b")
    a = ap.parse_args()
    {"run": cmd_run, "judge": cmd_judge, "rescore": cmd_rescore, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
