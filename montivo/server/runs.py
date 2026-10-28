"""Read side of the UI: a run folder as one JSON state, and run.log as stage-tagged lines.

Everything here reads files the engine already writes (run.json, summary.json, ledger.json, edl_vN.json,
review_vN.json, review/loop.json, voice.json, watch.json, plan.json, run.log); nothing imports the engine.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import SPARK_MODEL
from ..engine.graphics import normalize_look

LINE = re.compile(r"^\[\s*([\d.]+)s\] ?(.*)$")
EDL = re.compile(r"edl_v(\d+)\.json")

STAGES = [  # id, label, model shown on the rail, ledger stage prefixes
    ("ingest", "Ingest", "ffmpeg", ("ingest",)),
    ("voice", "Voice", "Muse Voice", ("voice",)),
    ("track", "Track", "SAM 3.1", ("track",)),
    ("watch", "Watch", "Muse Spark 1.3", ("spark.watch",)),
    ("plan", "Plan", "Muse Spark 1.3", ("spark.plan",)),
    ("edges", "Edges", "source audio", ("edges",)),
    ("render", "Render", "ffmpeg + Muse Image", ("render", "image")),
    ("review", "Review", "Muse Spark 1.3 · blind", ("spark.review", "review")),
]


def read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- run.log

def stage_of(msg: str, prev: str | None) -> str | None:
    """The rail stage a log line belongs to; indented lines continue the stage of the line before."""
    if msg.startswith(" "):
        return prev
    m = msg.lower()
    if m.startswith(("montivo ", "done:", "paper edit", "human:", "resume:")):
        return "meta"
    if m.startswith(("spark: watch", "watch")):
        return "watch"
    if m.startswith(("spark: plan", "plan")):
        return "plan"
    if m.startswith(("spark: review", "review", "patch v")):
        return "review"
    if m.startswith(("image:", "title:")):
        return "render"
    head = m.split(":", 1)[0]
    if head in ("ingest", "voice", "track", "edges", "render"):
        return head
    if m.startswith("error"):
        return "error"
    return prev


def parse_log(text: str) -> list[dict]:
    """run.log -> [{i, t, msg, stage, session}]. Every engine command starts a new RunLog clock, so a session starts
    where the clock goes back or a `montivo <cmd>:` header appears. Lines without a time stamp continue the previous
    message (multi-line facts and tracebacks)."""
    out: list[dict] = []
    session, last_t, stage = -1, None, None
    for raw in text.splitlines():
        m = LINE.match(raw)
        if not m:
            if out:
                out[-1]["msg"] += "\n" + raw
            continue
        t, msg = float(m.group(1)), m.group(2)
        if last_t is None or t < last_t or msg.startswith("montivo "):
            session += 1
            stage = None
        stage = stage_of(msg, stage)
        out.append({"i": len(out), "t": t, "msg": msg, "stage": stage, "session": session})
        last_t = t
    return out


def replay_script(lines: list[dict]) -> list[dict]:
    """The story of a finished run for a demo recording: its first `montivo run`, the last real edge pass, then the
    last review loop (each run folder holds many sessions from iterating on the engine)."""
    sessions: dict[int, list[dict]] = {}
    for ln in lines:
        sessions.setdefault(ln["session"], []).append(ln)
    order = sorted(sessions)
    first = next((s for s in order if sessions[s][0]["msg"].startswith("montivo run")), order[0] if order else None)
    if first is None:
        return []
    picked = [first]
    k = order.index(first)
    while k + 1 < len(order) and sessions[order[k + 1]][0]["t"] >= sessions[order[k]][-1]["t"]:
        k += 1  # the run's own review loop renders under `montivo render:` headers on the same clock: same process
        picked.append(order[k])
    first = order[k]
    edges = [s for s in order if s > first and any("edges moved" in l["msg"] and "dry run" not in l["msg"] for l in sessions[s])]
    if edges:
        picked.append(edges[-1])
    loops = [s for s in order if s > first and any(l["msg"].startswith("review loop:") for l in sessions[s])]
    if loops and loops[-1] not in picked and not any(l["msg"].startswith("review loop:") for s in picked for l in sessions[s]):
        # the loop's own renders log a `montivo render:` header, which splits it into sessions: take the ones before
        # its end back to where it started (a render or a review that no earlier loop finished)
        k = order.index(loops[-1])
        start = k
        while start > 0 and order[start - 1] > max(picked):
            prev = sessions[order[start - 1]]
            if any(l["msg"].startswith("review loop:") for l in prev):
                break
            if not (prev[0]["msg"].startswith("montivo render") or any(l["msg"].startswith("review: ") for l in prev)):
                break
            start -= 1
        picked.extend(order[start:k + 1])
    return [ln for s in picked for ln in sessions[s]]


# --------------------------------------------------------------------------- live progress

# the render's share of its own time, measured on run 015 (cut_v1, 1161 frames, 20.3 s): prep (paths, captions, title
# clip) 1.0 s, the segments 15.4 s, audio + mux 3.9 s
RENDER_PREP, RENDER_SEGS = 0.05, 0.75
COMMANDS = {"run": "Run", "render": "Render", "drop": "Delete clip", "trim": "Trim clip", "caption": "Edit captions",
            "punch": "Punch-ins", "replan": "Ask Montivo", "edges": "Edges", "text": "Edit text", "reorder": "Move clip",
            "pin": "Pin speaker", "cut": "Delete words", "style": "Style"}
TO_VERSION = re.compile(r"-> \S*?(?:edl|cut)_v(\d+)\b")
SEGMENTS = re.compile(r"^render: \d+ spans -> (\d+) segments(?: \((\d+) frames\))?")
SEG_DONE = re.compile(r"^render: seg (\d+) .*?\((\d+) frames")
CUT_READY = re.compile(r"^render: \S*cut_v(\d+)\.mp4 \(")
REVIEW_PREP = re.compile(r"^review: review_v(\d+): facts")
REVIEW_CALL = re.compile(r"^(?:review: review_v(\d+) facts:|spark: review_v(\d+) ->)")
REVIEW_DONE = re.compile(r"^review: review_v(\d+) overall (\d+) pass (\w+)")
HOOK_B = re.compile(r"^hook a/b: v\d+ opens on \S+ .*; v(\d+) opens on (\S+)")
HOOK_DONE = re.compile(r"^hook a/b: .* vs .*: keep v(\d+)")


def progress(lines: list[dict], label: str | None = None, ledger: dict | None = None,
             costs: dict | None = None) -> dict | None:
    """Where a job is, read from its run.log lines (one engine process: the lines after the last clock reset).

    Returns the rail stage, a title ("Render v2") and detail ("segment 2 of 3"), the bar's bounds from the checkpoints
    logged so far (`floor`, `ceil`) and the stage's expected seconds (this run's ledger, else the server's hints); the UI
    moves the bar by time between the two bounds, so it never runs past a checkpoint the log has not reached. A stage
    with no checkpoint and no expected time (Voice, SAM, Watch on a new run) has no estimate: the bar stays indeterminate."""
    start = 0
    for k in range(1, len(lines)):
        if lines[k]["t"] < lines[k - 1]["t"]:
            start = k
    lines = lines[start:]
    if not lines:
        return None
    costs = costs or {}
    entries = (ledger or {}).get("entries") or []

    def expect(stage: str, hint: str, model: str | None = None) -> tuple[float | None, str | None]:
        """The mean of this run's earlier calls of the stage (of that model), else the server's hint."""
        secs = [e["seconds"] for e in entries if e.get("stage") == stage and (model is None or e.get("model") == model)
                and e.get("seconds")]
        if secs:
            return round(sum(secs) / len(secs), 1), "ledger"
        return (costs[hint], "hint") if costs.get(hint) else (None, None)

    first = lines[0]["msg"]
    cmd = re.match(r"montivo (\w+)", first)
    kind = cmd.group(1) if cmd else ("hook" if first.startswith("hook a/b") else "edges" if first.startswith("edges")
                                     else "review" if first.startswith(("review", "spark: review")) else None)
    label = label or COMMANDS.get(kind or "", {"hook": "Hook A/B", "review": "Review loop"}.get(kind or "", "Montivo"))
    version = 1 if kind == "run" else None
    cur: dict | None = None
    steps: list[dict] = []
    seg = {"n": 0, "frames": 0, "k": 0, "done_frames": 0}
    analyse = {"voice": False, "watch": None, "sam": []}
    reviews, finished, error = 0, False, False

    def begin(stage: str, title: str, t: float, **kw) -> dict:
        nonlocal cur, finished
        if cur and (cur["stage"], cur["title"]) == (stage, title):
            cur.update(kw)
            return cur
        finished = False  # a step after the review loop (the run's hook A/B): the job goes on
        if cur:
            steps.append({"stage": cur["stage"], "title": cur["title"], "seconds": round(cur.get("t1", t) - cur["t0"], 1)})
        cur = {"stage": stage, "title": title, "t0": t, "detail": None, "floor": 0.0, "ceil": 0.95,
               "expected_s": None, "expected_from": None, **kw}
        return cur

    def done(t: float, detail: str | None = None) -> None:
        if cur:
            cur.update(floor=1.0, ceil=1.0, t1=t, **({"detail": detail} if detail else {}))

    for ln in lines:
        msg, t = ln["msg"], ln["t"]
        if msg.startswith(" "):
            continue
        if msg.startswith("montivo "):
            m = TO_VERSION.search(msg)
            if m:
                version = int(m.group(1))
            if msg.startswith("montivo run:"):
                begin("ingest", "Ingest", t, detail="proxy, audio and upload")
            continue
        if msg.startswith(("ERROR", "error")):
            error = True
            begin(cur["stage"] if cur else "error", cur["title"] if cur else "Error", t, detail=msg.split("\n")[0][:120])
            continue
        if msg.startswith("ingest:"):
            begin("ingest", "Ingest", t, detail="proxy, audio and upload")
            if "uploaded proxy" in msg:
                done(t)
            continue
        if msg.startswith(("voice:", "track:", "spark: watch", "watch")):
            if msg.startswith("voice:") and re.match(r"voice: \d+ turns", msg):
                analyse["voice"] = True
            if msg.startswith("spark: watch"):
                analyse["watch"] = "completed" in msg or "->" not in msg
            m = re.match(r"track: ([^:]+): \d+ ids", msg)
            if m:
                analyse["sam"].append(m.group(1))
            parts = ["Voice ✓" if analyse["voice"] else "Voice",
                     f"SAM {', '.join(analyse['sam'])} ✓" if analyse["sam"] else "SAM",
                     "Watch ✓" if analyse["watch"] else "Watch"]
            n_done = sum(p.endswith("✓") for p in parts)
            begin("voice", "Voice · SAM · Watch", t, detail=" · ".join(parts) if n_done else "running side by side",
                  floor=n_done / 3, ceil=max(n_done / 3, 0.95))
            continue
        if msg.startswith("speakers:"):
            begin("track", "Speaker map", t, detail=msg[len("speakers: "):][:80])
            continue
        if msg.startswith("plan: speech rate") or msg.startswith("spark: plan ->"):
            model = msg.split("-> ", 1)[1].split(" ")[0] if "->" in msg else SPARK_MODEL
            is_open = model.startswith("meta-models/")
            exp, src = expect("spark.plan", "plan_open_s" if is_open else "plan_s", model)
            begin("plan", "Plan", t, detail=planner_name(model), expected_s=exp, expected_from=src)
            continue
        if msg.startswith("spark: plan:"):
            done(t, f"{(cur or {}).get('detail') or 'planned'}, {msg.rsplit(', ', 1)[-1]}")
            continue
        if msg.startswith("edges:"):
            begin("edges", "Edges", t, detail=msg[len("edges: "):].split(" (")[0][:80], floor=1.0, ceil=1.0)
            continue
        if msg.startswith("patch v"):
            m = re.match(r"patch v\d+ -> v(\d+): (\S+)", msg)
            if m:
                version = int(m.group(1))
                c = cur if cur and cur["title"] == f"Patch v{version}" else None
                n = (c or {}).get("fixes", 0) + 1
                begin("edit", f"Patch v{version}", t, fixes=n, floor=1.0, ceil=1.0,
                      detail=m.group(2) if n == 1 else f"{n} fixes")
            continue
        if msg.startswith("image: title card"):
            if "saved" in msg:
                done(t)
            else:
                exp, src = expect("image.title", "image_s")
                begin("render", "Title card", t, detail="Muse Image", expected_s=exp, expected_from=src)
            continue
        if msg.startswith(("review: rendering", "hook a/b: rendering")):
            m = TO_VERSION.search(msg)
            if m:
                version = int(m.group(1))
            continue
        m = SEGMENTS.match(msg)
        if m:
            seg = {"n": int(m.group(1)), "frames": int(m.group(2) or 0), "k": 0, "done_frames": 0}
            exp, src = expect("render", "")
            if exp is None and seg["frames"] and costs.get("render_s_per_frame"):
                exp, src = round(seg["frames"] * costs["render_s_per_frame"], 1), "hint"
            begin("render", f"Render v{version}" if version else "Render", t, expected_s=exp, expected_from=src,
                  detail=f"segment 1 of {seg['n']}", floor=0.0,
                  ceil=RENDER_PREP + RENDER_SEGS * (1 if seg["frames"] else 1 / seg["n"]))
            continue
        m = SEG_DONE.match(msg)
        if m and cur and cur["stage"] == "render" and seg["n"]:
            seg["k"], seg["done_frames"] = int(m.group(1)), seg["done_frames"] + int(m.group(2))
            k, n = seg["k"], seg["n"]
            share = seg["done_frames"] / seg["frames"] if seg["frames"] else k / n
            nxt = 1.0 if seg["frames"] or k >= n else (k + 1) / n
            cur.update(floor=RENDER_PREP + RENDER_SEGS * min(share, 1.0),
                       ceil=0.98 if k >= n else RENDER_PREP + RENDER_SEGS * nxt,
                       detail=f"segment {k + 1} of {n}" if k < n else "audio and mux")
            continue
        m = CUT_READY.match(msg)
        if m:
            version = int(m.group(1))
            if cur and cur["stage"] == "render":
                done(t, f"cut_v{version}.mp4 ready")
            continue
        m = REVIEW_PREP.match(msg)
        if m:
            version, reviews = int(m.group(1)), reviews + 1
            prep, src = expect("review.prep", "review_prep_s")
            call, src2 = expect("spark.review", "review_s")
            exp = (prep or 0) + (call or 0) or None
            share = round(prep / exp, 3) if prep and exp else 0.3
            begin("review", f"Review v{version}", t, detail="facts, review copy and upload", expected_s=exp,
                  expected_from=src2 or src, ceil=share, prep_share=share)
            continue
        m = REVIEW_CALL.match(msg)
        if m:
            version = int(m.group(1) or m.group(2))
            if not (cur and cur["title"] == f"Review v{version}"):  # a log from before the prep line
                reviews += 1
                call, src = expect("spark.review", "review_s")
                begin("review", f"Review v{version}", t, expected_s=call, expected_from=src, ceil=0.97)
            else:
                cur.update(floor=cur.get("prep_share", 0.0), ceil=0.97)
            cur["detail"] = "Muse Spark 1.3, blind" + (f" · pass {reviews}" if reviews > 1 else "")
            continue
        m = REVIEW_DONE.match(msg)
        if m:
            done(t, f"scored {m.group(2)}{', pass' if m.group(3) == 'True' else ''}")
            continue
        m = HOOK_B.match(msg)
        if m:
            version = int(m.group(1))
            begin("review", "Hook A/B", t, detail=f"v{version} opens on {m.group(2)}", floor=1.0, ceil=1.0)
            continue
        m = HOOK_DONE.match(msg)
        if m:
            begin("review", "Hook A/B", t, detail=f"keeps v{m.group(1)}", floor=1.0, ceil=1.0)
            finished = True
            continue
        if msg.startswith("hook a/b:") and ("no challenger" in msg or "budget" in msg):
            begin("review", "Hook A/B", t, detail=msg.split(": ", 2)[-1][:80], floor=1.0, ceil=1.0)
            finished = True
            continue
        if msg.startswith(("review loop:", "done:", "human: v")):
            finished = True
            continue

    t_last = lines[-1]["t"]
    out = {"label": label, "kind": kind, "version": version, "finished": finished, "error": error,
           "stage": None, "title": "Starting", "detail": None, "floor": 0.0, "ceil": 0.95, "expected_s": None,
           "expected_from": None, "stage_elapsed": 0.0, "job_elapsed": round(t_last - lines[0]["t"], 1),
           "t": t_last, "steps": steps[-12:]}
    if cur:
        out.update({k: cur[k] for k in ("stage", "title", "detail", "floor", "ceil", "expected_s", "expected_from")},
                   stage_elapsed=round(t_last - cur["t0"], 1))
    if finished:
        out.update(floor=1.0, ceil=1.0)
    return out


# --------------------------------------------------------------------------- run state

_CACHE: dict[str, tuple[tuple[int, int], dict | None]] = {}


def _cached(p: Path, pick) -> dict | None:
    """pick(json) of a file, kept until the file changes: Home re-reads the list every 2 s while a job runs."""
    try:
        st = p.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(str(p))
    if hit and hit[0] == key:
        return hit[1]
    data = read_json(p)
    val = pick(data) if data is not None else None
    _CACHE[str(p)] = (key, val)
    return val


def _edl_card(edl: dict) -> dict:
    render = edl.get("render") or {}
    last = (edl.get("patches") or [{}])[-1]
    title = render.get("title") or {}
    return {"title_card_text": edl.get("title_card_text"), "source": last.get("source") or ("review" if edl.get("patches") else "plan"),
            "kept": (last.get("result") or {}).get("kept"), "duration_s": render.get("duration_s") or edl.get("runtime_s"),
            "title_s": float(title.get("seconds") or 0), "output": Path(render.get("output") or "").name}


def _review_card(rv: dict) -> dict:
    r = rv.get("review") or {}
    return {"cut": Path(rv.get("cut") or "").name, "overall": r.get("overall"), "pass": bool(r.get("pass"))}


def project_card(d: Path, versions_n: list[int], loop: dict) -> dict | None:
    """What a project's card on Home shows: the version the editor opens on (the newest a human edit made or the hook
    A/B kept, else the loop's best, else the newest; Editor.tsx `opening`), its name, a poster time a third into the
    cut after the title card, its blind score and every reviewed version's score in order (the sparkline)."""
    heads = {n: h for n in versions_n if (h := _cached(d / f"edl_v{n}.json", _edl_card))}
    cuts = [n for n in sorted(heads) if (d / f"cut_v{n}.mp4").is_file()]
    if not cuts:
        return None
    kept = [n if heads[n]["source"] == "human" else heads[n]["kept"] for n in cuts if n > 1
            and (heads[n]["source"] == "human" or (heads[n]["source"] == "hook_ab" and heads[n]["kept"]))]
    kept = [k for k in kept if k in cuts]
    best = (loop.get("best") or {}).get("version")
    shown = max(kept) if kept else best if best in cuts else cuts[-1]
    scores = []
    for n in cuts:
        rv = _cached(d / f"review_v{n}.json", _review_card)
        if rv and rv["overall"] is not None and rv["cut"] in ("", f"cut_v{n}.mp4"):
            scores.append({"v": n, "overall": rv["overall"], "pass": rv["pass"]})
    h = heads[shown]
    dur = float(h["duration_s"] or 0)
    mine = next((x for x in scores if x["v"] == shown), None)
    top = max(scores, key=lambda x: (x["overall"], x["v"])) if scores else None
    return {"shown": shown, "title_card_text": h["title_card_text"], "duration_s": round(dur, 1) if dur else None,
            "poster_t": round(h["title_s"] + max(dur - h["title_s"], 0) / 3, 1), "score": mine or top,
            "score_is_shown": mine is not None, "scores": scores}


