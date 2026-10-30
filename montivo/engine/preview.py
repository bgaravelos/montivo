"""The editor's live preview: what the browser needs to play a version (or a draft EDL) without a render.

The browser plays the cut from the edit proxy (ingest.make_edit_proxy: short GOP, video only) and the edit audio
(FLAC), segment by segment: the audio leads (Web Audio, one AudioBufferSourceNode per segment), two <video> elements
take turns on the picture, and the 9:16 crop is a CSS transform per frame from the same crop path the render burns
(render.plan_paths). Nothing here encodes the cut; the render stays the one source of truth for the exported file.
$0, ffmpeg only for the two edit files (once per source).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from PIL import Image

from . import broll
from . import music as bed
from .api import dump_json
from .captions import build_overlays, fit_line, highlight_words, norm
from .edges import Audio
from .edl import title_card_on
from .frame import frame_for
from .graphics import FONTS, CaptionStyle, font, normalize_look
from .ingest import EDIT_MAX_FPS, EDIT_PROXY_SHORT, ffprobe, make_edit_proxy
from .punch import plan_punches
from .render import TITLE_FADE_S, TITLE_S, load_track, plan_paths, reframe_inputs

EDIT_DIR = Path("ui") / "cache" / "edit"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(run_dir: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(run_dir.resolve()), threading.Lock())


def edit_media(run_dir: Path, edl: dict, log=None) -> dict:
    """The edit proxy and edit audio of the EDL's source, made once (about 4 s for 69 s of 1080p on the Mac) and
    kept in ui/cache/edit/ until the source changes (path, size, mtime)."""
    src = Path(edl["source"]["path"])
    if not src.is_file():
        raise FileNotFoundError(f"source footage {src} not found")
    st = src.stat()
    key = {"source": src.as_posix(), "bytes": st.st_size, "mtime": round(st.st_mtime, 3), "short": EDIT_PROXY_SHORT, "max_fps": EDIT_MAX_FPS}
    d = run_dir / EDIT_DIR
    video, audio, meta = d / "proxy.mp4", d / "audio.flac", d / "meta.json"
    with _lock(run_dir):
        if meta.is_file() and video.is_file() and audio.is_file():
            old = json.loads(meta.read_text())
            if old.get("key") == key:
                return old
        d.mkdir(parents=True, exist_ok=True)
        tmp_v, tmp_a = d / "proxy.part.mp4", d / "audio.part.flac"
        p = make_edit_proxy(src, tmp_v, tmp_a, ffprobe(src), log=log)
        tmp_v.replace(video)
        tmp_a.replace(audio)
        rec = {"key": key, "width": p.width, "height": p.height, "fps": p.fps, "duration_s": p.duration,
               "video": (EDIT_DIR / "proxy.mp4").as_posix(), "audio": (EDIT_DIR / "audio.flac").as_posix(),
               "bytes": video.stat().st_size + audio.stat().st_size}
        dump_json(meta, rec)
        return rec


def title_seconds(run_dir: Path, edl: dict) -> tuple[float, str | None]:
    """(seconds, png) of the opening card: what the render recorded, else what a render of this EDL would put there
    (the card only when the EDL wants it and title/title_card.png exists: a draft never generates one)."""
    rec = edl.get("render")
    if rec is not None and "title" in rec:
        t = rec["title"] or {}
        return float(t.get("seconds") or 0.0), t.get("png")
    if not title_card_on(edl):
        return 0.0, None
    png = run_dir / "title" / "title_card.png"
    if not png.is_file():
        return 0.0, None
    fps = float(edl["source"]["fps"])
    req = run_dir / "title" / "request.json"
    try:  # new words in a draft: the card made at "Make Version N" is not there yet, the browser shows the words
        same = f'"{edl["title_card_text"]}"' in json.loads(req.read_text()).get("prompt", "")
    except (OSError, ValueError):
        same = True  # an older run folder without the request: its card is the one there is
    return round(round(TITLE_S * fps) / fps, 4), png.as_posix() if same else None


def loudness_gain(edl: dict | None) -> float | None:
    """The gain (dB) that takes the cut's speech to the render's target loudness: the render's first loudness pass
    on that version (render.loudnorm). The browser adds it before a limiter, so the preview plays about as loud as
    the exported file; a draft borrows its base version's."""
    ln = ((edl or {}).get("render") or {}).get("loudnorm") or {}
    # with music the record's own first pass measured the mix: the speech's pass is under "speech"
    first = (ln.get("speech") or {}).get("first_pass") or ln.get("first_pass") or {}
    try:
        return round(float(ln.get("target_i", -14.0)) - float(first["input_i"]), 2)
    except (KeyError, TypeError, ValueError):
        return None


