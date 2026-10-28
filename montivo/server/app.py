"""`montivo-ui`: FastAPI + SSE over the run folders, the engine started as a subprocess, the React build served.

    .venv/bin/montivo-ui                 http://127.0.0.1:8765, runs/ and footage/ of the current folder
    .venv/bin/montivo-ui --port 9000 --root ~/Documents/montivo

GET endpoints only read files, so a finished run folder opens without MODEL_API_KEY; the actions that call a model
answer 409 when the key is not set (the key itself is never read beyond "is it set"). The hosted demo (modal_app.py)
adds a password and a spend cap from the environment (hosted.py).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import OPEN_MODEL
from ..engine.api import dump_json as write_json
from ..engine.frame import ASPECTS
from ..engine.graphics import FONTS, PRESET_NAMES, PRESETS, normalize_look
from . import hosted, media, preflight
from .runs import fingerprint, heads, list_runs, parse_log, progress, read_json, replay_script, run_state

STATIC = Path(__file__).parent / "static"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
RUN_PATH = re.compile(r"^/api/runs/([^/]+)")  # every route of one run (state, files, media, events, edits)
ARCHIVE = "_archive"  # runs/_archive/<id>: archived run folders (list_runs skips it: it has no run.json)
PUNCH_KEY = re.compile(r"emphasis:[^\s@]{1,40}@\d{1,5}\.\d{1,3}|jump_cut:s\d+")

# rough prices and times for the hint on each button (README "Measured behaviour"; the planner A/B on runs/012-open:
# Spark 1.3 $0.050 in 35 s, Muse Spark Open $0.012 in 6.8 s). The *_s times are the progress bar's expectation when the
# run's own ledger has no earlier call of that stage (run 015: review prep 8.3 s, Spark review 26 s, title card 10.3 s,
# render 20.3 s for 1161 frames)
# A new run's cost and wall time from the footage length (5 fps SAM, before any review): run 014, 120 s, $0.36 in
# 169 s; run 015, 69 s, $0.23 in about 130 s, so about $0.045 + $0.0026 per footage second and 60 s + 0.9 s per second;
# the review loop adds about two reviews and a render (run 015: $0.07, 85 s)
COST = {"plan": 0.05, "plan_open": 0.012, "plan_s": 35, "plan_open_s": 7, "review": 0.035, "run_fixed": 0.045,
        "run_per_s": 0.0026, "run_wall_fixed_s": 60, "run_wall_per_s": 0.9, "review_loop": 0.07, "review_loop_s": 85,
        "hook_ab": 0.04, "ask": 0.006, "look": 0.008, "cover": 0.025, "cover_design": 0.05, "cover_mask": 0.01,
        "review_s": 26, "review_prep_s": 8, "image_s": 10, "render_s_per_frame": 0.0175}


class WebFiles(StaticFiles):
    """The UI build. index.html is never cached: a browser holding the old page after a deploy asks for the old asset
    names and shows the old UI. The assets carry a content hash in their names, so they may be cached for good.

    index.html also never answers 304 and carries no ETag or Last-Modified: Modal's image gives every file the mtime
    1970-01-01 and the page is always 1066 bytes, so Starlette's ETag (mtime + size) was the same on every deploy and a
    browser revalidating its copy was told it had not changed (28/10: tabs kept the old UI after reloads)."""

    def is_not_modified(self, response_headers, request_headers) -> bool:
        if response_headers.get("content-type", "").startswith("text/html"):
            return False
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path: str, scope):
        r = await super().get_response(path, scope)
        if path.startswith("assets/") and r.status_code == 200:
            r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif r.headers.get("content-type", "").startswith("text/html") or r.status_code == 304:
            r.headers["Cache-Control"] = "no-cache"
            for h in ("etag", "last-modified"):
                if h in r.headers:
                    del r.headers[h]
        return r


@dataclass
class Job:
    id: str
    run: str
    kind: str
    label: str
    cmd: list[str]
    started: float = field(default_factory=time.time)
    proc: subprocess.Popen | None = None
    ended: float | None = None
    rc: int | None = None
    out: str = ""
    log_pos: int = 0  # run.log's size when the job started: its own lines come after

    def public(self) -> dict:
        return {"id": self.id, "run": self.run, "kind": self.kind, "label": self.label, "started": self.started,
                "ended": self.ended, "rc": self.rc, "running": self.ended is None,
                "tail": _tail(self.out) if self.rc not in (None, 0) else ""}


def _tail(path: str, n: int = 2000) -> str:
    try:
        return Path(path).read_text(errors="replace")[-n:]
    except OSError:
        return ""


class State:
    def __init__(self, root: Path):
        self.root = root
        self.runs = root / "runs"
        self.footage = root / "footage"
        self.jobs: dict[str, Job] = {}  # run id -> its latest job
        self.lock = threading.Lock()
        self.one_job = os.environ.get("MONTIVO_ONE_JOB") == "1"  # hosted: one engine job at a time over all runs

    def run_dir(self, run_id: str) -> Path:
        if not SAFE_NAME.match(run_id):
            raise HTTPException(404, "no such run")
        d = self.runs / run_id
        if not (d / "run.json").is_file() and not (run_id in self.jobs and d.is_dir()):
            raise HTTPException(404, f"no run folder runs/{run_id}")
        return d

    def job(self, run_id: str) -> Job | None:
        return self.jobs.get(run_id)

    def busy(self, run_id: str) -> bool:
        j = self.jobs.get(run_id)
        return bool(j and j.ended is None)


def job_progress(st: "State", job: Job) -> dict | None:
    """The job's progress from its own run.log lines (runs.progress), with the clock brought up to now: the stage
    keeps running between two lines, so the seconds since the last write are added."""
    logp = st.runs / job.run / "run.log"
    try:
        with logp.open("rb") as fh:
            fh.seek(job.log_pos)
            text = fh.read().decode(errors="replace")
        since = max(0.0, time.time() - logp.stat().st_mtime)
    except OSError:
        text, since = "", 0.0
    end = job.ended or time.time()
    p = progress(parse_log(text), label=job.label, ledger=read_json(st.runs / job.run / "ledger.json"), costs=COST) or {
        "label": job.label, "kind": job.kind, "stage": None, "title": "Starting", "detail": None, "floor": 0.0,
        "ceil": 0.95, "expected_s": None, "expected_from": None, "stage_elapsed": 0.0, "steps": [], "finished": False,
        "error": False, "version": None}
    if job.ended is None:
        p["stage_elapsed"] = round(p["stage_elapsed"] + since, 1)
    p.update(active=job.ended is None, job_elapsed=round(end - job.started, 1), rate=1.0 if job.ended is None else 0.0,
             cap=None, failed=bool(job.rc))
    return p


def has_key() -> bool:
    return bool(os.environ.get("MODEL_API_KEY"))


def has_open_key() -> bool:
    """Muse Spark Open plans on Baseten with its own key; the reviews still run on Spark 1.3 (MODEL_API_KEY)."""
    return bool(os.environ.get("BASETEN_API_KEY"))


def job_json(job: Job) -> dict | None:
    """The JSON a CLI command printed last (`montivo hook` prints its record), from the job's output file."""
    text = _tail(job.out, 20000)
    at = text.rfind("\n{")
    try:
        return json.loads(text[at + 1:] if at >= 0 else text)
    except ValueError:
        return None


def chat_append(run_dir: Path, entry: dict) -> None:
    p = run_dir / "ui" / "chat.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps({"t": time.time(), **entry}) + "\n")


def free_name(folder: Path, name: str) -> Path:
    """name, or name-2, name-3, ... (before the extension): the first that is not taken in folder."""
    p, stem, k = folder / name, Path(name).stem[:72], 2
    while p.exists():
        p, k = folder / f"{stem}-{k}{Path(name).suffix}", k + 1
    return p


def latest_version(run_dir: Path) -> int:
    ns = [int(m.group(1)) for p in run_dir.glob("edl_v*.json") if (m := re.fullmatch(r"edl_v(\d+)\.json", p.name))]
    return max(ns, default=0)