def list_runs(runs_dir: Path) -> list[dict]:
    out = []
    for d in sorted(runs_dir.iterdir() if runs_dir.is_dir() else [], key=lambda p: p.name):
        run = read_json(d / "run.json") if d.is_dir() else None
        if not run:
            continue
        loop = read_json(d / "review" / "loop.json") or {}
        ledger = read_json(d / "ledger.json") or {}
        versions = sorted(int(m.group(1)) for p in d.glob("edl_v*.json") if (m := EDL.fullmatch(p.name)))
        out.append({"id": d.name, "footage": Path(run.get("footage", "")).name, "brief": run.get("brief", ""),
                    "started": run.get("started"), "versions": versions,
                    "scores": [p["overall"] for p in loop.get("passes", [])], "stop": loop.get("stop"),
                    "cost_usd": ledger.get("total_cost_usd"), "has_cut": any(d.glob("cut_v*.mp4")),
                    "card": project_card(d, versions, loop)})
    return out


def _span(s: dict) -> dict:
    keys = ("id", "order", "turn_id", "speaker", "role", "words", "t_in", "t_out", "duration", "word_from", "word_to",
            "why", "callout", "callout_xy", "contiguous_with_previous", "verbatim")
    return {k: s.get(k) for k in keys}


def _gate(rv: dict) -> dict | None:
    """The pass gate of a review (review.gate_check + close_gate): why the cut does not pass although the reviewer may
    have passed it. `checks` are the measured faults, `open_majors` the reviewer's own blockers and majors still open;
    `issues` are the measured faults in the reviewer's issue form, numbered after its issues as plan_fixes numbers them.
    None for a review written before the gate."""
    g = rv.get("pass_gate")
    if g is None:
        return None
    n = len((rv.get("review") or {}).get("issues") or [])
    checks = g.get("checks", g.get("reasons")) or []
    return {"blocked": bool(g.get("blocked")), "reasons": g.get("reasons") or [], "checks": checks,
            "open_majors": g.get("open_majors") or [],
            "issues": [{"k": n + j, **i} for j, i in enumerate(g.get("issues") or [])]}


