"""Monti: the help chatbot. A question about the app -> a short answer, steps, and the place on screen to show.

    python -m montivo.monti "where do I add music?" [--page editor] [--panel captions] [--no-model]

One muse-spark-1.3 call (strict json_schema, reasoning effort low, the same Spark client and retry as Ask Montivo)
reads montivo/monti_guide.md, the closed list of Show me targets (TARGETS: each is a `data-monti` attribute on a real
element of the UI) and what is on screen (page, version, panel, Live), and answers {reply, steps, target, open_panel,
handoff_to_ask, suggestions, section}. The guide goes first in the prompt, the same text every time (the Meta API cached
none of it on 30/10: about $0.013 a question). The model may only pick a target from TARGETS that exists on the page the person is
on; anything else is dropped. An edit request ("add lofi and bigger captions") is not Monti's: it comes back as
`handoff_to_ask`, the message the editor puts in Ask Montivo's composer without sending it.

Monti calls are not part of a run: they go to `<root>/monti/ledger.json` (hosted.Budget counts it with the run
ledgers) and the request / response JSON to `<root>/monti/spark/`. Without MODEL_API_KEY, past the hosted budget, or
with --no-model, `fallback` matches the question's words against the guide's `words` lists for $0.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .config import SPARK_MODEL

GUIDE_PATH = Path(__file__).with_name("monti_guide.md")
COST_USD = 0.014  # the hosted budget check: about 9K tokens in (the whole guide) and 400-700 out, $0.013-0.014 (30/10)
PAGES = ("editor", "home", "export")
PANELS = ["assistant", "media", "captions", "style", "text", "music", "speakers", "versions"]
PANEL_KEY = {p: str(i + 1) for i, p in enumerate(PANELS)}

# The Show me targets: id -> (where it lives, what it is, a key that opens it, what must be open first). `where` is
# the page whose DOM has it ("editor" also covers the Export page, which opens over the editor); `needs` is a rail
# panel the editor opens before pointing, or "export" (the Export page). web/src carries the same ids as data-monti.
@dataclass(frozen=True)
class Target:
    where: str
    label: str
    key: str = ""
    needs: str = ""


TARGETS: dict[str, Target] = {
    **{f"rail.{p}": Target("editor", f"{p.capitalize()} panel", PANEL_KEY[p]) for p in PANELS},
    "top.home": Target("editor", "Back to all projects"),
    "top.undo": Target("editor", "Undo / redo", "⌘Z"),
    "top.score": Target("editor", "Score chip (best blind review)"),
    "top.loop": Target("editor", "Loop (every version side by side)"),
    "top.more": Target("editor", "More menu (replay, cost and time, shortcuts)"),
    "top.share": Target("editor", "Share"),
    "top.export": Target("editor", "Export"),
    "top.draft": Target("editor", "Discard / Make Version (the Live draft)"),
    "viewer.mode": Target("editor", "Live / Rendered switch", "V"),
    "viewer.player": Target("editor", "Viewer"),
    "viewer.transport": Target("editor", "Transport", "Space"),
    "viewer.safe": Target("editor", "Safe-area guides", "G"),
    "insp": Target("editor", "Inspector (selection or review)"),
    "chat.composer": Target("editor", "Ask Montivo composer", "1", "assistant"),
    "tl.tabs": Target("editor", "Timeline / Transcript tabs"),
    "tl.tools": Target("editor", "Clip tools (split, loop, ask, delete, pauses)", "S"),
    "tl.snap": Target("editor", "Snapping", "N"),
    "tl.zoom": Target("editor", "Timeline zoom", "⌘+"),
    "tl.broll": Target("editor", "B-roll lane"),
    "tl.text": Target("editor", "Text lane (callouts)"),
    "tl.captions": Target("editor", "Captions lane"),
    "tl.video": Target("editor", "Video lane (clips)"),
    "tl.voice": Target("editor", "Voice fader"),
    "tl.music": Target("editor", "Music lane and fader"),
    "export.cover": Target("editor", "Cover maker", "", "export"),
    "export.dest": Target("editor", "Destination (9:16, 4:5, 1:1, 720)", "", "export"),
    "export.include": Target("editor", "What goes in the bundle", "", "export"),
    "export.post": Target("editor", "Post text", "", "export"),
    "export.checks": Target("editor", "Pre-flight checks", "", "export"),
    "home.new": Target("home", "New project", "N"),
    "home.grid": Target("home", "Projects"),
    "home.tabs": Target("home", "Samples / Yours"),
    "home.budget": Target("home", "Demo budget left"),
    "home.hero": Target("home", "Best cut and its loop"),
    "home.how": Target("home", "How it works"),
}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "steps", "target", "open_panel", "handoff_to_ask", "suggestions", "section"],
    "properties": {
        "reply": {"type": "string", "description": "the answer's first line, one or two short sentences"},
        "steps": {"type": "array", "items": {"type": "string"}, "description": "0 to 5 numbered steps, short"},
        "target": {"type": "string", "enum": [*TARGETS, "none"]},
        "open_panel": {"type": "string", "enum": [*PANELS, "export", "none"]},
        "handoff_to_ask": {"type": "string", "description": "an edit request: the message for Ask Montivo, else ''"},
        "suggestions": {"type": "array", "items": {"type": "string"}, "description": "2 or 3 follow-up questions"},
        "section": {"type": "string", "description": "the guide section the answer comes from, else ''"},
    },
}

PROMPT = """You are Monti, the help gecko inside Montivo, an agent-first video editor. You answer questions about the app: where something is and how to do something, from THE GUIDE below and nothing else.

