"""Export bundle of one version, as an engine entry point the UI server runs as a subprocess:

    python -m montivo.export runs/015 --version 5 --out runs/015/ui/cache/exports/abc --name montivo-015-v5 \
        --include video,clean,srt,vtt,cover,audio,transcript [--preset 720] [--cover-t 12.3] [--aspect 4:5]

Writes the chosen files into --out and, when there is more than one, a zip of them; prints a JSON record of what it
wrote. $0, ffmpeg only, and nothing in the run folder changes: `video` is cut_vN.mp4 itself (or its 720x1280 copy),
`clean` renders the version again without burned-in captions from a copy of its EDL into --out (about the time of a
render; callouts and the title card stay), `srt` / `vtt` are the caption lines in cut time, `cover` the frame at
--cover-t (1080x1920, plus a 1080x1440 crop for the Instagram profile grid), `audio` the cut's loudness-normalised
sound as WAV, `transcript` the caption lines as text with their times.

`--aspect 4:5` (1080x1350, the Instagram / Facebook feed and LinkedIn) or `1:1` (1080x1080) renders `video` and
`clean` again in that frame from a copy of the EDL, the way `clean` does (engine/frame.py: the crop narrows and still
follows the head, captions and callouts are laid out again in the frame); the cover is that frame's still (engine/
look.py, the render's own pixels without the punch-in zoom) and its grid crop the 3:4 middle of it. The SRT, VTT, WAV
and transcript are the same in every aspect.

`--cover-design '{"template": "headline", "text": "...", "highlight": "..."}'` makes the cover a designed one
(engine/thumb.py: a clean still with the headline laid out around the head; `cutout` needs its person mask made first
with `--cover-mask`). `--design-cover` (about $0.03-0.05) and `--cover-mask` (about $0.01) are the two paid entries: the
agent's design into cover_vN.json, and SAM 3.1's person mask of the still at --cover-t.

`--post-text` (one muse-spark-1.3 call, strict json_schema, about $0.01) writes the publish pack into post_vN.json: a
title, a description and hashtags per platform (Reels, TikTok, Shorts, LinkedIn) from the brief, the words the cut
keeps (its caption lines, hand fixes included) and the blind review's summary; `post` in --include puts them in the
bundle as post.txt, one block per platform, exactly as the Export page copies them.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from .engine.frame import FRAME_916, Frame, frame_of, with_aspect
from .ledger import Ledger
from .run import render_run

KINDS = ("video", "clean", "srt", "vtt", "cover", "audio", "transcript", "post")
PLATFORMS = {"reels": "Instagram Reels", "tiktok": "TikTok", "shorts": "YouTube Shorts", "linkedin": "LinkedIn"}
# what each platform shows (Meta, TikTok, YouTube help pages): a Shorts title up to 100 characters, a Reels caption
# up to 2,200, TikTok's caption up to 4,000 but read in its first line, LinkedIn's post cut after about 210 characters
LIMITS = {"reels": {"title": 0, "description": 2200, "tags": 5}, "tiktok": {"title": 0, "description": 300, "tags": 5},
          "shorts": {"title": 100, "description": 1000, "tags": 3}, "linkedin": {"title": 0, "description": 1300, "tags": 3}}
POST_ONE = {"type": "object", "additionalProperties": False, "required": ["title", "description", "hashtags"],
            "properties": {"title": {"type": "string", "description": "Shorts: the video title, at most 100 characters; "
                                                                        "the other platforms: empty"},
                           "description": {"type": "string", "description": "the caption or post text, no hashtags in it"},
                           "hashtags": {"type": "array", "items": {"type": "string"}, "description": "without #, no spaces"}}}
POST_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(PLATFORMS),
               "properties": {k: POST_ONE for k in PLATFORMS}}
POST_PROMPT = """You write the post that goes with a short vertical video, for four platforms. Write only from what \
the video says (its words below) and the brief; never promise or claim anything the words do not say. Write in the \
language the video is spoken in.

- reels (Instagram Reels caption): the first line is a hook that makes people tap "more"; then two or three short \
lines on what the video shows; a question or call to comment at the end. 3 to 5 hashtags. title empty.
- tiktok: one or two short lines, conversational, under 150 characters. 3 to 5 hashtags. title empty.
- shorts (YouTube Shorts): title at most 100 characters, plain, searchable, no clickbait; description two or three \
sentences. Up to 3 hashtags (Shorts is added for you). 
- linkedin: a professional post: a first line that stands alone (LinkedIn cuts after about 210 characters), then \
three or four short paragraphs on the idea and why it matters, first person as the speaker. 3 hashtags. title empty.

