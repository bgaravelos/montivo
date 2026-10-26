#!/usr/bin/env python3
"""Draw SAM boxes on sampled frames of the prepared clip, tiled into one PNG, for a visual sanity check."""
import argparse, json, re, subprocess, tempfile
from collections import defaultdict
from pathlib import Path
from PIL import Image, ImageDraw

ap = argparse.ArgumentParser()
ap.add_argument("--sse", type=Path, required=True)
ap.add_argument("--video", type=Path, required=True)
ap.add_argument("--frames", default="0,150,300,450,600,750,899")
ap.add_argument("--out", type=Path, required=True)
args = ap.parse_args()
text = "".join(json.loads("\n".join(l[5:].lstrip() for l in b.split("\n") if l.startswith("data:")))["delta"]
               for b in args.sse.read_text().split("\n\n") if '"response.output_text.delta"' in b)
boxes = defaultdict(list)
for line in text.split("\n"):
    m = re.match(r"<(\d+)f>", line)
    if not m: continue
    for b in re.finditer(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)", line):
        boxes[int(m.group(1))].append((b.group(1), *map(int, b.group(2, 3, 4, 5))))
want = [int(x) for x in args.frames.split(",")]
colors = ["#ff5a00", "#00d0ff", "#ffe600", "#ff00c8", "#39ff14", "#ffffff"]
tiles = []
with tempfile.TemporaryDirectory() as td:
    for f in want:
        png = Path(td) / f"f{f}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(args.video), "-vf", f"select=eq(n\\,{f})", "-frames:v", "1", str(png)], check=True)
        im = Image.open(png).convert("RGB"); d = ImageDraw.Draw(im)
        for oid, x1, y1, x2, y2 in boxes.get(f, []):
            c = colors[int(oid) % len(colors)]
            d.rectangle((x1, y1, x2, y2), outline=c, width=4); d.text((x1 + 6, y1 + 6), f"id {oid}", fill=c)
        d.text((8, im.height - 20), f"frame {f}  boxes {len(boxes.get(f, []))}", fill="#ffffff")
        tiles.append(im.resize((im.width // 2, im.height // 2)))
w = sum(t.width for t in tiles); h = max(t.height for t in tiles)
sheet = Image.new("RGB", (w, h)); x = 0
for t in tiles:
    sheet.paste(t, (x, 0)); x += t.width
sheet.save(args.out); print(args.out, sheet.size)
