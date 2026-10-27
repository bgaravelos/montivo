"""The run: ingest -> (voice -> watch) || track -> plan -> edl_v1.json -> render cut_v1.mp4, with a ledger and every raw call saved."""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .config import REFRAME_CONCEPT, REFRAME_TRACK_FPS, SPARK_MODEL, open_api_key, sam_cost
from .engine import track as track_mod
from .engine.api import dump_json
from .engine.edges import Audio, Words, tighten_edges
from .engine.edl import build_edl
from .engine.image import generate_title_card
from .engine.ingest import Ingested, Probe, ingest
from .engine.render import render as render_edl
from .engine.spark import Spark, SparkCall
from .engine.speakers import is_mic, speakers_run
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
    replan: bool = False  # with resume: a new plan even when plan.json was made with the same brief, target and planner
    model: str = SPARK_MODEL
    planner_model: str | None = None  # None = model; config.OPEN_MODEL plans on Muse Spark Open (Baseten, text only)
    render: bool = True
    hflip: bool = False  # mirror the footage once in ingest (selfie / mirror-mode clips)
    title: bool = True  # Muse Image title card in front of the cut ($0.01)
    captions: bool = True
    review: bool = False  # blind review loop after cut_v1 (engine/review.py)
    review_passes: int = 3
    review_budget: float = 0.30
    punch: bool = False  # punch-in zooms on stressed words and jump cuts (engine/punch.py)


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


def plan_inputs(opts: RunOptions) -> dict:
    """What a plan depends on besides the transcript and watch; run.json keeps it as `plan_inputs`."""
    return {"brief": opts.brief, "target_s": opts.target_s, "planner_model": opts.planner_model or opts.model,
            "plan_with_video": opts.plan_with_video, "plan_effort": opts.plan_effort}


def plan_made_with(prev_run: dict) -> dict:
    """The inputs of the plan.json in a run folder; a run.json from before `plan_inputs` gives brief, target and
    planner (the run that wrote it planned with them)."""
    if prev_run.get("plan_inputs"):
        return prev_run["plan_inputs"]
    if not prev_run:
        return {}
    return {"brief": prev_run.get("brief"), "target_s": prev_run.get("target_s"),
            "planner_model": prev_run.get("planner_model") or prev_run.get("model")}


def plan_reuse(opts: RunOptions, made: dict, have_plan: bool, inputs_resumed: bool) -> tuple[bool, str]:
    """Whether a resumed run reuses plan.json, and why (logged). Reused only when nothing the plan read has changed:
    the transcript and watch came from disk and every recorded input equals this run's."""
    if not opts.resume:
        return False, "a new plan"
    if not have_plan:
        return False, "no plan.json yet: a new plan"
    if opts.replan:
        return False, "--replan: a new plan"
    if not inputs_resumed:
        return False, "voice or watch is new: a new plan"
    now = plan_inputs(opts)
    changed = [k for k in made if made[k] != now.get(k)] if made else ["(not recorded)"]
    if changed:
        return False, f"{', '.join(changed)} changed since plan.json: a new plan"
    return True, "same brief, target and planner as plan.json: plan reused (--replan makes a new one)"


def supersede_v1(out: Path, log=print) -> Path | None:
    """Before a new plan replaces edl_v1.json: plan.json and edl_v1.json are copied to superseded/<time>/ (cut_v1.mp4
    can be rendered again from that EDL, $0), and later versions, built on the old plan, are named in a warning.
    They are never touched; `montivo plan RUN` is the way to a new plan as the next version."""
    if not (out / "edl_v1.json").is_file():
        return None
    dest = out / "superseded" / time.strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("plan.json", "edl_v1.json"):
        if (out / name).is_file():
            shutil.copy2(out / name, dest / name)
    later = sorted((p for p in out.glob("edl_v*.json") if re.fullmatch(r"edl_v\d+\.json", p.name) and p.name != "edl_v1.json"),
                   key=lambda p: int(p.stem[5:]))
    log(f"plan: the previous plan.json and edl_v1.json are kept in {dest.relative_to(out)}/")
    if later:
        log(f"WARNING: {', '.join(p.stem for p in later)} were built on the previous plan and stay as they are; the new "
            f"edl_v1 does not lead to them (`montivo plan {out}` makes a new plan as the next version instead)")
    return dest