def _review(rv: dict, run_dir: Path) -> dict:
    r = rv.get("review") or {}
    facts = rv.get("facts") or {}
    copy = rv.get("review_copy")
    return {"name": rv.get("name"), "overall": r.get("overall"), "pass": r.get("pass"),
            "reviewer_pass": r.get("reviewer_pass", r.get("pass")), "gate": _gate(rv), "scores": r.get("scores") or {},
            "summary": r.get("summary", ""), "issues": [{"k": k, **i} for k, i in enumerate(r.get("issues") or [])],
            "fixes": rv.get("fixes") or [], "skipped": rv.get("skipped") or [], "facts_text": rv.get("facts_text", ""),
            "facts": {"silences": len(facts.get("silences") or []),
                      "over_face": len((facts.get("captions") or {}).get("over_face") or []),
                      "caption_lines": (facts.get("captions") or {}).get("lines"),
                      "cut_edges": len(facts.get("cut_edges") or []),
                      "loud_edges": sum(1 for e in facts.get("cut_edges") or [] if e.get("loud"))},
            "cost_usd": rv.get("cost_usd"), "seconds": rv.get("seconds"), "duration_s": rv.get("duration_s"),
            "clock": rv.get("clock"), "has_copy": bool(copy) and (run_dir / "review" / rv["name"] / Path(copy).name).is_file()}


