"""The editor's live preview drafts: structural edits (split, trim, reorder, drop, cut, insert, pauses) run in this
process on a draft EDL, in milliseconds and without a render, while the browser plays the draft from the edit proxy;
one render happens at "Make Version N".

    GET    /api/runs/{id}/draft[?edl=1]   the open draft (its ops, spans, runtime; the whole EDL with edl=1), or none
    POST   /api/runs/{id}/draft           {"op": "split", "args": {"span": "s3", "word": 12}[, "from": 5]} -> one op more
    POST   /api/runs/{id}/draft/undo      the last op off (onto the redo stack); the draft is replayed from its base
    POST   /api/runs/{id}/draft/redo      the last undone op back
    DELETE /api/runs/{id}/draft           the draft discarded
    POST   /api/runs/{id}/draft/commit    {"review": 0..3, "rebase": true} -> ONE job: `python -m montivo.human draft`
                                          writes one edl_vN with one patch listing the ops and renders it once

A draft lives in the run folder, `ui/draft/` (on the hosted demo per judge, `ui/draft/<judge>/`, so a sample is only
copied to the judge's own run at the commit, not at the first click): `state.json` (id, base version, the ops, the redo
stack, a commit in flight) and `edl.json`, the draft EDL as it stands (what the preview plays). Each op is
`human.step`, the pure op the CLI edit runs, so the committed version is byte for byte the EDL the draft showed. The
ops take the pure functions' arguments (human.OPS): split {span, word}, trim {span, edge, word}, reorder {span,
before|null}, drop {span}, cut {ranges: [[span, first, last], ...]}, insert {turn, a, b, before: span|"end"|null},
pauses {over, keep}. $0: no model is called. An op never runs beside a job on its run (a job writes the run's versions),
and one lock per run keeps two ops of one draft in order. A version made after the draft started (another tab, the
chat) is not lost: the commit replays the ops on the newest version, or refuses with the op that no longer applies.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .. import human
from ..engine.api import dump_json

SPAN = re.compile(r"s\d{1,4}")
JUDGE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_OPS = 200  # one draft; a person dragging clips makes a few dozen
CACHE_RUNS = 8  # runs whose words and audio stay in memory


# --------------------------------------------------------------------------- inputs, cached per run on mtime

_inputs: dict[str, tuple[tuple, object, object]] = {}
_inputs_lock = threading.Lock()


def _sig(p: Path) -> tuple:
    try:
        s = p.stat()
        return s.st_mtime_ns, s.st_size
    except OSError:
        return None, None


def inputs(run_dir: Path) -> tuple[human.Words, human.Audio | None]:
    """The run's Voice words and source audio, read once and kept while voice.json and audio.wav are unchanged (an
    op is milliseconds; reading audio.wav again for each would be most of it)."""
    key = str(run_dir.resolve())
    sig = (_sig(run_dir / "voice" / "voice.json"), _sig(run_dir / "audio.wav"))
    with _inputs_lock:
        hit = _inputs.get(key)
        if hit and hit[0] == sig:
            return hit[1], hit[2]
    try:
        words = human._words(run_dir)
    except (OSError, ValueError, KeyError):
        raise HTTPException(409, "this run has no Voice words (voice/voice.json): nothing to edit yet")
    audio = human.Audio.load(run_dir)
    with _inputs_lock:
        _inputs.pop(key, None)
        _inputs[key] = (sig, words, audio)
        while len(_inputs) > CACHE_RUNS:
            _inputs.pop(next(iter(_inputs)))
    return words, audio


_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def run_lock(run_dir: Path) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(str(run_dir.resolve()), threading.Lock())


# --------------------------------------------------------------------------- the op arguments


def _int(x, what: str) -> int:
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        raise HTTPException(400, f"{what}: a whole number")
    try:
        f = float(x)
    except ValueError:
        raise HTTPException(400, f"{what}: a whole number")
    if not math.isfinite(f) or f != int(f) or not 0 <= f < 100000:
        raise HTTPException(400, f"{what}: a whole number")
    return int(f)


def _span(x, what: str = "span") -> str:
    if not isinstance(x, str) or not SPAN.fullmatch(x):
        raise HTTPException(400, f"{what}: a clip id like s3")
    return x


def _seconds(x, what: str) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what}: seconds")
    if not math.isfinite(f):
        raise HTTPException(400, f"{what}: seconds")
    return f


def clean_op(b: dict) -> dict:
    """{"op", "args"} from a request body, with every argument checked and typed (400 otherwise); what the op makes
    of them (a span that is not in the EDL, a one-word clip) is the op's own refusal (409)."""
    op, a = b.get("op"), b.get("args")
    if op not in human.OPS:
        raise HTTPException(400, f"op: one of {', '.join(human.OPS)}")
    if a is None:
        a = {}
    if not isinstance(a, dict):
        raise HTTPException(400, "args: an object")
    if op in ("split", "drop", "trim", "reorder"):
        out = {"span": _span(a.get("span"))}
        if op in ("split", "trim"):
            out["word"] = _int(a.get("word"), "word")
        if op == "trim":
            if a.get("edge") not in ("in", "out"):
                raise HTTPException(400, "edge: in or out")
            out["edge"] = a["edge"]
        if op == "reorder":
            out["before"] = None if a.get("before") in (None, "end") else _span(a.get("before"), "before")
    elif op == "cut":
        rs = a.get("ranges")
        if not isinstance(rs, list) or not 1 <= len(rs) <= 200 or not all(isinstance(r, (list, tuple)) and len(r) == 3
                                                                           for r in rs):
            raise HTTPException(400, "ranges: [[span, first word, last word], ...]")
        out = {"ranges": [[_span(r[0]), _int(r[1], "first word"), _int(r[2], "last word")] for r in rs]}
    elif op == "insert":
        before = a.get("before")
        if before is not None and before != "end":
            before = _span(before, "before")
        out = {"turn": _int(a.get("turn"), "turn"), "a": _int(a.get("a"), "a"), "b": _int(a.get("b"), "b"),
               "before": before}
    else:
        out = {"over": _seconds(a.get("over", human.PAUSE_OVER_S), "over"),
               "keep": _seconds(a.get("keep", human.PAUSE_KEEP_S), "keep")}
    return {"op": op, "args": out}


