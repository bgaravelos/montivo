#!/usr/bin/env python3
"""Kill test (a): a 2 to 3 minute English mp4 into Muse Spark, timestamped shot log as JSON schema output.

    .venv/bin/python kt_a_spark_shotlog.py --video clip.mp4 --model muse-spark-1.2
    .venv/bin/python kt_a_spark_shotlog.py --video clip.mp4 --model muse-spark-1.3 --file-id file-...

Reports: input/output tokens, latency, cost, and the shot log for a manual timestamp check.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from common import client, ffprobe, save, spark_cost, upload

SHOT_LOG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "video_duration_s": {"type": "number", "description": "Total duration you observed, in seconds"},
        "speakers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["label", "description"],
            },
        },
        "shots": {
            "type": "array",
            "description": "One entry per camera shot or visual change, in order",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "shot_type": {"type": "string", "enum": ["talking_head", "b_roll", "screen", "wide", "closeup", "other"]},
                    "visual": {"type": "string", "description": "What is on screen"},
                    "speech": {"type": "string", "description": "What is said in this span, verbatim if short"},
                    "speaker": {"type": "string"},
                    "objects": {"type": "array", "items": {"type": "string"}, "description": "Nameable objects visible, noun phrases"},
                },
                "required": ["start_s", "end_s", "shot_type", "visual", "speech", "speaker", "objects"],
            },
        },
        "transcript_anchors": {
            "type": "array",
            "description": "Exactly 8 short verbatim quotes spread across the video with the second they start",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"t_s": {"type": "number"}, "quote": {"type": "string"}},
                "required": ["t_s", "quote"],
            },
        },
        "paper_edit": {
            "type": "array",
            "description": "A 30 to 45 second vertical cut: ordered source spans to keep",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "in_s": {"type": "number"},
                    "out_s": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["in_s", "out_s", "why"],
            },
        },
    },
    "required": ["video_duration_s", "speakers", "shots", "transcript_anchors", "paper_edit"],
}

PROMPT = (
    "You are a video editor's assistant. Watch this video with its audio. "
    "Return a shot log with precise timestamps in seconds from the start of the file, "
    "a list of speakers, 8 verbatim transcript anchors spread evenly across the whole video "
    "with the exact second each quote starts, and a paper edit for a 30 to 45 second vertical "
    "social cut that keeps the strongest hook first. Timestamps must be accurate to the second."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--model", default="muse-spark-1.2")
    ap.add_argument("--file-id", help="reuse an uploaded file id")
    ap.add_argument("--part", default="input_file", choices=["input_file", "input_video"])
    ap.add_argument("--effort", default=None, help="reasoning effort (minimal/low/medium/high), default = API default")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    probe = ffprobe(args.video)
    print(f"video: {probe['duration']:.1f}s {probe['width']}x{probe['height']} {probe['fps']:.2f}fps {probe['bytes']/1e6:.1f} MB")

    if args.file_id:
        file_id, up_s = args.file_id, 0.0
    else:
        file_id, up_s = upload(args.video, "video/mp4")
        print(f"uploaded {file_id} in {up_s:.1f}s")

    media = {"type": args.part, "file_id": file_id}
    body = dict(
        model=args.model,
        input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": PROMPT}, media]}],
        text={"format": {"type": "json_schema", "name": "shot_log", "strict": True, "schema": SHOT_LOG_SCHEMA}},
        max_output_tokens=16000,
    )
    if args.effort:
        body["reasoning"] = {"effort": args.effort}

    t0 = time.perf_counter()
    with client() as api:
        resp = api.responses.create(**body)
    latency = time.perf_counter() - t0

    usage = resp.usage.model_dump() if resp.usage else {}
    text = resp.output_text
    try:
        parsed = json.loads(text)
        parse_ok = True
    except json.JSONDecodeError:
        parsed, parse_ok = None, False
    cost = spark_cost(args.model, usage)
    inp = usage.get("input_tokens", 0)
    result = {
        "model": args.model, "part": args.part, "effort": args.effort, "file_id": file_id,
        "video": probe, "upload_s": round(up_s, 1), "latency_s": round(latency, 1),
        "usage": usage, "tokens_per_video_second": round(inp / probe["duration"], 1) if probe["duration"] else None,
        "cost_usd": round(cost, 4), "parse_ok": parse_ok, "status": resp.status,
        "incomplete": resp.incomplete_details.model_dump() if getattr(resp, "incomplete_details", None) else None,
        "shot_log": parsed, "raw_text": None if parse_ok else text[:4000],
    }
    name = f"kt_a_{args.model}_{args.part}{'_' + args.tag if args.tag else ''}"
    save(name, result)
    print(json.dumps({k: result[k] for k in ("latency_s", "usage", "tokens_per_video_second", "cost_usd", "parse_ok", "status")}, indent=2))
    if parsed:
        print(f"shots: {len(parsed['shots'])}, anchors: {len(parsed['transcript_anchors'])}, paper edit spans: {len(parsed['paper_edit'])}")
        for a in parsed["transcript_anchors"]:
            print(f"  anchor {a['t_s']:7.1f}s  {a['quote'][:80]}")


if __name__ == "__main__":
    main()
