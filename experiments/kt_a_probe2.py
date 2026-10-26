#!/usr/bin/env python3
"""Probe 2: what does Spark actually receive from a video input? Frames count, frame times, transcript form."""
import argparse, json, time
from common import client, save, spark_cost

ap = argparse.ArgumentParser()
ap.add_argument("--file-id", required=True)
ap.add_argument("--model", default="muse-spark-1.2")
ap.add_argument("--part", default="input_file", choices=["input_file", "input_video"])
ap.add_argument("--tag", default="nasa")
args = ap.parse_args()
prompt = ("I am debugging my pipeline and need to know exactly what you received for this video input. "
          "Answer precisely: (1) How many separate images or frames were you given? Describe each one in one line, in order, "
          "and say whether each carries a time label. (2) Was an audio transcript provided to you as text? If yes, copy its first 300 characters EXACTLY "
          "as you received them, including any timestamps or speaker markers, and say whether it has per-word, per-sentence or no timestamps. "
          "(3) Did you receive raw audio you can listen to, or only text? (4) What total duration, if any, were you told? Do not guess; say 'not provided' where applicable.")
t0 = time.perf_counter()
with client() as api:
    r = api.responses.create(model=args.model, max_output_tokens=3000,
        input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": args.part, "file_id": args.file_id}]}])
lat = time.perf_counter() - t0
u = r.usage.model_dump()
out = {"model": args.model, "part": args.part, "file_id": args.file_id, "latency_s": round(lat, 1), "usage": u, "cost_usd": round(spark_cost(args.model, u), 4), "status": r.status, "text": r.output_text}
save(f"kt_a_probe2_{args.model}_{args.part}_{args.tag}", out)
print(json.dumps({k: out[k] for k in ("latency_s", "cost_usd", "status")}), "input_tokens", u["input_tokens"], "\n", r.output_text[:3500])
