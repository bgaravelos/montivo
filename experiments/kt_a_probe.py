#!/usr/bin/env python3
"""Free-text probe: does Spark actually see and hear this video? No schema, plain prose answer."""
import argparse, json, time
from common import client, save, spark_cost

ap = argparse.ArgumentParser()
ap.add_argument("--file-id", required=True)
ap.add_argument("--model", default="muse-spark-1.2")
ap.add_argument("--part", default="input_file", choices=["input_file", "input_video"])
ap.add_argument("--tag", default="probe")
args = ap.parse_args()
prompt = ("Watch this video with its audio. 1) Describe what is on screen in the first 10 seconds and at 60, 90 and 120 seconds. "
          "2) Quote the first five spoken sentences with the second each starts. 3) How long is the video? "
          "If you cannot see or hear something, say so explicitly.")
t0 = time.perf_counter()
with client() as api:
    r = api.responses.create(model=args.model, max_output_tokens=4000,
        input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": args.part, "file_id": args.file_id}]}])
lat = time.perf_counter() - t0
u = r.usage.model_dump()
out = {"model": args.model, "part": args.part, "file_id": args.file_id, "latency_s": round(lat, 1), "usage": u, "cost_usd": round(spark_cost(args.model, u), 4), "status": r.status, "text": r.output_text}
save(f"kt_a_probe_{args.model}_{args.part}_{args.tag}", out)
print(json.dumps({k: out[k] for k in ("latency_s", "cost_usd", "status")}), "\n", r.output_text[:2500])
