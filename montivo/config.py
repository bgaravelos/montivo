"""Endpoints, model ids and list prices. Nothing here is secret."""
from __future__ import annotations

import os

BASE_URL = os.environ.get("MONTIVO_BASE_URL", "https://api.meta.ai/v1")

SPARK_MODEL = "muse-spark-1.3"
VOICE_MODEL = "muse-voice-transcribe-1.0"
SAM_MODEL = "sam-3.1"
IMAGE_MODEL = "muse-image-1.0"

# USD, from dev.meta.ai/docs/pricing-rate-limits (read 26/10/2026)
PRICE = {
    "muse-spark-1.3": {"in": 1.25, "out": 4.25, "cached": 0.15},  # per 1M tokens
    "sam-video-frame": 0.20 / 1000,  # per processed frame, minimum 50 frames per request
    "voice-hour": 0.18,  # per hour of audio
    "image": 0.01,  # per generated image
}

# Measured limits that shape the pipeline (see docs/measurements.md)
VOICE_MAX_SECONDS = 600  # 10 min per request
VOICE_MAX_BYTES = 32 * 1024 * 1024  # 32 MB per request
VOICE_LAG_MS = 500  # word deltas arrive about 0.5 s after the audio they describe
VOICE_SNAP_MS = 400  # snap word edges to speechStart/speechEnd inside this window
VOICE_CHUNK_TARGET_S = 480  # when a file must be split, aim for 8 min chunks
PROXY_MAX_DIM = 960
# Every SAM concept is tracked at 5 fps, the rate of the head track the crop follows, so person and head share one
# resampled upload and one batch. SAM is billed per frame: person at 10 fps was $0.33 of run 013's $0.69 (166 s);
# the person box only anchors callouts and is the reframe fallback, both smoothed over half a second or more.
TRACK_RESAMPLE_FPS = 5.0
# The 9:16 reframe follows the head, not the body: on a lean the head moves 3x more than the body box centre
# (measured 26/10 on Billy's clip). 5 fps is enough for a smoothed path and costs $0.125 per 125 s.
REFRAME_CONCEPT = "head"
REFRAME_TRACK_FPS = 5.0


def api_key() -> str:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise SystemExit("MODEL_API_KEY is not set. Export it (dev.meta.ai) before running montivo.")
    return key


def spark_cost(usage: dict, model: str = SPARK_MODEL) -> float:
    p = PRICE[model]
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
    inp = (usage.get("input_tokens") or 0) - cached
    out = usage.get("output_tokens") or 0
    return (inp * p["in"] + cached * p["cached"] + out * p["out"]) / 1_000_000


def sam_cost(frames: int) -> float:
    return max(50, frames) * PRICE["sam-video-frame"]


def voice_cost(seconds: float) -> float:
    return seconds / 3600 * PRICE["voice-hour"]
