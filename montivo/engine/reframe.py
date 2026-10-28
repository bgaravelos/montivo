"""Reframe: a 9:16 crop path per rendered segment from the SAM person track.

The track is the SAM "head" track (5 fps) when the run has one, else the "person" track: one box per frame in proxy
pixels. A lean moves the head about three times further than the body box centre (measured on Billy's clip: head
450 px, body box 130 px at 1920), so the person box is only a fallback. For a segment (t_in, t_out) we pick the
object whose box is largest and most centred inside that window, map its centre to source pixels, interpolate the
gaps, then run a camera-operator smoother: dead zone of +-8 % of the crop width (the camera does not chase small head
moves), EMA toward the target when it leaves the dead zone (tau 0.25 s), pan speed clamped (450 px/s on a 1280 px
source, 675 at 1920: a 340 px lean in 0.6 s is followed without lag), x clamped inside the frame. The crop is full height, width = even(h * 9/16).
Other output aspects (frame.py: 4:5, 1:1) only change that width. A source narrower than the aspect (a phone clip cut
to 4:5) keeps its full width and the height is cut instead, at one y per segment that puts the followed head's centre
HEAD_Y_FRAC down the crop.

With a speaker map (speakers.py) a segment is planned in pieces, one per run of spans by the same speaker: each piece
follows that speaker's head ids when one of them covers it, else the rule above; neighbouring pieces that land on the
same id are planned as one window, so the camera only cuts where the followed head changes.

Output per segment: one ffmpeg sendcmd line per source frame (`<t> crop x <px>;`, times relative to the segment
start) and a sparse list of keyframes (every 0.5 s or when a pan starts or stops) for the EDL and the UI.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ReframeParams:
    dead_zone_frac: float = 0.08  # of the crop width, each side
    ema_tau_s: float = 0.25  # time constant of the move toward the target
    max_pan_px_s: float = 450.0  # on a 1280 px wide source, scaled with the source width (675 px/s at 1920)
    keyframe_every_s: float = 0.5
    min_coverage: float = 0.2  # a track must cover this share of the window to be picked
    edge_px: int = 2  # ignore x changes smaller than this when deciding "moving"


@dataclass
class SegmentPath:
    t_in: float
    t_out: float
    track_id: str | None  # None = centre crop fallback
    crop_w: int
    crop_h: int
    xs: list[int]  # crop x per source frame, frame k at t_in + k / fps
    fps: float
    keyframes: list[dict] = field(default_factory=list)
    pieces: list[dict] = field(default_factory=list)  # [{t_in, t_out, speaker, track_id}] when a speaker map steered it
    crop_y: int = 0  # top of the crop, 0 unless the source is narrower than the aspect

    def sendcmd_text(self) -> str:
        """One line per source frame. Command k fires half a frame before frame k so rounding never skips it."""
        lines = []
        for k, x in enumerate(self.xs):
            t = 0.0 if k == 0 else (k - 0.5) / self.fps
            lines.append(f"{t:.4f} crop x {x};")
        return "\n".join(lines) + "\n"


HEAD_Y_FRAC = 0.4


def crop_size(source_w: int, source_h: int, aspect: tuple[int, int] = (9, 16)) -> tuple[int, int]:
    """The crop: full height and the aspect's width, or, on a source narrower than the aspect, full width and the
    aspect's height."""
    w = int(round(source_h * aspect[0] / aspect[1] / 2)) * 2
    if w <= source_w - source_w % 2:
        return w, source_h - source_h % 2
    w = source_w - source_w % 2
    return w, min(source_h - source_h % 2, int(round(w * aspect[1] / aspect[0] / 2)) * 2)