def _caption_style(look) -> dict:
    """The look's colours and modes the browser draws a caption line with (graphics.draw_caption's rules)."""
    return {"font": look.font, "text": look.text, "stroke": look.stroke, "active": look.active, "active_fg": look.active_fg,
            "active_bg": look.active_bg, "highlight": look.highlight, "box": look.box, "shadow": look.shadow,
            "upper": look.case == "upper", "callout_bg": look.callout_bg, "callout_fg": look.callout_fg}


def _line(c: dict, style: CaptionStyle, names: set[str]) -> dict:
    """One laid-out caption line with what draw_caption would draw it with: the size after fit_line, the pad and
    the stroke in frame pixels, the font's ascent and descent, which words are names."""
    texts = [w["w"] for w in c["words"]]
    st = fit_line(texts, style)
    asc, desc = font(st.size, st.font).getmetrics()
    return {"id": c["id"], "t_in": c["t_in"], "t_out": c["t_out"], "box": c["box"], "placement": c["placement"],
            "words": [{"w": st.shown(w["w"]), "t_in": w["t_in"], "t_out": w["t_out"],
                       "hl": norm(w["w"]).replace("'s", "") in names} for w in c["words"]],
            "size": st.size, "stroke": st.stroke, "space": st.space, "pad_x": st.pad_x, "pad_y": st.pad_y,
            "ascent": asc, "descent": desc}


