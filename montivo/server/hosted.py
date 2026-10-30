"""The hosted demo for the judges: a password in front of the UI and a cap on what the Meta models may spend.

Both switch on from the environment only, so `montivo serve` on a laptop is unchanged:

    MONTIVO_PASSWORD=...        every page and API call needs the sign-in cookie (/api/health and /login stay open)
    MONTIVO_BUDGET_USD=15       paid actions stop once the runs' ledgers have spent this much since the host started
    MONTIVO_ONE_JOB=1           one engine job at a time across all runs (one container, one CPU budget)
    MONTIVO_MAX_FOOTAGE_S=95    a new run's clip may be at most this long
    MONTIVO_MAX_UPLOAD_MB=1000  an upload stops past this size (95 s of iPhone 4K HDR is about 600 MB)
    MONTIVO_MAX_UPLOADS=5       clips one judge may upload (with the budget on)
    MONTIVO_JUDGE_SECRET=...    signs the judge cookies (else <root>/judge.key, made once; never the password)

The budget is read from the ledgers every run writes (runs/<id>/ledger.json `total_cost_usd`), counted from a baseline
taken the first time the host saw each run (budget.json next to runs/): the seeded sample runs bring their own Mac
spend with them, and only what was spent on the host counts. A paid action is refused when what is left does not cover
its expected cost, so the cap holds within one action's estimate; the $0 edits never touch it. Every browser gets a
judge id at sign-in (JUDGE): its first edit on a sample copies the sample to runs/<id>-<judge> (app.own), and the project
list shows each judge the samples (or their copy) and their own runs. A run copied onto the
host later is adopted the same way: `python -m montivo.server.hosted adopt --root /data 015`.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..engine.api import write_atomic

COOKIE = "montivo_auth"
JUDGE = "montivo_judge"  # one id per browser: the judge's own copies of the samples and their own runs
OPEN_PATHS = ("/login", "/api/login", "/api/health", "/favicon", "/apple-touch-icon", "/s/")  # /s/: share links (share.py)
MIN_PAID_USD = 0.01  # the cheapest paid action (a Muse Image title card)
MONTI = "_monti"  # the budget key of Monti's ledger (no run has a name starting with _)
ARCHIVED = "_archive"  # runs/_archive/<id> (app.ARCHIVE)


def usd(x: float) -> str:
    return f"${x:.0f}" if float(x).is_integer() else f"${x:.2f}"


def env_float(name: str) -> float | None:
    v = os.environ.get(name)
    return float(v) if v else None


# --------------------------------------------------------------------------- the budget

class Budget:
    """Spend since the host started, from the run ledgers, against a cap in dollars."""

    def __init__(self, root: Path, cap: float):
        self.root, self.cap = root, cap
        self.file = root / "budget.json"
        first = not self.file.is_file()
        self.data = {"baseline": {}, "since": time.time()} if first else json.loads(self.file.read_text())
        self.baseline: dict[str, float] = self.data["baseline"]
        if first:  # the runs already here (the seeded samples) are not host spend
            self.adopt(list(self.ledgers()))
        for k in self.ledgers():  # budget.json from before archived runs had keys of their own
            name = k.removeprefix(f"{ARCHIVED}/")
            if k != name and k not in self.baseline and name in self.baseline \
                    and not (root / "runs" / name / "ledger.json").is_file():
                self.baseline[k] = self.baseline.pop(name)
        self.save()

    def adopt(self, names: list[str]) -> None:
        """Runs copied onto the host (seeded samples): what their ledgers hold now was spent elsewhere."""
        totals = self.ledgers()
        self.baseline.update({n: totals.get(n, 0.0) for n in names})
        self.save()

    def ledgers(self) -> dict[str, float]:
        """Every run's ledger total, archived runs included (an archive moves the folder, the spend stays), keyed
        <id> and _archive/<id>: a run and an archived run of the same name are two runs (a judge's second copy of a
        sample after archiving the first used to take the archived ledger as its baseline, and nothing counted)."""
        out: dict[str, float] = {}
        for folder, pre in ((self.root / "runs", ""), (self.root / "runs" / ARCHIVED, f"{ARCHIVED}/")):
            for p in folder.glob("*/ledger.json") if folder.is_dir() else []:
                try:
                    out[pre + p.parent.name] = float(json.loads(p.read_text()).get("total_cost_usd") or 0.0)
                except (OSError, ValueError):
                    continue
        try:  # Monti's questions are not a run: <root>/monti/ledger.json (montivo/monti.py)
            out[MONTI] = float(json.loads((self.root / "monti" / "ledger.json").read_text()).get("total_cost_usd") or 0.0)
        except (OSError, ValueError):
            pass
        return out

    def move(self, old: str, new: str) -> None:
        """A run archived or restored: its baseline goes with the folder."""
        if old in self.baseline:
            self.baseline[new] = self.baseline.pop(old)
            self.save()

    def save(self) -> None:
        write_atomic(self.file, json.dumps(self.data, indent=1))

    def hold(self, usd: float, run: str) -> None:
        """Count `usd` as spent with no ledger entry: a stopped job's call may have reached the model (and been
        billed) before its answer, and with it the ledger entry, came back."""
        if usd > 0:
            self.data.setdefault("held", []).append({"run": run, "usd": round(usd, 4), "t": round(time.time())})
            self.save()

    def spent(self) -> float:
        held = sum(h.get("usd", 0.0) for h in self.data.get("held", []))
        return round(sum(max(0.0, c - self.baseline.get(n, 0.0)) for n, c in self.ledgers().items()) + held, 4)

    def left(self) -> float:
        return round(self.cap - self.spent(), 4)

    def public(self) -> dict:
        spent = self.spent()
        return {"cap": self.cap, "spent": round(spent, 2), "left": round(max(0.0, self.cap - spent), 2)}

    def note(self) -> str | None:
        """What the UI says instead of "set MODEL_API_KEY" once nothing paid fits any more."""
        if self.left() >= MIN_PAID_USD:
            return None
        return (f"This demo's model budget ({usd(self.cap)}) is spent: the finished projects open and the $0 edits "
                "(delete, trim, captions, move, punch-ins, speakers) still render.")

    def require(self, what: str, est: float) -> None:
        left = self.left()
        if left < round(max(est, MIN_PAID_USD), 4):  # 3 x $0.035 is 0.10500000000000001: refused at exactly $0.105
            raise HTTPException(409, f"{what} needs about ${est:.2f} of Meta model calls and this demo has "
                                     f"${max(left, 0):.2f} of its {usd(self.cap)} budget left. The finished projects "
                                     "and the $0 edits (delete, trim, captions, move, punch-ins, speakers) still work.")


# --------------------------------------------------------------------------- the sign-in

def token(password: str) -> str:
    return hmac.new(password.encode(), b"montivo-session-v1", hashlib.sha256).hexdigest()


def signed_in(request: Request, password: str) -> bool:
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), token(password))


JUDGE_ID = re.compile(r"[0-9a-f]{4,16}")
JUDGE_SCOPE = "montivo.judge"  # the gate puts the checked judge id in the request scope (app.judge_of reads it)


def judge_key(root: Path) -> str:
    """The secret the judge cookies are signed with: MONTIVO_JUDGE_SECRET, else <root>/judge.key, made the first time
    (on the Volume, so it outlives a redeploy). Never the password: every judge knows that one, and could sign any
    other judge's id with it (Codex review, 28/10)."""
    if env := os.environ.get("MONTIVO_JUDGE_SECRET"):
        return env
    p = root / "judge.key"
    try:
        if key := p.read_text().strip():
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    write_atomic(p, key)
    os.chmod(p, 0o600)
    return key


