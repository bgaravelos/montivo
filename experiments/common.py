"""Shared helpers for the Second Cut day-1 kill tests (hackathon build, 26/10/2026).

Reads MODEL_API_KEY from the environment or from experiments/.env (never printed).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

BASE_URL = "https://api.meta.ai/v1"
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

# Prices from dev.meta.ai/docs/pricing-rate-limits (read 26/10/2026)
PRICE = {
    "muse-spark-1.3": {"in": 1.25, "out": 4.25, "cached": 0.15},  # per 1M tokens
    "muse-spark-1.2": {"in": 1.25, "out": 4.25, "cached": 0.15},
    "sam-video-frame": 0.20 / 1000,  # min 50 frames per request
    "voice-hour": 0.18,
    "image": 0.01,
}


def api_key() -> str:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        env = HERE / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                line = line.strip().removeprefix("export ")
                if line.startswith("MODEL_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        raise SystemExit("MODEL_API_KEY is not set (env or experiments/.env)")
    return key


def client():
    from openai import OpenAI

    return OpenAI(base_url=BASE_URL, api_key=api_key(), timeout=1800, max_retries=1)


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,avg_frame_rate,nb_read_frames:format=duration,size",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    s = data["streams"][0]
    num, den = s["avg_frame_rate"].split("/")
    fps = float(num) / float(den) if float(den) else float(num)
    return {
        "width": int(s["width"]), "height": int(s["height"]), "fps": fps,
        "frames": int(s.get("nb_read_frames", 0)),
        "duration": float(data["format"]["duration"]),
        "bytes": int(data["format"]["size"]),
    }


def upload(path: Path, mime: str) -> tuple[str, float]:
    """Upload through the Files API (purpose user_data). Returns (file_id, seconds)."""
    t0 = time.perf_counter()
    with client() as api:
        up = api.files.create(file=(path.name, path.read_bytes(), mime), purpose="user_data")
    return up.id, time.perf_counter() - t0


def spark_cost(model: str, usage: dict) -> float:
    p = PRICE[model]
    cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0) or 0
    inp = (usage.get("input_tokens", 0) or 0) - cached
    out = usage.get("output_tokens", 0) or 0
    return (inp * p["in"] + cached * p["cached"] + out * p["out"]) / 1_000_000


def save(name: str, data: dict) -> Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"saved {path}")
    return path