def run(opts: RunOptions) -> dict:
    out = opts.out
    if opts.planner_model:
        open_api_key()  # fail before any spend, not at the plan call after Voice, SAM and watch
    out.mkdir(parents=True, exist_ok=True)
    log = RunLog(out / "run.log")
    ledger = Ledger.resume(out / "ledger.json") if opts.resume else Ledger(out / "ledger.json")
    prev_run = json.loads((out / "run.json").read_text()) if opts.resume and (out / "run.json").is_file() else {}
    run_json = {"footage": str(opts.footage), "brief": opts.brief, "concepts": opts.concepts, "reframe_concept": opts.reframe_concept, "reframe_fps": opts.reframe_fps,
                "target_s": opts.target_s, "model": opts.model, "planner_model": opts.planner_model or opts.model, "hflip": opts.hflip, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    made = plan_made_with(prev_run) if (out / "plan.json").is_file() else {}
    if made:
        run_json["plan_inputs"] = made  # what plan.json was made with, until a new plan replaces it
    dump_json(out / "run.json", run_json)
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
        return sam_cost(int(round(ing.proxy_probe.duration * track_mod.track_fps(ing.proxy_probe.fps, opts.track_fps))))

    # 2. voice and track in parallel; watch starts the moment the transcript exists (it needs it, see spark.py)
    fresh: set[str] = set()  # voice / watch made in this run, not read from disk: the plan read them

    def voice_then_watch():
        if have("voice", "voice.json"):
            voice = VoiceResult.from_json(json.loads((out / "voice" / "voice.json").read_text()))
            log(f"resume: voice from voice/voice.json ({len(voice.turns)} turns, {len(voice.words)} words)")
        else:
            voice = transcribe(ing.wav, out / "voice", ledger, language=opts.language, log=log)
            fresh.add("voice")
        if not voice.turns:
            raise RuntimeError("voice: no speech found in the footage")
        if have("watch.json"):
            watch = SparkCall(name="watch", parsed=json.loads((out / "watch.json").read_text()), status="resumed", usage={},
                              cost_usd=0.0, seconds=0.0, response_id="")
            log("resume: watch from watch.json")
        else:
            watch = spark.watch(ing.file_id, voice.transcript_text(), opts.brief, ing.probe.duration)
            dump_json(out / "watch.json", watch.parsed)
            fresh.add("watch")
        # Watch's trackable concepts are suggestions only. SAM is one request per concept (about $0.125 per 125 s at
        # 5 fps), so the only concepts ever sent to SAM are the CLI --concept list plus the reframe head track.
        suggested = [c for c in watch.parsed.get("trackable_concepts", []) if c.lower() not in {x.lower() for x in opts.concepts}
                     and not is_mic(c)]  # a microphone is tracked for the speaker map (2b)
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

    # 2b. which head is which speaker (speakers.py): only with two or more voices and two or more heads. A microphone
    # watch saw is tracked first on the head track's frames (one SAM request), the strongest signal of who talks
    head = tracks.get(opts.reframe_concept or "") if opts.reframe_concept == "head" else None
    if head is not None and len(voice.speakers) >= 2 and len(head.objects) >= 2:
        mic = next((c for c in watch.parsed.get("trackable_concepts", []) if is_mic(c)), None)
        new_mic = False
        if mic and not any(is_mic(c) for c in tracks):
            try:
                log(f"track: {mic} for the speaker map (watch saw one), about ${concept_price():.2f}")
                tracks[mic] = track_mod.track_concept(track_mod.reuse_source(head, ing.proxy), mic, out / "track", ledger, log)
                new_mic = True
            except Exception:
                errors.append(("track mic", traceback.format_exc()))
                log(f"ERROR tracking {mic} (the speaker map goes on without it):\n{errors[-1][1]}")
        if have("speakers.json") and not new_mic:
            log("resume: speakers from speakers.json")
        else:
            try:
                speakers_run(out, log=log)
            except Exception:
                errors.append(("speakers", traceback.format_exc()))
                log(f"ERROR in speaker mapping (the crop keeps the largest head):\n{errors[-1][1]}")

    # 3. plan, with the speech rate Voice measured on this clip (2.5 words/s made a 40 s target come out at 50.7 s)
    # On --resume plan.json is reused when nothing it read has changed; a new plan never replaces edl_v1 silently
    wps = voice.speech_rate()
    reuse, why = plan_reuse(opts, made, (out / "plan.json").is_file(), not fresh)
    if reuse:
        planned = SparkCall(name="plan", parsed=json.loads((out / "plan.json").read_text()), status="resumed", usage={},
                            cost_usd=0.0, seconds=0.0, response_id="")
        log(f"resume: {why}")
    else:
        if opts.resume:
            log(f"plan: {why}")
        supersede_v1(out, log)
        log(f"plan: speech rate {wps:.2f} words/s ({len(voice.words)} words over the spoken seconds of {len(voice.turns)} turns), "
            f"target {opts.target_s:.0f} s = about {opts.target_s * wps:.0f} to {opts.target_s * 1.2 * wps:.0f} words")
        planner = Spark(out, ledger, model=opts.planner_model, log=log) if opts.planner_model else spark
        planned = planner.plan(opts.brief, voice.transcript_text(), watch.parsed, opts.target_s,
                             file_id=ing.file_id if opts.plan_with_video else None, reasoning_effort=opts.plan_effort,
                             words_per_s=wps)
        dump_json(out / "plan.json", planned.parsed)
        dump_json(out / "run.json", {**run_json, "plan_inputs": plan_inputs(opts)})

    # 4. EDL v1 on word boundaries; the source is the file ingest produced (the mirrored copy under --hflip)
    source = {"path": str(ing.source), "original_path": str(opts.footage), "hflip": ing.source != opts.footage,
              "proxy": str(ing.proxy), "file_id": ing.file_id, "duration_s": ing.probe.duration,
              "width": ing.probe.width, "height": ing.probe.height, "fps": ing.probe.fps,
              "proxy_width": ing.proxy_probe.width, "proxy_height": ing.proxy_probe.height}
    track_summary = {c: t.summary() for c, t in tracks.items()}
    if reuse and (out / "edl_v1.json").is_file():
        # the same plan builds the same v1, and later versions were patched from this file: keep it as it is
        edl = json.loads((out / "edl_v1.json").read_text())
        log("resume: edl_v1 from edl_v1.json")
    else:
        edl = build_edl(planned.parsed, voice, watch.parsed, source, opts.brief, track_summary,
                        reframe_concept=opts.reframe_concept or "person", audio=Audio.load(out))
        if edl["role_fixes"]:
            log(f"roles set by position (one hook first, the payoff last): {', '.join(edl['role_fixes'])}")
        if edl.get("edge_pass"):
            ep = edl["edge_pass"]
            log(f"edges: {ep['moved']} cut edges tightened to the speech, {ep['removed_s']} s of pause removed "
                f"(floor {ep['floor_db']} dB, speech over {ep['threshold_db']} dB)")
    if opts.punch:
        edl["punch"] = True
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
                                  words=[w.__dict__ for w in voice.words] if opts.captions else None, title_png=title_png,
                                  punch=opts.punch)
        except Exception:
            errors.append(("render", traceback.format_exc()))
            log(f"ERROR in render:\n{errors[-1][1]}")

    # 6. the closed loop: Spark reviews cut_v1 blind, fix ops patch the EDL, re-render, up to review_passes reviews
    loop = None
    if opts.review and rendered:
        from .engine.review import review_loop
        try:
            loop = review_loop(out, out / "edl_v1.json", passes=opts.review_passes, budget_usd=opts.review_budget,
                               ledger=ledger, log=log, spark=spark,
                               render_fn=lambda ep, name: render_run(out, edl_path=ep, out_name=name, title=opts.title,
                                                                     captions=opts.captions, ledger=ledger, log=log))
        except Exception:
            errors.append(("review", traceback.format_exc()))
            log(f"ERROR in review loop:\n{errors[-1][1]}")

    summary = {
        "out": str(out), "runtime_s": edl["runtime_s"], "spans": len(edl["spans"]), "verbatim_rate": edl["verbatim_rate"],
        "unplaced": len(edl["unplaced"]), "turns": len(voice.turns), "words": len(voice.words), "speakers": voice.speakers,
        "tracks": {c: len(t.objects) for c, t in tracks.items()},
        "watch_suggested_concepts": watch.parsed.get("trackable_concepts", []), "speech_rate_wps": wps,
        "cut": rendered["output"] if rendered else None,
        "review": {k: loop[k] for k in ("stop", "reviews", "review_cost_usd", "best", "last_edl")} if loop else None,
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
               title: bool = True, captions: bool = True, callouts: bool = True, loudnorm: bool = True,
               ledger: Ledger | None = None, log=None, punch: bool | None = None) -> dict:
    """`montivo render runs/001`: ffmpeg only, from an existing EDL and the run's voice.json and tracks; appends a
    `render` entry to the run's ledger. The only possible API call is the title card, when title/title_card.png is
    missing ($0.01, muse-image-1.0); --no-title skips it. `punch` True or False is stored in the EDL (`punch`), so
    the review loop and the human edits keep it; None keeps what the EDL says."""
    edl_path = edl_path or run_dir / "edl_v1.json"
    if not edl_path.is_file():
        raise SystemExit(f"{edl_path} not found: run `montivo run` first")
    edl = json.loads(edl_path.read_text())
    source = source or Path(edl["source"]["path"])
    if not source.is_file():
        raise SystemExit(f"source footage {source} not found (run from the project root or pass --source)")
    log = log or RunLog(run_dir / "run.log")
    ledger = ledger or Ledger.resume(run_dir / "ledger.json")  # the review loop passes its own, one writer per file
    log(f"montivo render: {edl_path} -> {run_dir / out_name}")
    words = None
    vpath = run_dir / "voice" / "voice.json"
    if captions and vpath.is_file():
        words = json.loads(vpath.read_text())["words"]
    elif captions:
        log(f"render: {vpath} missing, no captions")
    # the review loop can drop the opening card (swap_hook on the title): the EDL then says title_card false
    title = title and edl.get("title_card", True)
    title_png = title_card(run_dir, edl, ledger, log) if title and edl.get("title_card_text") else None
    if punch is not None:
        edl["punch"] = punch
    info = render_edl(run_dir, edl, source, ledger, out_name=out_name, log=log, edl_path=edl_path, words=words,
                      title_png=title_png, captions=captions, callouts=callouts, loudnorm=loudnorm,
                      punch=bool(edl.get("punch")))
    return {"out": info["output"], "runtime_s": edl["runtime_s"], "duration_s": info["duration_s"], "segments": len(info["segments"]),
            "captions": info["captions"], "callouts": info["callouts"], "loudness": info["loudness_ebur128"],
            "seconds": info["seconds"], "cost_usd": ledger.total_cost()}