# --------------------------------------------------------------------------- the draft on disk


def folder(run_dir: Path, judge: str | None) -> Path:
    """ui/draft/, or ui/draft/<judge>/ on the hosted demo."""
    if judge is None:
        return run_dir / "ui" / "draft"
    if not JUDGE.fullmatch(judge):
        raise HTTPException(400, "bad judge id")
    return run_dir / "ui" / "draft" / judge


def load(fd: Path) -> dict | None:
    try:
        return json.loads((fd / "state.json").read_text())
    except (OSError, ValueError):
        return None


def save(fd: Path, state: dict, edl: dict | None) -> None:
    state["updated"] = time.time()
    if edl is not None:
        dump_json(fd / "edl.json", edl)
    dump_json(fd / "state.json", state)


def discard(fd: Path) -> None:
    for name in ("state.json", "edl.json"):
        (fd / name).unlink(missing_ok=True)


def base_edl(run_dir: Path, n: int) -> dict:
    p = run_dir / f"edl_v{n}.json"
    if not p.is_file():
        raise HTTPException(409, f"version {n} is gone (edl_v{n}.json): discard the draft")
    return json.loads(p.read_text())


def replay(run_dir: Path, base: int, ops: list[dict]) -> dict:
    """The base version through the ops again (undo; a draft whose edl.json is missing)."""
    words, audio = inputs(run_dir)
    cur = base_edl(run_dir, base)
    for k, o in enumerate(ops):
        try:
            cur, _ = human.step(cur, words, audio, o)
        except SystemExit as e:
            raise HTTPException(409, f"edit {k + 1} ({o.get('say') or o['op']}) no longer applies: {e}")
    return cur


def current(fd: Path, run_dir: Path, state: dict) -> dict:
    try:
        return json.loads((fd / "edl.json").read_text())
    except (OSError, ValueError):
        return replay(run_dir, state["base"], state["ops"])


def newest(run_dir: Path) -> int:
    ns = [int(m.group(1)) for p in run_dir.glob("edl_v*.json") if (m := re.fullmatch(r"edl_v(\d+)\.json", p.name))]
    return max(ns, default=0)


def newest_cut(run_dir: Path) -> int:
    vs = [int(m.group(1)) for p in run_dir.glob("cut_v*.mp4") if (m := re.fullmatch(r"cut_v(\d+)\.mp4", p.name))]
    return max(vs, default=0) or newest(run_dir)


def summary(run_dir: Path, state: dict | None, edl: dict | None, with_edl: bool = False) -> dict:
    latest = newest(run_dir)
    if state is None:
        return {"draft": None, "latest": latest}
    spans = [{k: s.get(k) for k in ("id", "turn_id", "word_from", "word_to", "t_in", "t_out", "duration", "role", "words",
                                    "callout")} for s in (edl or {}).get("spans", [])]
    out = {"draft": {"id": state["id"], "base": state["base"], "latest": latest, "stale": latest > state["base"],
                     "ops": [{"op": o["op"], "args": o["args"], "say": o.get("say")} for o in state["ops"]],
                     "can_undo": bool(state["ops"]), "can_redo": bool(state.get("redo")),
                     "runtime_s": (edl or {}).get("runtime_s"), "spans": spans,
                     "committing": state.get("commit"), "commit_failed": state.get("commit_failed")},
           "latest": latest}
    if with_edl:
        out["edl"] = edl
    return out