THE GUIDE
{guide}

SHOW ME TARGETS (target: what it is · key · page it lives on)
{targets}

Rules:
- Answer only from the guide. If the guide does not cover it, say so in one line, target none, and suggest asking Ask Montivo or reading the README.
- reply: the first line of the answer, short and concrete, naming the UI place exactly as the guide writes it (e.g. Music, key 6). steps: 0 to 5 short steps from the guide, no numbering inside the text. Never invent a button, key, price or model.
- Write reply, steps and suggestions in the language of the question (Greek or English); keep the UI names in English as they appear on screen.
- target: the one place on screen the answer is about, from the list, and only one that lives on the page the person is on ({page}); else none. On the project list (home) an editor place does not exist: say to open a project first, target none.
- open_panel: the rail panel (or export) the answer is about, if any, else none.
- handoff_to_ask: when the message asks for a change to the cut itself (add music, make captions bigger, cut the intro, make it shorter) rather than how or where, it is not yours: reply "That's an edit: Ask Montivo does edits." (in the question's language), put the message for Ask Montivo here in the person's own words, target chat.composer, open_panel assistant. A question about how to do it yourself ("how do I add music?") is yours: handoff_to_ask ''.
- Plain sentences: no em dashes, no markdown.
- suggestions: 2 or 3 short follow-up questions a person here might ask next.
- section: the guide heading you answered from, else ''.

ON SCREEN NOW
{context}

EARLIER IN THIS CONVERSATION
{history}

THE QUESTION
"{message}"
"""


# ------------------------------------------------------------------ the guide

@dataclass
class Section:
    title: str
    lead: str
    steps: list[str]
    target: str
    panel: str
    words: list[str]
    body: str = field(repr=False, default="")


META = re.compile(r"<!--\s*monti:(.*?)-->")


def guide_text() -> str:
    return GUIDE_PATH.read_text()


def sections(text: str | None = None) -> list[Section]:
    """The guide's `## ` sections with their monti: line (target, panel, words), lead paragraph and numbered steps."""
    out = []
    for block in re.split(r"^## ", text or guide_text(), flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        meta = {"target": "none", "panel": "", "words": ""}
        if m := META.search(body):
            for k, v in re.findall(r"(\w+)=([^=]+?)(?=\s+\w+=|$)", m.group(1).strip()):
                meta[k] = v.strip()
            body = META.sub("", body, count=1)
        lines = [ln.strip() for ln in body.strip().splitlines()]
        lead = next((ln for ln in lines if ln and not re.match(r"\d+\. ", ln)), "")
        steps = [re.sub(r"^\d+\. ", "", ln) for ln in lines if re.match(r"\d+\. ", ln)]
        out.append(Section(title.strip(), lead, steps, meta["target"], meta["panel"],
                           [w.strip() for w in meta["words"].split(",") if w.strip()], body.strip()))
    return out


def fold(s: str) -> str:
    """Lower case without accents (Greek tonos too): "Μουσική" and "μουσικη" match."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


ASKING = re.compile(r"\b(how|where|what|why|which|when|can i|is there|does|do i|explain|show me|help)\b|\?|"
                    r"πως|που |πού|τι |γιατι|ποιο|ποια|μπορω|εξηγησ|δειξε μου|βοηθ", re.I)
EDITING = re.compile(r"^\s*(please\s+)?(add|put|make|remove|delete|cut|change|turn|set|use|drop|move|trim|shorten|"
                     r"replace|give|apply|bring)\b|^\s*(βαλε|κανε|βγαλε|αλλαξε|κοψε|σβησε|προσθεσε|μικρυνε|"
                     r"μεγαλωσε|γυρνα|φερε|δωσε)", re.I)


def is_edit(message: str) -> bool:
    """An instruction to change the cut, not a question about the app (the fallback's hand-off rule)."""
    m = fold(message)
    return bool(EDITING.search(m)) and not ASKING.search(m)


def fallback(message: str, page: str = "editor") -> dict:
    """$0: the guide section whose keywords the question shares most, or the hand-off for an edit request."""
    if is_edit(message):
        greek = bool(re.search(r"[α-ω]", fold(message)))
        return answer(reply="Αυτό είναι edit: το Ask Montivo κάνει τα edits." if greek else
                      "That's an edit: Ask Montivo does edits.", target="chat.composer", open_panel="assistant",
                      handoff=message.strip(), page=page, section="Ask Montivo (the chat that edits)")
    q = fold(message)
    best, score = None, 0.0
    for s in sections():
        sc = 0.0
        for f in {fold(w) for w in s.words + [s.title]}:
            if len(f) > 2 and re.search(r"(?<!\w)" + re.escape(f), q):
                sc += 1 + len(f) / 10 + (1 if re.search(r"\d", f) else 0)  # longer and numbered (4:5) match better
        if sc > score:
            best, score = s, sc
    if best is None:
        greek = bool(re.search(r"[α-ω]", q))
        return answer(reply=("Δεν το βρίσκω στον οδηγό. Ρώτα με «πού είναι ...» ή «πώς κάνω ...»." if greek else
                             "I can't find that in the guide. Ask me \"where is ...\" or \"how do I ...\"."),
                      page=page, suggestions=DEFAULT_SUGGESTIONS.get(page, []))
    t = TARGETS.get(best.target)
    if page == "home" and t is not None and t.where == "editor":  # an editor place: there is none on the project list
        greek = bool(re.search(r"[α-ω]", q))
        return answer(reply=("Άνοιξε πρώτα ένα project. " if greek else "Open a project first. ") + best.lead,
                      steps=best.steps, page=page, section=best.title, suggestions=DEFAULT_SUGGESTIONS["home"])
    return answer(reply=best.lead, steps=best.steps, target=best.target, open_panel=best.panel or "none",
                  page=page, section=best.title, suggestions=DEFAULT_SUGGESTIONS.get(page, []))


DEFAULT_SUGGESTIONS = {
    "editor": ["Where do I add music?", "How do I fix a caption?", "What does the gate mean?"],
    "home": ["How do I start a new project?", "What does the score mean?", "Which stage runs on Spark Open?"],
    "export": ["How do I make a 4:5 version?", "How do I make a cover?", "What is in the bundle?"],
}


def answer(reply: str, steps: list[str] | None = None, target: str = "none", open_panel: str = "none",
           handoff: str = "", suggestions: list[str] | None = None, section: str = "", page: str = "editor") -> dict:
    """The route's answer, checked: a target that does not live on this page goes (the editor's Export page counts
    as the editor), a panel outside the list goes, the target's own panel is opened with it."""
    page = page if page in PAGES else "editor"
    where = "editor" if page == "export" else page
    t = TARGETS.get(target)
    if t is None or t.where != where:
        target, t = "none", None
    if open_panel not in (*PANELS, "export"):
        open_panel = "none"
    if t and t.needs and open_panel == "none":
        open_panel = t.needs
    if where == "home":
        open_panel = "none"
    reply, steps = plain(reply), [plain(s) for s in steps or []]
    return {"reply": reply, "steps": [s for s in steps if s][:6],
            "target": None if target == "none" else target,
            "target_label": t.label if t else None, "target_key": t.key if t else None,
            "needs": t.needs if t else None,
            "open_panel": None if open_panel == "none" else open_panel,
            "handoff": handoff.strip() or None,
            "suggestions": [s.strip() for s in (suggestions if suggestions is not None else []) if s.strip()][:3],
            "section": section or None}


def plain(s: str) -> str:
    """No em dashes in what Monti says (the house style): " — " becomes ": " the first time, ", " after."""
    s = re.sub(r"\s*—\s*", ": ", s.strip(), count=1)
    return re.sub(r"\s*—\s*", ", ", s).replace("**", "")


# ------------------------------------------------------------------ the model

def targets_text() -> str:
    return "\n".join(f"{k}: {t.label}{f' · key {t.key}' if t.key else ''} · {t.where}"
                     f"{f' (opens {t.needs} first)' if t.needs else ''}" for k, t in TARGETS.items())


def context_text(ctx: dict) -> str:
    page = ctx.get("page") if ctx.get("page") in PAGES else "editor"
    bits = [f"page: {page}" + (" (the Export page, over the editor)" if page == "export" else "")]
    if ctx.get("version"):
        bits.append(f"version: {ctx['version']}")
    if ctx.get("panel"):
        bits.append(f"open panel: {ctx['panel']}")
    if ctx.get("live") is not None and page != "home":
        bits.append("viewer: Live" if ctx.get("live") else "viewer: Rendered")
    if ctx.get("draft"):
        bits.append("a Live draft is open (Make Version renders it)")
    if ctx.get("selection"):
        bits.append(f"selected: {str(ctx['selection'])[:80]}")
    if ctx.get("hosted"):
        bits.append("the hosted demo (a shared model budget)")
    return "\n".join(bits)


def history_text(history: list[dict] | None) -> str:
    rows = [f"Q: {str(h.get('q', ''))[:200]}\nA: {str(h.get('a', ''))[:300]}" for h in (history or [])[-2:]]
    return "\n".join(rows) or "(none)"


LOCK = threading.Lock()  # one Monti call at a time: they share one ledger file


def ask(root: Path, message: str, ctx: dict | None = None, history: list[dict] | None = None) -> dict:
    """One Spark 1.3 call over the guide; the answer is checked like the fallback's. Raises on a failed call."""
    from .engine.spark import Spark
    from .ledger import Ledger
    from .run import RunLog
    ctx = ctx or {}
    page = ctx.get("page") if ctx.get("page") in PAGES else "editor"
    prompt = PROMPT.format(guide=guide_text(), targets=targets_text(), page=page, context=context_text(ctx),
                           history=history_text(history), message=message.replace('"', "'")[:600])
    folder = root / "monti"
    with LOCK:
        ledger = Ledger.resume(folder / "ledger.json")
        call = Spark(folder, ledger, model=SPARK_MODEL, log=RunLog(folder / "monti.log")).call(
            "monti", prompt, SCHEMA, max_output_tokens=3000, reasoning_effort="low", stage="spark.monti",
            schema_name="monti")
    p = dict(call.parsed)
    out = answer(reply=p.get("reply", ""), steps=p.get("steps"), target=p.get("target", "none"),
                 open_panel=p.get("open_panel", "none"), handoff=p.get("handoff_to_ask", ""),
                 suggestions=p.get("suggestions"), section=p.get("section", ""), page=page)
    if out["handoff"] and not out["target"] and page != "home":
        out.update(target="chat.composer", target_label=TARGETS["chat.composer"].label, open_panel="assistant",
                   needs="assistant", target_key="1")
    out.update(source="spark", model=SPARK_MODEL, cost_usd=call.cost_usd, seconds=call.seconds)
    return out


def log_question(root: Path, who: str, message: str, out: dict) -> None:
    """What people ask Monti (<root>/monti/questions.jsonl): which answers the guide is missing."""
    try:
        (root / "monti").mkdir(parents=True, exist_ok=True)
        with (root / "monti" / "questions.jsonl").open("a") as fh:
            fh.write(json.dumps({"t": round(time.time()), "who": who, "q": message[:600], "source": out.get("source"),
                                 "target": out.get("target"), "handoff": bool(out.get("handoff")),
                                 "section": out.get("section"), "cost_usd": out.get("cost_usd", 0)},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m montivo.monti", description=__doc__.split("\n\n")[0])
    ap.add_argument("message")
    ap.add_argument("--page", default="editor", choices=PAGES)
    ap.add_argument("--panel", default=None)
    ap.add_argument("--root", default=".")
    ap.add_argument("--no-model", action="store_true")
    a = ap.parse_args(argv)
    ctx = {"page": a.page, "panel": a.panel}
    out = (fallback(a.message, a.page) | {"source": "guide", "cost_usd": 0.0}) if a.no_model \
        else ask(Path(a.root), a.message, ctx)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
