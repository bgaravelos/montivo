"""Punch-ins: short zooms on the words the speaker leans on, and an alternating scale across jump cuts.

Short-form editors punch in on a stressed word and change the scale at a jump cut so the cut reads as a choice. Both
come from data the run already has, at no API cost:

- Emphasis: every kept word's level in the source audio (the middle 60 % of the word) against the median of all kept
  words. A word at least EXCESS_DB over it, at least MIN_WORD_S long, MIN_LETTERS letters and not a stop word is a
  candidate; a caption highlight word (Montivo, Meta, ...) is one at any level. Candidates are taken by excess, at most
  one per EVERY_S of cut and MIN_GAP_S apart. The zoom ramps to PUNCH_Z over RAMP_IN_S ending at the word's onset,
  holds to HOLD_AFTER_S after the word and ramps out over RAMP_OUT_S, inside the segment.
- Jump cuts: when a segment opens on the head at almost the place and size where the previous one closed (same shot,
  a cut inside it), it gets a base scale of JUMP_Z, alternating, so consecutive takes do not look like a glitch.

The zoom is about one anchor per segment, the median centre of the head the crop follows, so the face stays where it
is on screen and grows. An event is skipped when the zoomed head would leave the top of the frame or touch a caption
on screen at that moment; a base scale is skipped the same way. ffmpeg's crop cannot change size per frame (the graph
stalls), so the zoom is a zoompan after the scale, before the caption overlays: captions and callouts never zoom.

Every entry in the EDL's `punch_ins` carries a `key` that survives edits: `emphasis:<word>@<source onset>` and
`jump_cut:<first span id of the segment>`. The editor removes one punch-in by adding its key to the EDL's
`punch_skip` (montivo.human punch --skip). A skipped punch-in still holds its place (its slot in the budget and the
spacing, its turn in the jump-cut alternation), so removing one never makes another appear somewhere else.
"""
from __future__ import annotations

import statistics
from fractions import Fraction

from .captions import HEAD_MARGIN, HIGHLIGHT, OUT_H, OUT_W, dedupe_words, interp_box, largest_at, norm, overlaps, \
    span_words, to_output
from .reframe import id_at

PUNCH_Z = 1.12
JUMP_Z = 1.06
RAMP_IN_S, HOLD_AFTER_S, RAMP_OUT_S = 0.12, 0.35, 0.25
EXCESS_DB = 3.0
MIN_WORD_S = 0.2
MIN_LETTERS = 4
MIN_GAP_S = 4.0
EVERY_S = 6.0
JUMP_DX_PX, JUMP_SIZE = 60.0, 0.15  # head centre moved less than this (output px) and size within 15 %: a jump cut
STOP = {"that", "this", "with", "from", "have", "what", "when", "then", "they", "there", "their", "about", "just",
        "like", "your", "into", "were", "been", "which", "would", "could", "because", "really", "very", "okay"}


def source_px(track: dict | None, sw: float, sh: float) -> dict | None:
    """A tracks.json in source pixels (SAM boxes are in the pixels of the clip it saw)."""
    if not track or not track.get("objects"):
        return None
    fx, fy = sw / float(track["source"]["width"]), sh / float(track["source"]["height"])
    return {"objects": {oid: [{"t": r["t"], "box": [r["box"][0] * fx, r["box"][1] * fy, r["box"][2] * fx, r["box"][3] * fy]}
                              for r in recs] for oid, recs in track["objects"].items()}}


def head_out(head: dict | None, seg, path, k: int) -> list[float] | None:
    """The followed head at segment frame k, in output pixels (the largest head when the followed id has no box)."""
    if head is None:
        return None
    t = seg.t_in + k / seg.fps
    tid = id_at(path, t) if path.pieces else path.track_id
    box = interp_box(head["objects"][tid], t) if tid in head["objects"] else None
    if box is None:
        boxes = largest_at(head, t)
        box = boxes[0] if boxes else None
    if box is None:
        return None
    x = path.xs[min(k, len(path.xs) - 1)]
    return to_output(box, x, path.crop_w, path.crop_h)


def zoomed(box: list[float], anchor: tuple[float, float], z: float) -> list[float]:
    ax, ay = anchor
    return [ax + (box[0] - ax) * z, ay + (box[1] - ay) * z, ax + (box[2] - ax) * z, ay + (box[3] - ay) * z]


def fits(heads: list[tuple[float, list[float]]], anchor: tuple[float, float], z: float, a: float, b: float,
         captions: list[tuple[float, float, list[float]]]) -> bool:
    """The head zoomed by z about the anchor stays inside the top of the frame and off every caption on screen
    between segment-local a and b."""
    for t, box in heads:
        if not a <= t <= b:
            continue
        zb = zoomed(box, anchor, z)
        if zb[1] < 0:
            return False
        if any(ca < t + 0.05 and cb > t - 0.05 and overlaps(r, zb, HEAD_MARGIN / 2) for ca, cb, r in captions):
            return False
    return True


