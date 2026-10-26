"""The run: ingest -> (voice -> watch) || track -> plan -> edl_v1.json -> render cut_v1.mp4, with a ledger and every raw call saved."""
from __future__ import annotations

import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .config import REFRAME_CONCEPT, REFRAME_TRACK_FPS, SPARK_MODEL, TRACK_RESAMPLE_FPS, TRACK_RESAMPLE_OVER_S, sam_cost
from .engine import track as track_mod
from .engine.api import dump_json
from .engine.edl import build_edl
from .engine.image import generate_title_card
from .engine.ingest import Ingested, Probe, ingest
from .engine.render import render as render_edl
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
    reframe_concept: str | None = REFRAME_CONCEPT  # tracked at reframe_fps for the 9:16 crop; None = person only
    reframe_fps: float = REFRAME_TRACK_FPS
    language: str | None = "English"
    target_s: float = 40.0
    plan_with_video: bool = False
    plan_effort: str | None = None
    skip_track: bool = False
    resume: bool = False
    model: str = SPARK_MODEL
    render: bool = True
    hflip: bool = False  # mirror the footage once in ingest (selfie / mirror-mode clips)
    title: bool = True  # Muse Image title card in front of the cut ($0.01)
    captions: bool = True


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
    ledger = Ledger.resume(out / "ledger.json") if opts.resume else Ledger(out / "ledger.json")
    dump_json(out / "run.json", {"footage": str(opts.footage), "brief": opts.brief, "concepts": opts.concepts, "reframe_concept": opts.reframe_concept, "reframe_fps": opts.reframe_fps,
                                 "target_s": opts.target_s, "model": opts.model, "hflip": opts.hflip, "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    log(f"montivo run: {opts.footage.name} -> {out}")

    def have(*parts: str) -> bool:
        return opts.resume and (out.joinpath(*parts)).is_file()

    # 1. ingest: proxy + wav + one upload for Spark
    if have("ingest.json"):
        d = json.loads((out / "ingest.json").read_text())
        ing = Ingested(source=Path(d["source"]), probe=Probe(**d["probe"]), proxy=Path(d["proxy"]), proxy_probe=Probe(**d["proxy_probe"]),
                       wav=Path(d["wav"]), file_id=d["file_id"], upload_seconds=d["upload_seconds"])
        log(f"resume: ingest from ingest.json (file_id {ing.file_id}{', hflip' if d.get('hflip') else ''})")
    else:
        ing = ingest(opts.footage, out, ledger, log, hflip=opts.hflip)
    # from here on `ing.source` is the footage every stage reads: the mirrored copy when --hflip, else opts.footage
    spark = Spark(out, ledger, model=opts.model, log=log)

    def concept_price() -> float:
        """What one more SAM concept costs on this clip at the track fps (one request per concept)."""
        fps = opts.track_fps or (TRACK_RESAMPLE_FPS if ing.proxy_probe.duration > TRACK_RESAMPLE_OVER_S else ing.proxy_probe.fps)
        return sam_cost(int(round(ing.proxy_probe.duration * fps)))

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
        # Watch's trackable concepts are suggestions only. SAM is one request per concept (about $0.25 per 125 s at
        # 10 fps), so the only concepts ever sent to SAM are the CLI --concept list plus the reframe head track.
        suggested = [c for c in watch.parsed.get("trackable_concepts", []) if c.lower() not in {x.lower() for x in opts.concepts}]
        if suggested:
            log(f"watch suggests: {', '.join(suggested)} (not tracked; add --concept \"...\" to track one, "
                f"about ${concept_price():.2f} per concept on this clip)")
        for m in watch.parsed.get("movement_moments", []):
            log(f"watch: speaker moves {m['start_s']:.1f}-{m['end_s']:.1f}s: {m['what']}")
        return voice, watch

    def track_stage():
        if opts.skip_track:
            return {}
        if opts.resume and list((out / "track").glob("*/tracks.json")):
            loaded = track_mod.load_all(out / "track")
            log(f"resume: tracks from track/*/tracks.json ({', '.join(loaded)})")
            return loaded
        src = track_mod.prepare_source(ing.proxy, ing.proxy_probe, ing.file_id, out / "track", ledger, fps=opts.track_fps, log=log)
        concepts = list(opts.concepts)
        reframe = opts.reframe_concept
        if reframe and reframe not in concepts and abs(src.fps - opts.reframe_fps) < 0.01:
            concepts.append(reframe)  # same clip, one more request in the same batch
            reframe = None
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_main = pool.submit(track_mod.track_all, src, concepts, out / "track", ledger, log)
            f_head = None
            if reframe and reframe not in concepts:
                def head_track():
                    s5 = track_mod.prepare_source(ing.proxy, ing.proxy_probe, ing.file_id, out / "track", ledger, fps=opts.reframe_fps, log=log)
                    return track_mod.track_concept(s5, reframe, out / "track", ledger, log)
                f_head = pool.submit(head_track)
            tracks = f_main.result()
            if f_head is not None:
                ct = f_head.result()
                tracks[ct.concept] = ct
        return tracks

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

    # 3. plan, with the speech rate Voice measured on this clip (2.5 words/s made a 40 s target come out at 50.7 s)
    wps = voice.speech_rate()
    log(f"plan: speech rate {wps:.2f} words/s ({len(voice.words)} words over the spoken seconds of {len(voice.turns)} turns), "
        f"target {opts.target_s:.0f} s = about {opts.target_s * wps:.0f} to {opts.target_s * 1.2 * wps:.0f} words")
    planned = spark.plan(opts.brief, voice.transcript_text(), watch.parsed, opts.target_s,
                         file_id=ing.file_id if opts.plan_with_video else None, reasoning_effort=opts.plan_effort,
                         words_per_s=wps)
    dump_json(out / "plan.json", planned.parsed)

    # 4. EDL v1 on word boundaries; the source is the file ingest produced (the mirrored copy under --hflip)
    source = {"path": str(ing.source), "original_path": str(opts.footage), "hflip": ing.source != opts.footage,
              "proxy": str(ing.proxy), "file_id": ing.file_id, "duration_s": ing.probe.duration,
              "width": ing.probe.width, "height": ing.probe.height, "fps": ing.probe.fps,
              "proxy_width": ing.proxy_probe.width, "proxy_height": ing.proxy_probe.height}
    track_summary = {c: t.summary() for c, t in tracks.items()}
    edl = build_edl(planned.parsed, voice, watch.parsed, source, opts.brief, track_summary,
                    reframe_concept=opts.reframe_concept or "person")
    dump_json(out / "edl_v1.json", edl)

    # 5. title card (muse-image-1.0, $0.01) and render cut_v1.mp4 from the source (ffmpeg only)
    rendered = None
    if opts.render:
        title_png = None
        if opts.title and edl.get("title_card_text"):
            try:
                title_png = title_card(out, edl, ledger, log)
            except Exception:
                errors.append(("title", traceback.format_exc()))
                log(f"ERROR in title card (rendering without it):\n{errors[-1][1]}")
        try:
            rendered = render_edl(out, edl, ing.source, ledger, log=log, edl_path=out / "edl_v1.json",
                                  words=[w.__dict__ for w in voice.words] if opts.captions else None, title_png=title_png)
        except Exception:
            errors.append(("render", traceback.format_exc()))
            log(f"ERROR in render:\n{errors[-1][1]}")

    summary = {
        "out": str(out), "runtime_s": edl["runtime_s"], "spans": len(edl["spans"]), "verbatim_rate": edl["verbatim_rate"],
        "unplaced": len(edl["unplaced"]), "turns": len(voice.turns), "words": len(voice.words), "speakers": voice.speakers,
        "tracks": {c: len(t.objects) for c, t in tracks.items()},
        "watch_suggested_concepts": watch.parsed.get("trackable_concepts", []), "speech_rate_wps": wps,
        "cut": rendered["output"] if rendered else None,
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


def title_card(run_dir: Path, edl: dict, ledger: Ledger, log=print) -> Path:
    """run_dir/title/title_card.png for the EDL's title text: reused when it exists for the same text, else generated
    once with muse-image-1.0 ($0.01, in the ledger)."""
    tdir = run_dir / "title"
    png = tdir / "title_card.png"
    text = edl["title_card_text"]
    req = tdir / "request.json"
    if png.is_file() and req.is_file() and f'"{text}"' in json.loads(req.read_text()).get("prompt", ""):
        log(f"title: reusing {png} (\"{text}\")")
        return png
    return generate_title_card(text, tdir, ledger, log)


def render_run(run_dir: Path, edl_path: Path | None = None, source: Path | None = None, out_name: str = "cut_v1.mp4",
               title: bool = True, captions: bool = True, callouts: bool = True, loudnorm: bool = True) -> dict:
    """`montivo render runs/001`: ffmpeg only, from an existing EDL and the run's voice.json and tracks; appends a
    `render` entry to the run's ledger. The only possible API call is the title card, when title/title_card.png is
    missing ($0.01, muse-image-1.0); --no-title skips it."""
    edl_path = edl_path or run_dir / "edl_v1.json"
    if not edl_path.is_file():
        raise SystemExit(f"{edl_path} not found: run `montivo run` first")
    edl = json.loads(edl_path.read_text())
    source = source or Path(edl["source"]["path"])
    if not source.is_file():
        raise SystemExit(f"source footage {source} not found (run from the project root or pass --source)")
    log = RunLog(run_dir / "run.log")
    ledger = Ledger.resume(run_dir / "ledger.json")
    log(f"montivo render: {edl_path} -> {run_dir / out_name}")
    words = None
    vpath = run_dir / "voice" / "voice.json"
    if captions and vpath.is_file():
        words = json.loads(vpath.read_text())["words"]
    elif captions:
        log(f"render: {vpath} missing, no captions")
    title_png = title_card(run_dir, edl, ledger, log) if title and edl.get("title_card_text") else None
    info = render_edl(run_dir, edl, source, ledger, out_name=out_name, log=log, edl_path=edl_path, words=words,
                      title_png=title_png, captions=captions, callouts=callouts, loudnorm=loudnorm)
    return {"out": info["output"], "runtime_s": edl["runtime_s"], "duration_s": info["duration_s"], "segments": len(info["segments"]),
            "captions": info["captions"], "callouts": info["callouts"], "loudness": info["loudness_ebur128"],
            "seconds": info["seconds"], "cost_usd": ledger.total_cost()}