def edges_run(run_dir: Path, edl_path: Path | None = None, dry_run: bool = False, log=None) -> dict:
    """`montivo edges runs/001`: the edge pass (engine/edges.py) on an EDL already in a run folder (one built before
    the pass existed, or patched by hand). The EDL is rewritten in place, the old one kept under check/, and the
    render-time fields are cleared so the next render or review renders it again. $0, ffmpeg not needed."""
    edl_path = edl_path or run_dir / "edl_v1.json"
    if not edl_path.is_file():
        raise SystemExit(f"{edl_path} not found")
    audio = Audio.load(run_dir)
    if audio is None:
        raise SystemExit(f"{run_dir / 'audio.wav'} not found (ingest writes it)")
    log = log or RunLog(run_dir / "run.log")
    edl = json.loads(edl_path.read_text())
    words = Words(json.loads((run_dir / "voice" / "voice.json").read_text())["words"])
    before = edl["runtime_s"]
    summary = tighten_edges(edl, words, audio)
    for m in summary["moves"]:
        why = "clipped, widened to the gap" if m["widened"] else f"speech {m['speech']:.3f}"
        log(f"edges: {m['span']} {m['edge']:3s} {m['from']:8.3f} -> {m['to']:8.3f} ({why}"
            f"{', fragment skipped' if m['fragment'] else ''}; {m['db_before']:.1f} -> {m['db_after']:.1f} dB)")
    log(f"edges: {summary['moved']} edges moved ({summary['widened']} widened), runtime {before} -> {edl['runtime_s']} s"
        + (" (dry run, nothing written)" if dry_run else f" -> {edl_path}"))
    if not dry_run and summary["moved"]:
        backup = run_dir / "check" / f"{edl_path.stem}_before_edges.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.is_file():
            backup.write_text(edl_path.read_text())
        for k in ("captions", "callouts", "reframe"):
            edl[k] = []
        edl.pop("render", None)
        dump_json(edl_path, edl)
    return {"edl": str(edl_path), "runtime_before_s": before, **{k: v for k, v in summary.items() if k != "moves"},
            "runtime_s": edl["runtime_s"], "moves": summary["moves"]}