Hashtags: specific to the topic, a mix of one broad and the rest narrow; no spaces; no # sign. No emoji walls: at \
most one emoji per platform.

Brief from the editor: {brief}
Title card: {title}
Length: {seconds:.0f} s
The words of the video, in order:
{words}
{review}"""


def compose(platform: str, p: dict) -> str:
    """One platform's post as it is pasted: Shorts' title on its own line, the text, the hashtags."""
    tags = " ".join(f"#{t}" for t in p.get("hashtags") or [])
    head = f"{p['title']}\n\n" if p.get("title") else ""
    return f"{head}{p.get('description', '').strip()}" + (f"\n\n{tags}" if tags else "")


def settle_post(parsed: dict) -> dict:
    """The model's answer kept to each platform's limits: hashtags without # or spaces, deduplicated and counted,
    Shorts gets #Shorts, a title only where the platform has one, text cut at the limit on a word."""
    out = {}
    for k in PLATFORMS:
        p, lim = parsed.get(k) or {}, LIMITS[k]
        tags = []
        for t in p.get("hashtags") or []:
            t = "".join(ch for ch in str(t).lstrip("#") if ch.isalnum() or ch == "_")
            if t and t.lower() not in {x.lower() for x in tags}:
                tags.append(t)
        tags = tags[:lim["tags"]]
        if k == "shorts" and "shorts" not in {x.lower() for x in tags}:
            tags.append("Shorts")
        cut = lambda text, n: text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"  # noqa: E731
        one = {"title": cut(" ".join(str(p.get("title") or "").split()), lim["title"]) if lim["title"] else "",
               "description": cut(str(p.get("description") or "").strip(), lim["description"]), "hashtags": tags}
        out[k] = {**one, "text": compose(k, one)}
    return out