def crop_top(track: dict | None, track_id: str | None, t_in: float, t_out: float, sh: int, crop_h: int) -> int:
    """The crop's y when it is shorter than the source: the followed box's median centre HEAD_Y_FRAC down the crop
    over the window, else the middle; even and inside the frame."""
    if crop_h >= sh:
        return 0
    y = (sh - crop_h) / 2
    if track and track_id and track_id in (track.get("objects") or {}):
        fy = sh / float(track["source"]["height"])
        cs = sorted((r["box"][1] + r["box"][3]) / 2 * fy for r in track["objects"][track_id] if t_in <= r["t"] <= t_out)
        if cs:
            y = cs[len(cs) // 2] - HEAD_Y_FRAC * crop_h
    return int(round(min(sh - crop_h, max(0.0, y)) / 2)) * 2


def pick_track(objects: dict[str, list[dict]], t_in: float, t_out: float, track_fps: float, frame_w: int,
               frame_h: int, min_coverage: float = 0.2, prefer: list[str] | None = None) -> str | None:
    """The object whose box is largest and most centred inside [t_in, t_out], or None when nothing covers it. With
    `prefer` (the speaker's head ids) those are tried first and the rest only when none of them covers the window."""
    if prefer:
        got = pick_track({o: objects[o] for o in prefer if o in objects}, t_in, t_out, track_fps, frame_w, frame_h,
                         min_coverage)
        if got is not None:
            return got
    expected = max(1.0, (t_out - t_in) * track_fps)
    best: tuple[float, str] | None = None
    for oid, recs in objects.items():
        inside = [r for r in recs if t_in - 0.5 / track_fps <= r["t"] <= t_out + 0.5 / track_fps]
        if not inside:
            continue
        coverage = min(1.0, len(inside) / expected)
        if coverage < min_coverage:
            continue
        area = sum((r["box"][2] - r["box"][0]) * (r["box"][3] - r["box"][1]) for r in inside) / len(inside)
        area /= float(frame_w * frame_h)
        cx = sum((r["box"][0] + r["box"][2]) / 2 for r in inside) / len(inside)
        centred = max(0.1, 1.0 - 2.0 * abs(cx / frame_w - 0.5))
        score = area * coverage * centred
        if best is None or score > best[0]:
            best = (score, oid)
    return best[1] if best else None


def centre_samples(recs: list[dict], t_in: float, t_out: float, track_fps: float, scale_x: float) -> list[float]:
    """Box centre x in source pixels on the track grid t_in, t_in + 1/fps, ... <= t_out; gaps interpolated,
    the edges held."""
    pts = sorted(((r["t"], (r["box"][0] + r["box"][2]) / 2 * scale_x) for r in recs), key=lambda p: p[0])
    n = int(math.floor((t_out - t_in) * track_fps + 1e-6)) + 1
    out: list[float] = []
    j = 0
    for k in range(n):
        t = t_in + k / track_fps
        while j + 1 < len(pts) and pts[j + 1][0] <= t:
            j += 1
        if t <= pts[0][0]:
            out.append(pts[0][1])
        elif j + 1 >= len(pts):
            out.append(pts[-1][1])
        else:
            (t0, c0), (t1, c1) = pts[j], pts[j + 1]
            out.append(c0 if t1 == t0 else c0 + (c1 - c0) * (t - t0) / (t1 - t0))
    return out


def smooth_centres(targets: list[float], dt: float, crop_w: int, source_w: int, params: ReframeParams) -> list[float]:
    """Camera centre per sample: dead zone, EMA toward the target, pan speed clamp, clamp inside the frame."""
    lo, hi = crop_w / 2, source_w - crop_w / 2
    clamp = lambda v: min(hi, max(lo, v))  # noqa: E731
    if not targets:
        return []
    dead = params.dead_zone_frac * crop_w
    alpha = 1.0 - math.exp(-dt / params.ema_tau_s)
    max_step = params.max_pan_px_s * (source_w / 1280.0) * dt
    cur = clamp(targets[0])
    out = [cur]
    for target in targets[1:]:
        target = clamp(target)
        diff = target - cur
        if abs(diff) > dead:
            step = alpha * diff
            step = max(-max_step, min(max_step, step))
            cur = clamp(cur + step)
        out.append(cur)
    return out


def resample(values: list[float], src_fps: float, dst_fps: float, duration: float) -> list[float]:
    """Linear resampling of a path sampled at src_fps onto frames k / dst_fps for the whole duration."""
    n = max(1, int(math.ceil(duration * dst_fps - 1e-6)))
    out: list[float] = []
    for k in range(n):
        p = k / dst_fps * src_fps
        i = int(math.floor(p))
        if i + 1 >= len(values):
            out.append(values[-1])
        else:
            f = p - i
            out.append(values[i] * (1 - f) + values[i + 1] * f)
    return out


def keyframes_for(xs: list[int], fps: float, t_in: float, every_s: float, edge_px: int, span_ids: list[str]) -> list[dict]:
    """Sparse keyframes: one every `every_s` and one whenever the camera starts or stops moving."""
    kfs: list[dict] = []
    last_t = -1e9
    moving = False
    for k, x in enumerate(xs):
        t = k / fps
        now_moving = k > 0 and abs(x - xs[k - 1]) >= edge_px
        if k == 0 or t - last_t >= every_s - 1e-9 or now_moving != moving or k == len(xs) - 1:
            kfs.append({"t": round(t_in + t, 3), "t_seg": round(t, 3), "x": x})
            last_t = t
        moving = now_moving
    for kf in kfs:
        kf["spans"] = span_ids
    return kfs


def _window_centres(t_in: float, t_out: float, sw: int, crop_w: int, fps: float, track: dict | None,
                    params: ReframeParams, prefer: list[str] | None = None) -> tuple[str | None, list[float]]:
    """Picked track id and the smoothed crop centre per source frame over [t_in, t_out] (centre crop without one)."""
    duration = max(1.0 / fps, t_out - t_in)
    if track and track.get("objects"):
        track_fps = float(track.get("track_fps") or 10.0)
        tw = int(track["source"]["width"])
        th = int(track["source"]["height"])
        track_id = pick_track(track["objects"], t_in, t_out, track_fps, tw, th, params.min_coverage, prefer)
        if track_id is not None:
            raw = centre_samples(track["objects"][track_id], t_in, t_out, track_fps, sw / tw)
            smooth = smooth_centres(raw, 1.0 / track_fps, crop_w, sw, params)
            return track_id, resample(smooth, track_fps, fps, duration)
    return None, [sw / 2] * max(1, int(math.ceil(duration * fps - 1e-6)))


def plan_segment(t_in: float, t_out: float, source: dict, track: dict | None, params: ReframeParams | None = None,
                 span_ids: list[str] | None = None, follow: list[dict] | None = None,
                 aspect: tuple[int, int] = (9, 16)) -> SegmentPath:
    """Crop path for one rendered segment. `source` has width/height/fps; `track` is tracks.json's content
    (objects + track_fps + source size) or None for a centre crop. `follow` ([{t_in, t_out, speaker, ids}] covering
    the segment in order, from the speaker map) steers each piece to its speaker's head. `aspect` is the output's."""
    params = params or ReframeParams()
    sw, sh, fps = int(source["width"]), int(source["height"]), float(source["fps"])
    crop_w, crop_h = crop_size(sw, sh, aspect)
    pieces: list[dict] = []
    if follow and any(p.get("ids") for p in follow) and track and track.get("objects"):
        n = max(1, int(math.ceil(max(1.0 / fps, t_out - t_in) * fps - 1e-6)))
        tw, th = int(track["source"]["width"]), int(track["source"]["height"])
        track_fps = float(track.get("track_fps") or 10.0)
        for p in follow:
            tid = pick_track(track["objects"], p["t_in"], p["t_out"], track_fps, tw, th, params.min_coverage, p.get("ids"))
            if pieces and pieces[-1]["track_id"] == tid:
                pieces[-1]["t_out"] = p["t_out"]
                pieces[-1]["speaker"] = pieces[-1]["speaker"] if pieces[-1]["speaker"] == p.get("speaker") else None
            else:
                pieces.append({"t_in": p["t_in"], "t_out": p["t_out"], "speaker": p.get("speaker"), "track_id": tid})
        centres: list[float] = []
        for k, p in enumerate(pieces):
            k0 = 0 if k == 0 else int(round((p["t_in"] - t_in) * fps))
            k1 = n if k == len(pieces) - 1 else int(round((p["t_out"] - t_in) * fps))
            if k1 <= k0:
                continue
            a = t_in + k0 / fps
            _, cs = _window_centres(a, t_in + k1 / fps, sw, crop_w, fps, track, params,
                                    [p["track_id"]] if p["track_id"] else None)
            centres += (cs + [cs[-1]] * (k1 - k0))[:k1 - k0]
            p["t_in"], p["t_out"] = round(a, 4), round(t_in + k1 / fps, 4)
        pieces = [p for p in pieces if p["t_out"] > p["t_in"]]
        lengths: dict[str, float] = {}
        for p in pieces:
            if p["track_id"]:
                lengths[p["track_id"]] = lengths.get(p["track_id"], 0.0) + p["t_out"] - p["t_in"]
        track_id = max(lengths, key=lengths.get) if lengths else None
    else:
        track_id, centres = _window_centres(t_in, t_out, sw, crop_w, fps, track, params)
    xs = [int(round((c - crop_w / 2) / 2)) * 2 for c in centres]
    xs = [min(sw - crop_w, max(0, x)) for x in xs]
    seg = SegmentPath(t_in=t_in, t_out=t_out, track_id=track_id, crop_w=crop_w, crop_h=crop_h, xs=xs, fps=fps,
                      pieces=pieces, crop_y=crop_top(track, track_id, t_in, t_out, sh, crop_h))
    seg.keyframes = keyframes_for(xs, fps, t_in, params.keyframe_every_s, params.edge_px, span_ids or [])
    return seg


def id_at(path: SegmentPath, t: float) -> str | None:
    """The head id the crop follows at source time t (the piece that holds t, else the segment's id)."""
    for p in path.pieces:
        if p["t_in"] <= t < p["t_out"] and p["track_id"]:
            return p["track_id"]
    return path.track_id