def planner_name(model: str | None) -> str:
    """The planner as the UI names it: Muse Spark 1.3, or Muse Spark Open for a Baseten meta-models/ id."""
    return "Muse Spark Open" if model and model.startswith("meta-models/") else "Muse Spark 1.3"


def versions(run_dir: Path) -> list[dict]:
    out = []
    run = read_json(run_dir / "run.json") or {}
    first_planner = run.get("planner_model") or run.get("model") or SPARK_MODEL
    for p in sorted(run_dir.glob("edl_v*.json"), key=lambda p: int(EDL.fullmatch(p.name).group(1)) if EDL.fullmatch(p.name) else 0):
        m = EDL.fullmatch(p.name)
        edl = read_json(p) if m else None
        if not edl:
            continue
        n = int(m.group(1))
        cut = f"cut_v{n}.mp4"
        if not (run_dir / cut).is_file():
            cut = None
        render = edl.get("render") or {}
        title = render.get("title")
        card = edl.get("title_card", True) is not False and bool(edl.get("title_card_text"))
        title_s = float(title.get("seconds", 1.5)) if title else (0.0 if render else 1.5 if card else 0.0)
        patches = edl.get("patches") or []
        origin = {"source": "plan"}
        if n > 1 and patches:
            last = patches[-1]
            origin = {"source": last.get("source") or "review", "from": last.get("from"), "review": last.get("review"),
                      "action": last.get("action") or "review", "instruction": last.get("instruction"),
                      "result": last.get("result"), "challenger": last.get("challenger"), "why": last.get("why"),
                      "fixes": [{k: f.get(k) for k in ("op", "target", "issue", "severity", "t_s", "note", "args", "check")} for f in last.get("fixes") or []]}
        # the planner that made this paper edit: v1's for the plan, a re-plan's own model; patches keep their parent's
        planner = first_planner if origin["source"] == "plan" else last.get("model") if origin.get("action") == "replan" else None
        if origin.get("action") == "replan" and not planner:
            planner = SPARK_MODEL  # re-plans before the model was recorded ran on Spark 1.3
        cmp = read_json(run_dir / f"plan_compare_v1_v{n}.json")
        rv = read_json(run_dir / f"review_v{n}.json")
        if rv and cut and Path(rv.get("cut", "")).name != cut:
            rv = None  # a review of another file of the same name pattern
        out.append({"n": n, "edl": p.name, "cut": cut, "runtime_s": edl.get("runtime_s"),
                    "duration_s": render.get("duration_s"), "title_card": edl.get("title_card", True) is not False,
                    "title_card_text": edl.get("title_card_text"), "title_s": title_s,
                    "spans": [_span(s) for s in edl.get("spans", [])], "origin": origin,
                    "loudness": render.get("loudness_ebur128"), "captions": render.get("captions"),
                    "callouts": [{"span": c.get("span"), "text": c.get("text"), "t_in": c.get("t_in"), "t_out": c.get("t_out"),
                                  "box": c.get("box"), "placement": c.get("placement"), "head_box": c.get("head_box"),
                                  "over_face": bool(c.get("over_face"))}
                                 for c in edl.get("callouts") or []],
                    "look": _look(edl),
                    "caption_lines": [{"id": c.get("id"), "text": c.get("text"), "t_in": c.get("t_in"), "t_out": c.get("t_out"),
                                       "spans": c.get("spans") or [], "placement": c.get("placement"),
                                       "over_face": bool(c.get("over_face")), "box": c.get("box"),
                                       "head_box": c.get("head_box"),
                                       "heard": next((w["voice"] for w in c.get("words") or [] if w.get("voice")), None)}
                                      for c in edl.get("captions") or []],
                    "caption_style": edl.get("caption_style"), "caption_style_note": edl.get("caption_style_note"),
                    "punch": bool(edl.get("punch")), "punch_skip": list(edl.get("punch_skip") or []),
                    "speaker_pins": {str(k): str(v) for k, v in (edl.get("reframe_speaker_ids") or {}).items()},
                    "punch_ins": [{k: e.get(k) for k in ("key", "kind", "word", "span", "excess_db", "t_in", "t_out", "zoom")}
                                  for e in edl.get("punch_ins") or [] if e.get("key")],
                    "bytes": (run_dir / cut).stat().st_size if cut else None,
                    "planner": {"model": planner, "name": planner_name(planner)} if planner else None,
                    "plan_compare": cmp,
                    "review": _review(rv, run_dir) if rv else None})
    return out


