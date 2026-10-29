"""The hosted demo for the judges on Modal: `montivo serve` behind a password, the keys only in a Modal secret, the
run folders on a Volume, the Meta model spend capped (montivo/server/hosted.py).

    .venv/bin/modal secret create montivo-keys MODEL_API_KEY=... BASETEN_API_KEY=... MONTIVO_PASSWORD=...
    python -m montivo.server.sample pack runs/015 --footage   # dist/montivo-sample-015.zip, the clip included
    .venv/bin/modal volume put montivo-data dist/montivo-sample-015.zip /seed/
    .venv/bin/modal run modal_app.py::seed                     # unzip into the Volume, adopt the runs' spend so far
    .venv/bin/modal deploy modal_app.py                        # https://<workspace>--montivo-web.modal.run
    .venv/bin/modal container list                             # then stop the web container older than the deploy:
    .venv/bin/modal container stop <id>                        # its open SSE streams never end, and max_containers=1
                                                               # keeps the new version from starting (28/10: every
                                                               # request pending from the deploy until it was stopped)

One container at most (the server keeps its jobs in memory, MONTIVO_ONE_JOB runs them one at a time), 4 CPUs for the
ffmpeg renders, scaled to zero when nobody has the page open. The engine runs as a subprocess with /data as its
folder, exactly as on a laptop; a thread commits the Volume every 30 s so a finished render survives the container.
"""
from __future__ import annotations

import os
from pathlib import Path

import modal

DATA = "/data"
HOSTED = {"MONTIVO_BUDGET_USD": "15", "MONTIVO_ONE_JOB": "1", "MONTIVO_MAX_FOOTAGE_S": "95",
          "MONTIVO_MAX_UPLOAD_MB": "1000", "MONTIVO_MAX_UPLOADS": "5","PYTHONPATH": "/root/app"}

app = modal.App("montivo")
volume = modal.Volume.from_name("montivo-data", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .add_local_file("constraints.txt", "/root/constraints.txt", copy=True)  # the versions `make test` passed with
    .pip_install("httpx>=0.28", "pillow>=10.1", "fastapi>=0.115", "starlette>=0.38", "uvicorn>=0.32",
                 "meta-sam-parser==0.1.0", extra_options="-c /root/constraints.txt")
    .env(HOSTED)
    .add_local_dir("montivo", "/root/app/montivo", ignore=["**/__pycache__/**"])
)


@app.function(image=image, volumes={DATA: volume}, secrets=[modal.Secret.from_name("montivo-keys")],
              cpu=4.0, memory=8192, max_containers=1, scaledown_window=900, timeout=3600)
@modal.concurrent(max_inputs=200)
@modal.asgi_app()
def web():
    import threading
    import time

    from montivo.server.app import create_app

    def commit_forever():
        while True:
            time.sleep(30)
            try:
                volume.commit()
            except Exception as e:  # a commit can race a file being written; the next one picks it up
                print(f"volume commit: {e}", flush=True)

    threading.Thread(target=commit_forever, daemon=True).start()
    os.chdir(DATA)
    return create_app(Path(DATA))


@app.function(image=image, volumes={DATA: volume}, timeout=1800)
def seed(adopt: str = ""):
    """Unzip every /data/seed/*.zip into /data (runs/<id>/..., footage/...) and count the runs' ledgers so far as spent
    elsewhere, so the cap only sees what the judges spend."""
    import zipfile

    from montivo.server.hosted import Budget

    root = Path(DATA)
    names: set[str] = set()
    for z in sorted((root / "seed").glob("*.zip")):
        with zipfile.ZipFile(z) as f:
            members = [n for n in f.namelist() if n.startswith(("runs/", "footage/")) and ".." not in n]
            f.extractall(root, members=members)
            names |= {n.split("/")[1] for n in members if n.startswith("runs/")}
        print(f"{z.name}: {len(members)} files", flush=True)
    names |= {n for n in adopt.split(",") if n}
    b = Budget(root, float(HOSTED["MONTIVO_BUDGET_USD"]))
    b.adopt(sorted(names))
    volume.commit()
    print(f"adopted {sorted(names)}; budget {b.public()}", flush=True)