def post_text(run_dir: Path, n: int, ledger: Ledger, log=print) -> dict:
    """The publish pack of version n (one Spark call) -> post_vN.json."""
    from .engine.api import dump_json
    from .engine.spark import Spark
    edl = json.loads((run_dir / f"edl_v{n}.json").read_text())
    run = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").is_file() else {}
    words = " ".join(text for _, _, text in caption_cues(edl)) or " ".join(s.get("words", "") for s in edl["spans"])
    rv = run_dir / f"review_v{n}.json"
    review = ""
    if rv.is_file():
        r = json.loads(rv.read_text()).get("review") or {}
        review = f"A blind reviewer scored it {r.get('overall')}/100: {r.get('summary', '')}"
    prompt = POST_PROMPT.format(brief=run.get("brief") or "", title=edl.get("title_card_text") or "none",
                                seconds=float((edl.get("render") or {}).get("duration_s") or edl.get("runtime_s") or 0),
                                words=words[:6000], review=review)
    call = Spark(run_dir, ledger, log=log).call(f"post_v{n}", prompt, POST_SCHEMA, max_output_tokens=4000,
                                                reasoning_effort="low", stage="spark.post", schema_name="post_text")
    rec = {"version": n, "platforms": settle_post(call.parsed), "cost_usd": call.cost_usd, "seconds": call.seconds,
           "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    dump_json(run_dir / f"post_v{n}.json", rec)
    log(f"post: v{n} title {rec['platforms']['shorts']['title']!r}, ${call.cost_usd:.4f} in {call.seconds:.1f} s")
    return rec


def post_file(rec: dict) -> str:
    """post.txt: one block per platform, as the Export page copies it."""
    return "\n\n".join(f"== {PLATFORMS[k]} ==\n{rec['platforms'][k]['text']}" for k in PLATFORMS if k in rec["platforms"]) + "\n"


def grid_crop(w: int, h: int) -> str:
    """The ffmpeg crop of the Instagram profile grid's 3:4 window, the middle of a cover of any aspect (1080x1440 of 9:16,
    1012x1350 of 4:5, 810x1080 of 1:1)."""
    if w * 4 <= h * 3:
        gh = w * 4 // 3 // 2 * 2
        return f"crop={w}:{gh}:0:(ih-{gh})/2"
    gw = h * 3 // 4 // 2 * 2
    return f"crop={gw}:{h}:(iw-{gw})/2:0"


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
    """A 720 px wide copy (720x1280 of the 9:16 cut)."""
    ffmpeg("-i", str(src), "-vf", "scale=720:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
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


def render_copy(run_dir: Path, edl: dict, out: Path, captions: bool = True, frame: Frame = FRAME_916) -> Path:
    """The version again (without captions, or in another frame) from a copy of its EDL, its ledger and log kept
    apart from the run's."""
    work = out.parent / "render_work"
    work.mkdir(parents=True, exist_ok=True)
    tmp = work / "edl.json"
    tmp.write_text(json.dumps(with_aspect(copy.deepcopy(edl), frame.aspect)))
    try:
        rel = str(out.resolve().relative_to(run_dir.resolve()))
    except ValueError:  # --out outside the run folder: the render writes there (run_dir / an absolute path = it)
        rel = str(out.resolve())
    render_run(run_dir, edl_path=tmp, out_name=rel, captions=captions, ledger=Ledger(work / "ledger.json"),
               log=lambda *a, **k: None, title=title_ready(run_dir, edl))
    shutil.rmtree(work, ignore_errors=True)
    return out


def cover_still(run_dir: Path, edl: dict, t: float, cut: Path, frame: Frame, dst: Path) -> Path:
    """The cover of a feed aspect: the frame at cut second t laid out as that aspect's render would burn it."""
    from .engine.look import preview
    img, _ = preview(run_dir, with_aspect(copy.deepcopy(edl), frame.aspect), t, cut)
    img.convert("RGB").save(dst, quality=95)
    return dst


def export(run_dir: Path, n: int, out: Path, name: str, include: list[str], preset: str = "full",
           cover_t: float | None = None, aspect: str = "9:16", cover_design: dict | None = None) -> dict:
    edl_path, cut = run_dir / f"edl_v{n}.json", run_dir / f"cut_v{n}.mp4"
    if not edl_path.is_file() or not cut.is_file():
        raise SystemExit(f"version {n} has no edl_v{n}.json and cut_v{n}.mp4 in {run_dir}")
    bad = [k for k in include if k not in KINDS]
    if bad or not include:
        raise SystemExit(f"--include: one or more of {', '.join(KINDS)}")
    if preset not in ("full", "720"):
        raise SystemExit("--preset: full or 720")
    try:
        frame = frame_of(aspect)
    except ValueError as e:
        raise SystemExit(f"--aspect: {e}")
    feed = frame != FRAME_916
    tag = f"-{frame.tag}" if feed else ""
    edl = json.loads(edl_path.read_text())
    out.mkdir(parents=True, exist_ok=True)
    cues = caption_cues(edl)
    files: list[Path] = []
    p720 = preset == "720"
    if "video" in include and not feed:
        dst = out / f"{name}{'-720p' if p720 else ''}.mp4"
        small(cut, dst) if p720 else shutil.copyfile(cut, dst)
        files.append(dst)
    for kind, captions, stem in (("video", True, f"{name}{tag}"), ("clean", False, f"{name}{tag}-no-captions")):
        if kind not in include or (kind == "video" and not feed):
            continue
        full = out / f"{stem}{'-full' if p720 else ''}.mp4"
        render_copy(run_dir, edl, full, captions=captions, frame=frame)
        if p720:
            files.append(small(full, out / f"{stem}-720p.mp4"))
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
        dst = out / f"{name}{tag}-cover.jpg"
        if cover_design:
            from .engine.thumb import NeedMask, cover_image
            try:
                img, _ = cover_image(run_dir, n, {**cover_design, "t_s": t}, frame.aspect)
            except NeedMask as e:
                raise SystemExit(f"cover: {e}; make it with --cover-mask --cover-t {t:.2f} first")
            img.save(dst, quality=95)
        elif feed:
            cover_still(run_dir, edl, t, cut, frame, dst)
        else:
            ffmpeg("-ss", f"{t:.3f}", "-i", str(cut), "-frames:v", "1", "-q:v", "2", str(dst))
        grid = out / f"{name}{tag}-cover-grid.jpg"
        ffmpeg("-i", str(dst), "-vf", grid_crop(frame.w, frame.h), "-q:v", "2", str(grid))
        files += [dst, grid]
    if "post" in include:
        rec_p = run_dir / f"post_v{n}.json"
        if not rec_p.is_file():
            raise SystemExit(f"post: no post_v{n}.json yet; write it with --post-text first")
        (dst := out / f"{name}-post.txt").write_text(post_file(json.loads(rec_p.read_text())), encoding="utf-8")
        files.append(dst)
    if "audio" in include:
        dst = out / f"{name}.wav"
        ffmpeg("-i", str(cut), "-vn", "-c:a", "pcm_s16le", "-ar", "48000", str(dst))
        files.append(dst)
    record = {"version": n, "aspect": frame.aspect, "files": [{"name": f.name, "bytes": f.stat().st_size} for f in files], "zip": None}
    if len(files) > 1:
        z = out / f"{name}{tag}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:  # mp4 / jpg / wav do not compress further
            for f in files:
                zf.write(f, f.name)
        record["zip"] = {"name": z.name, "bytes": z.stat().st_size}
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m montivo.export", description="One version's export bundle ($0).")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--version", type=int, required=True)
    ap.add_argument("--out", type=Path, default=None, help="folder for the files (inside the run folder)")
    ap.add_argument("--name", default=None, help="file name stem (default montivo-<run>-v<N>)")
    ap.add_argument("--include", default="video", help=f"comma list of {', '.join(KINDS)}")
    ap.add_argument("--preset", default="full", choices=("full", "720"))
    ap.add_argument("--aspect", default="9:16", choices=("9:16", "4:5", "1:1"),
                    help="the frame: 9:16 as rendered, or 4:5 / 1:1 rendered again for feed posts")
    ap.add_argument("--cover-t", type=float, default=None, help="cut second of the cover frame (default a third in)")
    ap.add_argument("--suggest-cover", action="store_true",
                    help="only ask Muse Spark for the best cover moments (one call, about $0.03) -> cover_vN.json")
    ap.add_argument("--cover-design", default=None,
                    help='JSON {"template": frame|headline|sticker|cutout, "text": ..., "highlight": ...} for the cover')
    ap.add_argument("--design-cover", action="store_true",
                    help="only let Muse Spark design the cover: still, headline, template, check (about $0.04) -> cover_vN.json")
    ap.add_argument("--post-text", action="store_true",
                    help="only write the post text per platform with Muse Spark (one call, about $0.01) -> post_vN.json")
    ap.add_argument("--cover-mask", action="store_true",
                    help="only make SAM 3.1's person mask of the still at --cover-t for the cutout cover (about $0.01)")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if not a.run_dir.is_dir():
        ap.error(f"{a.run_dir} is not a run folder")
    if a.post_text:
        from .run import RunLog
        with contextlib.redirect_stdout(sys.stderr):  # stdout is the one JSON record the server reads
            rec = post_text(a.run_dir, a.version, Ledger.resume(a.run_dir / "ledger.json"), RunLog(a.run_dir / "run.log"))
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return 0
    if a.suggest_cover or a.design_cover or a.cover_mask:
        if a.cover_mask and a.cover_t is None:
            ap.error("--cover-mask needs --cover-t")
        from .engine import thumb
        from .engine.cover import suggest
        from .run import RunLog
        ledger, log = Ledger.resume(a.run_dir / "ledger.json"), RunLog(a.run_dir / "run.log")
        # RunLog echoes every line to stdout; the server reads stdout as this one JSON record
        with contextlib.redirect_stdout(sys.stderr):
            if a.suggest_cover:
                rec = suggest(a.run_dir, a.version, ledger, log)
            elif a.design_cover:
                rec = thumb.design(a.run_dir, a.version, ledger, log, a.aspect)
            else:
                rec = {"mask": str(thumb.still_mask(a.run_dir, a.version, a.cover_t, a.aspect, ledger, log))}
        print(json.dumps(rec, indent=2))
        return 0
    if a.out is None:
        ap.error("--out is needed for an export")
    design = None
    if a.cover_design:
        try:
            design = json.loads(a.cover_design)
        except json.JSONDecodeError as e:
            ap.error(f"--cover-design: {e}")
    name = a.name or f"montivo-{a.run_dir.name}-v{a.version}"
    rec = export(a.run_dir, a.version, a.out, name, [k.strip() for k in a.include.split(",") if k.strip()], a.preset,
                 a.cover_t, a.aspect, design)
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