def _look(edl: dict) -> dict | None:
    try:
        return normalize_look(edl.get("look")).to_json()
    except ValueError:
        return None


def heads(run_dir: Path) -> dict:
    """The Speakers panel: every Voice speaker with who watch saw and the head ids the speaker map gave it, and every
    head of the SAM head track with a frame to show it (the record with the largest box, its time and box on the
    tracked video)."""
    voice = read_json(run_dir / "voice" / "voice.json") or {}
    spk = read_json(run_dir / "speakers.json") or {}
    track = read_json(run_dir / "track" / "head" / "tracks.json") or {}
    words: dict[str, int] = {}
    for w in voice.get("words") or []:
        words[str(w.get("speaker"))] = words.get(str(w.get("speaker")), 0) + 1
    speakers = [{"id": str(x), "who": ((spk.get("speakers") or {}).get(str(x)) or {}).get("who"),
                 "ids": [str(i) for i in ((spk.get("speakers") or {}).get(str(x)) or {}).get("ids") or []],
                 "method": ((spk.get("speakers") or {}).get(str(x)) or {}).get("method"), "words": words.get(str(x), 0)}
                for x in voice.get("speakers") or []]
    out = []
    for hid, recs in (track.get("objects") or {}).items():
        boxed = [r for r in recs if r.get("box")]
        if not boxed:
            continue
        r = max(boxed, key=lambda r: (r["box"][2] - r["box"][0]) * (r["box"][3] - r["box"][1]))
        out.append({"id": str(hid), "t": r["t"], "box": r["box"], "frames": len(boxed),
                    "speaker": ((spk.get("heads") or {}).get(str(hid)) or {}).get("speaker")})
    out.sort(key=lambda h: int(h["id"]) if h["id"].isdigit() else 0)
    return {"speakers": speakers, "heads": out, "source": (track.get("source") or {}),
            "video": "track/track_source_5fps.mp4" if (run_dir / "track" / "track_source_5fps.mp4").is_file()
            else "proxy.mp4" if (run_dir / "proxy.mp4").is_file() else None}