SKIP_MATCH_S = 0.3  # a skipped emphasis matches the same word starting this close in the source (edges clip a span's words)


def punch_key(kind: str, word: str | None = None, src_t: float | None = None, span: str | None = None) -> str:
    return f"jump_cut:{span}" if kind == "jump_cut" else f"emphasis:{word}@{src_t:.3f}"


def parse_key(key: str) -> dict:
    """'emphasis:Montivo@12.340' -> {kind, word, src_t}; 'jump_cut:s4' -> {kind, span}. ValueError otherwise."""
    kind, _, rest = key.partition(":")
    if kind == "jump_cut" and rest:
        return {"kind": kind, "span": rest}
    if kind == "emphasis" and "@" in rest:
        word, _, t = rest.rpartition("@")
        if word:
            return {"kind": kind, "word": word, "src_t": float(t)}
    raise ValueError(f"not a punch-in key: {key!r} (emphasis:<word>@<seconds> or jump_cut:<span id>)")


def skipped(skips: list[dict], kind: str, word: str | None = None, src_t: float | None = None,
            span: str | None = None) -> bool:
    for k in skips:
        if k["kind"] != kind:
            continue
        if kind == "jump_cut" and k["span"] == span:
            return True
        if kind == "emphasis" and norm(k["word"]) == norm(word or "") and abs(k["src_t"] - (src_t or 0.0)) <= SKIP_MATCH_S:
            return True
    return False


def word_level(audio, t0: float, t1: float) -> float:
    pad = (t1 - t0) * 0.2
    return audio.level(t0 + pad, t1 - pad)


