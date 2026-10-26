"""montivo CLI.

    montivo run footage.mp4 --brief "..." --out runs/001
    montivo render runs/001
    montivo review runs/001
    montivo probe footage.mp4
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
    r.add_argument("--track-fps", type=float, default=None, help="resample the clip to this fps before SAM (default: 10 fps over 60 s)")
    r.add_argument("--reframe-concept", default="head", help="SAM concept the 9:16 crop follows (default head; 'none' = person track)")
    r.add_argument("--reframe-fps", type=float, default=5.0, help="track fps for the reframe concept (default 5)")
    r.add_argument("--language", default="English", help="Voice languageBias (default English, 'none' to disable)")
    r.add_argument("--no-track", action="store_true", help="skip SAM")
    r.add_argument("--plan-with-video", action="store_true", help="attach the proxy to the plan call too (watch already saw it)")
    r.add_argument("--plan-effort", default=None, choices=["low", "medium", "high"], help="reasoning effort for the plan call")
    r.add_argument("--resume", action="store_true", help="reuse ingest, voice, watch and tracks already in --out")
    r.add_argument("--model", default=None, help="Spark model id (default muse-spark-1.3)")
    r.add_argument("--no-render", action="store_true", help="stop after edl_v1.json, do not render cut_v1.mp4")
    r.add_argument("--hflip", action="store_true",
                   help="mirror the footage once before everything else (selfie / mirror-mode clips with reversed text); "
                        "writes <out>/source_hflip.mp4 (libx264 crf 18) and uses it as the source for proxy, wav, SAM and render")
    r.add_argument("--no-title", action="store_true", help="no Muse Image title card in front of the cut (saves $0.01)")
    r.add_argument("--no-captions", action="store_true", help="render without burned-in captions")
    r.add_argument("--review", action="store_true",
                   help="after cut_v1: blind Spark review -> fix ops -> edl_v2 -> cut_v2 ..., up to --review-passes reviews")
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

    rv = sub.add_parser("review", help="blind review loop on a run folder: review cut_vN with Spark 1.3, patch the EDL with "
                                       "the fix ops, render cut_vN+1, repeat (max passes, $ budget, same fix twice = stop)")
    rv.add_argument("run_dir", type=Path)
    rv.add_argument("--edl", type=Path, default=None, help="EDL to start from (default <run_dir>/edl_v1.json; cut_vN.mp4 is "
                                                         "rendered first when it is missing or stale)")
    rv.add_argument("--passes", type=int, default=3, help="max reviews (default 3; 1 = review once, propose fixes, no patch)")
    rv.add_argument("--budget", type=float, default=0.30, help="max dollars for review calls (default 0.30)")
    rv.add_argument("--no-clock", action="store_true", help="do not burn the cut-time clock into the copy Spark watches")
    rv.add_argument("--no-apply", action="store_true", help="review once and write the proposed fixes, no patch, no render")

    ed = sub.add_parser("edges", help="tighten a run folder's EDL to the speech onset and offset in the source audio "
                                      "($0; new runs get this in edl_v1 already); the old EDL is kept under check/")
    ed.add_argument("run_dir", type=Path)
    ed.add_argument("--edl", type=Path, default=None, help="EDL to tighten in place (default <run_dir>/edl_v1.json)")
    ed.add_argument("--dry-run", action="store_true", help="print the moves, write nothing")

    p = sub.add_parser("probe", help="ffprobe summary of a file")
    p.add_argument("footage", type=Path)

    args = ap.parse_args(argv)
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
                                    callouts=not args.no_callouts, loudnorm=not args.no_loudnorm), indent=2))
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
        print(json.dumps(review_loop(args.run_dir, edl_path=edl, passes=args.passes, budget_usd=args.budget, log=log,
                                     clock=not args.no_clock, apply=not args.no_apply), indent=2))
        return 0

    if args.cmd == "run":
        from .run import RunOptions, run
        if not args.footage.is_file():
            ap.error(f"{args.footage} is not a file")
        out = args.out or Path("runs") / time.strftime("%Y%m%d-%H%M%S")
        opts = RunOptions(footage=args.footage, brief=args.brief, out=out, concepts=args.concept or ["person"],
                          track_fps=args.track_fps,
                          reframe_concept=None if args.reframe_concept.lower() == "none" else args.reframe_concept,
                          reframe_fps=args.reframe_fps, language=None if args.language.lower() == "none" else args.language,
                          target_s=args.target, plan_with_video=args.plan_with_video, plan_effort=args.plan_effort,
                          skip_track=args.no_track, resume=args.resume, render=not args.no_render, hflip=args.hflip,
                          title=not args.no_title, captions=not args.no_captions, review=args.review,
                          review_passes=args.review_passes, review_budget=args.review_budget,
                          model=args.model or RunOptions.__dataclass_fields__["model"].default)
        summary = run(opts)
        print(json.dumps(summary, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
