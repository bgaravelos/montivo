"""A share link per version: /s/<token> is a read-only page with the cut, its blind review and how the score got there,
open without the hosted demo's sign-in (hosted.OPEN_PATHS). A token names one run and one version and nothing else; the
video and the poster are served under the same token, so a link never reaches another file of the run folder.
Tokens live in <root>/shares.json ({token: {run, version, created}}); the same version keeps its token. Unshare
removes the token: the link and its video and poster answer 404 from then on, and sharing that version again makes a
new token, so an old link never comes back."""
from __future__ import annotations

import html
import secrets
import subprocess
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from ..engine.api import dump_json
from .runs import read_json, versions

CRITERIA = {"hook_in_1_5s": "Hook in 1.5 s", "brief_covered": "Brief covered", "pacing": "Pacing",
            "no_cut_mid_word": "No cut mid-word", "captions_match_speech": "Captions match speech",
            "captions_legible": "Captions legible", "subject_framed": "Subject framed",
            "callouts_anchored": "Callouts anchored", "ending": "Ending"}  # web/src/api.ts CRITERIA
SEV = {"blocker": 0, "major": 1, "minor": 2}


class Shares:
    def __init__(self, root: Path):
        self.path = root / "shares.json"

    def all(self) -> dict:
        return read_json(self.path) or {}

    def create(self, run: str, version: int) -> str:
        d = self.all()
        for tok, s in d.items():
            if s.get("run") == run and s.get("version") == version:
                return tok
        tok = secrets.token_urlsafe(9)
        d[tok] = {"run": run, "version": version, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
        dump_json(self.path, d)
        return tok

    def of_run(self, run: str) -> list[dict]:
        """This run's links, newest version first."""
        return sorted(({"token": t, "path": f"/s/{t}", **s} for t, s in self.all().items() if s.get("run") == run),
                      key=lambda x: (-int(x.get("version") or 0), x.get("created") or ""))

    def delete(self, run: str, tok: str) -> bool:
        d = self.all()
        if d.get(tok, {}).get("run") != run:
            return False
        del d[tok]
        dump_json(self.path, d)
        return True

    def get(self, tok: str) -> dict:
        s = self.all().get(tok)
        if not s:
            raise HTTPException(404, "This link does not exist or was removed.")
        return s


def _clock(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:04.1f}"


def poster_time(v: dict) -> float:
    """A third into the cut after the title card, like the Home card (runs.project_card)."""
    dur = v.get("duration_s") or v.get("runtime_s") or 0
    title = v.get("title_s") or 0
    return round(title + max(dur - title, 0) / 3, 2)


def page(tok: str, run: dict, v: dict, history: list[dict], base: str) -> str:
    e = html.escape
    r = v.get("review")
    name = v.get("title_card_text") or (run.get("brief") or "").split(".")[0][:60] or "A Montivo cut"
    dur = v.get("duration_s") or v.get("runtime_s") or 0
    score = (f'<div class="score {"pass" if r["pass"] else ""}"><b>{r["overall"]}</b><span>/100 · '
             f'{"passes the blind review" if r["pass"] else "blind review"}</span></div>') if r else \
        '<div class="score"><span>not reviewed</span></div>'
    hist = " → ".join(f'v{h["n"]} <b>{h["overall"]}</b>' for h in history)
    bars = "".join(f'<div class="bar"><span>{e(CRITERIA.get(k, k))}</span><i><u style="width:{s * 10}%"></u></i><em>{s}</em></div>'
                   for k, s in (r or {}).get("scores", {}).items())
    issues = sorted((r or {}).get("issues", []), key=lambda i: (SEV.get(i.get("severity"), 3), i.get("t_s", 0)))[:3]
    notes = "".join(f'<li><button data-t="{i["t_s"]}">{_clock(i["t_s"])}</button> <span class="sev {e(i["severity"])}">'
                    f'{e(i["severity"])}</span> {e(i["what"])}</li>' for i in issues)
    desc = (f"{r['overall']}/100 from a blind AI review. " if r else "") + \
        f"Cut by Montivo: Muse Spark planned the edit, then reviewed its own render and re-cut it."
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(name)} · Montivo</title><link rel="icon" href="/favicon.svg">
<meta property="og:type" content="video.other"><meta property="og:title" content="{e(name)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:image" content="{base}s/{tok}/poster.jpg"><meta property="og:video" content="{base}s/{tok}/video.mp4">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(name)}">
<meta name="twitter:description" content="{e(desc)}"><meta name="twitter:image" content="{base}s/{tok}/poster.jpg">
<style>
:root {{ --ink: #141414; --paper: #F3F0E8; --marker: #FFC629; --pass: #5FD08E; --major: #FF8A3D; --cut: #FF5A4E; --mute: #9a968c; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--ink); color: var(--paper); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 28px 16px 40px; display: grid; grid-template-columns: minmax(0, 380px) minmax(0, 1fr); gap: 32px; }}
video {{ width: 100%; aspect-ratio: 9 / 16; background: #000; border-radius: 10px; display: block; }}
h1 {{ font-size: 24px; line-height: 1.2; margin: 0 0 6px; }}
.meta {{ color: var(--mute); font-size: 13px; }}
.score {{ display: flex; align-items: baseline; gap: 8px; margin: 18px 0 4px; color: var(--mute); }}
.score b {{ font-size: 44px; color: var(--paper); line-height: 1; }}
.score.pass b {{ color: var(--pass); }}
.hist {{ color: var(--mute); font-size: 13px; }} .hist b {{ color: var(--paper); }}
.bar {{ display: grid; grid-template-columns: 170px 1fr 28px; gap: 10px; align-items: center; font-size: 13px; margin: 4px 0; }}
.bar i {{ height: 6px; background: #2a2a2a; border-radius: 3px; overflow: hidden; }} .bar u {{ display: block; height: 100%; background: var(--pass); }}
.bar em {{ font-style: normal; text-align: right; }}
h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--mute); margin: 24px 0 8px; }}
ul {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; }}
li button {{ font: inherit; font-variant-numeric: tabular-nums; color: var(--marker); background: none; border: 0; padding: 0; cursor: pointer; }}
.sev {{ font-size: 11px; text-transform: uppercase; color: var(--mute); }} .sev.major {{ color: var(--major); }} .sev.blocker {{ color: var(--cut); }}
.summary {{ color: #d8d4ca; }}
footer {{ grid-column: 1 / -1; color: var(--mute); font-size: 13px; border-top: 1px solid #2a2a2a; padding-top: 14px; }}
footer b {{ color: var(--marker); }}
@media (max-width: 760px) {{ main {{ grid-template-columns: minmax(0, 1fr); }} .bar {{ grid-template-columns: 130px 1fr 24px; }} }}
</style></head><body><main>
<div><video id="v" src="/s/{tok}/video.mp4" poster="/s/{tok}/poster.jpg" controls playsinline preload="metadata"></video></div>
<div>
<h1>{e(name)}</h1>
<div class="meta">Version {v["n"]} · {len(v.get("spans") or [])} clips · {_clock(dur)}</div>
{score}
{f'<div class="hist">The agent loop: {hist}</div>' if len(history) > 1 else ""}
{f'<p class="summary">{e(r.get("summary", ""))}</p>' if r else ""}
{f'<h2>Scores</h2>{bars}' if bars else ""}
{f'<h2>The reviewer’s notes</h2><ul>{notes}</ul>' if notes else ""}
</div>
<footer><b>montivo</b> · an agent-first video editor on Meta models: Muse Spark plans the cut, Muse Voice keeps time, SAM 3.1
runs the camera, and a blind Muse Spark review scores the render before it ships.</footer>
</main>
<script>document.querySelectorAll("button[data-t]").forEach(b => b.onclick = () => {{
  const v = document.getElementById("v"); v.currentTime = Math.max(0, +b.dataset.t - 1); v.play(); }});</script>
</body></html>"""


def install(app, st, protected=lambda d: False) -> None:
    """`protected(run_dir)`: a run whose links nobody may remove (the hosted demo's samples, shared by every judge: a
    link to one may be in a public post)."""
    shares = Shares(st.root)

    def resolve(tok: str) -> tuple[Path, dict, dict, list[dict]]:
        s = shares.get(tok)
        d = st.runs / s["run"]
        vs = versions(d) if d.is_dir() else []
        v = next((x for x in vs if x["n"] == s["version"]), None)
        if not v or not v.get("cut"):
            raise HTTPException(404, "The version behind this link is gone.")
        history = [{"n": x["n"], "overall": x["review"]["overall"]} for x in vs if x.get("review") and x["n"] <= v["n"]]
        return d, v, read_json(d / "run.json") or {}, history

    @app.post("/api/runs/{run_id}/share")
    async def share(run_id: str, request: Request):
        """A read-only link to one version ({"version": N}): the same version keeps its link."""
        d = st.run_dir(run_id)
        b = await request.json()
        try:
            n = int(b["version"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "version: the version to share")
        if not (d / f"cut_v{n}.mp4").is_file():
            raise HTTPException(404, f"version {n} has no cut")
        return {"token": (tok := shares.create(run_id, n)), "path": f"/s/{tok}"}

    @app.get("/api/runs/{run_id}/shares")
    def run_shares(run_id: str):
        """This run's share links: [{token, path, version, created}]."""
        st.run_dir(run_id)
        return {"shares": shares.of_run(run_id)}

    @app.delete("/api/runs/{run_id}/share/{tok}")
    def unshare(run_id: str, tok: str):
        """Unshare: the link stops working at once (its page, video and poster); the version and its cut stay."""
        if protected(st.run_dir(run_id)):
            raise HTTPException(403, "A sample's links stay: one may already be in a post. Share your own copy instead.")
        if not shares.delete(run_id, tok):
            raise HTTPException(404, "no such link on this run")
        return {"removed": tok, "shares": shares.of_run(run_id)}

    @app.get("/s/{tok}")
    def share_page(tok: str, request: Request):
        d, v, run, history = resolve(tok)
        return HTMLResponse(page(tok, run, v, history, str(request.base_url)), headers={"Cache-Control": "no-cache"})

    @app.get("/s/{tok}/video.mp4")
    def share_video(tok: str):
        d, v, _, _ = resolve(tok)
        return FileResponse(d / v["cut"], media_type="video/mp4")  # answers Range requests

    @app.get("/s/{tok}/poster.jpg")
    def share_poster(tok: str):
        d, v, _, _ = resolve(tok)
        src, t = d / v["cut"], poster_time(v)
        out = d / "ui" / "cache" / "share" / f"v{v['n']}_{round(t * 10):05d}.jpg"
        if not out.is_file() or out.stat().st_mtime < src.stat().st_mtime:
            out.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
                            "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", str(out)], capture_output=True)
        if not out.is_file():
            raise HTTPException(404, "poster extraction failed")
        return FileResponse(out, headers={"Cache-Control": "max-age=3600"})

