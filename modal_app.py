"""The hosted demo for the judges on Modal: `montivo serve` behind a password, the keys only in a Modal secret, the
run folders on a Volume, the Meta model spend capped (montivo/server/hosted.py).

    .venv/bin/modal secret create montivo-keys MODEL_API_KEY=... BASETEN_API_KEY=... MONTIVO_PASSWORD=...
    python -m montivo.server.sample pack runs/015 --footage   # dist/montivo-sample-015.zip, the clip included
    .venv/bin/modal volume put montivo-data dist/montivo-sample-015.zip /seed/
    .venv/bin/modal run modal_app.py::seed                     # unzip into the Volume, adopt the runs' spend so far
    .venv/bin/modal deploy modal_app.py                        # https://<workspace>--montivo-web.modal.run

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
          "MONTIVO_MAX_UPLOAD_MB": "400", "PYTHONPATH": "/root/app"}

app = modal.App("montivo")
volume = modal.Volume.from_name("montivo-data", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("httpx>=0.28", "pillow>=10.1", "fastapi>=0.115", "uvicorn>=0.32")
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
def smoke(run: str = "015", span: str = "s4"):
    """`modal run modal_app.py::smoke`: a $0 drop on a copy of a sample outside the Volume, so this image's ffmpeg is
    checked end to end (render, captions, loudness) without changing what the judges see."""
    import shutil
    import subprocess
    import sys

    root = Path("/tmp/smoke")
    shutil.rmtree(root, ignore_errors=True)
    (root / "runs").mkdir(parents=True)
    shutil.copytree(Path(DATA) / "runs" / run, root / "runs" / run)
    (root / "footage").symlink_to(Path(DATA) / "footage")
    r = subprocess.run([sys.executable, "-m", "montivo.human", "drop", f"runs/{run}", "--span", span], cwd=root,
                       capture_output=True, text=True, env={**os.environ, "MODEL_API_KEY": ""})
    print(r.stdout[-1500:], r.stderr[-3000:], f"exit {r.returncode}", sep="\n", flush=True)
    log = (root / "runs" / run / "run.log").read_text().splitlines()
    print("\n".join(log[-6:]), flush=True)