def judge_cookie(key: str, jid: str) -> str:
    """<id>.<signature>: the id alone could be copied from a run name (runs/<sample>-<id>) and let one judge act as
    another; the signature (HMAC with the server's judge_key) makes the cookie the server's own."""
    sig = hmac.new(key.encode(), f"montivo-judge-v1:{jid}".encode(), hashlib.sha256).hexdigest()[:20]
    return f"{jid}.{sig}"


def judge_id(request: Request, key: str | None) -> str | None:
    """The browser's judge id when its cookie is valid (signed, when there is a key), else None."""
    raw = request.cookies.get(JUDGE, "")
    if key is None:
        return raw if JUDGE_ID.fullmatch(raw) else None
    jid = raw.partition(".")[0]
    return jid if JUDGE_ID.fullmatch(jid) and hmac.compare_digest(raw, judge_cookie(key, jid)) else None


def set_judge(resp, key: str, jid: str) -> None:
    resp.set_cookie(JUDGE, judge_cookie(key, jid), max_age=60 * 86400, httponly=True, samesite="lax")


LOGIN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Montivo: sign in</title><link rel="icon" href="/favicon.svg">
<style>
:root { --ink: #141414; --paper: #F3F0E8; --marker: #FFC629; --slate: #8A877F; --cut: #FF5A4E; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--ink); color: var(--paper);
       font: 16px/1.5 "Inter Tight", system-ui, -apple-system, sans-serif; padding: 16px; }