def turn_words(run_dir: Path) -> dict[str, list[dict]]:
    voice = read_json(run_dir / "voice" / "voice.json") or {}
    by: dict[str, list[dict]] = {}
    for w in voice.get("words", []):
        by.setdefault(str(w["turn_id"]), []).append({"w": w["w"], "i": w["i"], "t": w["t_start"], "e": w["t_end"]})
    for tw in by.values():
        tw.sort(key=lambda w: w["i"])
    return by


def stage_summaries(run_dir: Path, vs: list[dict]) -> list[dict]:
    """One line per rail stage from the artefacts on disk, with the ledger's dollars and seconds."""
    ledger = read_json(run_dir / "ledger.json") or {}
    ls = ledger.get("stages") or {}
    ing = read_json(run_dir / "ingest.json") or {}
    voice = read_json(run_dir / "voice" / "voice.json")
    watch = read_json(run_dir / "watch.json")
    plan = read_json(run_dir / "plan.json")
    loop = read_json(run_dir / "review" / "loop.json")
    v1 = read_json(run_dir / "edl_v1.json") or {}
    probe = ing.get("probe") or ing.get("source_probe") or {}
    art: dict[str, str | None] = {}
    if ing:
        dims = f"{probe.get('width')}x{probe.get('height')}" if probe.get("width") else ""
        art["ingest"] = " · ".join(x for x in (dims, f"{probe['fps']:.0f} fps" if probe.get("fps") else "",
                                               f"{probe['duration']:.0f} s" if probe.get("duration") else "",
                                               "hflip" if ing.get("hflip") else "") if x) or "proxy + wav"
    if voice:
        art["voice"] = f"{len(voice.get('words', []))} words · {len(voice.get('turns', []))} turns · {len(voice.get('speakers', []))} speaker(s)"
    tracks = v1.get("tracks") or {}
    if tracks:
        art["track"] = " · ".join(f"{c} {t.get('frames_with_records')}/{t.get('frames_processed')}" for c, t in tracks.items())
    if watch:
        art["watch"] = f"{len(watch.get('visual_shots', []))} shots · {len(watch.get('movement_moments', []))} moves"
    if plan:
        art["plan"] = f"{len(plan.get('paper_edit', []))} spans · ~{plan.get('estimated_runtime_s', 0):.0f} s"
    ep = v1.get("edge_pass")
    if ep:
        art["edges"] = f"{ep.get('moved')} edges · {ep.get('removed_s')} s of pause out"
    v1r = next((v for v in vs if v["n"] == 1), None)
    if v1r and v1r["cut"]:
        art["render"] = f"cut_v1 {v1r['duration_s'] or v1r['runtime_s']:.1f} s · {v1r['captions'] or 0} captions"
    if loop and loop.get("passes"):
        art["review"] = " → ".join(f"v{p['version']} {p['overall']}" for p in loop["passes"]) + f" · {loop.get('stop', '')}"
    run = read_json(run_dir / "run.json") or {}
    planner = planner_name(run.get("planner_model") or run.get("model"))
    out = []
    for sid, label, model, prefixes in STAGES:
        if sid == "plan":
            model = planner
        cost = sum(v.get("cost_usd", 0) for k, v in ls.items() if k.split(".")[0] in prefixes or k in prefixes)
        secs = sum(v.get("seconds", 0) for k, v in ls.items() if k.split(".")[0] in prefixes or k in prefixes)
        out.append({"id": sid, "label": label, "model": model, "artefact": art.get(sid), "cost_usd": round(cost, 4),
                    "seconds": round(secs, 1), "done": art.get(sid) is not None})
    return out


