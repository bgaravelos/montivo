"""Read side of the UI: a run folder as one JSON state, and run.log as stage-tagged lines.

Everything here reads files the engine already writes (run.json, summary.json, ledger.json, edl_vN.json,
review_vN.json, review/loop.json, voice.json, watch.json, plan.json, run.log); nothing imports the engine.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

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
    edges = [s for s in order if s > first and any("edges moved" in l["msg"] and "dry run" not in l["msg"] for l in sessions[s])]
    if edges:
        picked.append(edges[-1])
    loops = [s for s in order if s > first and any(l["msg"].startswith("review loop:") for l in sessions[s])]
    if loops and loops[-1] not in picked and not any(l["msg"].startswith("review loop:") for l in sessions[first]):
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


# --------------------------------------------------------------------------- run state

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
                    "cost_usd": ledger.get("total_cost_usd"), "has_cut": any(d.glob("cut_v*.mp4"))})
    return out


def _span(s: dict) -> dict:
    keys = ("id", "order", "turn_id", "speaker", "role", "words", "t_in", "t_out", "duration", "word_from", "word_to",
            "why", "callout", "contiguous_with_previous", "verbatim")
    return {k: s.get(k) for k in keys}


def _review(rv: dict, run_dir: Path) -> dict:
    r = rv.get("review") or {}
    facts = rv.get("facts") or {}
    copy = rv.get("review_copy")
    return {"name": rv.get("name"), "overall": r.get("overall"), "pass": r.get("pass"), "scores": r.get("scores") or {},
            "summary": r.get("summary", ""), "issues": [{"k": k, **i} for k, i in enumerate(r.get("issues") or [])],
            "fixes": rv.get("fixes") or [], "skipped": rv.get("skipped") or [], "facts_text": rv.get("facts_text", ""),
            "facts": {"silences": len(facts.get("silences") or []),
                      "over_face": len((facts.get("captions") or {}).get("over_face") or []),
                      "caption_lines": (facts.get("captions") or {}).get("lines"),
                      "cut_edges": len(facts.get("cut_edges") or []),
                      "loud_edges": sum(1 for e in facts.get("cut_edges") or [] if e.get("loud"))},
            "cost_usd": rv.get("cost_usd"), "seconds": rv.get("seconds"), "duration_s": rv.get("duration_s"),
            "clock": rv.get("clock"), "has_copy": bool(copy) and (run_dir / "review" / rv["name"] / Path(copy).name).is_file()}


def versions(run_dir: Path) -> list[dict]:
    out = []
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
                      "result": last.get("result"),
                      "fixes": [{k: f.get(k) for k in ("op", "target", "issue", "severity", "t_s", "note", "args")} for f in last.get("fixes") or []]}
        rv = read_json(run_dir / f"review_v{n}.json")
        if rv and cut and Path(rv.get("cut", "")).name != cut:
            rv = None  # a review of another file of the same name pattern
        out.append({"n": n, "edl": p.name, "cut": cut, "runtime_s": edl.get("runtime_s"),
                    "duration_s": render.get("duration_s"), "title_card": edl.get("title_card", True) is not False,
                    "title_card_text": edl.get("title_card_text"), "title_s": title_s,
                    "spans": [_span(s) for s in edl.get("spans", [])], "origin": origin,
                    "loudness": render.get("loudness_ebur128"), "captions": render.get("captions"),
                    "callouts": [{"span": c.get("span"), "text": c.get("text"), "t_in": c.get("t_in"), "t_out": c.get("t_out")}
                                 for c in edl.get("callouts") or []],
                    "caption_lines": [{"id": c.get("id"), "text": c.get("text"), "t_in": c.get("t_in"), "t_out": c.get("t_out"),
                                       "spans": c.get("spans") or [], "placement": c.get("placement"),
                                       "over_face": bool(c.get("over_face")),
                                       "heard": next((w["voice"] for w in c.get("words") or [] if w.get("voice")), None)}
                                      for c in edl.get("captions") or []],
                    "caption_style": edl.get("caption_style"), "caption_style_note": edl.get("caption_style_note"),
                    "punch": bool(edl.get("punch")), "punch_skip": list(edl.get("punch_skip") or []),
                    "punch_ins": [{k: e.get(k) for k in ("key", "kind", "word", "span", "excess_db", "t_in", "t_out", "zoom")}
                                  for e in edl.get("punch_ins") or [] if e.get("key")],
                    "bytes": (run_dir / cut).stat().st_size if cut else None,
                    "review": _review(rv, run_dir) if rv else None})
    return out


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
    out = []
    for sid, label, model, prefixes in STAGES:
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
