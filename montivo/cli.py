"""montivo CLI.

    montivo run footage.mp4 --brief "..." --out runs/001
    montivo render runs/001
    montivo review runs/001
    montivo speakers runs/001
    montivo plan runs/001 --planner open
    montivo probe footage.mp4
    montivo serve
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="montivo", description="Montivo: agent-first video editing on Meta models.")
    ap.add_argument("--version", action="version", version=f"montivo {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="ingest, transcribe, track, watch, plan -> run folder with edl_v1.json and ledger.json")
    r.add_argument("footage", type=Path)
    r.add_argument("--brief", required=True, help="what the cut should be, in one or two sentences")
    r.add_argument("--out", type=Path, default=None, help="run folder (default runs/<timestamp>)")
    r.add_argument("--target", type=float, default=40.0, help="target runtime of the cut in seconds (default 40)")
    r.add_argument("--concept", action="append", default=None, help="SAM concept to track (repeatable, default: person)")
    r.add_argument("--track-fps", type=float, default=None, help="resample the clip to this fps before SAM (default 5, the head track's rate, so both share one upload)")
    r.add_argument("--reframe-concept", default="head", help="SAM concept the 9:16 crop follows (default head; 'none' = person track)")
    r.add_argument("--reframe-fps", type=float, default=5.0, help="track fps for the reframe concept (default 5)")
    r.add_argument("--language", default="English", help="Voice languageBias (default English, 'none' to disable)")
    r.add_argument("--no-track", action="store_true", help="skip SAM")
    r.add_argument("--plan-with-video", action="store_true", help="attach the proxy to the plan call too (watch already saw it)")
    r.add_argument("--plan-effort", default=None, choices=["low", "medium", "high"], help="reasoning effort for the plan call")
    r.add_argument("--resume", action="store_true",
                   help="reuse ingest, voice, watch and tracks already in --out, and plan.json + edl_v1.json when the "
                        "brief, target and planner are the ones it was made with")
    r.add_argument("--replan", action="store_true", help="with --resume: make a new plan even when nothing changed "
                                                         "(the old plan.json and edl_v1.json go to superseded/)")
    r.add_argument("--model", default=None, help="Spark model id (default muse-spark-1.3)")
    r.add_argument("--planner", choices=("spark", "open"), default="spark",
                   help="who writes the paper edit: spark = the --model above, open = Muse Spark Open on Baseten "
                        "(text only, needs BASETEN_API_KEY); watch and review stay on Spark 1.3")
    r.add_argument("--no-render", action="store_true", help="stop after edl_v1.json, do not render cut_v1.mp4")
    r.add_argument("--hflip", action="store_true",
                   help="mirror the footage once before everything else (selfie / mirror-mode clips with reversed text); "
                        "writes <out>/source_hflip.mp4 (libx264 crf 18) and uses it as the source for proxy, wav, SAM and render")
    r.add_argument("--no-title", action="store_true", help="no Muse Image title card in front of the cut (saves $0.01)")
    r.add_argument("--no-captions", action="store_true", help="render without burned-in captions")
    r.add_argument("--punch", action="store_true", help="punch-in zooms on stressed words and an alternating scale "
                                                        "across jump cuts (kept by the review loop and later edits)")
    r.add_argument("--review", action="store_true",
                   help="after cut_v1: blind Spark review -> fix ops -> edl_v2 -> cut_v2 ..., up to --review-passes reviews")
    r.add_argument("--hook-ab", action="store_true",
                   help="with --review: after the loop, a hook A/B on its best version (one or two more reviews, $0.10 cap)")
    r.add_argument("--review-passes", type=int, default=3, help="max reviews in the loop (default 3)")
    r.add_argument("--review-budget", type=float, default=0.30, help="max dollars for the loop's review calls (default 0.30)")

    rr = sub.add_parser("render", help="re-render a run folder's edl_v1.json with captions, callouts, title card and loudnorm (ffmpeg only; the title card is generated once if missing)")
    rr.add_argument("run_dir", type=Path)
    rr.add_argument("--edl", type=Path, default=None, help="EDL to render (default <run_dir>/edl_v1.json)")
    rr.add_argument("--source", type=Path, default=None, help="source footage (default: the EDL's source path)")
    rr.add_argument("--out", default="cut_v1.mp4", help="output file name inside the run folder (default cut_v1.mp4)")
    rr.add_argument("--no-title", action="store_true", help="no title card (otherwise title/title_card.png, generated once if missing, $0.01)")
    rr.add_argument("--no-captions", action="store_true", help="no burned-in captions")
    rr.add_argument("--no-callouts", action="store_true", help="no callouts")
    rr.add_argument("--no-loudnorm", action="store_true", help="keep the source loudness")
    pg = rr.add_mutually_exclusive_group()
    pg.add_argument("--punch", dest="punch", action="store_const", const=True, default=None,
                    help="punch-in zooms on stressed words and jump cuts; stored in the EDL so later renders keep it")
    pg.add_argument("--no-punch", dest="punch", action="store_const", const=False, help="turn the punch-ins off again")

    rv = sub.add_parser("review", help="blind review loop on a run folder: review cut_vN with Spark 1.3, patch the EDL with "
                                       "the fix ops, render cut_vN+1, repeat (max passes, $ budget, same fix twice = stop)")
    rv.add_argument("run_dir", type=Path)
    rv.add_argument("--edl", type=Path, default=None, help="EDL to start from (default <run_dir>/edl_v1.json; cut_vN.mp4 is "
                                                         "rendered first when it is missing or stale)")
    rv.add_argument("--passes", type=int, default=3, help="max reviews (default 3; 1 = review once, propose fixes, no patch)")
    rv.add_argument("--budget", type=float, default=0.30, help="max dollars for review calls (default 0.30)")
    rv.add_argument("--no-clock", action="store_true", help="do not burn the cut-time clock into the copy Spark watches")
    rv.add_argument("--no-apply", action="store_true", help="review once and write the proposed fixes, no patch, no render")
    rv.add_argument("--hook-ab", action="store_true", help="after the loop, a hook A/B on its best version (one more review)")
    rv.add_argument("--hook-budget", type=float, default=0.10, help="max dollars for the hook A/B reviews (default 0.10)")

    hk = sub.add_parser("hook", help="hook A/B: the next version opens on another line, the blind reviewer judges both, the "
                                     "better one is kept (two reviews, one when the version was reviewed already)")
    hk.add_argument("run_dir", type=Path)
    hk.add_argument("--edl", type=Path, default=None, help="version A (default: the newest edl_vN.json)")
    hk.add_argument("--budget", type=float, default=0.10, help="max dollars for the review calls (default 0.10)")
    hk.add_argument("--no-clock", action="store_true", help="do not burn the cut-time clock into the copy Spark watches")

    ed = sub.add_parser("edges", help="tighten a run folder's EDL to the speech onset and offset in the source audio "
                                      "($0; new runs get this in edl_v1 already); the old EDL is kept under check/")
    ed.add_argument("run_dir", type=Path)
    ed.add_argument("--edl", type=Path, default=None, help="EDL to tighten in place (default <run_dir>/edl_v1.json)")
    ed.add_argument("--dry-run", action="store_true", help="print the moves, write nothing")

    pl = sub.add_parser("plan", help="a fresh paper edit for an existing run folder from its own brief, voice and watch "
                                     "(one plan call, no Voice / SAM) -> the next edl_vN and cut_vN; the planner A/B")
    pl.add_argument("run_dir", type=Path)
    pl.add_argument("--planner", choices=("spark", "open"), default="open",
                    help="open = Muse Spark Open on Baseten (default, needs BASETEN_API_KEY), spark = muse-spark-1.3")
    pl.add_argument("--target", type=float, default=None, help="runtime target in seconds (default: run.json)")
    pl.add_argument("--plan-effort", default=None, choices=["low", "medium", "high"],
                    help="reasoning effort for the plan call (default: low on the open model, the model's own on Spark)")
    pl.add_argument("--review", type=int, default=0, help="review passes after the render (default 0; reviews run on Spark 1.3)")
    pl.add_argument("--budget", type=float, default=0.15, help="max dollars for the review calls (default 0.15)")

    sp = sub.add_parser("speakers", help="map Voice speakers to SAM head ids from mouth motion ($0, ffmpeg) -> "
                                         "speakers.json; the next render follows whoever talks")
    sp.add_argument("run_dir", type=Path)

    p = sub.add_parser("probe", help="ffprobe summary of a file")
    p.add_argument("footage", type=Path)

    sv = sub.add_parser("serve", help="the UI: run folders, live log, review room, drop span / re-plan (http://127.0.0.1:8765)")
    sv.add_argument("--root", type=Path, default=Path.cwd(), help="project folder with runs/ and footage/ (default: here)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)

    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from .server.app import main as serve
        return serve(["--root", str(args.root), "--host", args.host, "--port", str(args.port)])

    if args.cmd == "probe":
        from .engine.ingest import ffprobe
        print(json.dumps(ffprobe(args.footage).__dict__, indent=2))
        return 0

    if args.cmd == "render":
        from .run import render_run
        if not args.run_dir.is_dir():
            ap.error(f"{args.run_dir} is not a run folder")
        print(json.dumps(render_run(args.run_dir, edl_path=args.edl, source=args.source, out_name=args.out,
                                    title=not args.no_title, captions=not args.no_captions,
                                    callouts=not args.no_callouts, loudnorm=not args.no_loudnorm, punch=args.punch),
                         indent=2))
        return 0

    if args.cmd == "plan":
        from .config import OPEN_MODEL, SPARK_MODEL
        from .human import replan
        if not args.run_dir.is_dir():
            ap.error(f"{args.run_dir} is not a run folder")
        model = OPEN_MODEL if args.planner == "open" else SPARK_MODEL
        print(json.dumps(replan(args.run_dir, "", target_s=args.target, passes=args.review, budget=args.budget,
                                model=model, reasoning_effort=args.plan_effort), indent=2))
        return 0

    if args.cmd == "speakers":
        from .engine.speakers import speakers_run
        from .run import RunLog
        if not args.run_dir.is_dir():
            ap.error(f"{args.run_dir} is not a run folder")
        try:
            res = speakers_run(args.run_dir, log=RunLog(args.run_dir / "run.log"))
        except FileNotFoundError as exc:
            ap.error(str(exc))
        print(json.dumps(res["speakers"], indent=2))
        return 0

    if args.cmd == "edges":
        from .run import edges_run
        if not args.run_dir.is_dir():
            ap.error(f"{args.run_dir} is not a run folder")
        edl = args.edl if args.edl is None or args.edl.is_absolute() or args.edl.is_file() else args.run_dir / args.edl
        print(json.dumps(edges_run(args.run_dir, edl_path=edl, dry_run=args.dry_run), indent=2))
        return 0

    if args.cmd == "review":
        from .engine.review import review_loop
        from .run import RunLog
        if not args.run_dir.is_dir():
            ap.error(f"{args.run_dir} is not a run folder")
        edl = args.edl if args.edl is None or args.edl.is_absolute() or args.edl.is_file() else args.run_dir / args.edl
        log = RunLog(args.run_dir / "run.log")
        out = review_loop(args.run_dir, edl_path=edl, passes=args.passes, budget_usd=args.budget, log=log,
                          clock=not args.no_clock, apply=not args.no_apply)
        if args.hook_ab and out.get("best"):
            from .engine.hook import hook_ab
            out["hook_ab"] = hook_ab(args.run_dir, args.run_dir / f"edl_v{out['best']['version']}.json",
                                     budget_usd=args.hook_budget, log=log, clock=not args.no_clock)
        print(json.dumps(out, indent=2))
        return 0

    if args.cmd == "hook":
        from .engine.hook import hook_ab
        from .run import RunLog
        if not args.run_dir.is_dir():
            ap.error(f"{args.run_dir} is not a run folder")
        edl = args.edl if args.edl is None or args.edl.is_absolute() or args.edl.is_file() else args.run_dir / args.edl
        print(json.dumps(hook_ab(args.run_dir, edl_path=edl, budget_usd=args.budget,
                                 log=RunLog(args.run_dir / "run.log"), clock=not args.no_clock), indent=2))
        return 0

    if args.cmd == "run":
        from .config import OPEN_MODEL
        from .run import RunOptions, run
        if not args.footage.is_file():
            ap.error(f"{args.footage} is not a file")
        if args.hook_ab and not args.review:
            ap.error("--hook-ab compares two reviewed cuts: add --review")
        if args.replan and not args.resume:
            ap.error("--replan goes with --resume (a run without --resume always plans)")
        if args.planner == "open" and args.plan_with_video:
            ap.error("--planner open plans from text only; drop --plan-with-video")
        out = args.out or Path("runs") / time.strftime("%Y%m%d-%H%M%S")
        opts = RunOptions(footage=args.footage, brief=args.brief, out=out, concepts=args.concept or ["person"],
                          track_fps=args.track_fps,
                          reframe_concept=None if args.reframe_concept.lower() == "none" else args.reframe_concept,
                          reframe_fps=args.reframe_fps, language=None if args.language.lower() == "none" else args.language,
                          target_s=args.target, plan_with_video=args.plan_with_video, plan_effort=args.plan_effort,
                          skip_track=args.no_track, resume=args.resume, replan=args.replan, render=not args.no_render, hflip=args.hflip,
                          title=not args.no_title, captions=not args.no_captions, review=args.review,
                          review_passes=args.review_passes, review_budget=args.review_budget, punch=args.punch,
                          hook_ab=args.hook_ab,
                          model=args.model or RunOptions.__dataclass_fields__["model"].default,
                          planner_model=OPEN_MODEL if args.planner == "open" else None)
        summary = run(opts)
        print(json.dumps(summary, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
