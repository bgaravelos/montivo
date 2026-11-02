"""The sample runs for judges: a finished run folder and its clip as one zip on a GitHub release (media is never in
the repo), opened keyless with `montivo serve`.

    python -m montivo.server.sample fetch                    -> runs/015 + footage/billy_demo_2710.mp4 (the default)
    python -m montivo.server.sample fetch --sample all       -> every sample in SAMPLES
    python -m montivo.server.sample fetch --from ~/Downloads  a zip downloaded by hand (a folder, a file or file://)
    python -m montivo.server.sample pack runs/015 --footage  -> dist/montivo-sample-015.zip + .sha256

Every zip is checked against its size and sha256 in SAMPLES before anything is unpacked, and only runs/<id>/... and
footage/<clip> entries are written. The folder keeps its id: the EDLs name their source as footage/<clip> and
runs/<id>/..., so a $0 edit (drop, trim, captions, style, music) re-renders it with the local ffmpeg and no key.
An existing runs/<id> is left alone (it may hold your edits); --force moves it to runs/_archive/ first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    id: str
    what: str
    bytes: int
    sha256: str

    @property
    def file(self) -> str:
        return f"montivo-sample-{self.id}.zip"

    @property
    def url(self) -> str:
        return f"{BASE}/sample-{self.id}/{self.file}"


BASE = "https://github.com/bgaravelos/montivo/releases/download"
SAMPLES = {s.id: s for s in [
    Sample("015", "the demo video's run: Billy's 69 s clip, v1 68 -> v5 87 pass", 222796455,
           "46c0388fcddbe71427bd0395df2071ca1d66b68de09fa499e4a00e658dafbe9b"),
    Sample("017", "a 132 s seated showcase clip, 13 versions, v13 92 pass", 423129038,
           "5c84ce2c3e3e99d3555637f12065a44ae98b8a5db2c7a363eda8b552d8fe96d7"),
]}
DEFAULT = "015"
RELEASE = SAMPLES[DEFAULT].url

# the UI reads the JSON, the cuts and the review copies; a re-render (drop span) also needs the source, the audio,
# the words, the tracks and the title card; the model calls' request and response JSON go along as the run's record
KEEP = ["run.json", "summary.json", "ledger.json", "run.log", "ingest.json", "watch.json", "plan.json", "plan_*.json",
        "edl_v*.json", "review_v*.json", "cut_v[0-9].mp4", "cut_v[0-9][0-9].mp4", "cover_v*.json", "post_v*.json",
        "review/loop*.json", "review/hook_ab_*.json", "review/review_v*/cut_v*_review.mp4",
        "review/review_v*/heard/*.json", "voice/voice.json", "voice/request.json", "audio.wav", "proxy.mp4",
        "source_hflip.mp4", "source_upright.mp4", "track/*/tracks.json", "track/*/masks.jsonl",
        "track/*/request.json", "track/track_source_5fps.mp4", "title/title_card.png", "title/request.json",
        "speakers.json", "spark/*.json", "render/*.speech.m4a", "render/*.music.m4a", "ui/chat.jsonl",
        "ui/labels.json"]
SECRETS = ("MODEL_API_KEY", "BASETEN_API_KEY", "MONTIVO_PASSWORD", "MONTIVO_JUDGE_SECRET")
CHUNK = 1 << 20


class SampleError(Exception):
    """A zip that is not the sample it claims to be, or one that would write outside runs/<id> and footage/."""


def sources(run_dir: Path) -> set[str]:
    """The footage/ clips the run's EDLs render from (run 015 cuts from footage/billy_demo_2710.mp4, not a copy)."""
    out = set()
    for p in run_dir.glob("edl_v*.json"):
        src = (json.loads(p.read_text()).get("source") or {}).get("path") or ""
        if src.startswith("footage/") and ".." not in src:
            out.add(src)
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while b := f.read(CHUNK):
            h.update(b)
    return h.hexdigest()


def leaked(files: list[Path], env: dict | None = None) -> list[str]:
    """The packed text files that hold the value of a key or password set in this shell (never in a public zip)."""
    env = os.environ if env is None else env
    values = [env[k].encode() for k in SECRETS if len(env.get(k) or "") >= 8]
    if not values:
        return []
    out = []
    for p in files:
        if p.suffix in (".json", ".jsonl", ".log", ".sse", ".txt") and p.stat().st_size < 64 << 20:
            data = p.read_bytes()
            if any(v in data for v in values):
                out.append(str(p))
    return out