def plan_punches(edl: dict, segments: list, paths: list, words: list[dict], audio, head_track: dict | None,
                 captions: list[dict], title_s: float = 0.0) -> tuple[dict[int, dict], list[dict]]:
    """Per segment index {"base": z, "anchor": (x, y), "events": [{"a", "b", "c", "d", "z"}]} in segment-local
    seconds (ramp in a to b, hold to c, ramp out to d), and the EDL entries (cut times, the word, its excess, the key).
    The keys in the EDL's `punch_skip` are left out, each one holding its place."""
    skips = []
    for k in edl.get("punch_skip") or []:
        try:
            skips.append(parse_key(k))
        except ValueError:
            continue
    head = source_px(head_track, float(edl["source"]["width"]), float(edl["source"]["height"]))
    if head is None:
        return {}, []
    cut0, t = [], title_s
    for seg in segments:
        cut0.append(t)
        t += seg.duration
    cut_len = t - title_s

    heads: dict[int, list[tuple[float, list[float]]]] = {}
    plan: dict[int, dict] = {}
    for i, (seg, path) in enumerate(zip(segments, paths)):
        hs = [(k / seg.fps, b) for k in range(0, max(1, seg.frames), 3) if (b := head_out(head, seg, path, k))]
        heads[i] = hs
        if hs:
            plan[i] = {"base": 1.0, "events": [],
                       "anchor": (statistics.median((b[0] + b[2]) / 2 for _, b in hs),
                                  statistics.median((b[1] + b[3]) / 2 for _, b in hs))}
    caps: dict[int, list[tuple[float, float, list[float]]]] = {}
    for c in captions:
        x, y, w, h = c["box"]
        i = c["segment"]
        caps.setdefault(i, []).append((c["t_in"] - cut0[i], c["t_out"] - cut0[i], [x, y, x + w, y + h]))

    entries: list[dict] = []
    alt: set[int] = set()  # segments on the zoomed side of the alternation (a skipped one too)
    # jump cuts: alternate the base scale while the head stays put across the cut
    for i in range(1, len(segments)):
        if i not in plan or i - 1 not in plan or i - 1 in alt:
            continue
        (_, prev), (_, cur) = heads[i - 1][-1], heads[i][0]
        dx = abs((prev[0] + prev[2]) / 2 - (cur[0] + cur[2]) / 2)
        size = (cur[2] - cur[0]) / max(1.0, prev[2] - prev[0])
        if dx < JUMP_DX_PX and abs(size - 1) < JUMP_SIZE and \
                fits(heads[i], plan[i]["anchor"], JUMP_Z, 0, segments[i].duration, caps.get(i, [])):
            alt.add(i)
            sid = segments[i].span_ids[0]
            if skipped(skips, "jump_cut", span=sid):
                continue
            plan[i]["base"] = JUMP_Z
            entries.append({"kind": "jump_cut", "segment": i, "span": sid, "key": punch_key("jump_cut", span=sid),
                            "t_in": round(cut0[i], 3), "t_out": round(cut0[i] + segments[i].duration, 3), "zoom": JUMP_Z})

    # emphasis: the loudest content words over the cut's median level
    if audio is not None:
        seg_of_span = {sid: i for i, seg in enumerate(segments) for sid in seg.span_ids}
        cands = []
        per_span = span_words(edl["spans"], words)
        kept = []
        for s in edl["spans"]:
            ws, _ = dedupe_words(per_span.get(s["id"], []))
            i = seg_of_span.get(s["id"])
            if i is None:
                continue
            for w in ws:
                if w.t_out - w.t_in > 0.05:
                    kept.append((i, w, word_level(audio, w.t_in, w.t_out)))
        if kept:
            median = statistics.median(db for _, _, db in kept)
            for i, w, db in kept:
                n = norm(w.w).replace("'s", "")
                hl = n in HIGHLIGHT
                ok = w.t_out - w.t_in >= MIN_WORD_S and len(n) >= MIN_LETTERS and n not in STOP
                if i in plan and (hl or (ok and db - median >= EXCESS_DB)):
                    cands.append((db - median + (3.0 if hl else 0.0), i, w, round(db - median, 1)))
        budget = max(1, int(cut_len // EVERY_S))
        taken: list[float] = []
        for score, i, w, excess in sorted(cands, key=lambda c: -c[0]):
            if len(taken) >= budget:
                break
            seg = segments[i]
            on = w.t_in - seg.t_in
            a, b = max(0.0, on - RAMP_IN_S), max(0.0, on)
            c = min(seg.duration - RAMP_OUT_S, w.t_out - seg.t_in + HOLD_AFTER_S)
            d = c + RAMP_OUT_S
            at = cut0[i] + b
            if c - b < 0.2 or any(abs(at - x) < MIN_GAP_S for x in taken):
                continue
            z = plan[i]["base"] * PUNCH_Z
            if not fits(heads[i], plan[i]["anchor"], z, a, d, caps.get(i, [])):
                continue
            taken.append(at)
            if skipped(skips, "emphasis", word=w.w, src_t=w.t_in):
                continue
            plan[i]["events"].append({"a": a, "b": b, "c": c, "d": d, "z": PUNCH_Z})
            entries.append({"kind": "emphasis", "segment": i, "span": w.span, "word": w.w, "src_t": round(w.t_in, 3),
                            "key": punch_key("emphasis", word=w.w, src_t=w.t_in), "excess_db": excess,
                            "t_in": round(cut0[i] + a, 3), "t_out": round(cut0[i] + d, 3), "zoom": PUNCH_Z})
    for p in plan.values():
        p["events"].sort(key=lambda e: e["a"])
    plan = {i: p for i, p in plan.items() if p["base"] != 1.0 or p["events"]}
    for e in entries:
        e["anchor"] = [round(v) for v in plan[e["segment"]]["anchor"]]
    return plan, sorted(entries, key=lambda e: e["t_in"])


def zoom_filter(p: dict, fps: float) -> str:
    """The zoompan filter for one segment: the base scale times 1 + (z - 1) * a trapezoid per event over the
    output frame number `on`, about the segment's anchor, at the output size and the segment's exact rate."""
    def f(t: float) -> int:
        return int(round(t * fps))

    terms = []
    for e in p["events"]:
        a, b, c, d = f(e["a"]), f(e["b"]), f(e["c"]), f(e["d"])
        up = f"clip((on-{a})/{b - a},0,1)" if b > a else f"gte(on,{a})"
        down = f"clip(({d}-on)/{d - c},0,1)" if d > c else f"lt(on,{d})"
        terms.append(f"{e['z'] - 1:.4f}*{up}*{down}")
    z = f"{p['base']:.4f}*(1+{'+'.join(terms)})" if terms else f"{p['base']:.4f}"
    ax, ay = p["anchor"]
    rate = Fraction(fps).limit_denominator(1001)
    return (f"zoompan=z='{z}':x='{ax:.1f}-{ax:.1f}/zoom':y='{ay:.1f}-{ay:.1f}/zoom':d=1:s={OUT_W}x{OUT_H}"
            f":fps={rate.numerator}/{rate.denominator}")


def zoom_at(p: dict, t: float) -> float:
    """The scale at segment-local t (the same curve as zoom_filter, for tests and the UI)."""
    z = 1.0
    for e in p["events"]:
        up = 1.0 if e["b"] <= e["a"] else min(1.0, max(0.0, (t - e["a"]) / (e["b"] - e["a"])))
        down = min(1.0, max(0.0, (e["d"] - t) / max(1e-9, e["d"] - e["c"])))
        z += (e["z"] - 1) * up * down
    return p["base"] * z

