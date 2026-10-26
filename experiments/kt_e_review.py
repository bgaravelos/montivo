#!/usr/bin/env python3
"""Review pass test: Spark watches a rendered clip and scores it against the Second Cut rubric (JSON schema)."""
import argparse, json, time
from pathlib import Path
from common import client, ffprobe, save, spark_cost, upload

ap = argparse.ArgumentParser()
ap.add_argument("--video", type=Path, required=True)
ap.add_argument("--file-id")
ap.add_argument("--model", default="muse-spark-1.2")
ap.add_argument("--brief", default="A 30 second vertical teaser: why launching from US soil again matters.")
ap.add_argument("--tag", default="nasa30")
args = ap.parse_args()
CRITERIA = ["hook_in_1_5s", "brief_covered", "no_cut_mid_word", "captions_legible", "captions_match_speech", "subject_framed", "callouts_anchored", "pacing", "ending"]
SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "pass": {"type": "boolean"}, "overall": {"type": "integer", "minimum": 0, "maximum": 100},
    "scores": {"type": "object", "additionalProperties": False, "properties": {c: {"type": "integer", "minimum": 0, "maximum": 10} for c in CRITERIA}, "required": CRITERIA},
    "issues": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
        "t_s": {"type": "number"}, "criterion": {"type": "string", "enum": CRITERIA}, "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
        "what": {"type": "string"}, "fix_op": {"type": "string", "enum": ["move_cut", "drop_span", "extend_span", "reframe_bias", "caption_fix", "retime_callout", "swap_hook", "none"]},
        "fix_args": {"type": "string"}}, "required": ["t_s", "criterion", "severity", "what", "fix_op", "fix_args"]}},
    "summary": {"type": "string"}}, "required": ["pass", "overall", "scores", "issues", "summary"]}
prompt = (f"You are the reviewer of an automated video editor. Brief: {args.brief}\nWatch this rendered cut with its audio and score it on each criterion 0 to 10 "
          "(10 = perfect). Be strict and concrete: every issue needs a timestamp and a fix operation. If there are no captions or callouts, score those criteria 0 and say so. "
          "pass = true only if overall >= 80 and no blocker.")
p = ffprobe(args.video)
fid, up_s = (args.file_id, 0.0) if args.file_id else upload(args.video, "video/mp4")
t0 = time.perf_counter()
with client() as api:
    r = api.responses.create(model=args.model, max_output_tokens=8000,
        input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": "input_file", "file_id": fid}]}],
        text={"format": {"type": "json_schema", "name": "review", "strict": True, "schema": SCHEMA}})
lat = time.perf_counter() - t0
u = r.usage.model_dump()
try:
    parsed = json.loads(r.output_text); ok = True
except json.JSONDecodeError:
    parsed, ok = None, False
out = {"model": args.model, "file_id": fid, "video": p, "upload_s": round(up_s, 1), "latency_s": round(lat, 1), "usage": u, "cost_usd": round(spark_cost(args.model, u), 4), "status": r.status, "parse_ok": ok, "review": parsed, "raw": None if ok else r.output_text[:2000]}
save(f"kt_e_review_{args.model}_{args.tag}", out)
print(json.dumps({k: out[k] for k in ("upload_s", "latency_s", "cost_usd", "status", "parse_ok")}), "input_tokens", u["input_tokens"])
if parsed:
    print("pass", parsed["pass"], "overall", parsed["overall"], parsed["scores"])
    for i in parsed["issues"]:
        print(f"  issue {i['t_s']:6.1f}s {i['criterion']:22s} {i['severity']:8s} {i['fix_op']:14s} {i['what'][:80]}")
    print("  summary:", parsed["summary"][:300])
