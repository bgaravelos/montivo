"""The sample run for judges: a finished run folder as one zip on a GitHub release (media is never in the repo).

    python -m montivo.server.sample pack runs/012          -> dist/montivo-sample-012.zip (what the UI and a $0 drop need)
    python -m montivo.server.sample pack runs/015 --footage   + the clip in footage/ the EDLs render from (the hosted demo)
    python -m montivo.server.sample fetch                  -> runs/012 from the release, then `montivo serve`

The folder keeps its id: the EDLs name their source as runs/<id>/..., so `drop span` re-renders it without a key.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

RELEASE = "https://github.com/bgaravelos/montivo/releases/download/sample-012/montivo-sample-012.zip"

# the UI reads the JSON, the cuts and the review copies; a re-render (drop span) also needs the source, the audio,
# the words, the tracks and the title card
KEEP = ["run.json", "summary.json", "ledger.json", "run.log", "ingest.json", "watch.json", "plan.json",
        "edl_v*.json", "review_v*.json", "cut_v[0-9].mp4", "cut_v[0-9][0-9].mp4", "review/loop.json",
        "review/review_v*/cut_v*_review.mp4", "voice/voice.json", "audio.wav", "source_hflip.mp4",
        "track/*/tracks.json", "track/*/masks.jsonl", "title/title_card.png", "title/request.json", "speakers.json",
        "ui/chat.jsonl"]


def sources(run_dir: Path) -> set[str]:
    """The footage/ clips the run's EDLs render from (run 015 cuts from footage/billy_demo_2710.mp4, not a copy)."""
    out = set()
    for p in run_dir.glob("edl_v*.json"):
        src = (json.loads(p.read_text()).get("source") or {}).get("path") or ""
        if src.startswith("footage/") and ".." not in src:
            out.add(src)
    return out


def pack(run_dir: Path, out: Path | None = None, footage: bool = False) -> Path:
    out = out or Path("dist") / f"montivo-sample-{run_dir.name}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted({p for pat in KEEP for p in run_dir.glob(pat) if p.is_file()})
    clips = sorted(sources(run_dir)) if footage else []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:  # the media is already compressed
        for p in files:
            z.write(p, Path("runs") / run_dir.name / p.relative_to(run_dir))
        for c in clips:
            z.write(run_dir.parent.parent / c, c)
    print(f"{out}: {len(files) + len(clips)} files, {out.stat().st_size / 1e6:.0f} MB")
    return out


def fetch(url: str = RELEASE, root: Path = Path(".")) -> None:
    dest = root / "dist" / Path(url).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        print(f"downloading {url} ...", flush=True)
        urllib.request.urlretrieve(url, dest)
    with zipfile.ZipFile(dest) as z:
        names = [n for n in z.namelist() if n.startswith(("runs/", "footage/")) and ".." not in n]
        z.extractall(root, members=names)
    print(f"unpacked {len(names)} files into {root / 'runs'}; now: .venv/bin/montivo serve")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m montivo.server.sample")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--footage", action="store_true", help="also pack the footage/ clips the EDLs render from")
    f = sub.add_parser("fetch")
    f.add_argument("--url", default=RELEASE)
    a = ap.parse_args(argv)
    if a.cmd == "pack":
        pack(a.run_dir, a.out, a.footage)
    else:
        fetch(a.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