@app.function(image=image, volumes={DATA: volume})
def budget():
    """`modal run modal_app.py::budget`: the cap, what the host spent, what is left."""
    from montivo.server.hosted import Budget

    volume.reload()
    print(Budget(Path(DATA), float(HOSTED["MONTIVO_BUDGET_USD"])).public(), flush=True)


@app.function(image=image, volumes={DATA: volume}, cpu=4.0, memory=8192, timeout=900)
def smoke(run: str = "015", span: str = "s4", music: str = "", cover: bool = False, reorder: bool = False):
    """`modal run modal_app.py::smoke`: a $0 drop on a copy of a sample outside the Volume, so this image's ffmpeg is
    checked end to end (render, captions, loudness) without changing what the judges see. `--music ID` puts that
    bundled music bed under the speech instead (the mix, its loudness and the speech stem). `--cover` renders the
    newest version's headline and sticker covers instead (engine/thumb.py, $0) and checks Meta's SAM mask parser
    imports for the cutout. `--reorder` moves the last clip in front of the first (reorder_smoke) and fails unless
    the new cut's audio lasts as long as its video: ffmpeg before 8.0 dropped the audio after such a clip."""
    import shutil
    import subprocess
    import sys

    from montivo.engine.ingest import has_zscale
    print(subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0], flush=True)
    print(f"zscale: {'yes, HDR clips are tone-mapped' if has_zscale() else 'no: HLG via colorspace, PQ refused'}", flush=True)
    root = Path("/tmp/smoke")
    shutil.rmtree(root, ignore_errors=True)
    (root / "runs").mkdir(parents=True)
    shutil.copytree(Path(DATA) / "runs" / run, root / "runs" / run)
    (root / "footage").symlink_to(Path(DATA) / "footage")
    if cover:
        code = ("import json, sys; from pathlib import Path; import meta_sam_parser; from montivo.engine.thumb import cover_image; "
                "d = Path(sys.argv[1]); n = max(int(p.stem[5:]) for p in d.glob('edl_v*.json')); "
                "[print(t, json.dumps({k: v for k, v in cover_image(d, n, {'t_s': 2.0, 'template': t, 'text': 'AI that fixes itself', "
                "'highlight': 'fixes'})[1].items() if k in ('lines', 'size', 'checks')})) for t in ('headline', 'sticker')]")
        r = subprocess.run([sys.executable, "-c", code, f"runs/{run}"], cwd=root, capture_output=True, text=True,
                           env={**os.environ, "MODEL_API_KEY": ""})
        print(r.stdout[-3000:], r.stderr[-3000:], f"exit {r.returncode}", sep="\n", flush=True)
        return
    if reorder:
        reorder_smoke(root, run)
        return
    op = ["music", f"runs/{run}", "--track", music] if music else ["drop", f"runs/{run}", "--span", span]
    r = subprocess.run([sys.executable, "-m", "montivo.human", *op], cwd=root,
                       capture_output=True, text=True, env={**os.environ, "MODEL_API_KEY": ""})
    print(r.stdout[-1500:], r.stderr[-3000:], f"exit {r.returncode}", sep="\n", flush=True)
    log = (root / "runs" / run / "run.log").read_text().splitlines()
    print("\n".join(log[-6:]), flush=True)


@app.function(image=image, volumes={DATA: volume}, secrets=[modal.Secret.from_name("montivo-keys")], cpu=4.0,
              memory=8192, timeout=1800)
def rereview(run: str, version: int, passes: int = 3):
    """`modal run modal_app.py::rereview --run ID --version N`: render cut_vN again with today's code (an engine fix that
    changes how vN looks, like the caption retext of 28/10) and run the review loop from it; the spend goes to the run's
    ledger, so the cap counts it. Stop the web container first (`modal container stop`): it does not see another
    container's writes until it starts again."""
    import subprocess
    import sys

    volume.reload()
    for args in (["render", f"runs/{run}", "--edl", f"runs/{run}/edl_v{version}.json", "--out", f"cut_v{version}.mp4"],
                 ["review", f"runs/{run}", "--edl", f"runs/{run}/edl_v{version}.json", "--passes", str(passes)]):
        r = subprocess.run([sys.executable, "-m", "montivo.cli", *args], cwd=DATA, capture_output=True, text=True)
        print(r.stdout[-4000:], r.stderr[-2000:], f"exit {r.returncode}", sep="\n", flush=True)
        if r.returncode:
            break
    volume.commit()