def preview_data(run_dir: Path, edl: dict, overlays: bool = True, laid: bool = False) -> dict:
    """What the live player draws for an EDL, planned by the render's own code without drawing or encoding: the
    frame-aligned segments in source and cut time with each one's crop path (one crop x per source frame, source
    pixels) and punch-in zoom, the caption lines and callouts laid out (frame pixels, cut seconds), the B-roll stills
    (composed PNGs in ui/cache/preview/), the title card and the loudness gain. `overlays` False gives the segments
    and crop only (about 5 ms on run 015; with overlays about 30-60 ms). `laid` adds "_edl": the EDL with the
    layout fields a render would write (captions, callouts, punch_ins and a render record of its length and card),
    for the editor's version entry of a draft (runs.version_entry)."""
    edl = json.loads(json.dumps(edl))  # the layout writes into it
    concept, track, speaker_ids, fallback = reframe_inputs(run_dir, edl)
    segments, paths = plan_paths(edl, track, speaker_ids, fallback=fallback)
    fps = float(edl["source"]["fps"])
    title_s, title_png = title_seconds(run_dir, edl)
    frame = frame_for(edl)
    out, t = [], title_s
    for seg, path in zip(segments, paths):
        dur = seg.frames / fps
        out.append({"index": seg.index, "t_in": seg.t_in, "t_out": seg.t_out, "frames": seg.frames,
                    "cut_t0": round(t, 6), "span_ids": seg.span_ids,
                    "crop": {"w": path.crop_w, "h": path.crop_h, "y": path.crop_y, "xs": path.xs}})
        t += dur
    src = edl["source"]
    data = {"fps": fps, "source": {"width": src["width"], "height": src["height"]},
            "frame": {"aspect": frame.aspect, "width": frame.w, "height": frame.h},
            "title": {"seconds": title_s, "png": title_png, "fade_s": TITLE_FADE_S,
                      "text": edl.get("title_card_text") or ""} if title_s else None,
            "duration_s": round(t, 6), "segments": out, "reframe_concept": concept,
            "gain_db": loudness_gain(edl), "captions": [], "callouts": [], "stills": [], "punch_ins": []}
    music = bed.normalize_music(edl.get("music"))
    if music:  # the bed as render_audio builds it: the loop at level_db, ducked along the speech, faded in and out
        t_ = bed.track(music["track"])
        vpath0 = run_dir / "voice" / "voice.json"
        vwords = json.loads(vpath0.read_text())["words"] if vpath0.is_file() else None
        intervals, how = bed.speech_intervals(segments, vwords, title_s, None if vwords else Audio.load(run_dir))
        intervals = bed.edit_ducks(intervals, music)
        data["music"] = {"track": t_["id"], "name": t_["name"], "gain_db": round(music["level_db"] - float(t_["lufs"]), 2),
                         "level_db": music["level_db"], "track_lufs": float(t_["lufs"]), "duck_db": music["duck_db"],
                         "voice_db": float(music.get("voice_db") or 0.0), "ducked": [list(x) for x in intervals],
                         "duck_source": how, "attack_s": bed.ATTACK_S, "release_s": bed.RELEASE_S,
                         "fade_in_s": min(bed.FADE_IN_S, t / 3), "fade_out_s": bed.fade_out_for(music, t),
                         "start_s": float(music.get("start_s") or 0.0), "duck_edits": music.get("duck_edits") or [],
                         "loop_s": float(t_["loop_s"])}
    if not overlays:
        return data
    look = normalize_look(edl.get("look"))
    style = CaptionStyle.from_look(look)
    vpath = run_dir / "voice" / "voice.json"
    words = json.loads(vpath.read_text())["words"] if vpath.is_file() else []
    cache = run_dir / "ui" / "cache" / "preview"
    head = load_track(run_dir, edl, "head")
    fields, _ = build_overlays(edl, words, segments, paths, head, load_track(run_dir, edl, "person"), cache / "layout",
                               title_s=title_s, captions=bool(words), reframe_concept=concept, draw_at=-1.0)
    edl.update(fields)
    names = highlight_words(edl, look)
    data["look"] = _caption_style(look)
    data["fonts"] = {look.font: FONTS[look.font][0]}
    data["captions"] = []
    for c in fields["captions"]:  # a clip's own look (span_looks) rides on its lines and its callout
        if c.get("look"):
            lk = normalize_look(c["look"])
            data["fonts"][lk.font] = FONTS[lk.font][0]
            data["captions"].append({**_line(c, CaptionStyle.from_look(lk), highlight_words(edl, lk)), "look": _caption_style(lk)})
        else:
            data["captions"].append(_line(c, style, names))
    data["callouts"] = []
    for c in fields["callouts"]:
        lk = normalize_look(c["look"]) if c.get("look") else look
        data["fonts"][lk.font] = FONTS[lk.font][0]
        ca, cd = font(58, lk.font).getmetrics()  # graphics.draw_callout's size
        data["callouts"].append({"span": c["span"], "text": c["text"], "t_in": c["t_in"], "t_out": c["t_out"], "box": c["box"],
                                 "size": 58, "ascent": ca, "descent": cd, **({"look": _caption_style(lk)} if c.get("look") else {})})
    if fields.get("title_header"):  # the title over the opening speech: the same pill as a callout, at its own size
        h = fields["title_header"]
        ca, cd = font(h["size"], look.font).getmetrics()
        data["callouts"].append({"span": "title", "text": h["text"], "t_in": h["t_in"], "t_out": h["t_out"], "box": h["box"],
                                 "size": h["size"], "ascent": ca, "descent": cd})
    if edl.get("overlays"):
        stills, records = broll.place(edl, segments, title_s, run_dir, cache / "broll", frame)
        at = {o.png.as_posix(): o for ovs in stills.values() for o in ovs}
        for r in records:
            o = at.get(r.get("png") or "")
            if not r["shown"] or o is None:
                continue
            with Image.open(o.png) as im:
                w, h = im.size
            data["stills"].append({"id": r["id"], "placement": r["placement"], "t_in": r["t_in"], "t_out": r["t_out"],
                                   "x": o.x, "y": o.y, "w": w, "h": h, "fade_s": o.fade_s,
                                   "png": o.png.relative_to(run_dir).as_posix()})
    if edl.get("punch"):
        zooms, punches = plan_punches(edl, segments, paths, words, Audio.load(run_dir),
                                      head if concept == "head" else None, fields["captions"], title_s=title_s)
        for k, z in zooms.items():
            out[k]["zoom"] = {"base": z["base"], "anchor": list(z["anchor"]),
                              "events": [{x: e[x] for x in ("a", "b", "c", "d", "z")} for e in z["events"]]}
        data["punch_ins"] = punches
        edl["punch_ins"] = punches
    if laid:
        edl.setdefault("render", {"duration_s": data["duration_s"],
                                  "title": {"seconds": title_s, "png": title_png} if title_s else None})
        data["_edl"] = edl
    return data