def pack(run_dir: Path, out: Path | None = None, footage: bool = False) -> Path:
    out = out or Path("dist") / f"montivo-sample-{run_dir.name}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted({p for pat in KEEP for p in run_dir.glob(pat) if p.is_file()})
    clips = sorted(sources(run_dir)) if footage else []
    for c in clips:
        if not (run_dir.parent.parent / c).is_file():
            raise SampleError(f"{c} (the run's source) is not there: pack from the project folder")
    if bad := leaked(files):
        raise SampleError(f"a key or password set in this shell is in {', '.join(bad)}: not packed")
    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:  # the media is already compressed
        for p in files:
            z.write(p, (Path("runs") / run_dir.name / p.relative_to(run_dir)).as_posix())
        for c in clips:
            z.write(run_dir.parent.parent / c, c)
    tmp.replace(out)
    digest = sha256(out)
    out.with_name(out.name + ".sha256").write_text(f"{digest}  {out.name}\n")
    print(f"{out}: {len(files) + len(clips)} files, {out.stat().st_size} bytes "
          f"({out.stat().st_size / 1e6:.0f} MB), sha256 {digest}")
    return out


# ------------------------------------------------------------------------------------------------------------- fetch

def check(path: Path, s: Sample) -> None:
    """Size first (cheap, and it names a truncated download), then the sha256."""
    size = path.stat().st_size
    if s.bytes and size != s.bytes:
        raise SampleError(f"{path} is {size} bytes, sample {s.id} is {s.bytes}: an incomplete or other file")
    if s.sha256 and (got := sha256(path)) != s.sha256:
        raise SampleError(f"{path}: sha256 {got}, sample {s.id} is {s.sha256}: not the published zip")


def local_path(src: str) -> Path | None:
    """A file path or file:// URL as a Path; None for http(s)."""
    u = urllib.parse.urlparse(src)
    if u.scheme == "file":
        return Path(urllib.request.url2pathname(u.path))
    if u.scheme in ("http", "https"):
        return None
    return Path(src).expanduser()


def download(url: str, dest: Path, s: Sample) -> None:
    tmp = dest.with_name(dest.name + ".part")
    print(f"downloading {url} ({s.bytes / 1e6:.0f} MB) ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "montivo-sample"})
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:  # noqa: S310 - the URL is ours or given
        total = int(r.headers.get("Content-Length") or s.bytes or 0)
        done, shown = 0, -1
        while b := r.read(CHUNK):
            f.write(b)
            done += len(b)
            pct = int(100 * done / total) if total else -1
            if pct // 10 > shown // 10:
                shown = pct
                print(f"  {pct} %  {done / 1e6:.0f} MB", flush=True)
    try:
        check(tmp, s)
    except SampleError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)


def get_zip(s: Sample, src: str | None, root: Path, verify: bool = True) -> Path:
    """The verified zip of sample `s`: dist/<file> when it is already there and checks out, else from `src` (a path,
    a folder holding <file>, file:// or a URL; default the GitHub release)."""
    s = s if verify else Sample(s.id, s.what, 0, "")
    dist = root / "dist" / s.file
    dist.parent.mkdir(parents=True, exist_ok=True)
    if src is None and dist.is_file():
        try:
            check(dist, s)
            print(f"{dist}: already downloaded, checks out")
            return dist
        except SampleError as e:
            print(f"{e}; downloading it again", flush=True)
            dist.unlink()
    p = local_path(src) if src else None
    if p is None:
        download(src or s.url, dist, s)
        return dist
    if p.is_dir():
        p = p / s.file
    if not p.is_file():
        raise SampleError(f"{p}: no such file")
    check(p, s)
    print(f"{p}: checks out")
    return p


def members(z: zipfile.ZipFile, sample_id: str) -> list[zipfile.ZipInfo]:
    """The entries to write: files under runs/<id>/ and footage/<clip>, nothing absolute, no .., no links. Anything
    else fails the whole zip: the published samples hold nothing else."""
    out = []
    for info in z.infolist():
        name = info.filename
        if info.is_dir():
            continue
        parts = name.split("/")
        mode = info.external_attr >> 16
        ok = (not name.startswith("/") and "\\" not in name and ".." not in parts and "" not in parts
              and not stat.S_ISLNK(mode)
              and ((parts[0] == "runs" and len(parts) >= 3 and parts[1] == sample_id)
                   or (parts[0] == "footage" and len(parts) == 2 and not parts[1].startswith("."))))
        if not ok:
            raise SampleError(f"unexpected entry {name!r} in the zip of sample {sample_id}: nothing unpacked")
        out.append(info)
    if not any(i.filename == f"runs/{sample_id}/run.json" for i in out):
        raise SampleError(f"no runs/{sample_id}/run.json in the zip: not a Montivo sample")
    return out