def result_message(st: State, job: Job) -> dict:
    """The agent's chat line when a job ends: the newest version and its score, or the error."""
    d = st.runs / job.run
    if job.rc != 0:
        return {"role": "agent", "text": f"{job.label} failed (exit {job.rc}). The log has the details.", "kind": "error"}
    vs = sorted(int(m.group(1)) for p in d.glob("cut_v*.mp4") if (m := re.fullmatch(r"cut_v(\d+)\.mp4", p.name)))
    n = vs[-1] if vs else None
    rv = read_json(d / f"review_v{n}.json") if n else None
    loop = read_json(d / "review" / "loop.json") or {}
    text = f"{job.label}: done."
    if job.kind == "ask" and (rec := job_json(job)) is not None:
        # the agent's own words: its reply (or the question it asks back) and what it could not do
        said = " ".join(x for x in (rec.get("reply"), rec.get("question")) if x)
        if rec.get("skipped"):
            said += " Not done: " + "; ".join(rec["skipped"]) + "."
        if not rec.get("version") and rec.get("ops") and not rec.get("done"):
            said += " Nothing changed, so there is no new version."
        return {"role": "agent", "kind": "ask", "text": said.strip() or "Nothing to change.", "version": rec.get("version"),
                "after": rec.get("from"), "message": rec.get("message"), "router": rec.get("router")}
    if job.kind == "hook":
        rec = job_json(job) or {}
        if rec.get("stop"):
            return {"role": "agent", "text": f"Hook A/B did not run: {rec['stop']}.", "kind": "result"}
        if rec.get("kept"):
            a, b = rec["a"], rec["b"]
            noise = "" if rec.get("decisive") else ", within the reviewer's 10-point noise"
            return {"role": "agent", "kind": "result", "version": rec["kept"],
                    "text": f"Hook A/B: v{a['version']} opens on {a['opener']} ({a['overall']}, hook {a['hook']}), "
                            f"v{b['version']} on {b['opener']} ({b['overall']}, hook {b['hook']}). Keeping "
                            f"v{rec['kept']}, {rec['reason']}{noise}."}
    if n:
        edl = read_json(d / f"edl_v{n}.json") or {}
        dur = (edl.get("render") or {}).get("duration_s") or edl.get("runtime_s")
        text = f"v{n} rendered, {dur:.1f} s." if dur else f"v{n} rendered."
        if rv:
            text += f" Blind review: {rv['review']['overall']}{' (pass)' if rv['review']['pass'] else ''}."
        if job.kind in ("run", "review") and loop.get("stop"):
            text += f" Loop stop: {loop['stop']}."
    return {"role": "agent", "text": text, "version": n, "kind": "result"}


def start_job(st: State, run_id: str, kind: str, label: str, args: list[str], chat: dict | None = None) -> Job:
    d = st.runs / run_id
    with st.lock:
        if st.busy(run_id):
            raise HTTPException(409, f"{st.jobs[run_id].label} is still running on this run")
        other = next((j for j in st.jobs.values() if j.ended is None), None) if st.one_job else None
        if other:
            raise HTTPException(409, f"{other.label} is running on {other.run}: this demo server runs one job at a "
                                     "time, try again when it finishes")
        (d / "ui").mkdir(parents=True, exist_ok=True)
        jid = time.strftime("%Y%m%d-%H%M%S")
        out = d / "ui" / f"job-{jid}-{kind}.out"
        cmd = [sys.executable, *args]
        logp = d / "run.log"
        job = Job(id=jid, run=run_id, kind=kind, label=label, cmd=cmd, out=str(out),
                  log_pos=logp.stat().st_size if logp.is_file() else 0)
        fh = out.open("w")
        job.proc = subprocess.Popen(cmd, cwd=st.root, stdout=fh, stderr=subprocess.STDOUT, env=os.environ.copy())
        st.jobs[run_id] = job
    if chat:
        chat_append(d, chat)

    def wait():
        rc = job.proc.wait()
        fh.close()
        job.rc, job.ended = rc, time.time()
        chat_append(d, result_message(st, job))

    threading.Thread(target=wait, daemon=True).start()
    return job


