"""Build the bundled music beds from CC0 tracks: `python3 montivo/music/build.py DOWNLOADS_DIR [--ids a,b]`.

The source files come from OpenGameArt.org, each released there under CC0 by its author (TRACKS below: page, file,
author). They are not in the repo; download them from the pages into one folder and run this. For each track:
leading silence is trimmed, then the loop length is picked between LOOP_MIN_S and LOOP_MAX_S where the track's level
envelope after that point matches its first seconds best (the same place in the groove), and the first XFADE_S of the
loop are crossfaded with what follows the loop point. Played from its start the loop opens like the track does; looped,
the end flows into the start with no seam. Written as AAC (.m4a), measured with ffmpeg ebur128 into tracks.json, which
the render uses to set the bed's level.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_RATE = 100  # envelope frames per second
LOOP_MIN_S, LOOP_MAX_S = 55.0, 85.0
MATCH_S = 8.0  # how much of the opening the loop point has to match
XFADE_S = 0.25
RATE = 48000

TRACKS = [
    {"id": "chill_lofi", "name": "Chill Lofi", "mood": "lo-fi keys and soft drums", "file": "chilllofir-loop.ogg",
     "author": "omfgdude (loop edit by qubodup)", "page": "https://opengameart.org/content/chill-lofi-inspired-loop-edit",
     "whole": True},  # already a seamless loop edit: kept whole
    {"id": "lofi_hip_hop", "name": "Lofi Hip Hop", "mood": "lo-fi hip hop beat", "file": "lofihiphop.ogg",
     "author": "omfgdude", "page": "https://opengameart.org/content/lofi-hip-hop"},
    {"id": "funky_jam", "name": "Funky Jam", "mood": "funky hip hop jam", "file": "CVP701Jam_0.ogg",
     "author": "omfgdude", "page": "https://opengameart.org/content/funky-hip-hop-lofi-jam"},
    {"id": "midnight_cruiser", "name": "Midnight Cruiser", "mood": "upbeat city-pop drive", "file": "midnight_cruiser_0.mp3",
     "author": "Zane Little Music", "page": "https://opengameart.org/content/midnight-cruiser"},
    {"id": "apple_cider", "name": "Apple Cider", "mood": "cozy acoustic", "file": "apple_cider.ogg",
     "author": "Zane Little Music", "page": "https://opengameart.org/content/apple-cider"},
    {"id": "ukulele", "name": "Island Ukulele", "mood": "happy ukulele", "file": "ukele.mp3",
     "author": "Tarush Singhal", "page": "https://opengameart.org/content/happy-ukelele-island-surfing-theme"},
    {"id": "raspberry_jam", "name": "Raspberry Jam", "mood": "new jack swing, funky", "file": "raspberry_jam.ogg",
     "author": "congusbongus", "page": "https://opengameart.org/content/raspberry-jam"},
    {"id": "cup_of_tea", "name": "A Cup of Tea", "mood": "mellow lo-fi", "file": "A_cup_of_tea_0.mp3",
     "author": "TAD", "page": "https://opengameart.org/content/lofi-compilation"},
]


def decode(path: Path, rate: int, channels: int, start: float = 0.0, seconds: float | None = None) -> array.array:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.4f}", *(["-t", f"{seconds:.4f}"] if seconds else []), "-i", str(path),
           "-f", "f32le", "-ac", str(channels), "-ar", str(rate), "-"]
    a = array.array("f")
    a.frombytes(subprocess.run(cmd, capture_output=True, check=True).stdout)
    return a


def lead_in(path: Path) -> float:
    """Seconds of near silence (under -50 dBFS) before the music starts."""
    a = decode(path, 8000, 1, seconds=10.0)
    for k, x in enumerate(a):
        if abs(x) > 10 ** (-50 / 20):
            return max(0.0, round(k / 8000 - 0.01, 3))
    return 0.0


def envelope(path: Path, start: float) -> list[float]:
    """Onset strength per 10 ms from `start` (8 kHz mono): the rise of the log level over the frame before, so the
    match below lines up the beats (their phase), not only the loudness."""
    a = decode(path, 8000, 1, start=start)
    n = 8000 // ENV_RATE
    lv = [math.log10(1e-6 + sum(x * x for x in a[k:k + n]) / n) for k in range(0, len(a) - n, n)]
    return [max(0.0, b - a) for a, b in zip([lv[0]] + lv, lv)]


def loop_point(env: list[float], duration: float) -> float:
    """The loop length in [LOOP_MIN_S, LOOP_MAX_S] (envelope frames) whose next MATCH_S correlate best with the start."""
    m = int(MATCH_S * ENV_RATE)
    head = env[:m]
    mh = sum(head) / m
    hd = [x - mh for x in head]
    nh = math.sqrt(sum(x * x for x in hd)) or 1.0
    best, best_k = -2.0, int(LOOP_MIN_S * ENV_RATE)
    hi = min(int(LOOP_MAX_S * ENV_RATE), len(env) - m - int(XFADE_S * ENV_RATE) - 1)
    for k in range(int(LOOP_MIN_S * ENV_RATE), hi):
        seg = env[k:k + m]
        ms = sum(seg) / m
        c = sum(a * (b - ms) for a, b in zip(hd, seg)) / (nh * (math.sqrt(sum((b - ms) ** 2 for b in seg)) or 1.0))
        if c > best:
            best, best_k = c, k
    return best_k / ENV_RATE, best


def build(src_dir: Path, t: dict) -> dict:
    from montivo.engine.render import measure_loudness

    src = src_dir / t["file"]
    if t.get("whole"):
        return keep_whole(src, t)
    start = lead_in(src)
    env = envelope(src, start)
    length, corr = loop_point(env, len(env) / ENV_RATE)
    x = XFADE_S
    out = HERE / f"{t['id']}.m4a"
    # O[0:x] = S[0:x] fading in + S[L:L+x] fading out; O[x:L] = S[x:L]: the end of O flows into S[L], which is O[0]
    graph = (f"[0:a]atrim=start={start:.4f}:duration={length + x:.4f},asetpts=PTS-STARTPTS,"
             f"aformat=sample_fmts=fltp:sample_rates={RATE}:channel_layouts=stereo,asplit=3[a][b][c];"
             f"[a]atrim=duration={x},afade=t=in:d={x}:curve=qsin[head];"
             f"[b]atrim=start={length:.4f},asetpts=PTS-STARTPTS,afade=t=out:d={x}:curve=qsin[tail];"
             f"[head][tail]amix=inputs=2:normalize=0:duration=first[xf];"
             f"[c]atrim=start={x}:end={length:.4f},asetpts=PTS-STARTPTS[body];"
             f"[xf][body]concat=n=2:v=0:a=1[o]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", graph, "-map", "[o]",
                    "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE), "-map_metadata", "-1", "-movflags", "+faststart", str(out)],
                   check=True)
    loud = measure_loudness(out) or {}
    rec = {k: t[k] for k in ("id", "name", "mood")} | {
        "file": out.name, "loop_s": round(length, 3), "lufs": loud.get("integrated_lufs"),
        "true_peak_dbtp": loud.get("true_peak_dbtp"), "licence": "CC0-1.0", "author": t["author"], "source": t["page"],
        "source_file": t["file"], "cut": {"start_s": start, "loop_s": round(length, 3), "xfade_s": x, "match": round(corr, 3)}}
    print(f"{out.name}: from {start:.2f} s, loop {length:.2f} s (match {corr:.2f}), {rec['lufs']} LUFS, "
          f"{out.stat().st_size / 1e6:.2f} MB")
    return rec


def keep_whole(src: Path, t: dict) -> dict:
    from montivo.engine.render import measure_loudness

    out = HERE / f"{t['id']}.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "2", "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE),
                    "-map_metadata", "-1", "-movflags", "+faststart", str(out)], check=True)
    loud = measure_loudness(out) or {}
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                                  capture_output=True, text=True, check=True).stdout)
    print(f"{out.name}: whole ({length:.2f} s, a loop edit already), {loud.get('integrated_lufs')} LUFS")
    return {k: t[k] for k in ("id", "name", "mood")} | {
        "file": out.name, "loop_s": round(length, 3), "lufs": loud.get("integrated_lufs"),
        "true_peak_dbtp": loud.get("true_peak_dbtp"), "licence": "CC0-1.0", "author": t["author"], "source": t["page"],
        "source_file": t["file"], "cut": {"whole": True}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src_dir", type=Path, help="the folder with the downloaded source files")
    ap.add_argument("--ids", default=None, help="comma-separated track ids (default: all)")
    a = ap.parse_args()
    sys.path.insert(0, str(HERE.parent.parent))
    ids = a.ids.split(",") if a.ids else [t["id"] for t in TRACKS]
    tracks = [build(a.src_dir, t) for t in TRACKS if t["id"] in ids]
    (HERE / "tracks.json").write_text(json.dumps({"tracks": tracks}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
