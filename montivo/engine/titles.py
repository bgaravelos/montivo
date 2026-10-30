"""Title hooks (rail panels T3): three short lines for the title card that Muse Spark writes from what the cut says,
in one muse-spark-1.3 call (about $0.009, text only, the run's ledger). The editor shows them as choices; picking one
is an ordinary text edit (new title words, one Muse Image card at the render, $0.01).
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_CHARS = 40  # the card sets one to three lines in the middle third (image.TITLE_PROMPT): short reads on a phone

PROMPT = """You write the title card of a short vertical video: the words on screen for its first second, before the
speaker starts. Write three different title lines for this cut.

The brief: "{brief}"
What the speaker says in the cut, in order:
{said}
The current title card: "{current}"

Rules: each line at most 6 words and {max_chars} characters; say only what the cut says (no claim, number or name
that is not in it); three different angles (a question, a bold claim the cut backs up, a payoff tease, ...); no
hashtags, no emoji, no quotes around the line, sentence case. None of them may be the current title.
For each, one short "why" (what makes a viewer stay)."""


def schema() -> dict:
    item = {"type": "object", "additionalProperties": False, "required": ["text", "why"],
            "properties": {"text": {"type": "string"}, "why": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["titles"],
            "properties": {"titles": {"type": "array", "items": item}}}


def said(edl: dict, limit: int = 2400) -> str:
    """The cut's words in order, one clip a line (the EDL's span words), cut at `limit` characters."""
    out, n = [], 0
    for s in edl.get("spans") or []:
        w = " ".join(str(s.get("words") or "").split())
        if not w:
            continue
        out.append(f"- {w}")
        n += len(w)
        if n > limit:
            break
    return "\n".join(out) or "- (no words)"


def settle(raw: list[dict], current: str | None) -> list[dict]:
    """Spark's lines checked: whitespace and wrapping quotes gone, too long or empty ones dropped, no repeat of the
    current title or of each other; at most three."""
    out, seen = [], {(current or "").strip().lower()}
    for t in raw:
        text = " ".join(str(t.get("text") or "").split()).strip("\"'“”‘’ ")
        if not text or len(text) > MAX_CHARS + 10 or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append({"text": text, "why": " ".join(str(t.get("why") or "").split())[:160]})
    return out[:3]


def suggest(run_dir: Path, edl: dict, brief: str, ledger, log=print, model: str | None = None) -> list[dict]:
    """One Spark call: up to three title lines ({"text", "why"}) for the version's title card."""
    from .spark import Spark  # the API client only when titles are asked for
    prompt = PROMPT.format(brief=brief.replace('"', "'")[:600], said=said(edl), max_chars=MAX_CHARS,
                           current=(edl.get("title_card_text") or "none").replace('"', "'"))
    kw = {"model": model} if model else {}
    call = Spark(run_dir, ledger, log=log, **kw).call("titles", prompt, schema(), max_output_tokens=4000,
                                                       reasoning_effort="low", stage="spark.titles",
                                                       schema_name="title_hooks")
    titles = settle(list(call.parsed.get("titles") or []), edl.get("title_card_text"))
    if not titles:
        raise ValueError(f"no usable title in Spark's answer: {json.dumps(call.parsed)[:200]}")
    return titles
