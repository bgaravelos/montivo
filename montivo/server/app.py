"""`montivo-ui`: FastAPI + SSE over the run folders, the engine started as a subprocess, the React build served.

    .venv/bin/montivo-ui                 http://127.0.0.1:8765, runs/ and footage/ of the current folder
    .venv/bin/montivo-ui --port 9000 --root ~/Documents/montivo

GET endpoints only read files, so a finished run folder opens without MODEL_API_KEY; the actions that call a model
answer 409 when the key is not set (the key itself is never read beyond "is it set").
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import media
from .runs import fingerprint, list_runs, parse_log, read_json, replay_script, run_state

STATIC = Path(__file__).parent / "static"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

# rough prices for the cost hint on each button (README "Measured behaviour")
COST = {"plan": 0.05, "review": 0.035, "run_per_s": 0.004}


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


def has_key() -> bool:
    return bool(os.environ.get("MODEL_API_KEY"))


def chat_append(run_dir: Path, entry: dict) -> None:
    p = run_dir / "ui" / "chat.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps({"t": time.time(), **entry}) + "\n")


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
        (d / "ui").mkdir(parents=True, exist_ok=True)
        jid = time.strftime("%Y%m%d-%H%M%S")
        out = d / "ui" / f"job-{jid}-{kind}.out"
        cmd = [sys.executable, *args]
        job = Job(id=jid, run=run_id, kind=kind, label=label, cmd=cmd, out=str(out))
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

    @app.get("/api/health")
    def health():
        return {"ok": True, "root": str(st.root), "has_key": has_key(), "costs": COST}

    @app.get("/api/runs")
    def runs():
        out = list_runs(st.runs)
        for r in out:
            j = st.job(r["id"])
            r["job"] = j.public() if j else None
        return {"runs": out, "has_key": has_key()}

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        d = st.run_dir(run_id)
        s = run_state(d)
        j = st.job(run_id)
        s["job"] = j.public() if j else None
        s["has_key"] = has_key()
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
    def frame(run_id: str, v: int, t: float):
        """The frame the reviewer saw at cut time t: from the review copy (720x1280 with the clock) when it exists."""
        d = st.run_dir(run_id)
        src = d / "review" / f"review_v{v}" / f"cut_v{v}_review.mp4"
        if not src.is_file():
            src = d / f"cut_v{v}.mp4"
        if not src.is_file():
            raise HTTPException(404, "no cut for this version")
        out = d / "ui" / "frames" / f"v{v}_{round(max(t, 0) * 10):05d}.jpg"
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

    @app.get("/api/runs/{run_id}/peaks")
    def peaks(run_id: str, v: int):
        """The audio track's waveform: the absolute peak of every 10 ms of cut_vN.mp4 (cached in ui/cache/)."""
        d, src = cut_of(run_id, v)
        try:
            return media.peaks(src, d / "ui" / "cache" / f"peaks_v{v}.json")
        except subprocess.CalledProcessError:
            raise HTTPException(500, "no audio stream in this cut")

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, v: int):
        """cut_vN.mp4 as a download named after the run and version."""
        _, src = cut_of(run_id, v)
        return FileResponse(src, media_type="video/mp4", filename=f"montivo-{run_id}-v{v}.mp4")

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
            pos = len(text.encode())
            fp = fingerprint(d)
            seen_job = (j.id, j.ended) if j else None
            n_lines, session, t_last = len(lines), last_session, (lines[-1]["t"] if lines else None)
            buf = ""
            while not await request.is_disconnected():
                await asyncio.sleep(0.4)
                if logp.is_file() and logp.stat().st_size > pos:
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
                new_fp = fingerprint(d)
                if new_fp != fp:
                    fp = new_fp
                    yield sse("changed", {})

        async def replay_gen(speed: float):
            text = logp.read_text(errors="replace") if logp.is_file() else ""
            script = replay_script(parse_log(text))
            yield sse("hello", {"mode": "replay", "speed": speed, "lines": len(script)})
            prev = None
            for ln in script:
                if await request.is_disconnected():
                    return
                if prev is not None:
                    gap = (ln["t"] - prev["t"]) if ln["session"] == prev["session"] else 1.0
                    await asyncio.sleep(min(max(gap, 0) / speed, 2.5))
                prev = ln
                yield sse("line", ln)
            yield sse("replay_end", {})
            while not await request.is_disconnected():
                await asyncio.sleep(15)
                yield ": keep-alive\n\n"

        gen = replay_gen(max(replay, 0.25)) if replay else live()
        return StreamingResponse(gen, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------- actions

    def need_key(what: str):
        if not has_key():
            raise HTTPException(409, f"{what} calls Meta models: set MODEL_API_KEY and restart montivo serve")

    @app.get("/api/footage")
    def footage():
        st.footage.mkdir(exist_ok=True)
        files = [{"path": f"footage/{p.name}", "name": p.name, "mb": round(p.stat().st_size / 1e6, 1)}
                 for p in sorted(st.footage.iterdir()) if p.suffix.lower() in VIDEO_EXT]
        return {"files": files}

    @app.put("/api/footage/{name}")
    async def upload(name: str, request: Request):
        if not SAFE_NAME.match(name) or Path(name).suffix.lower() not in VIDEO_EXT:
            raise HTTPException(400, "name: letters, digits, . _ - and a video extension")
        st.footage.mkdir(exist_ok=True)
        out = st.footage / name
        with out.open("wb") as fh:
            async for chunk in request.stream():
                fh.write(chunk)
        return {"path": f"footage/{name}", "mb": round(out.stat().st_size / 1e6, 1)}

    @app.post("/api/runs")
    async def new_run(request: Request):
        b = await request.json()
        need_key("A new run")
        src = (st.root / b.get("footage", "")).resolve()
        if st.footage.resolve() not in src.parents or not src.is_file():
            raise HTTPException(400, "pick a file in footage/")
        brief = (b.get("brief") or "").strip()
        if not brief:
            raise HTTPException(400, "the brief is empty")
        rid = b.get("id") or time.strftime("ui-%m%d-%H%M")
        if not SAFE_NAME.match(rid) or (st.runs / rid / "run.json").exists():
            raise HTTPException(400, f"run id {rid!r} is taken or not a folder name")
        target = float(b.get("target") or 40)
        args = ["-m", "montivo.cli", "run", str(src.relative_to(st.root)), "--brief", brief, "--out", f"runs/{rid}",
                "--target", f"{target:g}"]
        if b.get("hflip"):
            args.append("--hflip")
        if b.get("review", True):
            args.append("--review")
        (st.runs / rid).mkdir(parents=True, exist_ok=True)
        job = start_job(st, rid, "run", "Run", args, chat={"role": "user", "text": brief, "kind": "brief"})
        return {"id": rid, "job": job.public()}

    @app.post("/api/runs/{run_id}/drop")
    async def drop(run_id: str, request: Request):
        d = st.run_dir(run_id)
        b = await request.json()
        span = str(b.get("span", ""))
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut")
        if not re.fullmatch(r"s\d+", span):
            raise HTTPException(400, "span id like s3")
        args = ["-m", "montivo.human", "drop", f"runs/{run_id}", "--span", span, "--from", str(frm), "--review", str(passes)]
        job = start_job(st, run_id, "drop", f"Delete clip {span}", args,
                        chat={"role": "user", "text": f"Delete clip {span} from v{frm}.", "chips": [span, f"v{frm}"], "kind": "drop"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/trim")
    async def trim(run_id: str, request: Request):
        """Move one clip edge to a word boundary (a timeline drag): the reviewer's trim_span / extend_span op."""
        d = st.run_dir(run_id)
        b = await request.json()
        span, edge = str(b.get("span", "")), str(b.get("edge", ""))
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        if passes:
            need_key("Reviewing the new cut")
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

    @app.post("/api/runs/{run_id}/replan")
    async def replan(run_id: str, request: Request):
        d = st.run_dir(run_id)
        b = await request.json()
        need_key("Re-planning")
        text = (b.get("instruction") or "").strip()
        if not text:
            raise HTTPException(400, "the instruction is empty")
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("review") or 0)
        chips = [str(c) for c in b.get("chips") or []]
        instr = text + (f" (about {', '.join(chips)})" if chips else "")
        args = ["-m", "montivo.human", "replan", f"runs/{run_id}", "--instruction", instr, "--from", str(frm),
                "--review", str(passes)]
        job = start_job(st, run_id, "replan", "Ask Montivo", args,
                        chat={"role": "user", "text": text, "chips": chips + [f"v{frm}"], "kind": "instruction"})
        return {"job": job.public()}

    @app.post("/api/runs/{run_id}/review")
    async def review(run_id: str, request: Request):
        d = st.run_dir(run_id)
        b = await request.json()
        need_key("The review loop")
        frm = int(b.get("from") or latest_version(d))
        passes = int(b.get("passes") or 3)
        args = ["-m", "montivo.cli", "review", f"runs/{run_id}", "--edl", f"edl_v{frm}.json", "--passes", str(passes)]
        job = start_job(st, run_id, "review", "Review loop", args,
                        chat={"role": "user", "text": f"Review v{frm} blind, up to {passes} passes.", "kind": "review"})
        return {"job": job.public()}

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
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="web")
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
