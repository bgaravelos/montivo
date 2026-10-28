"""Export bundle of one version, as an engine entry point the UI server runs as a subprocess:

    python -m montivo.export runs/015 --version 5 --out runs/015/ui/cache/exports/abc --name montivo-015-v5 \
        --include video,clean,srt,vtt,cover,audio,transcript [--preset 720] [--cover-t 12.3]

Writes the chosen files into --out and, when there is more than one, a zip of them; prints a JSON record of what it
wrote. $0, ffmpeg only, and nothing in the run folder changes: `video` is cut_vN.mp4 itself (or its 720x1280 copy),
`clean` renders the version again without burned-in captions from a copy of its EDL into --out (about the time of a
render; callouts and the title card stay), `srt` / `vtt` are the caption lines in cut time, `cover` the frame at
--cover-t (1080x1920, plus a 1080x1440 crop for the Instagram profile grid), `audio` the cut's loudness-normalised
sound as WAV, `transcript` the caption lines as text with their times.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from .ledger import Ledger
from .run import render_run

KINDS = ("video", "clean", "srt", "vtt", "cover", "audio", "transcript")
GRID_H = 1440  # the Instagram profile grid shows a 3:4 crop of a 9:16 cover


def stamp(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def caption_cues(edl: dict) -> list[tuple[float, float, str]]:
    """The rendered caption lines in cut time (what is burned in, retexts included), one cue each."""
    return [(float(c["t_in"]), float(c["t_out"]), c["text"]) for c in edl.get("captions") or [] if c.get("text")]


def srt(cues: list[tuple[float, float, str]]) -> str:
    return "".join(f"{k}\n{stamp(a, ',')} --> {stamp(b, ',')}\n{text}\n\n" for k, (a, b, text) in enumerate(cues, 1))


def vtt(cues: list[tuple[float, float, str]]) -> str:
    return "WEBVTT\n\n" + "".join(f"{stamp(a, '.')} --> {stamp(b, '.')}\n{text}\n\n" for a, b, text in cues)


def transcript(edl: dict, cues: list[tuple[float, float, str]]) -> str:
    head = f"{edl.get('title_card_text') or 'Montivo cut'} (version {edl.get('version')}, {edl.get('runtime_s')} s)\n\n"
    return head + "".join(f"[{stamp(a, '.')[3:-1]}] {text}\n" for a, _, text in cues)


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, check=True)


def small(src: Path, out: Path) -> Path:
    ffmpeg("-i", str(src), "-vf", "scale=720:1280", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out))
    return out


def title_ready(run_dir: Path, edl: dict) -> bool:
    """The version had a title card and its image is still on disk for the same words (so the clean copy never calls
    Muse Image: a later version with new title words replaces title_card.png, and then this copy goes without it)."""
    if not (edl.get("render") or {}).get("title") or not edl.get("title_card_text"):
        return False
    req = run_dir / "title" / "request.json"
    return (run_dir / "title" / "title_card.png").is_file() and req.is_file() and \
        f'"{edl["title_card_text"]}"' in json.loads(req.read_text()).get("prompt", "")


def clean_render(run_dir: Path, edl: dict, out: Path) -> Path:
    """The version again without captions, from a copy of its EDL, its ledger and log kept apart from the run's."""
    work = out.parent / "clean_work"
    work.mkdir(parents=True, exist_ok=True)
    tmp = work / "edl.json"
    tmp.write_text(json.dumps(copy.deepcopy(edl)))
    rel = out.resolve().relative_to(run_dir.resolve())
    render_run(run_dir, edl_path=tmp, out_name=str(rel), captions=False, ledger=Ledger(work / "ledger.json"),
               log=lambda *a, **k: None, title=title_ready(run_dir, edl))
    shutil.rmtree(work, ignore_errors=True)
    return out