def create_app(root: Path) -> FastAPI:
    st = State(root.resolve())
    app = FastAPI(title="Montivo", docs_url="/api/docs", openapi_url="/api/openapi.json")
    cap = hosted.env_float("MONTIVO_BUDGET_USD")
    budget = hosted.Budget(st.root, cap) if cap is not None else None
    max_footage_s = hosted.env_float("MONTIVO_MAX_FOOTAGE_S")
    max_upload_mb = hosted.env_float("MONTIVO_MAX_UPLOAD_MB")

    @app.middleware("http")
    async def only_their_runs(request: Request, call_next):
        """On the hosted demo every /api/runs/<id>/... route, read or write, serves a judge the samples and their own
        projects only; another judge's run answers exactly like a run that does not exist. Added before the sign-in
        gate, so the gate runs first."""
        m = RUN_PATH.match(request.url.path)
        if budget is not None and m:
            d = st.runs / m.group(1)
            if not (SAFE_NAME.match(m.group(1)) and d.is_dir() and (is_sample(d) or owner(d) == judge_of(request))):
                return JSONResponse({"error": "no such run"}, status_code=404)
        return await call_next(request)

    hosted.install(app, os.environ.get("MONTIVO_PASSWORD"))

    def key_ok() -> bool:
        """The key is set and, on the hosted demo, the budget still covers the cheapest paid action."""
        return has_key() and (budget is None or budget.note() is None)

    copying = threading.Lock()

    def judge_of(request: Request) -> str:
        return request.cookies.get(hosted.JUDGE) or "guest"

    def owner(d: Path) -> str | None:
        p = d / "ui" / "owner"
        return p.read_text().strip() if p.is_file() else None

    def is_sample(d: Path) -> bool:
        """A run seeded onto the host for everyone (adopted, no owner): the judges edit their own copies of it."""
        return budget is not None and d.name in budget.baseline and owner(d) is None

    def copy_sample(run_id: str, d: Path, judge: str) -> str:
        mine = f"{run_id}-{judge}"
        with copying:
            if not (st.runs / mine / "run.json").is_file():
                tmp = st.root / ".copying" / mine  # outside runs/: the budget and the list never see a half copy
                shutil.rmtree(tmp, ignore_errors=True)
                tmp.parent.mkdir(exist_ok=True)
                shutil.copytree(d, tmp, ignore=shutil.ignore_patterns("job-*.out", "cache", "frames", "heard"))
                (tmp / "ui").mkdir(exist_ok=True)
                (tmp / "ui" / "owner").write_text(judge)
                tmp.rename(st.runs / mine)
                budget.adopt([mine])  # the copy's ledger so far is the sample's, not the judge's spend
                chat_append(st.runs / mine, {"role": "agent", "kind": "result", "text": f"Your own copy of the "
                             f"sample {run_id}: your edits go here and the sample stays as it was for the next judge."})
        return mine

    async def own(run_id: str, request: Request) -> str:
        """The run an edit goes to: the run itself, or on the hosted demo the judge's own copy of a sample (made on
        their first edit, runs/<id>-<judge>), so one judge's edits never become the next judge's opening version.
        The request body is read before the copy (Starlette keeps it for the route) and the copy runs in a thread:
        015 takes 13 s on the Modal Volume, and copying first left the body unread until the client gave up (408,
        the edit lost) while the event loop answered nobody else (28/10)."""
        d = st.run_dir(run_id)
        if not is_sample(d):
            if budget is not None and owner(d) != judge_of(request):
                raise HTTPException(404, "no such run")  # another judge's project
            return run_id
        await request.body()
        return await asyncio.to_thread(copy_sample, run_id, d, judge_of(request))

    def key_info() -> dict:
        return {"has_key": key_ok(), "key_note": budget.note() if budget else None,
                "budget": budget.public() if budget else None}

    @app.get("/api/health")
    def health():
        return {"ok": True, "root": str(st.root), **key_info(), "costs": COST}

    @app.get("/api/runs")
    def runs(request: Request):
        out = list_runs(st.runs)
        if budget is not None:  # the hosted demo: the samples (or this judge's copy of one) and their own runs
            judge = judge_of(request)
            ids = {r["id"] for r in out}
            out = [r for r in out if (o := owner(st.runs / r["id"])) == judge
                   or (o is None and f"{r['id']}-{judge}" not in ids)]
        for r in out:
            j = st.job(r["id"])
            r["job"] = j.public() if j else None
            r["progress"] = job_progress(st, j) if j and j.ended is None else None
        return {"runs": out, **key_info(), "has_open_key": has_open_key(), "costs": COST}

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        d = st.run_dir(run_id)
        s = run_state(d)
        j = st.job(run_id)
        s["job"] = j.public() if j else None
        s.update(key_info())
        s["has_open_key"] = has_open_key()
        s["costs"] = COST
        return s

    @app.get("/api/runs/{run_id}/file/{path:path}")
    def file(run_id: str, path: str):
        d = st.run_dir(run_id).resolve()
        p = (d / path).resolve()
        if d not in p.parents or not p.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(p)  # answers Range requests, so the player can seek

    @app.get("/api/runs/{run_id}/frame")
    def frame(run_id: str, v: int, t: float, clean: bool = False):
        """The frame the reviewer saw at cut time t: from the review copy (720x1280 with the clock) when it exists;
        clean=1 takes it from cut_vN.mp4 itself (the loop film's posters)."""
        d = st.run_dir(run_id)
        src = d / "review" / f"review_v{v}" / f"cut_v{v}_review.mp4"
        if clean or not src.is_file():
            src = d / f"cut_v{v}.mp4"
        if not src.is_file():
            raise HTTPException(404, "no cut for this version")
        out = d / "ui" / "frames" / f"v{v}_{round(max(t, 0) * 10):05d}{'_clean' if clean else ''}.jpg"
        if not out.is_file() or out.stat().st_mtime < src.stat().st_mtime:
            out.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(src),
                            "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)], capture_output=True)
        if not out.is_file():
            raise HTTPException(404, "frame extraction failed")
        return FileResponse(out, headers={"Cache-Control": "max-age=3600"})

    def cut_of(run_id: str, v: int) -> tuple[Path, Path]:
        d = st.run_dir(run_id)
        src = d / f"cut_v{v}.mp4"
        if not src.is_file():
            raise HTTPException(404, "no cut for this version")
        return d, src

    @app.get("/api/runs/{run_id}/filmstrip")
    def filmstrip(run_id: str, v: int):
        """The timeline's thumbnails: one JPEG row of 54x96 frames, one every 0.5 s of cut_vN.mp4 (cached in ui/cache/)."""
        d, src = cut_of(run_id, v)
        try:
            out = media.filmstrip(src, d / "ui" / "cache" / f"filmstrip_v{v}.jpg")
        except subprocess.CalledProcessError:
            raise HTTPException(500, "filmstrip extraction failed")
        return FileResponse(out, headers={"Cache-Control": "no-cache", "X-Thumb": f"{media.THUMB_W}x{media.THUMB_H}@{media.THUMB_STEP_S}"})

    def stem_of(run_id: str, v: int, track: str) -> tuple[Path, Path]:
        """A version with music: its speech stem (`voice`) or its ducked bed (`music`), as the render kept them; a bed
        rendered before the render kept it is rebuilt once into ui/cache/ ($0)."""
        d, cut = cut_of(run_id, v)
        try:
            edl = json.loads((d / f"edl_v{v}.json").read_text())
        except (OSError, ValueError):
            raise HTTPException(404, "no EDL for this version")
        rec = (edl.get("render") or {}).get("music") or {}
        if not edl.get("music") or not rec:
            raise HTTPException(404, "this version has no music, so the cut's own audio is the voice")
        if track == "voice":
            p = d / "render" / f"cut_v{v}.speech.m4a"
        elif track == "music":
            p = d / "render" / f"cut_v{v}.music.m4a"
            if not p.is_file():
                p = d / "ui" / "cache" / f"music_v{v}.m4a"
                with media._lock(p):
                    if not media.fresh(p, cut):
                        from ..engine.render import music_stem
                        try:
                            music_stem(d, edl, p)
                        except (ValueError, RuntimeError) as e:
                            raise HTTPException(500, f"could not rebuild the music bed: {e}")
        else:
            raise HTTPException(400, "track is voice or music")
        if not p.is_file():
            raise HTTPException(404, f"no {track} stem for this version")
        return d, p

    @app.get("/api/runs/{run_id}/stem")
    def stem(run_id: str, v: int, track: str):
        """One audio track of a version with music, for the editor's live faders."""
        _, p = stem_of(run_id, v, track)
        return FileResponse(p, media_type="audio/mp4", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs/{run_id}/peaks")
    def peaks(run_id: str, v: int, track: str = "cut"):
        """An audio track's waveform: the absolute peak of every 10 ms of cut_vN.mp4, or of its voice or music stem
        (cached in ui/cache/)."""
        if track == "cut":
            d, src = cut_of(run_id, v)
            name = f"peaks_v{v}.json"
        else:
            d, src = stem_of(run_id, v, track)
            name = f"peaks_v{v}_{track}.json"
        try:
            return media.peaks(src, d / "ui" / "cache" / name)
        except subprocess.CalledProcessError:
            raise HTTPException(500, "no audio stream in this cut")

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, v: int, preset: str = "full"):
        """cut_vN.mp4 as a download named after the run and version; preset=720 a 720x1280 copy for messaging and
        review links (H.264 CRF 26, AAC 128k, made once into ui/cache/)."""
        d, src = cut_of(run_id, v)
        if preset == "full":
            return FileResponse(src, media_type="video/mp4", filename=f"montivo-{run_id}-v{v}.mp4")
        if preset != "720":
            raise HTTPException(400, "preset: full or 720")
        try:
            out = media.small_copy(src, d / "ui" / "cache" / f"export_v{v}_720.mp4")
        except subprocess.CalledProcessError:
            raise HTTPException(500, "the 720p copy failed")
        return FileResponse(out, media_type="video/mp4", filename=f"montivo-{run_id}-v{v}-720p.mp4")

    @app.get("/api/runs/{run_id}/export/check")
    def export_check(run_id: str, v: int, aspect: str = "9:16"):
        """The Export page's pre-flight for version v (preflight.py): review, loudness, format, length, safe areas;
        aspect=4:5 or 1:1 checks the feed frame the export renders."""
        d, _ = cut_of(run_id, v)
        if aspect not in ASPECTS:
            raise HTTPException(400, f"aspect: one of {', '.join(ASPECTS)}")
        return {"checks": preflight.checks(d, v, aspect)}

    export_lock = threading.Lock()

    @app.get("/api/runs/{run_id}/export/cover")
    def cover_get(run_id: str, v: int):
        """Muse Spark's cover moments for version v, when they were asked for before (cover_vN.json)."""
        d, _ = cut_of(run_id, v)
        return read_json(d / f"cover_v{v}.json") or {"moments": []}

    @app.post("/api/runs/{run_id}/export/cover")
    def cover_post(run_id: str, b: dict):
        """{"v": N}: Muse Spark watches the version and names its best cover moments (engine/cover.py, one call, about
        $0.025, 10-20 s; python -m montivo.export --suggest-cover)."""
        v = int(b.get("v") or 0)
        d, _ = cut_of(run_id, v)
        need_key("Picking a cover (Muse Spark)", COST["cover"])
        if st.busy(run_id):
            raise HTTPException(409, f"{st.jobs[run_id].label} is running on this run: ask for a cover after it")
        args = [sys.executable, "-m", "montivo.export", str(d.relative_to(st.root)) if d.is_relative_to(st.root) else str(d),
                "--version", str(v), "--suggest-cover"]
        proc = subprocess.run(args, cwd=st.root, capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(500, f"cover failed: {(proc.stderr or proc.stdout).strip().splitlines()[-1:]}")
        return json.loads(proc.stdout)

    def export_cmd(d: Path, v: int, *extra: str) -> dict:
        args = [sys.executable, "-m", "montivo.export", str(d.relative_to(st.root)) if d.is_relative_to(st.root) else str(d),
                "--version", str(v), *extra]
        proc = subprocess.run(args, cwd=st.root, capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(500, f"cover failed: {(proc.stderr or proc.stdout).strip().splitlines()[-1:]}")
        return json.loads(proc.stdout)

    def cover_body(b: dict) -> tuple[int, str, dict]:
        from ..engine.thumb import TEMPLATES
        aspect = b.get("aspect") or "9:16"
        template = b.get("template") or "frame"
        try:
            v, t = int(b.get("v")), round(float(b.get("t")), 2)
        except (TypeError, ValueError):
            raise HTTPException(400, "v: a version, t: a cut second")
        if aspect not in ASPECTS or template not in TEMPLATES:
            raise HTTPException(400, f"aspect: one of {', '.join(ASPECTS)}; template: one of {', '.join(TEMPLATES)}")
        text = " ".join(str(b.get("text") or "").split())[:80]
        highlight = " ".join(str(b.get("highlight") or "").split())[:40]
        return v, aspect, {"t_s": t, "template": template, "text": text, "highlight": highlight}

    @app.post("/api/runs/{run_id}/cover/design")
    def cover_design(run_id: str, b: dict):
        """{"v": N, "aspect"}: the agent designs the cover (engine/thumb.py: the exact still from a contact sheet,
        three headlines, a template, a render, Spark's check at grid-tile size and one fix; about $0.04, 30-60 s;
        python -m montivo.export --design-cover). Returns cover_vN.json."""
        v = int(b.get("v") or 0)
        aspect = b.get("aspect") or "9:16"
        if aspect not in ASPECTS:
            raise HTTPException(400, f"aspect: one of {', '.join(ASPECTS)}")
        d, _ = cut_of(run_id, v)
        need_key("Designing a cover (Muse Spark)", COST["cover_design"])
        if st.busy(run_id):
            raise HTTPException(409, f"{st.jobs[run_id].label} is running on this run: design the cover after it")
        return export_cmd(d, v, "--design-cover", *(["--aspect", aspect] if aspect != "9:16" else []))

    @app.post("/api/runs/{run_id}/cover/mask")
    def cover_mask(run_id: str, b: dict):
        """{"v": N, "t": s, "aspect"}: SAM 3.1's person mask of that still for the cutout cover (one image request,
        about $0.01, cached under ui/cache/cover/)."""
        from ..engine.thumb import mask_path
        v, aspect, design = cover_body(b)
        d, _ = cut_of(run_id, v)
        if not mask_path(d, v, design["t_s"], aspect).is_file():
            need_key("Cutting out the speaker (SAM 3.1)", COST["cover_mask"])
            export_cmd(d, v, "--cover-mask", "--cover-t", f"{design['t_s']:.2f}", *(["--aspect", aspect] if aspect != "9:16" else []))
        return {"ok": True}

    @app.post("/api/runs/{run_id}/cover/render")
    def cover_render(run_id: str, b: dict):
        """{"v", "t", "template", "text", "highlight", "aspect"}: the designed cover as the export would write it, its
        grid tile and the measured checks ($0, about 0.1 s after the first call of a version). A cutout without its
        mask answers {"need_mask": true}."""
        from ..engine.thumb import NeedMask, cover_image, tile
        v, aspect, design = cover_body(b)
        d = st.run_dir(run_id)
        edl_path = d / f"edl_v{v}.json"
        if not edl_path.is_file():
            raise HTTPException(404, f"no edl_v{v}.json")
        key = hashlib.sha1(json.dumps([v, aspect, design, edl_path.stat().st_mtime_ns], sort_keys=True).encode()).hexdigest()[:16]
        out = d / "ui" / "cache" / "cover" / f"{key}.jpg"
        meta = out.with_suffix(".json")
        if not (out.is_file() and meta.is_file()):
            try:
                img, info = cover_image(d, v, design, aspect)
            except NeedMask:
                return {"need_mask": True}
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                raise HTTPException(500, f"cover failed: {e}")
            out.parent.mkdir(parents=True, exist_ok=True)
            for old in sorted(out.parent.glob("*.jpg"), key=lambda p: p.stat().st_mtime)[:-60]:  # keep the cache small
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)
                old.with_name(old.stem + "-tile.jpg").unlink(missing_ok=True)
            img.save(out, quality=88)
            tile(img).save(out.with_name(f"{key}-tile.jpg"), quality=92)
            write_json(meta, info)
        base = f"/api/runs/{run_id}/file/ui/cache/cover/"
        return {"image": base + out.name, "tile": base + f"{key}-tile.jpg", **read_json(meta)}

    @app.post("/api/runs/{run_id}/export/bundle")
    def export_bundle(run_id: str, b: dict):
        """{"v": N, "include": ["video", "clean", "srt", ...], "preset": "full" | "720", "name": stem, "cover_t": s,
        "aspect": "9:16" | "4:5" | "1:1"}: the files (python -m montivo.export, $0) under ui/cache/exports/<key>/, made
        once per choice; returns their download URLs and the zip's when there is more than one. "clean", and "video" in
        a feed aspect, render again, about as long as a render."""
        from ..export import KINDS
        d, _ = cut_of(run_id, int(b.get("v") or 0))
        v = int(b["v"])
        inc = [k for k in KINDS if k in (b.get("include") or [])]
        preset = b.get("preset") or "full"
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(b.get("name") or f"montivo-{run_id}-v{v}")).strip("-.")[:80] or f"montivo-{run_id}-v{v}"
        cover = b.get("cover_t")
        aspect = b.get("aspect") or "9:16"
        design = None
        if "cover" in inc and isinstance(b.get("cover_design"), dict):
            _, _, design = cover_body({**b["cover_design"], "v": b.get("v"), "t": cover or 0, "aspect": aspect})
            design.pop("t_s")
        if not inc or preset not in ("full", "720") or (cover is not None and not isinstance(cover, (int, float))) \
                or aspect not in ASPECTS:
            raise HTTPException(400, f"include: some of {', '.join(KINDS)}; preset: full or 720; cover_t: seconds; "
                                     f"aspect: one of {', '.join(ASPECTS)}")
        renders = "clean" in inc or (aspect != "9:16" and "video" in inc)
        if renders and st.busy(run_id):
            raise HTTPException(409, f"{st.jobs[run_id].label} is running: the "
                                     f"{'copy without captions' if 'clean' in inc else aspect + ' video'} renders after it")
        edl = d / f"edl_v{v}.json"
        key_in = [v, inc, preset, name, cover, edl.stat().st_mtime_ns] + ([aspect] if aspect != "9:16" else []) \
            + ([design] if design else [])
        key = hashlib.sha1(json.dumps(key_in).encode()).hexdigest()[:12]
        out = d / "ui" / "cache" / "exports" / key
        rec_p = out / "export.json"
        with export_lock:  # one export at a time: a render ("clean", a feed aspect) uses the run's render/ scratch folder
            if not rec_p.is_file():
                shutil.rmtree(out, ignore_errors=True)
                old = sorted((x for x in out.parent.glob("*") if x.is_dir()), key=lambda x: x.stat().st_mtime) if out.parent.is_dir() else []
                for x in old[:-3]:  # keep the last three bundles: each holds a copy of the cut
                    shutil.rmtree(x, ignore_errors=True)
                args = [sys.executable, "-m", "montivo.export", str(d.relative_to(st.root)) if d.is_relative_to(st.root) else str(d),
                        "--version", str(v), "--out", str(out), "--name", name, "--include", ",".join(inc), "--preset", preset,
                        *(["--cover-t", f"{float(cover):.2f}"] if cover is not None else []),
                        *(["--aspect", aspect] if aspect != "9:16" else []),
                        *(["--cover-design", json.dumps(design)] if design else [])]
                proc = subprocess.run(args, cwd=st.root, capture_output=True, text=True)
                if proc.returncode != 0:
                    raise HTTPException(500, f"export failed: {(proc.stderr or proc.stdout).strip().splitlines()[-1:]}")
                rec_p.write_text(proc.stdout)
        rec = json.loads(rec_p.read_text())
        url = lambda f: f"/api/runs/{run_id}/file/ui/cache/exports/{key}/{f}"  # noqa: E731
        return {"files": [{**f, "url": url(f["name"])} for f in rec["files"]],
                "zip": {**rec["zip"], "url": url(rec["zip"]["name"])} if rec.get("zip") else None}

    @app.get("/api/runs/{run_id}/log")
    def log(run_id: str, replay: bool = False):
        d = st.run_dir(run_id)
        lines = parse_log((d / "run.log").read_text(errors="replace")) if (d / "run.log").is_file() else []
        return {"lines": replay_script(lines) if replay else lines}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, replay: float = 0.0):
        """SSE. Live: the current session of run.log, then every new line, `job` when a job starts or ends and
        `changed` when a version, review or ledger file changes. replay=1 or 4: the finished run's story played
        back at that speed (pauses capped at 2.5 s), for recording the demo without a fresh run."""
        d = st.run_dir(run_id)
        logp = d / "run.log"

        def sse(event: str, data) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        async def live():
            text = logp.read_text(errors="replace") if logp.is_file() else ""
            lines = parse_log(text)
            last_session = lines[-1]["session"] if lines else 0
            j = st.job(run_id)
            yield sse("hello", {"mode": "live", "job": j.public() if j else None})
            if j and j.ended is None:  # a job is running: replay its session so the rail picks up where it is
                for ln in lines:
                    if ln["session"] == last_session:
                        yield sse("line", ln)
                yield sse("progress", job_progress(st, j))
            pos = len(text.encode())
            fp = fingerprint(d)
            seen_job = (j.id, j.ended) if j else None
            n_lines, session, t_last = len(lines), last_session, (lines[-1]["t"] if lines else None)
            buf = ""
            while not await request.is_disconnected():
                await asyncio.sleep(0.4)
                grew = False
                if logp.is_file() and logp.stat().st_size > pos:
                    grew = True
                    with logp.open("rb") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                    pos += len(chunk)
                    buf += chunk.decode(errors="replace")
                    *complete, buf = buf.split("\n")
                    for ln in parse_log("\n".join(complete)):
                        if t_last is None or ln["t"] < t_last or ln["msg"].startswith("montivo "):
                            session += 1
                        ln["session"], ln["i"] = session, n_lines
                        t_last = ln["t"]
                        n_lines += 1
                        yield sse("line", ln)
                j = st.job(run_id)
                key = (j.id, j.ended) if j else None
                if key != seen_job:
                    seen_job = key
                    yield sse("job", j.public())
                    yield sse("progress", job_progress(st, j))
                elif grew and j and j.ended is None:
                    yield sse("progress", job_progress(st, j))
                new_fp = fingerprint(d)
                if new_fp != fp:
                    fp = new_fp
                    yield sse("changed", {})

        def pause(a: dict, b: dict) -> float:
            """Replay: the wall seconds between two lines (the log's own gap at `replay` speed, capped at 2.5 s)."""
            gap = (b["t"] - a["t"]) if b["session"] == a["session"] else 1.0
            return min(max(gap, 0) / max(replay, 0.25), 2.5)

        async def replay_gen(speed: float):
            text = logp.read_text(errors="replace") if logp.is_file() else ""
            script = replay_script(parse_log(text))
            yield sse("hello", {"mode": "replay", "speed": speed, "lines": len(script)})
            ledger = read_json(d / "ledger.json")
            prev = None
            for k, ln in enumerate(script):
                if await request.is_disconnected():
                    return
                if prev is not None:
                    await asyncio.sleep(pause(prev, ln))
                prev = ln
                yield sse("line", ln)
                # the same progress a live job shows; the clock runs on the log's time, at the pace the next pause plays it
                p = progress(script[:k + 1], ledger=ledger, costs=COST)
                if p:
                    nxt = script[k + 1] if k + 1 < len(script) else None
                    same = nxt is not None and nxt["t"] >= ln["t"]  # the same engine clock (a session header keeps it)
                    wait = pause(ln, nxt) if nxt else 0.0
                    p.update(active=not p["finished"], replay=True, cap=round(nxt["t"] - ln["t"], 2) if same else 0.0,
                             rate=round((nxt["t"] - ln["t"]) / wait, 3) if same and wait > 0 else 0.0)
                    yield sse("progress", p)
            yield sse("replay_end", {})
            while not await request.is_disconnected():
                await asyncio.sleep(15)
                yield ": keep-alive\n\n"

        gen = replay_gen(max(replay, 0.25)) if replay else live()
        return StreamingResponse(gen, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------- actions

    def need_key(what: str, est: float = hosted.MIN_PAID_USD):
        """409 without the key, or on the hosted demo when the budget left does not cover `est` dollars."""
        if not has_key():
            raise HTTPException(409, f"{what} calls Meta models: set MODEL_API_KEY and restart montivo serve")
        if budget is not None:
            budget.require(what, est)

    def planner_of(b: dict) -> str | None:
        """{"planner": "spark" | "open"} -> None (Spark 1.3, the default) or the Muse Spark Open model id."""
        p = b.get("planner") or "spark"
        if p not in ("spark", "open"):
            raise HTTPException(400, "planner: spark or open")
        if p == "spark":
            return None
        if not has_open_key():
            raise HTTPException(409, "Muse Spark Open plans on Baseten: set BASETEN_API_KEY and restart montivo serve")
        return OPEN_MODEL

    durations: dict[str, tuple[tuple[int, int], float]] = {}

    def footage_duration(p: Path) -> float | None:
        """ffprobe's duration, kept until the file changes (the New project form shows it and prices the run by it)."""
        stt = p.stat()
        key = (stt.st_mtime_ns, stt.st_size)
        hit = durations.get(p.name)
        if not hit or hit[0] != key:
            hit = durations[p.name] = (key, media.probe_duration(p))
        return round(hit[1], 1) or None

    owners_file = st.footage / ".owners.json"  # hosted demo: uploaded clip -> the judge who uploaded it

    def visible(p: Path, request: Request) -> bool:
        """On the hosted demo a judge sees the seeded clips (no owner) and their own uploads, never another judge's."""
        if budget is None:
            return True
        o = (read_json(owners_file) or {}).get(p.name)
        return o is None or o == judge_of(request)

    @app.get("/api/footage")
    def footage(request: Request):
        st.footage.mkdir(exist_ok=True)
        files = [{"path": f"footage/{p.name}", "name": p.name, "mb": round(p.stat().st_size / 1e6, 1),
                  "duration_s": footage_duration(p)}
                 for p in sorted(st.footage.iterdir()) if p.suffix.lower() in VIDEO_EXT and visible(p, request)]
        return {"files": files}

    @app.get("/api/footage/{name}")
    def footage_file(name: str, request: Request):
        """A clip in footage/ for the New project preview (FileResponse answers Range requests)."""
        p = st.footage / name
        if not SAFE_NAME.match(name) or p.suffix.lower() not in VIDEO_EXT or not p.is_file() or not visible(p, request):
            raise HTTPException(404, "no such clip in footage/")
        return FileResponse(p)

    @app.put("/api/footage/{name}")
    async def upload(name: str, request: Request):
        """An upload never replaces a clip: earlier runs render from footage/ by name, so the file lands under the
        first free name (clip.mp4, clip-2.mp4, ...) once it is complete and within the size limit."""
        if not SAFE_NAME.match(name) or Path(name).suffix.lower() not in VIDEO_EXT:
            raise HTTPException(400, "name: letters, digits, . _ - and a video extension")
        st.footage.mkdir(exist_ok=True)
        part = st.footage / f".upload-{os.urandom(6).hex()}.part"
        size, limit = 0, (max_upload_mb or 0) * 1e6
        try:
            with part.open("wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if limit and size > limit:
                        raise HTTPException(413, f"the demo server takes uploads up to {max_upload_mb:.0f} MB")
                    fh.write(chunk)
            out = free_name(st.footage, name)  # no await from here on: two uploads cannot take the same name
            part.rename(out)
        finally:
            part.unlink(missing_ok=True)
        if budget is not None:
            owners = read_json(owners_file) or {}
            owners[out.name] = judge_of(request)
            write_json(owners_file, owners)
        return {"path": f"footage/{out.name}", "name": out.name, "mb": round(out.stat().st_size / 1e6, 1)}

    @app.post("/api/runs")
    async def new_run(request: Request):
        b = await request.json()
        src = (st.root / b.get("footage", "")).resolve()
        if st.footage.resolve() not in src.parents or not src.is_file() or not visible(src, request):
            raise HTTPException(400, "pick a file in footage/")
        dur = footage_duration(src) or 0.0
        if max_footage_s and dur > max_footage_s:
            raise HTTPException(400, f"this clip is {dur:.0f} s: the demo server takes clips up to {max_footage_s:.0f} s")
        need_key("A new run", COST["run_fixed"] + COST["run_per_s"] * dur
                 + (COST["review_loop"] if b.get("review", True) else 0.0)
                 + (0.07 if b.get("review", True) and b.get("hook_ab") else 0.0))
        brief = (b.get("brief") or "").strip()
        if not brief:
            raise HTTPException(400, "the brief is empty")
        rid = b.get("id") or free_name(st.runs, time.strftime("ui-%m%d-%H%M")).name  # two judges in one minute
        if not SAFE_NAME.match(rid) or (st.runs / rid / "run.json").exists():
            raise HTTPException(400, f"run id {rid!r} is taken or not a folder name")
        target = float(b.get("target") or 40)
        open_planner = planner_of(b)
        args = ["-m", "montivo.cli", "run", str(src.relative_to(st.root)), "--brief", brief, "--out", f"runs/{rid}",
                "--target", f"{target:g}"]
        if b.get("hflip"):
            args.append("--hflip")
        if b.get("review", True):
            args.append("--review")
            if b.get("hook_ab"):
                args.append("--hook-ab")
        if b.get("punch"):
            args.append("--punch")
        if open_planner:
            args += ["--planner", "open"]
        (st.runs / rid / "ui").mkdir(parents=True, exist_ok=True)
        if budget is not None:
            (st.runs / rid / "ui" / "owner").write_text(judge_of(request))
        job = start_job(st, rid, "run", "Run", args, chat={"role": "user", "text": brief, "kind": "brief"})
        return {"id": rid, "job": job.public()}

    @app.post("/api/runs/{run_id}/drop")
    async def drop(run_id: str, request: Request):
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        span = str(b.get("span", ""))
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        if not re.fullmatch(r"s\d+", span):
            raise HTTPException(400, "span id like s3")
        args = ["-m", "montivo.human", "drop", f"runs/{run_id}", "--span", span, "--from", str(frm), "--review", str(passes)]
        job = start_job(st, run_id, "drop", f"Delete clip {span}", args,
                        chat={"role": "user", "text": f"Delete clip {span} from v{frm}.", "chips": [span, f"v{frm}"], "kind": "drop"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/trim")
    async def trim(run_id: str, request: Request):
        """Move one clip edge to a word boundary (a timeline drag): the reviewer's trim_span / extend_span op."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        span, edge = str(b.get("span", "")), str(b.get("edge", ""))
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        if not re.fullmatch(r"s\d+", span) or edge not in ("in", "out"):
            raise HTTPException(400, "span id like s3 and edge in or out")
        try:
            word = int(b.get("word"))
        except (TypeError, ValueError):
            raise HTTPException(400, "word: the turn word index the edge lands on")
        args = ["-m", "montivo.human", "trim", f"runs/{run_id}", "--span", span, "--edge", edge, "--word", str(word),
                "--from", str(frm), "--review", str(passes)]
        side = "start" if edge == "in" else "end"
        job = start_job(st, run_id, "trim", f"Trim clip {span}", args,
                        chat={"role": "user", "text": f"Trim the {side} of clip {span} in v{frm}.", "chips": [span, f"v{frm}"],
                              "kind": "trim"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/punch")
    async def punch(run_id: str, request: Request):
        """Punch-ins on or off ({"on": true|false}) or one removed ({"skip": key}): the next version, ffmpeg only."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        skip, on = b.get("skip"), b.get("on")
        if skip is not None:
            skip = str(skip)
            if not PUNCH_KEY.fullmatch(skip):
                raise HTTPException(400, "skip: a punch-in key like emphasis:Montivo@47.120 or jump_cut:s4")
            flag, label, text = ["--skip", skip], "Remove a punch-in", f"Remove the punch-in {skip} from v{frm}."
        elif isinstance(on, bool):
            flag, label = ["--on" if on else "--off"], f"Punch-ins {'on' if on else 'off'}"
            text = f"Turn the punch-ins {'on' if on else 'off'} in v{frm}."
        else:
            raise HTTPException(400, "on: true or false, or skip: a punch-in key")
        args = ["-m", "montivo.human", "punch", f"runs/{run_id}", *flag, "--from", str(frm), "--review", str(passes)]
        job = start_job(st, run_id, "punch", label, args, chat={"role": "user", "text": text, "chips": [f"v{frm}"], "kind": "punch"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/restore")
    async def restore(run_id: str, request: Request):
        """An earlier version back as the newest ({"from": N}): its EDL copied to the next version and rendered, $0."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        try:
            frm = int(b["from"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "from: the version to restore")
        if not (d / f"edl_v{frm}.json").is_file():
            raise HTTPException(404, f"no version {frm}")
        args = ["-m", "montivo.human", "restore", f"runs/{run_id}", "--from", str(frm)]
        job = start_job(st, run_id, "restore", f"Restore Version {frm}", args,
                        chat={"role": "user", "text": f"Restore Version {frm}.", "chips": [f"v{frm}"], "kind": "restore"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/caption")
    async def caption(run_id: str, request: Request):
        """Retext caption lines ({"edits": [{"at": cut second, "text": words spoken}]}): the next version, ffmpeg only."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        edits = b.get("edits") or []
        if not isinstance(edits, list) or not 0 < len(edits) <= 40:
            raise HTTPException(400, "edits: 1 to 40 of {at, text}")
        flags, quoted = [], []
        for e in edits:
            try:
                at, text = float(e["at"]), " ".join(str(e["text"]).split())
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "each edit needs at (cut seconds) and text")
            if not text or len(text) > 200:
                raise HTTPException(400, "caption text: 1 to 200 characters")
            flags += [f"--at={at:.3f}", f"--text={text}"]  # = keeps a text that starts with "-" a value
            quoted.append(f"“{text}”")
        args = ["-m", "montivo.human", "caption", f"runs/{run_id}", *flags, "--from", str(frm), "--review", str(passes)]
        n = len(edits)
        job = start_job(st, run_id, "caption", f"Edit {n} caption line{'s' if n > 1 else ''}", args,
                        chat={"role": "user", "text": f"Caption{'s' if n > 1 else ''} in v{frm}: {', '.join(quoted)}.",
                              "chips": [f"v{frm}"], "kind": "caption"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/text")
    async def text_edit(run_id: str, request: Request):
        """Callouts ({"callouts": {span: text}}, "" removes one), the title card on or off ({"title_card": bool}) or new
        title words ({"title": text}: one Muse Image call, $0.01, so it needs the key): the next version."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        callouts = b.get("callouts") or {}
        title, card = b.get("title"), b.get("title_card")
        if not isinstance(callouts, dict) or not all(re.fullmatch(r"s\d+", k) and isinstance(v, str) and len(v) <= 80
                                                     for k, v in callouts.items()):
            raise HTTPException(400, "callouts: {span id: text up to 80 characters}")
        if title is not None and (not isinstance(title, str) or not 0 < len(title.strip()) <= 80):
            raise HTTPException(400, "title: 1 to 80 characters")
        if card is not None and not isinstance(card, bool):
            raise HTTPException(400, "title_card: true or false")
        if not callouts and title is None and card is None:
            raise HTTPException(400, "nothing to change")
        if title is not None or passes:
            need_key("New title card words (Muse Image)" if title is not None else "Reviewing the new cut",
                     (0.01 if title is not None else 0.0) + COST["review"] * passes)
        args = ["-m", "montivo.human", "text", f"runs/{run_id}", *[f"--callout={k}={v}" for k, v in callouts.items()],
                *([f"--title={title}"] if title is not None else []),
                *(["--title-on" if card else "--title-off"] if card is not None else []),
                "--from", str(frm), "--review", str(passes)]
        what = [f"{k}: “{v}”" if v else f"{k}: no callout" for k, v in callouts.items()]
        if title is not None:
            what.append(f"title: “{title}”")
        if card is not None:
            what.append("title card on" if card else "title card off")
        job = start_job(st, run_id, "text", "Edit text", args,
                        chat={"role": "user", "text": f"Text in v{frm}: {'; '.join(what)}.", "chips": [f"v{frm}"], "kind": "text"})
        return {"job": job.public()}

    @app.get("/api/music")
    def music_tracks():
        """The Music panel's choices: the bundled CC0 tracks (montivo/music/tracks.json, each with its author and source
        page), the defaults and ranges."""
        from ..engine import music as bed
        return {"tracks": [{k: t.get(k) for k in ("id", "name", "mood", "loop_s", "licence", "author", "source")} | {"url": f"/api/music/{t['id']}"}
                           for t in bed.tracks()],
                "default": {"level_db": bed.LEVEL_DB, "duck_db": bed.DUCK_DB},
                "level_range": list(bed.LEVEL_RANGE), "duck_range": list(bed.DUCK_RANGE)}

    @app.get("/api/music/{track_id}")
    def music_file(track_id: str):
        """A bundled track's loop for the panel's preview player."""
        from ..engine import music as bed
        try:
            t = bed.track(track_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return FileResponse(t["path"], media_type="audio/mp4", headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/runs/{run_id}/music")
    async def music_edit(run_id: str, request: Request):
        """A music bed under the speech ({"track": id, "level_db": -20, "duck_db": -12, "voice_db": 0}), another track
        or level, or none ({"track": null}): the next version, ffmpeg only. "mix": true names it the timeline faders'."""
        from ..engine.music import normalize_music
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        track = b.get("track")
        if track is None:
            flag, label, text = ["--off"], "Music off", f"Take the music out of v{frm}."
        else:
            try:
                m = normalize_music({k: b[k] for k in ("track", "level_db", "duck_db", "voice_db") if b.get(k) is not None})
            except ValueError as e:
                raise HTTPException(400, str(e))
            flag = ["--track", m["track"], "--level", f"{m['level_db']:g}", "--duck", f"{m['duck_db']:g}"]
            voice = ""
            if b.get("voice_db") is not None:  # the timeline's Voice fader; left out keeps the version's own
                flag += ["--voice", f"{m.get('voice_db', 0.0):g}"]
                voice = f", voice {m.get('voice_db', 0.0):+g} dB"
            label = "Mix" if b.get("mix") else "Music"
            text = (f"Mix in v{frm}: music at {m['level_db']:g} LUFS{voice}." if b.get("mix") else
                    f"Music in v{frm}: {m['track'].replace('_', ' ')} at {m['level_db']:g} LUFS, ducked {m['duck_db']:g} dB{voice}.")
        args = ["-m", "montivo.human", "music", f"runs/{run_id}", *flag, "--from", str(frm), "--review", str(passes)]
        job = start_job(st, run_id, "music", label, args, chat={"role": "user", "text": text, "chips": [f"v{frm}"], "kind": "music"})
        return {"job": job.public()}

    looks_lock = threading.Lock()

    def saved_path(request: Request) -> Path:
        """A person's saved looks (their brand kit): <root>/looks/saved.json, or one file per judge on the hosted demo."""
        who = "saved" if budget is None else re.sub(r"[^A-Za-z0-9_-]", "", judge_of(request))[:40] or "guest"
        return st.root / "looks" / f"{who}.json"

    def saved_looks(request: Request) -> list[dict]:
        return (read_json(saved_path(request)) or {}).get("looks") or []

    @app.get("/api/looks")
    def looks(request: Request):
        """The Style panel's choices: every preset as a full look, the fonts, the default (marker) look, and the looks
        this person saved."""
        return {"presets": [{"id": k, "name": PRESET_NAMES[k], "look": normalize_look({"preset": k}).to_json()} for k in PRESETS],
                "fonts": [{"id": k, "name": v[0]} for k, v in FONTS.items()], "default": normalize_look(None).to_json(),
                "saved": saved_looks(request)}

    @app.post("/api/looks/saved")
    def save_look(request: Request, b: dict):
        """{"name": "Nocta", "look": {...}}: kept with the person's looks (same name replaces it, 24 at most)."""
        name = " ".join(str(b.get("name") or "").split())[:40]
        if not name:
            raise HTTPException(400, "name: 1 to 40 characters")
        try:
            look = normalize_look(b.get("look")).to_json()
        except ValueError as e:
            raise HTTPException(400, f"look: {e}")
        with looks_lock:
            items = [x for x in saved_looks(request) if x["name"].lower() != name.lower()]
            if len(items) >= 24:
                raise HTTPException(409, "24 saved looks at most: delete one first")
            sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "look"
            while any(x["id"] == sid for x in items):
                sid += "-2"
            items.append({"id": sid, "name": name, "look": look, "saved": time.strftime("%Y-%m-%d %H:%M")})
            write_json(saved_path(request), {"looks": items})
        return {"saved": items}

    @app.delete("/api/looks/saved/{sid}")
    def delete_look(request: Request, sid: str):
        with looks_lock:
            items = saved_looks(request)
            if not any(x["id"] == sid for x in items):
                raise HTTPException(404, "no such saved look")
            items = [x for x in items if x["id"] != sid]
            write_json(saved_path(request), {"looks": items})
        return {"saved": items}

    def style_body(b: dict) -> tuple[dict | None, dict[str, list[float] | None]]:
        look, places = b.get("look"), b.get("callouts") or {}
        if look is not None:
            if not isinstance(look, dict):
                raise HTTPException(400, "look: an object")
            try:
                normalize_look(look)
            except ValueError as e:
                raise HTTPException(400, f"look: {e}")
        if not isinstance(places, dict) or not all(
                re.fullmatch(r"s\d+", k) and (v is None or (isinstance(v, list) and len(v) == 2
                                                           and all(isinstance(x, (int, float)) for x in v)))
                for k, v in places.items()):
            raise HTTPException(400, "callouts: {span id: [x, y] or null}")
        return look, places

    @app.post("/api/runs/{run_id}/preview")
    def preview(run_id: str, b: dict):
        """The Style panel's still ({"v": N, "t": cut second, "look": {...}, "callouts": {span: [x, y] | null}}): the
        frame of version N at t as a new version with that look would burn it (engine/look.py), saved under
        ui/cache/preview/, and the caption and callout boxes on screen then. "aspect" 4:5 or 1:1 lays it out in that
        frame (the Export page's feed cover). $0, about 0.4 s."""
        from ..engine.frame import with_aspect
        from ..engine.look import preview as still, with_style
        d = st.run_dir(run_id)
        try:
            v, t = int(b.get("v")), round(float(b.get("t")), 2)
        except (TypeError, ValueError):
            raise HTTPException(400, "v: a version, t: a cut second")
        look, places = style_body(b)
        aspect = b.get("aspect") or "9:16"
        if aspect not in ASPECTS:
            raise HTTPException(400, f"aspect: one of {', '.join(ASPECTS)}")
        edl_path = d / f"edl_v{v}.json"
        if not edl_path.is_file():
            raise HTTPException(404, f"no edl_v{v}.json")
        key_in = [v, t, look, places, edl_path.stat().st_mtime_ns] + ([aspect] if aspect != "9:16" else [])
        key = hashlib.sha1(json.dumps(key_in, sort_keys=True).encode()).hexdigest()[:16]
        out = d / "ui" / "cache" / "preview" / f"{key}.jpg"
        meta = out.with_suffix(".json")
        if not (out.is_file() and meta.is_file()):
            try:
                img, info = still(d, with_aspect(with_style(read_json(edl_path), look, places), aspect), t, d / f"cut_v{v}.mp4")
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                raise HTTPException(500, f"preview failed: {e}")
            out.parent.mkdir(parents=True, exist_ok=True)
            for old in sorted(out.parent.glob("*.jpg"), key=lambda p: p.stat().st_mtime)[:-80]:  # keep the cache small
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)
            img.convert("RGB").save(out, quality=86)
            write_json(meta, info)
        return {"image": f"/api/runs/{run_id}/file/ui/cache/preview/{out.name}", **read_json(meta)}

    @app.post("/api/runs/{run_id}/style")
    async def style_edit(run_id: str, request: Request):
        """The caption look and callout places ({"look": {...}, "callouts": {span: [x, y] | null}}): the next version,
        ffmpeg only."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        look, places = style_body(b)
        prompt = " ".join(str(b.get("prompt") or "").split())[:300] or None
        if look is None and not places and not prompt:
            raise HTTPException(400, "nothing to change")
        if prompt and (look is not None or places):
            raise HTTPException(400, "a prompt or a look, not both")
        if passes or prompt:
            need_key("Styling from a sentence (Muse Spark)" if prompt else "Reviewing the new cut",
                     (COST["look"] if prompt else 0.0) + COST["review"] * passes)
        if prompt:
            args = ["-m", "montivo.human", "style", f"runs/{run_id}", "--prompt", prompt, "--from", str(frm), "--review", str(passes)]
            job = start_job(st, run_id, "style", "Style", args,
                            chat={"role": "user", "text": f"Style v{frm}: {prompt}", "chips": [f"v{frm}"], "kind": "style"})
            return {"job": job.public()}
        args = ["-m", "montivo.human", "style", f"runs/{run_id}", *(["--look", json.dumps(look)] if look is not None else []),
                *[f"--callout={k}={'auto' if v is None else f'{v[0]:.0f},{v[1]:.0f}'}" for k, v in places.items()],
                "--from", str(frm), "--review", str(passes)]
        what = []
        if look is not None:
            what.append(f"caption style {PRESET_NAMES.get(look.get('preset') or 'marker', 'custom')}"
                        + (f", fixed at y {look.get('top')}" if look.get("position") == "fixed" else ""))
        what += [f"callout {k} {'back to auto' if v is None else 'moved'}" for k, v in places.items()]
        job = start_job(st, run_id, "style", "Style", args,
                        chat={"role": "user", "text": f"Style in v{frm}: {'; '.join(what)}.", "chips": [f"v{frm}"], "kind": "style"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/reorder")
    async def reorder(run_id: str, request: Request):
        """Move a clip in front of another ({"span": "s4", "before": "s2"}) or to the end ("before": null)."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        span, before = str(b.get("span", "")), b.get("before")
        if not re.fullmatch(r"s\d+", span) or (before is not None and not re.fullmatch(r"s\d+", str(before))):
            raise HTTPException(400, "span and before: span ids like s3 (before null = to the end)")
        args = ["-m", "montivo.human", "reorder", f"runs/{run_id}", "--span", span,
                *(["--before", str(before)] if before is not None else ["--end"]), "--from", str(frm), "--review", str(passes)]
        job = start_job(st, run_id, "reorder", f"Move clip {span}", args,
                        chat={"role": "user", "chips": [span, f"v{frm}"], "kind": "reorder",
                              "text": f"Move clip {span} {'before ' + str(before) if before else 'to the end'} in v{frm}."})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/cut")
    async def cut(run_id: str, request: Request):
        """Delete the words of a transcript selection ({"words": [{"span": "s2", "from": 16, "to": 18}, ...]}, turn
        word indices) in one version: whole clips dropped, clip edges trimmed, the middle of a clip split."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        ranges = b.get("words")
        if not isinstance(ranges, list) or not ranges:
            raise HTTPException(400, "words: a list of {span, from, to}")
        flags, spans = [], []
        for r in ranges:
            try:
                span, a, z = str(r["span"]), int(r["from"]), int(r["to"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "words: a list of {span, from, to}")
            if not re.fullmatch(r"s\d+", span) or not 0 <= a <= z:
                raise HTTPException(400, "span ids like s3 and word indices from <= to")
            flags += ["--words", f"{span}:{a}-{z}"]
            spans.append(span)
        n = sum(int(r["to"]) - int(r["from"]) + 1 for r in ranges)
        args = ["-m", "montivo.human", "cut", f"runs/{run_id}", *flags, "--from", str(frm), "--review", str(passes)]
        quote = str(b.get("text") or "").strip()[:120]
        job = start_job(st, run_id, "cut", "Delete words", args,
                        chat={"role": "user", "chips": [*dict.fromkeys(spans), f"v{frm}"], "kind": "cut",
                              "text": f"Delete {n} word{'s' if n != 1 else ''} from v{frm}" + (f": “{quote}”." if quote else ".")})
        return {"job": job.public()}

    @app.get("/api/runs/{run_id}/heads")
    def run_heads(run_id: str):
        return heads(st.run_dir(run_id))

    @app.get("/api/runs/{run_id}/head/{hid}.jpg")
    def head_jpg(run_id: str, hid: str):
        d = st.run_dir(run_id)
        info = heads(d)
        h = next((x for x in info["heads"] if x["id"] == hid), None)
        if h is None or not info["video"]:
            raise HTTPException(404, "no such head")
        src = d / info["video"]
        scale = 1.0
        if info["video"] == "proxy.mp4" and info["source"].get("width"):  # the boxes are on the tracked video's pixels
            from ..engine.ingest import ffprobe
            scale = ffprobe(src).width / info["source"]["width"]
        try:
            out = media.head_thumb(src, h["t"], [c * scale for c in h["box"]], d / "ui" / "cache" / f"head_{hid}.jpg")
        except subprocess.CalledProcessError:
            raise HTTPException(500, "head thumbnail failed")
        return FileResponse(out, headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/runs/{run_id}/pin")
    async def pin(run_id: str, request: Request):
        """{"speaker": "A", "head": "3"}: the crop follows that head when A talks; "head": null drops the pin."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        spk, head = str(b.get("speaker", "")), b.get("head")
        if not re.fullmatch(r"[A-Za-z0-9]{1,8}", spk) or (head is not None and not re.fullmatch(r"\d{1,4}", str(head))):
            raise HTTPException(400, "speaker: a Voice label like A; head: a head id or null")
        args = ["-m", "montivo.human", "pin", f"runs/{run_id}", "--speaker", spk, "--head", str(head) if head is not None else "none",
                "--from", str(frm), "--review", str(passes)]
        job = start_job(st, run_id, "pin", f"Pin speaker {spk}", args,
                        chat={"role": "user", "chips": [f"v{frm}"], "kind": "pin",
                              "text": f"Follow head {head} when speaker {spk} talks, in v{frm}." if head is not None
                              else f"Speaker {spk} back on the speaker map, in v{frm}."})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/replan")
    async def replan(run_id: str, request: Request):
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        model = planner_of(b)
        text = (b.get("instruction") or "").strip()
        if not text:
            raise HTTPException(400, "the instruction is empty")
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        need_key("Re-planning", COST["plan_open" if model else "plan"] + COST["review"] * passes)
        chips = [str(c) for c in b.get("chips") or []]
        instr = text + (f" (about {', '.join(chips)})" if chips else "")
        args = ["-m", "montivo.human", "replan", f"runs/{run_id}", "--instruction", instr, "--from", str(frm),
                "--review", str(passes), *(["--model", model] if model else [])]
        job = start_job(st, run_id, "replan", "Ask Montivo" + (" (Muse Spark Open)" if model else ""), args,
                        chat={"role": "user", "text": text, "chips": chips + [f"v{frm}"], "kind": "instruction"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/ask")
    async def ask(run_id: str, request: Request):
        """Ask Montivo: {"message", "chips", "from", "review", "planner"} -> montivo.agent: one Spark call picks the
        edits (music, look, punch-ins, captions, callouts, title, clips, a re-plan), one new version. Without the key
        (or past the hosted budget) a keyword reader does the few edits it can name safely, $0."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        model = planner_of(b)
        text = (b.get("message") or b.get("instruction") or "").strip()
        if not text:
            raise HTTPException(400, "the message is empty")
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        use = key_ok()
        if not use:
            passes = 0  # the keyword reader edits for $0: no review without the key
        if use and budget is not None:
            budget.require("Ask Montivo", COST["ask"] + COST["plan_open" if model else "plan"] + COST["review"] * passes)
        chips = [str(c) for c in b.get("chips") or []]
        msg = text + (f" (about {', '.join(chips)})" if chips else "")
        args = ["-m", "montivo.agent", f"runs/{run_id}", "--message", msg, "--from", str(frm), "--review", str(passes),
                "--planner", "open" if model else "spark", *([] if use else ["--no-model"])]
        job = start_job(st, run_id, "ask", "Ask Montivo", args,
                        chat={"role": "user", "text": text, "chips": chips + [f"v{frm}"], "kind": "ask"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/review")
    async def review(run_id: str, request: Request):
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("passes") or 3)
        need_key("The review loop" if passes > 1 else "A blind review", COST["review"] * passes)
        args = ["-m", "montivo.cli", "review", f"runs/{run_id}", "--edl", f"edl_v{frm}.json", "--passes", str(passes)]
        job = start_job(st, run_id, "review", "Review loop" if passes > 1 else "Blind review", args,
                        chat={"role": "user", "kind": "review",
                              "text": f"Review v{frm} blind, up to {passes} passes." if passes > 1 else f"Review v{frm} blind."})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/hook")
    async def hook(run_id: str, request: Request):
        """The hook A/B on one version: the next version opens on another line, both are reviewed blind, the higher
        (pass, overall) is kept (`montivo hook`, one or two reviews, $0.10 budget)."""
        run_id = await own(run_id, request)
        d = st.run_dir(run_id)
        b = await request.json()
        need_key("The hook A/B", 0.07)
        frm = int(b.get("from") or latest_version(d))
        if not (d / f"edl_v{frm}.json").is_file():
            raise HTTPException(400, f"no edl_v{frm}.json in this run")
        args = ["-m", "montivo.cli", "hook", f"runs/{run_id}", "--edl", f"edl_v{frm}.json", "--budget", "0.10"]
        job = start_job(st, run_id, "hook", "Hook A/B", args,
                        chat={"role": "user", "text": f"Try another opening line on v{frm}; the blind reviewer judges both.",
                              "chips": [f"v{frm}"], "kind": "hook"})
        return {"job": job.public()}

    # ------------------------------------------------------------------- archive (a move, never a delete)

    def side_logs(folder: Path, run_id: str) -> list[Path]:
        """The logs a run left next to its folder (runs/014.stdout.log, runs/002_300s.log): they move with it."""
        return [p for p in folder.glob(f"{run_id}.*log") if p.is_file() and p.name.split(".")[0] == run_id]

    def judges(d: Path, request: Request) -> bool:
        """On the hosted demo a judge archives, lists and restores only their own projects."""
        return budget is None or owner(d) == judge_of(request)

    @app.get("/api/archive")
    def archived(request: Request):
        return {"runs": [r for r in list_runs(st.runs / ARCHIVE) if judges(st.runs / ARCHIVE / r["id"], request)]}

    @app.post("/api/runs/{run_id}/archive")
    def archive(run_id: str, request: Request):
        """Move runs/<id> (and its side logs) to runs/_archive/<id>: off the project list, restorable, nothing deleted."""
        d = st.run_dir(run_id)
        if st.busy(run_id):
            raise HTTPException(409, f"{st.jobs[run_id].label} is still running on this run")
        if is_sample(d):
            raise HTTPException(403, "the sample projects stay on the demo server for everyone")
        if not judges(d, request):
            raise HTTPException(404, "no such run")
        dest = st.runs / ARCHIVE / run_id
        if dest.exists():
            raise HTTPException(409, f"runs/{ARCHIVE}/{run_id} already exists: restore or rename that one first")
        dest.parent.mkdir(exist_ok=True)
        shutil.move(str(d), str(dest))
        for p in side_logs(st.runs, run_id):
            shutil.move(str(p), str(dest.parent / p.name))
        st.jobs.pop(run_id, None)
        return {"archived": run_id}

    @app.post("/api/archive/{run_id}/restore")
    def restore(run_id: str, request: Request):
        if not SAFE_NAME.match(run_id):
            raise HTTPException(404, "no such run")
        src = st.runs / ARCHIVE / run_id
        if not (src / "run.json").is_file() or not judges(src, request):
            raise HTTPException(404, f"no archived run {run_id}")
        if (st.runs / run_id).exists():
            raise HTTPException(409, f"runs/{run_id} exists again: rename one of the two first")
        shutil.move(str(src), str(st.runs / run_id))
        for p in side_logs(st.runs / ARCHIVE, run_id):
            shutil.move(str(p), str(st.runs / p.name))
        return {"restored": run_id}

    @app.post("/api/runs/{run_id}/stop")
    def stop(run_id: str):
        j = st.job(run_id)
        if not j or j.ended is not None or not j.proc:
            raise HTTPException(409, "nothing running")
        j.proc.terminate()
        return {"job": j.public()}

    @app.exception_handler(HTTPException)
    async def http_error(_req, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    if STATIC.is_dir():
        app.mount("/", WebFiles(directory=STATIC, html=True), name="web")
    else:
        @app.get("/")
        def no_build():
            return JSONResponse({"error": "no UI build in montivo/server/static: run `make web`"}, status_code=503)

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="montivo-ui", description="Montivo UI: run folders, live log, review room.")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="project folder with runs/ and footage/ (default: here)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    import uvicorn
    print(f"montivo-ui: http://{a.host}:{a.port}  (runs from {a.root.resolve() / 'runs'}; "
          f"MODEL_API_KEY {'set' if has_key() else 'not set: finished runs and drop span only'})", flush=True)
    uvicorn.run(create_app(a.root), host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