def heads_at(run_dir: Path, edl: dict, t: float) -> dict:
    """Every tracked head on screen at cut second t of `edl` (SP4: click a face in the viewer to pin it): the source
    second there, the speaker of the clip there, and each head box of the SAM head track through the crop into frame
    pixels [x0, y0, x1, y1], scaled about the anchor of a punch-in playing there (punch.zoom_at: the picture the viewer
    shows, zoomed). Empty on the title card or without a head track."""
    from .captions import interp_box, to_output
    from .punch import zoom_at
    data = preview_data(run_dir, edl, overlays=bool(edl.get("punch")))  # the punch-ins are planned with the layout
    track = load_track(run_dir, edl, "head")
    seg = next((s for s in data["segments"] if s["cut_t0"] <= t < s["cut_t0"] + s["frames"] / data["fps"]), None)
    if seg is None or not track:
        return {"t": t, "src_t": None, "speaker": None, "heads": []}
    src_t = seg["t_in"] + (t - seg["cut_t0"])
    k = min(len(seg["crop"]["xs"]) - 1, max(0, int((t - seg["cut_t0"]) * data["fps"])))
    span = next((s for s in edl["spans"] if s["id"] in seg["span_ids"] and s["t_in"] <= src_t + 1e-3 and src_t < s["t_out"]),
                next((s for s in edl["spans"] if s["id"] in seg["span_ids"]), None))
    fx = float(edl["source"]["width"]) / float(track["source"]["width"])
    fy = float(edl["source"]["height"]) / float(track["source"]["height"])
    frame, crop = frame_for(edl), seg["crop"]
    zoom = seg.get("zoom")
    z = zoom_at(zoom, t - seg["cut_t0"]) if zoom else 1.0
    ax, ay = zoom["anchor"] if zoom else (0.0, 0.0)
    heads = []
    for oid, recs in (track.get("objects") or {}).items():
        b = interp_box([r for r in recs if r.get("box")], src_t)
        if b is None:
            continue
        x0, y0, x1, y1 = to_output([b[0] * fx, b[1] * fy, b[2] * fx, b[3] * fy], crop["xs"][k], crop["w"], crop["h"],
                                   crop.get("y") or 0, frame)
        x0, x1 = ax + (x0 - ax) * z, ax + (x1 - ax) * z
        y0, y1 = ay + (y0 - ay) * z, ay + (y1 - ay) * z
        if x1 <= 0 or x0 >= frame.w or y1 <= 0 or y0 >= frame.h:
            continue  # outside the 9:16 window
        heads.append({"id": str(oid), "box": [round(x0), round(y0), round(x1), round(y1)]})
    return {"t": t, "src_t": round(src_t, 3), "speaker": str(span["speaker"]) if span and span.get("speaker") is not None else None,
            "span": span["id"] if span else None, "heads": heads}
