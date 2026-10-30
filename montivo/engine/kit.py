"""Brand kits (rail panels S3): a person's saved look with the music and the title card that go with it, put on a
version in one edit (`montivo.human` kit op) or on a new project's first cut (`montivo run --kit`).

A kit is {"look": {...}, "music": {track, level_db, duck_db} | "off", "title_card": true | false}; every key but the
look may be left out, and what is left out stays as the version has it. The look's callout colours ride in the look
(callout_bg / callout_fg). The line breaks stay the version's (graphics.restyle), the speech gain of a mix stays the
version's (music voice_db), and the title card keeps its words: a kit turns the card on or off, it does not write it.
"""
from __future__ import annotations

from .graphics import normalize_look, restyle
from .music import normalize_music


def normalize_kit(d: dict | None) -> dict:
    """A kit checked: the look normalised (a full look, so a saved kit draws the same on every project), the music
    normalised or "off", the title card a bool. Raises ValueError for anything else or a kit with nothing in it."""
    if not isinstance(d, dict):
        raise ValueError("a kit is an object {look, music, title_card}")
    if extra := set(d) - {"look", "music", "title_card"}:
        raise ValueError(f"kit: unknown keys {', '.join(sorted(extra))}")
    out: dict = {}
    if d.get("look") is not None:
        if not isinstance(d["look"], dict):
            raise ValueError("kit look: an object")
        out["look"] = normalize_look(d["look"]).to_json()
    if "music" in d and d["music"] is not None:
        m = d["music"]
        if m == "off":
            out["music"] = "off"
        else:
            if not isinstance(m, dict):
                raise ValueError('kit music: {track, level_db, duck_db} or "off"')
            m = normalize_music({k: m[k] for k in ("track", "level_db", "duck_db") if k in m})
            out["music"] = m
    if d.get("title_card") is not None:
        if not isinstance(d["title_card"], bool):
            raise ValueError("kit title_card: true or false")
        out["title_card"] = d["title_card"]
    if not out:
        raise ValueError("kit: give a look, music or the title card")
    return out


def with_kit(edl: dict, kit: dict) -> tuple[dict, list[dict]]:
    """`edl` with the kit's look, music and title card set in place (the caller passes its own copy), and what changed
    as patch entries (op look / music / music_off / title_on / title_off). A title card turned on needs the EDL's
    title words; without them it stays off and says so in no entry."""
    out: list[dict] = []
    k = normalize_kit(kit)
    if "look" in k:
        now = restyle(edl.get("look"), k["look"]).to_json()
        if now != normalize_look(edl.get("look")).to_json():
            edl["look"] = now
            out.append({"op": "look", "target": "captions", "issue": -1, "severity": "human", "args": {"look": now},
                        "note": f"look {now['preset']}, font {now['font']}"})
    if "music" in k:
        try:
            was = normalize_music(edl.get("music"))
        except ValueError:
            was = edl.get("music") or None
        if k["music"] == "off":
            if was:
                edl.pop("music", None)
                out.append({"op": "music_off", "target": "audio", "issue": -1, "severity": "human",
                            "note": f"music {was['track']} off"})
        else:
            now = {**k["music"], **({"voice_db": was["voice_db"]} if was and was.get("voice_db") else {})}
            if now != was:
                edl["music"] = now
                out.append({"op": "music", "target": "audio", "issue": -1, "severity": "human", "args": {"music": now},
                            "note": f"music {now['track']} at {now['level_db']:g} LUFS, ducked {now['duck_db']:g} dB"})
    if "title_card" in k and k["title_card"] != (edl.get("title_card", True) is not False):
        if not k["title_card"] or edl.get("title_card_text"):
            edl["title_card"] = k["title_card"]
            out.append({"op": "title_on" if k["title_card"] else "title_off", "target": "title", "issue": -1,
                        "severity": "human", "note": "title card on" if k["title_card"] else "title card off"})
    return edl, out