def export(run_dir: Path, n: int, out: Path, name: str, include: list[str], preset: str = "full",
           cover_t: float | None = None) -> dict:
    edl_path, cut = run_dir / f"edl_v{n}.json", run_dir / f"cut_v{n}.mp4"
    if not edl_path.is_file() or not cut.is_file():
        raise SystemExit(f"version {n} has no edl_v{n}.json and cut_v{n}.mp4 in {run_dir}")
    bad = [k for k in include if k not in KINDS]
    if bad or not include:
        raise SystemExit(f"--include: one or more of {', '.join(KINDS)}")
    if preset not in ("full", "720"):
        raise SystemExit("--preset: full or 720")
    edl = json.loads(edl_path.read_text())
    out.mkdir(parents=True, exist_ok=True)
    cues = caption_cues(edl)
    files: list[Path] = []
    p720 = preset == "720"
    if "video" in include:
        dst = out / f"{name}{'-720p' if p720 else ''}.mp4"
        small(cut, dst) if p720 else shutil.copyfile(cut, dst)
        files.append(dst)
    if "clean" in include:
        full = out / f"{name}-no-captions{'-full' if p720 else ''}.mp4"
        clean_render(run_dir, edl, full)
        if p720:
            files.append(small(full, out / f"{name}-no-captions-720p.mp4"))
            full.unlink()
        else:
            files.append(full)
    if "srt" in include:
        (dst := out / f"{name}.srt").write_text(srt(cues), encoding="utf-8")
        files.append(dst)
    if "vtt" in include:
        (dst := out / f"{name}.vtt").write_text(vtt(cues), encoding="utf-8")
        files.append(dst)
    if "transcript" in include:
        (dst := out / f"{name}-transcript.txt").write_text(transcript(edl, cues), encoding="utf-8")
        files.append(dst)
    if "cover" in include:
        dur = float((edl.get("render") or {}).get("duration_s") or edl.get("runtime_s") or 1.0)
        t = min(max(0.0, cover_t if cover_t is not None else dur / 3), max(0.0, dur - 0.05))
        dst = out / f"{name}-cover.jpg"
        ffmpeg("-ss", f"{t:.3f}", "-i", str(cut), "-frames:v", "1", "-q:v", "2", str(dst))
        grid = out / f"{name}-cover-grid.jpg"
        ffmpeg("-i", str(dst), "-vf", f"crop=1080:{GRID_H}:0:(ih-{GRID_H})/2", "-q:v", "2", str(grid))
        files += [dst, grid]
    if "audio" in include:
        dst = out / f"{name}.wav"
        ffmpeg("-i", str(cut), "-vn", "-c:a", "pcm_s16le", "-ar", "48000", str(dst))
        files.append(dst)
    record = {"version": n, "files": [{"name": f.name, "bytes": f.stat().st_size} for f in files], "zip": None}
    if len(files) > 1:
        z = out / f"{name}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:  # mp4 / jpg / wav do not compress further
            for f in files:
                zf.write(f, f.name)
        record["zip"] = {"name": z.name, "bytes": z.stat().st_size}
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m montivo.export", description="One version's export bundle ($0).")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--version", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True, help="folder for the files (inside the run folder)")
    ap.add_argument("--name", default=None, help="file name stem (default montivo-<run>-v<N>)")
    ap.add_argument("--include", default="video", help=f"comma list of {', '.join(KINDS)}")
    ap.add_argument("--preset", default="full", choices=("full", "720"))
    ap.add_argument("--cover-t", type=float, default=None, help="cut second of the cover frame (default a third in)")
    a = ap.parse_args(argv)
    if not a.run_dir.is_dir():
        ap.error(f"{a.run_dir} is not a run folder")
    name = a.name or f"montivo-{a.run_dir.name}-v{a.version}"
    rec = export(a.run_dir, a.version, a.out, name, [k.strip() for k in a.include.split(",") if k.strip()], a.preset,
                 a.cover_t)
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
