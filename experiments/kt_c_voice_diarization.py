#!/usr/bin/env python3
"""Kill test (c): the clip's audio into muse-voice-transcribe-1.0 with DIARIZATION.

    .venv/bin/python kt_c_voice_diarization.py --video clip.mp4 [--seconds 30] [--start 0]

Reports: turns with startMs/endMs/speaker, audioDurationMs, latency, cost.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path

import httpx

from common import BASE_URL, PRICE, api_key, save


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--rate", type=int, default=24000, choices=[16000, 24000])
    ap.add_argument("--language-bias", action="append", default=None)
    ap.add_argument("--mode", default="DIARIZATION", choices=["DIARIZATION", "ENDPOINTING", "PUSH_TO_TALK"])
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    work = Path(__file__).resolve().parent / "results" / "kt_c_work"
    work.mkdir(parents=True, exist_ok=True)
    wav = work / f"audio_{args.rate}{'_' + str(int(args.seconds)) + 's' if args.seconds else ''}.wav"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(args.start)]
    if args.seconds:
        cmd += ["-t", str(args.seconds)]
    cmd += ["-i", str(args.video), "-vn", "-ac", "1", "-ar", str(args.rate), "-c:a", "pcm_s16le", "-map_metadata", "-1", str(wav)]
    subprocess.run(cmd, check=True)
    size = wav.stat().st_size
    print(f"wav: {size/1e6:.1f} MB at {args.rate} Hz mono 16-bit")

    request = {"mode": args.mode, "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV"}
    if args.language_bias:
        request["languageBias"] = args.language_bias
    t0 = time.perf_counter()
    with httpx.Client(timeout=600) as http:
        r = http.post(
            f"{BASE_URL}/asr/transcribe", params={"sessionId": f"secondcut-{uuid.uuid4().hex[:8]}"},
            headers={"Authorization": f"Bearer {api_key()}"},
            files={"request": (None, json.dumps(request), "application/json"),
                   "audio": ("audio.wav", wav.read_bytes(), "audio/wav")},
        )
    latency = time.perf_counter() - t0
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:1000]}")
        raise SystemExit(1)
    data = r.json()
    turns = data.get("turns", [])
    dur_s = (data.get("audioDurationMs") or 0) / 1000
    summary = {
        "mode": args.mode, "rate": args.rate, "wav_bytes": size, "latency_s": round(latency, 1), "audio_duration_s": dur_s,
        "realtime_factor": round(latency / dur_s, 3) if dur_s else None,
        "cost_usd": round(dur_s / 3600 * PRICE["voice-hour"], 5),
        "turns": len(turns), "speakers": sorted({t.get("speaker", "?") for t in turns}),
        "turn_duration_s": {"min": min((t["endMs"] - t["startMs"]) / 1000 for t in turns) if turns else None,
                            "max": max((t["endMs"] - t["startMs"]) / 1000 for t in turns) if turns else None},
        "response_keys": sorted(data.keys()), "turn_keys": sorted(turns[0].keys()) if turns else [],
        "response": data,
    }
    save(f"kt_c_voice{'_' + args.tag if args.tag else ''}", summary)
    print(json.dumps({k: summary[k] for k in ("latency_s", "audio_duration_s", "realtime_factor", "cost_usd", "turns", "speakers", "turn_duration_s", "turn_keys")}, indent=2))
    for t in turns:
        print(f"  [{t['startMs']/1000:7.2f} - {t['endMs']/1000:7.2f}] {t.get('speaker','?')}: {t['transcript'][:90]}")


if __name__ == "__main__":
    main()