form { width: 100%; max-width: 360px; display: grid; gap: 14px; }
h1 { margin: 0 0 4px; font-size: 28px; letter-spacing: -0.02em; }
h1 span { color: var(--marker); }
p { margin: 0; color: var(--slate); font-size: 14px; }
input { width: 100%; padding: 12px 14px; border-radius: 8px; border: 1px solid #3a3a3a; background: #1e1e1e;
        color: var(--paper); font: inherit; }
input:focus { outline: 2px solid var(--marker); outline-offset: 1px; }
button { padding: 12px 14px; border: 0; border-radius: 8px; background: var(--marker); color: var(--ink);
         font: inherit; font-weight: 700; cursor: pointer; }
.err { color: var(--cut); }
</style></head>
<body><form method="post" action="/api/login">
<h1>montivo <span>[v]</span></h1>
<p>The hosted demo for the Meta hackathon judges. The password is in the submission form.</p>
__ERROR__
<input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
<button type="submit">Sign in</button>
</form></body></html>"""


def install(app, password: str | None, root: Path) -> None:
    """Put the sign-in in front of every route of the app when a password is set."""
    if not password:
        return
    key = judge_key(root)

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        if path.startswith(OPEN_PATHS):
            return await call_next(request)
        if signed_in(request, password):
            jid = judge_id(request, key)
            request.scope[JUDGE_SCOPE] = jid or secrets.token_hex(4)
            if jid:
                return await call_next(request)
            resp = await call_next(request)  # no judge cookie yet, or one the server did not sign: a new id
            set_judge(resp, key, request.scope[JUDGE_SCOPE])
            return resp
        if path.startswith("/api/"):
            return JSONResponse({"error": "sign in first"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    def login_page(error: int = 0):
        msg = '<p class="err">That password is not right.</p>' if error else ""
        return HTMLResponse(LOGIN_HTML.replace("__ERROR__", msg))

    @app.post("/api/login")
    async def login(request: Request):
        body = (await request.body())[:2048].decode(errors="replace")
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                given = str(json.loads(body or "{}").get("password", ""))
            except ValueError:
                given = ""
        else:
            given = (parse_qs(body).get("password") or [""])[0]
        if not hmac.compare_digest(token(given), token(password)):
            await asyncio.sleep(1.0)  # slows a guesser down without holding the other requests
            return RedirectResponse("/login?error=1", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        if not judge_id(request, key):
            set_judge(resp, key, secrets.token_hex(4))
        resp.set_cookie(COOKIE, token(password), max_age=14 * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        return resp

    @app.post("/api/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m montivo.server.hosted")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_ = sub.add_parser("adopt", help="count a copied run's ledger so far as spent elsewhere")
    a_.add_argument("runs", nargs="+")
    s_ = sub.add_parser("status", help="the budget: cap, spent on the host, left")
    for p in (a_, s_):
        p.add_argument("--root", type=Path, default=Path("."))
        p.add_argument("--cap", type=float, default=env_float("MONTIVO_BUDGET_USD") or 15.0)
    a = ap.parse_args(argv)
    b = Budget(a.root, a.cap)
    if a.cmd == "adopt":
        b.adopt(a.runs)
    print(json.dumps({**b.public(), "baseline": b.baseline}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
