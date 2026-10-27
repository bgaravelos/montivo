"""Speaker -> head: which SAM head the 9:16 crop follows while a given Voice speaker talks.

pick_track alone follows the largest, most centred head. With one person in frame (Billy's clip, the NASA interview
with the interviewer off camera, run 013) that is the speaker; with two or more people in one shot (an interview
two-shot, a crew message) it is often the listener. Voice labels every span's speaker, and watch's speaker_map says
where each label sits on camera ("left", "centre", "right", "off_camera", "varies"), so a segment of an on-camera
speaker at a fixed side prefers the head nearest that side. A human pin in the EDL, `reframe_speaker_ids`
{"A": "3"} (Voice label -> head track id), wins over the side while that id is in frame. Off camera, "varies" or a
label watch did not map: the default pick, as before.
"""
from __future__ import annotations

from dataclasses import dataclass

POSITION_X = {"left": 0.25, "centre": 0.5, "center": 0.5, "right": 0.75}  # share of the frame width


@dataclass
class Follow:
    speaker: str | None  # the Voice label of the segment, None when its spans carry none
    prefer_x: float | None  # the speaker's side of the frame (0..1), None = the centre
    prefer_id: str | None  # a pinned head track id
    by: str  # "pin", "position <side>" or "default"


def label(speaker: str | None) -> str:
    """Voice labels are "A", "B", ...; watch sometimes answers "speaker A" or "Speaker a"."""
    s = (speaker or "").strip()
    if s.lower().startswith("speaker"):
        s = s[len("speaker"):].strip(" :")
    return s.upper()


def segment_speaker(spans: list[dict], span_ids: list[str]) -> str | None:
    """The speaker with the most seconds among a segment's spans (contiguous spans share a turn, so nearly always one)."""
    secs: dict[str, float] = {}
    for s in spans:
        if s["id"] in span_ids and label(s.get("speaker")) not in ("", "?"):
            secs[label(s["speaker"])] = secs.get(label(s["speaker"]), 0.0) + float(s["t_out"]) - float(s["t_in"])
    return max(secs, key=lambda k: secs[k]) if secs else None


def follow_for(edl: dict, span_ids: list[str]) -> Follow:
    """Whom the crop should follow over a segment made of span_ids (see the module docstring)."""
    who = segment_speaker(edl.get("spans") or [], span_ids)
    if who is None:
        return Follow(None, None, None, "default")
    pins = {label(k): str(v) for k, v in (edl.get("reframe_speaker_ids") or {}).items()}
    sides = {label(m.get("speaker")): m for m in edl.get("speaker_map") or []}
    m = sides.get(who) or {}
    x = POSITION_X.get(str(m.get("position") or "").lower()) if m.get("on_camera", True) else None
    if who in pins:
        return Follow(who, x, pins[who], "pin")
    if x is not None and x != 0.5:
        return Follow(who, x, None, f"position {m['position']}")
    return Follow(who, None, None, "default")
