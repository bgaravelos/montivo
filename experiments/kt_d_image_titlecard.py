#!/usr/bin/env python3
"""Kill test (d): one Muse Image title card, requested 1080x1920 (9:16).

    .venv/bin/python kt_d_image_titlecard.py --prompt "..." [--size 1080x1920] [--strength low]

Reports: returned pixel size (size only sets the aspect ratio per the docs), latency, usage, cost.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import time
from pathlib import Path

import httpx
from PIL import Image

from common import BASE_URL, PRICE, api_key, save

DEFAULT_PROMPT = (
    "Vertical 9:16 social video title card. Bold condensed white headline text 'SECOND CUT' centered, "
    "smaller subtitle 'the editor that watches its own work' below it, on a deep navy background with a "
    "subtle film-grain texture and one thin electric-orange diagonal stripe. Clean, modern, legible on a phone. No people."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--strength", default="low", choices=["low", "high"])
    ap.add_argument("--format", default="png", choices=["png", "jpeg", "webp"])
    ap.add_argument("--no-tools", action="store_true", help="disable web/image search and shell for speed")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    body = {"model": "muse-image-1.0", "prompt": args.prompt, "n": 1, "size": args.size,
            "response_format": "b64_json", "output_format": args.format, "reasoning_strength": args.strength}
    if args.no_tools:
        body["tool_enablement"] = {"enable_image_search": False, "enable_web_search": False, "enable_shell": False}
    t0 = time.perf_counter()
    with httpx.Client(timeout=600) as http:
        r = http.post(f"{BASE_URL}/images/generations", headers={"Authorization": f"Bearer {api_key()}"}, json=body)
    latency = time.perf_counter() - t0
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:1000]}")
        raise SystemExit(1)
    data = r.json()
    item = data["data"][0]
    raw = base64.b64decode(item["b64_json"])
    img = Image.open(io.BytesIO(raw))
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"kt_d_titlecard{'_' + args.tag if args.tag else ''}.{args.format}"
    out.write_bytes(raw)
    summary = {
        "requested_size": args.size, "returned_size": f"{img.width}x{img.height}", "aspect": round(img.width / img.height, 4),
        "format": img.format, "bytes": len(raw), "latency_s": round(latency, 1), "strength": args.strength,
        "tools_disabled": args.no_tools, "usage": data.get("usage"), "revised_prompt": item.get("revised_prompt"),
        "cost_usd": PRICE["image"], "file": str(out), "response_keys": sorted(data.keys()),
    }
    save(f"kt_d_image{'_' + args.tag if args.tag else ''}", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "revised_prompt"}, indent=2))


if __name__ == "__main__":
    main()