# --------------------------------------------------------------------------- the routes


def router(st, own, judge_of, need_key, hosted: bool = False) -> APIRouter:
    """The draft routes, on the server's State (`st`) and create_app's helpers: `own` (the run an edit goes to: on the
    hosted demo the judge's copy of a sample, made here only at the commit), `judge_of` (the request's judge id) and
    `need_key` (the key and budget check for the review passes a commit asks for)."""
    r = APIRouter()

    def where(run_id: str, request: Request) -> tuple[Path, Path]:
        d = st.run_dir(run_id)
        return d, folder(d, judge_of(request) if hosted else None)

    def settle(run_id: str, fd: Path, state: dict | None) -> dict | None:
        """A commit in flight that has ended: done -> the draft goes (its version exists now); failed or stopped ->
        the draft is editable again, with why. After a restart (no job known) the newest version's patch says."""
        c = (state or {}).get("commit")
        if not c:
            return state
        job = st.jobs.get(c["run"])
        if job is not None and job.id == c["job"] and job.ended is None:
            return state
        if job is not None and job.id == c["job"]:
            ok = job.rc == 0
        else:
            td = st.runs / c["run"]
            n = newest(td)
            patch = ((json.loads((td / f"edl_v{n}.json").read_text()) if n else {}).get("patches") or [{}])[-1]
            ok = (patch.get("draft") or {}).get("id") == state["id"] and (td / f"cut_v{n}.mp4").is_file()
        if ok:
            discard(fd)
            return None
        state.pop("commit", None)
        state["commit_failed"] = "Making the version did not finish: the draft is still here."
        save(fd, state, None)
        return state

    def refuse_beside_job(run_id: str, state: dict | None) -> None:
        if state and state.get("commit"):
            raise HTTPException(409, "a version is being made from this draft: edit again when it is ready")
        if st.busy(run_id):
            raise HTTPException(409, f"{st.jobs[run_id].label} is running on this run: edit again when it ends")

    @r.get("/api/runs/{run_id}/draft")
    def get_draft(run_id: str, request: Request, edl: int = 0):
        d, fd = where(run_id, request)
        with run_lock(d):
            state = settle(run_id, fd, load(fd))
            cur = current(fd, d, state) if state else None
            return summary(d, state, cur, bool(edl))

    def apply(run_id: str, d: Path, fd: Path, o: dict, frm) -> dict:
        with run_lock(d):
            state = settle(run_id, fd, load(fd))
            refuse_beside_job(run_id, state)
            if state is None:
                base = newest_cut(d) if frm is None else _int(frm, "from")
                cur = base_edl(d, base)
                state = {"id": uuid.uuid4().hex[:12], "base": base, "ops": [], "redo": [], "created": time.time()}
            else:
                if frm is not None and _int(frm, "from") != state["base"]:
                    raise HTTPException(409, f"a draft from v{state['base']} is open: make its version or discard it "
                                             f"before editing v{frm}")
                if len(state["ops"]) >= MAX_OPS:
                    raise HTTPException(409, f"this draft has {MAX_OPS} edits: make its version first")
                cur = current(fd, d, state)
            words, audio = inputs(d)
            t0 = time.perf_counter()
            try:
                new, rec = human.step(cur, words, audio, o)
            except SystemExit as e:
                raise HTTPException(409, str(e))
            state["ops"].append({**o, "say": rec.say})
            state["redo"] = []
            state.pop("commit_failed", None)
            save(fd, state, new)
            out = summary(d, state, new)
            out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return out

    @r.post("/api/runs/{run_id}/draft")
    async def post_draft(run_id: str, request: Request):
        d, fd = where(run_id, request)
        b = await request.json()
        if not isinstance(b, dict):
            raise HTTPException(400, '{"op": ..., "args": {...}}')
        return await asyncio.to_thread(apply, run_id, d, fd, clean_op(b), b.get("from"))

    def undo_redo(run_id: str, request: Request, undo: bool) -> dict:
        d, fd = where(run_id, request)
        with run_lock(d):
            state = settle(run_id, fd, load(fd))
            refuse_beside_job(run_id, state)
            if state is None or not (state["ops"] if undo else state.get("redo")):
                raise HTTPException(409, f"nothing to {'undo' if undo else 'redo'}")
            if undo:
                state.setdefault("redo", []).append(state["ops"].pop())
                cur = replay(d, state["base"], state["ops"])
            else:
                o = state["redo"].pop()
                words, audio = inputs(d)
                try:
                    cur, _ = human.step(current(fd, d, state), words, audio, o)
                except SystemExit as e:
                    raise HTTPException(409, str(e))
                state["ops"].append(o)
            save(fd, state, cur)
            return summary(d, state, cur)

    @r.post("/api/runs/{run_id}/draft/undo")
    def undo(run_id: str, request: Request):
        return undo_redo(run_id, request, True)

    @r.post("/api/runs/{run_id}/draft/redo")
    def redo(run_id: str, request: Request):
        return undo_redo(run_id, request, False)

    @r.delete("/api/runs/{run_id}/draft")
    def delete_draft(run_id: str, request: Request):
        d, fd = where(run_id, request)
        with run_lock(d):
            state = settle(run_id, fd, load(fd))
            if state and state.get("commit"):
                raise HTTPException(409, "a version is being made from this draft")
            discard(fd)
            return summary(d, None, None)

    @r.post("/api/runs/{run_id}/draft/commit")
    async def commit(run_id: str, request: Request):
        """The draft as ONE new version: the ops replayed (on the newest version when one came after the draft's
        base) as a check here, then one job writes edl_vN and renders it once. On the hosted demo this is where a
        sample becomes the judge's own copy."""
        from .app import COST, review_passes, start_job
        d, fd = where(run_id, request)

        def ready() -> None:  # before any copy of a sample: nothing to make, or a job / a commit already going
            with run_lock(d):
                state = settle(run_id, fd, load(fd))
                refuse_beside_job(run_id, state)
                if state is None or not state["ops"]:
                    raise HTTPException(409, "nothing to make: the draft has no edits")
        await asyncio.to_thread(ready)
        target = await own(run_id, request)  # the run itself, or the judge's copy of a sample
        b = await request.json() if (await request.body()) else {}
        b = b if isinstance(b, dict) else {}
        passes = review_passes(b)
        if passes:
            need_key("Reviewing the new cut", COST["review"] * passes)
        rebase = b.get("rebase", True) is not False
        td = st.runs / target

        def go() -> dict:
            with run_lock(d):
                state = settle(run_id, fd, load(fd))
                refuse_beside_job(run_id, state)
                if state is None or not state["ops"]:
                    raise HTTPException(409, "nothing to make: the draft has no edits")
                if target != run_id:  # the copy carried the sample's drafts: they belong to the sample
                    for x in (td / "ui" / "draft").glob("*/state.json"):
                        if (d / "ui" / "draft" / x.parent.name / "state.json").is_file():
                            shutil.rmtree(x.parent, ignore_errors=True)
                ops = [{"op": o["op"], "args": o["args"]} for o in state["ops"]]
                frm, latest = state["base"], newest(td)
                if latest > frm and not rebase:
                    raise HTTPException(409, f"v{latest} was made after this draft started from v{frm}: discard the "
                                             f"draft, or make it on v{latest}")
                if latest > frm:
                    frm = latest
                words, audio = inputs(td)
                cur = base_edl(td, frm)
                for k, o in enumerate(ops):  # the job must not refuse halfway: every op checked on its real base
                    try:
                        cur, _ = human.step(cur, words, audio, o)
                    except SystemExit as e:
                        later = f"v{frm} came after this draft (from v{state['base']}): " if frm != state["base"] else ""
                        raise HTTPException(409, f"{later}edit {k + 1} ({state['ops'][k].get('say') or o['op']}) "
                                                 f"does not apply: {e}")
                args = ["-m", "montivo.human", "draft", f"runs/{target}", "--ops", json.dumps(ops), "--id", state["id"],
                        "--from", str(frm), "--review", str(passes)]
                k = len(ops)
                says = "; ".join(o.get("say") or o["op"] for o in state["ops"])
                job = start_job(st, target, "draft", f"Make a version from {k} edit{'s' if k != 1 else ''}", args,
                                chat={"role": "user", "kind": "draft", "chips": [f"v{frm}"],
                                      "text": f"{says[0].upper() + says[1:]}." if says else "Apply the draft."})
                state["commit"] = {"job": job.id, "run": target, "from": frm}
                state.pop("commit_failed", None)
                save(fd, state, None)
                return {"job": job.public(), "run": target, "from": frm, "ops": k}

        return await asyncio.to_thread(go)

    return r
