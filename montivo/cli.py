"""montivo CLI.

    montivo run footage.mp4 --brief "..." --out runs/001
    montivo render runs/001
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

    rr = sub.add_parser("render", help="re-render cut_v1.mp4 from a run folder's edl_v1.json (ffmpeg only, no API call)")
    rr.add_argument("run_dir", type=Path)
    rr.add_argument("--edl", type=Path, default=None, help="EDL to render (default <run_dir>/edl_v1.json)")
    rr.add_argument("--source", type=Path, default=None, help="source footage (default: the EDL's source path)")
    rr.add_argument("--out", default="cut_v1.mp4", help="output file name inside the run folder (default cut_v1.mp4)")

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
        print(json.dumps(render_run(args.run_dir, edl_path=args.edl, source=args.source, out_name=args.out), indent=2))
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
                          skip_track=args.no_track, resume=args.resume, render=not args.no_render,
                          model=args.model or RunOptions.__dataclass_fields__["model"].default)
        summary = run(opts)
        print(json.dumps(summary, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
