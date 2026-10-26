#!/usr/bin/env python3
"""Fast path: pull per-frame boxes out of a SAM SSE file with a regex (no mask decoding). Masks stay as opaque payloads
for the frames that need them. Reports timing so we know what the build should use."""
import argparse, json, re, statistics, time
from collections import defaultdict
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--sse", type=Path, required=True)
ap.add_argument("--fps", type=float, default=30)
args = ap.parse_args()
t0 = time.perf_counter()
text_parts = []
for block in args.sse.read_text().replace("\r\n", "\n").split("\n\n"):
    payload = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
    if payload and payload != "[DONE]":
        e = json.loads(payload)
        if e.get("type") == "response.output_text.delta":
            text_parts.append(e["delta"])
text = "".join(text_parts)
t1 = time.perf_counter()
frame_re = re.compile(r"<(\d+)f>")
box_re = re.compile(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([0-9.]+))?\|>")
tracks = defaultdict(list)
frames = 0
for line in text.split("\n"):
    m = frame_re.match(line)
    if not m:
        continue
    frames += 1
    f = int(m.group(1))
    for b in box_re.finditer(line):
        oid, x1, y1, x2, y2, w, h = b.group(1), *map(int, b.group(2, 3, 4, 5, 6, 7))
        tracks[oid].append((f, x1, y1, x2, y2, w, h))
t2 = time.perf_counter()
print(f"sse->text {t1-t0:.2f}s, regex boxes {t2-t1:.2f}s, frames {frames}, tracks {len(tracks)}, text chars {len(text)}")
for oid, recs in sorted(tracks.items(), key=lambda kv: int(kv[0])):
    recs.sort()
    idx = [r[0] for r in recs]
    cx = [(r[1] + r[3]) / 2 for r in recs]
    jit = [abs(b - a) for a, b in zip(cx, cx[1:])]
    print(f"  track {oid}: frames {idx[0]}-{idx[-1]} ({idx[0]/args.fps:.1f}s-{idx[-1]/args.fps:.1f}s) visible {len(set(idx))} coverage {len(set(idx))/(idx[-1]-idx[0]+1):.2f} "
          f"cx median {statistics.median(cx):.0f} jitter median {statistics.median(jit) if jit else 0:.1f}px p95 {sorted(jit)[int(len(jit)*0.95)] if len(jit)>1 else 0:.1f}px "
          f"source {recs[0][5]}x{recs[0][6]} box {statistics.mean(r[3]-r[1] for r in recs):.0f}x{statistics.mean(r[4]-r[2] for r in recs):.0f}")
