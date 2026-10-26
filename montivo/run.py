"""The run: ingest -> (voice -> watch) || track -> plan -> edl_v1.json, with a ledger and every raw call saved."""
from __future__ import annotations

import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .config import SPARK_MODEL
from .engine import track as track_mod
from .engine.api import dump_json
from .engine.edl import build_edl
from .engine.ingest import Ingested, Probe, ingest
from .engine.spark import Spark, SparkCall
from .engine.voice import VoiceResult, transcribe
from .ledger import Ledger


@dataclass
class RunOptions:
    footage: Path
    brief: str
    out: Path
    concepts: list[str] = field(default_factory=lambda: ["person"])
    track_fps: float | None = None
    language: str | None = "English"
    target_s: float = 40.0
    plan_with_video: bool = False
    plan_effort: str | None = None
    skip_track: bool = False
    resume: bool = False
    model: str = SPARK_MODEL


class RunLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a")
        self.t0 = time.time()

    def __call__(self, msg: str) -> None:
        line = f"[{time.time() - self.t0:7.1f}s] {msg}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()


def run(opts: RunOptions) -> dict:
    out = opts.out
    out.mkdir(parents=True, exist_ok=True)
    log = RunLog(out / "run.log")
    ledger = Ledger(out / "ledger.json")
    if opts.resume and (out / "ledger.json").is_file():
        from .ledger import Entry
        prev = json.loads((out / "ledger.json").read_text())
        ledger.entries = [Entry(**e) for e in prev.get("entries", [])]
        ledger.started -= prev.get("wall_seconds", 0.0)
    dump_json(out / "run.json", {"footage": str(opts.footage), "brief": opts.brief, "concepts": opts.concepts,
                                 "target_s": opts.target_s, "model": opts.model, "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    log(f"montivo run: {opts.footage.name} -> {out}")

    def have(*parts: str) -> bool:
        return opts.resume and (out.joinpath(*parts)).is_file()

    # 1. ingest: proxy + wav + one upload for Spark
    if have("ingest.json"):
        d = json.loads((out / "ingest.json").read_text())
        ing = Ingested(source=Path(d["source"]), probe=Probe(**d["probe"]), proxy=Path(d["proxy"]), proxy_probe=Probe(**d["proxy_probe"]),
                       wav=Path(d["wav"]), file_id=d["file_id"], upload_seconds=d["upload_seconds"])
        log(f"resume: ingest from ingest.json (file_id {ing.file_id})")
    else:
        ing = ingest(opts.footage, out, ledger, log)
    spark = Spark(out, ledger, model=opts.model, log=log)

    # 2. voice and track in parallel; watch starts the moment the transcript exists (it needs it, see spark.py)
    def voice_then_watch():
        if have("voice", "voice.json"):
            voice = VoiceResult.from_json(json.loads((out / "voice" / "voice.json").read_text()))
            log(f"resume: voice from voice/voice.json ({len(voice.turns)} turns, {len(voice.words)} words)")
        else:
            voice = transcribe(ing.wav, out / "voice", ledger, language=opts.language, log=log)
        if not voice.turns:
            raise RuntimeError("voice: no speech found in the footage")
        if have("watch.json"):
            watch = SparkCall(name="watch", parsed=json.loads((out / "watch.json").read_text()), status="resumed", usage={},
                              cost_usd=0.0, seconds=0.0, response_id="")
            log("resume: watch from watch.json")
        else:
            watch = spark.watch(ing.file_id, voice.transcript_text(), opts.brief, ing.probe.duration)
            dump_json(out / "watch.json", watch.parsed)
        return voice, watch

    def track_stage():
        if opts.skip_track:
            return {}
        if opts.resume and list((out / "track").glob("*/tracks.json")):
            loaded = track_mod.load_all(out / "track")
            log(f"resume: tracks from track/*/tracks.json ({', '.join(loaded)})")
            return loaded
        src = track_mod.prepare_source(ing.proxy, ing.proxy_probe, ing.file_id, out / "track", ledger, fps=opts.track_fps, log=log)
        return track_mod.track_all(src, opts.concepts, out / "track", ledger, log)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_voice = pool.submit(voice_then_watch)
        f_track = pool.submit(track_stage)
        errors = []
        try:
            voice, watch = f_voice.result()
        except Exception:
            errors.append(("voice/watch", traceback.format_exc()))
            voice = watch = None
        try:
            tracks = f_track.result()
        except Exception:
            errors.append(("track", traceback.format_exc()))
            tracks = {}
    for stage, tb in errors:
        log(f"ERROR in {stage}:\n{tb}")
    if voice is None or watch is None:
        raise SystemExit("run aborted: voice/watch failed (see run.log)")

    # 3. plan
    planned = spark.plan(opts.brief, voice.transcript_text(), watch.parsed, opts.target_s,
                         file_id=ing.file_id if opts.plan_with_video else None, reasoning_effort=opts.plan_effort)
    dump_json(out / "plan.json", planned.parsed)

    # 4. EDL v1 on word boundaries
    source = {"path": str(opts.footage), "proxy": str(ing.proxy), "file_id": ing.file_id, "duration_s": ing.probe.duration,
              "width": ing.probe.width, "height": ing.probe.height, "fps": ing.probe.fps,
              "proxy_width": ing.proxy_probe.width, "proxy_height": ing.proxy_probe.height}
    track_summary = {c: t.summary() for c, t in tracks.items()}
    edl = build_edl(planned.parsed, voice, watch.parsed, source, opts.brief, track_summary)
    dump_json(out / "edl_v1.json", edl)

    summary = {
        "out": str(out), "runtime_s": edl["runtime_s"], "spans": len(edl["spans"]), "verbatim_rate": edl["verbatim_rate"],
        "unplaced": len(edl["unplaced"]), "turns": len(voice.turns), "words": len(voice.words), "speakers": voice.speakers,
        "tracks": {c: len(t.objects) for c, t in tracks.items()},
        "cost_usd": ledger.total_cost(), "wall_s": ledger.summary()["wall_seconds"], "errors": [e[0] for e in errors],
    }
    dump_json(out / "summary.json", summary)
    log("paper edit:")
    for s in edl["spans"]:
        log(f"  {s['id']} {s['role']:6s} {s['t_in']:7.2f}-{s['t_out']:7.2f}s {'verbatim' if s['verbatim'] else f'FUZZY {s['match_ratio']}'} "
            f"spk {s['speaker']}: {s['words'][:80]}")
    for m in edl["unplaced"]:
        log(f"  UNPLACED turn {m['turn_id']}: {m['words'][:80]}")
    log(f"done: {edl['runtime_s']} s cut from {len(edl['spans'])} spans, verbatim {edl['verbatim_rate']:.0%}, "
        f"${ledger.total_cost():.4f}, {summary['wall_s']} s wall")
    return summary
