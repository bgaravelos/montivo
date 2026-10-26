#!/usr/bin/env python3
"""Voice Transcribe over SSE with DELTA partials: do partial events give sub-turn (pseudo word-level) timing?"""
import argparse, json, subprocess, time, uuid
from pathlib import Path
import httpx
from common import BASE_URL, api_key, save

ap = argparse.ArgumentParser()
ap.add_argument("--video", type=Path, required=True)
ap.add_argument("--start", type=float, default=0)
ap.add_argument("--seconds", type=float, default=30)
ap.add_argument("--mode", default="DIARIZATION")
ap.add_argument("--tag", default="stream")
args = ap.parse_args()
work = Path(__file__).resolve().parent / "results" / "kt_c_work"; work.mkdir(parents=True, exist_ok=True)
wav = work / f"stream_{int(args.start)}_{int(args.seconds)}s.wav"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(args.start), "-t", str(args.seconds), "-i", str(args.video), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", "-map_metadata", "-1", str(wav)], check=True)
request = {"mode": args.mode, "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "languageBias": ["English"], "partialMode": "DELTA", "emitAudioProgress": True}
events = []
t0 = time.perf_counter()
with httpx.Client(timeout=600) as http, http.stream("POST", f"{BASE_URL}/asr/transcribe", params={"sessionId": f"sc-{uuid.uuid4().hex[:8]}"},
        headers={"Authorization": f"Bearer {api_key()}", "Accept": "text/event-stream"},
        files={"request": (None, json.dumps(request), "application/json"), "audio": ("audio.wav", wav.read_bytes(), "audio/wav")}) as r:
    print("http", r.status_code)
    buf = ""
    for chunk in r.iter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            data = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
            if data:
                try:
                    events.append({"t_wall": round(time.perf_counter() - t0, 2), **json.loads(data)})
                except json.JSONDecodeError:
                    events.append({"t_wall": round(time.perf_counter() - t0, 2), "raw": data[:200]})
lat = time.perf_counter() - t0
types = {}
for e in events:
    types[e.get("type", "?")] = types.get(e.get("type", "?"), 0) + 1
partials = [e for e in events if "partial" in str(e.get("type", "")).lower() or "transcript" in e and e.get("type") not in ("speechComplete",)]
save(f"kt_c_voice_stream_{args.tag}", {"latency_s": round(lat, 1), "audio_s": args.seconds, "event_types": types, "events": events})
print(json.dumps({"latency_s": round(lat, 1), "audio_s": args.seconds, "event_types": types}, indent=1))
for e in events[:60]:
    print({k: (v[:60] if isinstance(v, str) else v) for k, v in e.items()})