def media(run_dir: Path, run: dict) -> dict:
    """The source clip for the Media panel: ingest.json's probe, the proxy to preview, the transcript's size."""
    ing = read_json(run_dir / "ingest.json") or {}
    probe = ing.get("probe") or {}
    voice = read_json(run_dir / "voice" / "voice.json") or {}
    return {"footage": run.get("footage") or ing.get("original_source"), "width": probe.get("width"),
            "height": probe.get("height"), "fps": probe.get("fps"), "duration": probe.get("duration"),
            "bytes": probe.get("bytes"), "codec": probe.get("codec"), "hflip": bool(ing.get("hflip") or run.get("hflip")),
            "proxy": "proxy.mp4" if (run_dir / "proxy.mp4").is_file() else None,
            "title_png": "title/title_card.png" if (run_dir / "title" / "title_card.png").is_file() else None,
            "words": len(voice.get("words") or []), "turns": len(voice.get("turns") or []),
            "speakers": len(voice.get("speakers") or [])}


def run_state(run_dir: Path) -> dict:
    vs = versions(run_dir)
    ledger = read_json(run_dir / "ledger.json") or {}
    loop = read_json(run_dir / "review" / "loop.json")
    chat = []
    cp = run_dir / "ui" / "chat.jsonl"
    if cp.is_file():
        for line in cp.read_text().splitlines():
            try:
                chat.append(json.loads(line))
            except ValueError:
                pass
    run = read_json(run_dir / "run.json") or {}
    return {"id": run_dir.name, "run": run, "media": media(run_dir, run), "summary": read_json(run_dir / "summary.json"),
            "ledger": {"total_cost_usd": ledger.get("total_cost_usd", 0), "wall_seconds": ledger.get("wall_seconds", 0),
                       "stages": ledger.get("stages") or {}},
            "loop": loop, "versions": vs, "words": turn_words(run_dir), "stages": stage_summaries(run_dir, vs),
            "chat": chat}


def fingerprint(run_dir: Path) -> str:
    """Changes when a version, review, loop, ledger or chat file changes: the UI then re-reads the state."""
    parts = []
    for pat in ("edl_v*.json", "cut_v*.mp4", "review_v*.json", "review/loop.json", "ledger.json", "ui/chat.jsonl", "summary.json",
                "ingest.json", "voice/voice.json", "watch.json", "plan.json"):
        for p in run_dir.glob(pat):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                pass
    return "|".join(sorted(parts))