def reorder_smoke(root: Path, run: str, timeout_s: float = 300.0) -> dict:
    """The --reorder smoke, plain Python so it runs off Modal too (checked on a synthetic run, 29/10): on root/runs/<run> (a
    copy), `montivo.human reorder` moves the newest version's last clip in front of its first ($0, MODEL_API_KEY
    empty), then the new cut must exist within timeout_s, its audio must last as long as its video within 2 frames
    and the moved clip must not be silent. Any miss raises RuntimeError, so `modal run` exits non-zero."""
    import json
    import re
    import subprocess
    import sys

    d = root / "runs" / run
    vs = sorted(int(m.group(1)) for p in d.glob("edl_v*.json") if (m := re.fullmatch(r"edl_v(\d+)\.json", p.name)))
    if not vs:
        raise RuntimeError(f"reorder smoke: no edl_vN in {d}")
    edl = json.loads((d / f"edl_v{vs[-1]}.json").read_text())
    ids = [sp["id"] for sp in edl["spans"]]
    if len(ids) < 2:
        raise RuntimeError(f"reorder smoke: v{vs[-1]} has {len(ids)} clip(s), nothing to move")
    op = ["reorder", f"runs/{run}", "--span", ids[-1], "--before", ids[0]]
    print(f"reorder smoke: v{vs[-1]} {' '.join(op)}", flush=True)
    try:
        r = subprocess.run([sys.executable, "-m", "montivo.human", *op], cwd=root, capture_output=True, text=True,
                           env={**os.environ, "MODEL_API_KEY": ""}, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"reorder smoke: the render did not finish in {timeout_s:.0f} s (ffmpeg < 8 hung here)")
    print(r.stdout[-1500:], r.stderr[-3000:], f"exit {r.returncode}", sep="\n", flush=True)
    n = vs[-1] + 1
    cut = d / f"cut_v{n}.mp4"
    if r.returncode or not cut.is_file():
        raise RuntimeError(f"reorder smoke: no cut_v{n}.mp4 (exit {r.returncode})")

    def dur(sel: str) -> float:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", sel, "-show_entries", "stream=duration",
                              "-of", "json", str(cut)], capture_output=True, text=True).stdout
        st = json.loads(out or "{}").get("streams") or []
        return float(st[0]["duration"]) if st and st[0].get("duration") not in (None, "N/A") else 0.0

    video_s, audio_s = dur("v:0"), dur("a:0")
    new = json.loads((d / f"edl_v{n}.json").read_text())
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                           "-of", "csv=p=0", str(cut)], capture_output=True, text=True).stdout.strip() or "30/1"
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1) or 30.0
    # the moved clip plays first, after the title card if there is one: its audio (0.2 s in, 1.0 s long) must sound
    lead = float((((new.get("render") or {}).get("title")) or {}).get("seconds") or 0.0)
    vol = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{lead + 0.2:.3f}", "-t", "1.0", "-i", str(cut),
                          "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    mean_db = float(m.group(1)) if m else -91.0
    rec = {"version": n, "video_s": round(video_s, 3), "audio_s": round(audio_s, 3), "moved_mean_db": mean_db}
    print(f"reorder smoke: {json.dumps(rec)}", flush=True)
    if abs(audio_s - video_s) > 2 / fps + 0.004:
        raise RuntimeError(f"reorder smoke: audio {audio_s:.3f} s vs video {video_s:.3f} s: this ffmpeg drops the "
                           f"audio after a clip moved earlier (render.cut_graph per_input)")
    if mean_db < -60:
        raise RuntimeError(f"reorder smoke: the moved clip is silent ({mean_db} dB)")
    return rec
