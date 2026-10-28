"""The Export page's pre-flight: what a version is ready for, read from its EDL, its blind review and its render record.

Every check is one row {id, label, status ok | warn | fail | info, detail}; nothing here runs ffmpeg or a model.
Platform limits: Instagram Reels and YouTube Shorts take up to 3 minutes, TikTok uploads up to 10 minutes. The UI
zones are the viewer's safe-area guides (web/src/SafeArea.tsx), approximate published guidance for 1080x1920.

For a feed aspect (4:5, 1:1: the Export page renders the version again in that frame) the format row names that frame,
the app safe-area rows give way to one row saying they are for 9:16 (a feed post has its UI outside the picture), and
the face row reads the captions and callouts as they would be laid out in that frame (engine/look.layout, no render).
"""
from __future__ import annotations

from pathlib import Path

from ..engine.frame import FRAME_916, frame_of, with_aspect
from .runs import read_json

W, H = 1080, 1920
# fractions of the frame each app covers with its own UI (top, bottom, left, right), as SafeArea.tsx draws them
ZONES = {
    "Reels": (0.14, 0.35, 0.06, 0.06),
    "TikTok": (150 / H, 440 / H, 60 / W, 140 / W),
    "Shorts": (160 / H, 480 / H, 60 / W, 140 / W),
}
RIGHT_COLUMN_FROM = 0.45
MAX_S = {"Reels": 180, "TikTok": 600, "Shorts": 180}
LUFS_TARGET, LUFS_TOL, PEAK_MAX = -14.0, 1.0, -1.0


def under_ui(box: list[float], zone: tuple[float, float, float, float]) -> bool:
    """A [x, y, w, h] box (frame px) that reaches into the app's UI margins; the right-hand margin is the like / share
    column, which sits in the lower half of the screen."""
    x, y, w, h = box
    top, bottom, left, right = zone
    return (y < top * H or y + h > H - bottom * H or x < left * W
            or (x + w > W - right * W and y + h > RIGHT_COLUMN_FROM * H))


def checks(run_dir: Path, n: int, aspect: str = "9:16") -> list[dict]:
    frame = frame_of(aspect)
    feed = frame != FRAME_916
    edl = read_json(run_dir / f"edl_v{n}.json") or {}
    render = edl.get("render") or {}
    rows: list[dict] = []

    def row(id_: str, label: str, status: str, detail: str) -> None:
        rows.append({"id": id_, "label": label, "status": status, "detail": detail})

    rv = read_json(run_dir / f"review_v{n}.json")
    r = (rv or {}).get("review") or {}
    if rv and Path(rv.get("cut") or "").name in ("", f"cut_v{n}.mp4") and r.get("overall") is not None:
        gate = rv.get("pass_gate") or {}
        reasons = gate.get("reasons") or []
        if r.get("pass"):
            row("review", "Blind review", "ok", f"{r['overall']}/100, passes the gate")
        else:
            row("review", "Blind review", "warn", f"{r['overall']}/100, no pass" + (f": {reasons[0]}" if reasons else ""))
    else:
        row("review", "Blind review", "info", "Not reviewed: Review this version first to know what a viewer will notice")

    loud = render.get("loudness_ebur128") or {}
    i, tp = loud.get("integrated_lufs"), loud.get("true_peak_dbtp")
    if i is None:
        row("loudness", "Loudness", "info", "Not measured")
    elif abs(i - LUFS_TARGET) <= LUFS_TOL and (tp is None or tp <= PEAK_MAX):
        row("loudness", "Loudness", "ok", f"{i} LUFS, peak {tp} dBTP: " + ("as in the 9:16 cut" if feed else
                                                                          "the level Reels, TikTok and Shorts play at"))
    else:
        row("loudness", "Loudness", "warn", f"{i} LUFS, peak {tp} dBTP (target {LUFS_TARGET:g} LUFS, peak under {PEAK_MAX:g})")

    w, h = render.get("width"), render.get("height")
    fps = (edl.get("source") or {}).get("fps")
    if feed:
        row("format", "Format", "ok", f"{frame.w} x {frame.h} ({frame.aspect}), rendered again from this version"
            + (f", {fps:g} fps" if fps else "") + ", H.264 + AAC")
    elif w and h:
        ok = (w, h) == (W, H)
        row("format", "Format", "ok" if ok else "warn", f"{w} x {h}{' (9:16)' if ok else ''}, {fps:g} fps, H.264 + AAC" if fps
            else f"{w} x {h}")

    d = render.get("duration_s") or edl.get("runtime_s")
    if d and feed:
        row("duration", "Length", "ok", f"{d:.1f} s")
    elif d:
        over = [p for p, lim in MAX_S.items() if d > lim]
        row("duration", "Length", "fail" if over else "ok",
            f"{d:.1f} s: too long for {', '.join(over)}" if over else f"{d:.1f} s: fits Reels, TikTok and Shorts")

    caps = edl.get("captions") or []
    calls = edl.get("callouts") or []
    if feed:
        try:
            from ..engine.look import layout
            laid = layout(run_dir, with_aspect(dict(edl), frame.aspect))
            caps, calls = laid["captions"], laid["callouts"]
        except (FileNotFoundError, KeyError, ValueError) as e:
            row("layout", "Captions in this frame", "info", f"Not measured ({e})")
        row("safe_feed", "Safe areas", "info", f"The Reels, TikTok and Shorts guides are for 9:16; a {frame.aspect} feed post "
            "shows the whole frame with the app's buttons outside it")
    for plat, zone in ({} if feed else ZONES).items():
        nc = sum(1 for c in caps if c.get("box") and under_ui(c["box"], zone))
        nk = sum(1 for c in calls if c.get("box") and under_ui(c["box"], zone))
        if not caps and not calls:
            continue
        what = ", ".join(x for x in (f"{nc} of {len(caps)} caption lines" if nc else "", f"{nk} callout{'s' if nk > 1 else ''}" if nk else "") if x)
        row(f"safe_{plat.lower()}", f"{plat} safe area", "warn" if what else "ok",
            f"{what} under the app's buttons or caption (approx.{', Meta ads guidance' if plat == 'Reels' else ''}): move them in Style" if what else "Captions and callouts clear of the app's UI (approx.)")

    on_face = [c for c in calls if c.get("over_face")]
    if feed and on_face:
        row("callout_face", "Callouts over the face", "warn", f"{len(on_face)} in {frame.aspect}: pin them in Style")
    face = [c for c in caps if c.get("over_face")]
    if face:
        mine = all(c.get("placement") == "fixed" for c in face)
        row("face", "Captions over the face", "info" if mine else "warn",
            f"{len(face)} line{'s' if len(face) > 1 else ''} at your fixed position" if mine
            else f"{len(face)} line{'s' if len(face) > 1 else ''} touch the face")
    if not caps:
        row("captions", "Captions", "info", "None burned in")
    return rows