def _write(z: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with z.open(info) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, CHUNK)
    tmp.replace(dest)


def unpack(zip_path: Path, sample_id: str, root: Path, force: bool = False) -> Path:
    """runs/<id> (written into runs/.fetch-<id> first and renamed, so a stopped unpack leaves no half run the UI
    lists) and the footage/ clips (a clip already there with the same size is kept)."""
    runs = root / "runs"
    run_dir = runs / sample_id
    with zipfile.ZipFile(zip_path) as z:
        infos = members(z, sample_id)
        if (run_dir / "run.json").is_file() and not force:
            print(f"{run_dir} is already there (your edits stay): --force moves it to runs/_archive/ and unpacks "
                  f"a fresh copy")
        else:
            tmp = runs / f".fetch-{sample_id}"
            shutil.rmtree(tmp, ignore_errors=True)
            for info in infos:
                if info.filename.startswith("runs/"):
                    _write(z, info, tmp / info.filename.split("/", 2)[2])
            if run_dir.exists():
                arch = runs / "_archive" / f"{sample_id}-{time.strftime('%Y%m%d-%H%M%S')}"
                arch.parent.mkdir(parents=True, exist_ok=True)
                run_dir.rename(arch)
                print(f"moved the old {run_dir} to {arch}")
            tmp.rename(run_dir)
        for info in infos:
            if info.filename.startswith("footage/"):
                dest = root / info.filename
                if dest.is_file() and dest.stat().st_size == info.file_size:
                    continue
                _write(z, info, dest)
    print(f"sample {sample_id}: {run_dir} ready")
    return run_dir


def fetch(sample: str = DEFAULT, src: str | None = None, root: Path = Path("."), force: bool = False,
          verify: bool = True) -> list[Path]:
    ids = list(SAMPLES) if sample == "all" else [sample]
    if src and len(ids) > 1 and (p := local_path(src)) is not None and not p.is_dir():
        raise SampleError("--sample all with --from takes a folder holding the zips")
    out = []
    for sid in ids:
        if sid not in SAMPLES:
            raise SampleError(f"no sample {sid!r}: {', '.join(SAMPLES)} or all")
        out.append(unpack(get_zip(SAMPLES[sid], src, root, verify), sid, root, force))
    print("next: docker compose up, then open http://localhost:8765" if os.environ.get("MONTIVO_DOCKER")  # the image sets it
          else "next: make ui (or .venv/bin/montivo serve), then open http://127.0.0.1:8765")
    return out


def guess_id(src: str | None) -> str | None:
    """`--from dist/montivo-sample-017.zip` alone names its sample."""
    m = re.search(r"montivo-sample-(\d+)\.zip$", src or "")
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m montivo.server.sample")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="zip a finished run folder for a release")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--footage", action="store_true", help="also pack the footage/ clips the EDLs render from")
    f = sub.add_parser("fetch", help="download, check and unpack a sample into runs/ and footage/")
    f.add_argument("--sample", default=None, help=f"{', '.join(SAMPLES)} or all (default {DEFAULT})")
    f.add_argument("--from", dest="src", default=None,
                   help="a zip, a folder holding it, file:// or a URL instead of the GitHub release")
    f.add_argument("--url", dest="src", help=argparse.SUPPRESS)  # the old flag
    f.add_argument("--root", type=Path, default=Path("."), help="the project folder (default: here)")
    f.add_argument("--force", action="store_true", help="unpack again over runs/<id> (the old one is archived)")
    f.add_argument("--no-verify", action="store_true", help="skip the size and sha256 check (a zip you packed)")
    sub.add_parser("list", help="the samples this version knows")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "pack":
            pack(a.run_dir, a.out, a.footage)
        elif a.cmd == "list":
            for s in SAMPLES.values():
                print(f"{s.id}{' (default)' if s.id == DEFAULT else ''}  {s.bytes / 1e6:.0f} MB  {s.what}\n     {s.url}")
        else:
            fetch(a.sample or guess_id(a.src) or DEFAULT, a.src, a.root, a.force, not a.no_verify)
    except SampleError as e:
        print(f"montivo sample: {e}", file=sys.stderr)
        return 1
    except OSError as e:  # a download that failed, a full disk
        print(f"montivo sample: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
