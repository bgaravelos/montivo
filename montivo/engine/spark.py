"""Spark: the Responses client for muse-spark-1.3 with strict JSON schemas, plus the watch / plan / review helpers.

Rules measured on 26/10/2026: always 1.3 (1.2 degenerates with a strict schema and video), always hand it the Voice
transcript as text (1.3 fabricates quotes when asked to transcribe), one uploaded proxy reused by every call.
Every call writes <name>_request.json and <name>_response.json and a ledger entry with tokens and dollars.
A Muse Spark Open model id (config.OPEN_MODEL, Baseten) sends the same prompt and schema as an OpenAI-compatible chat
completion instead; the response is read back in the Responses shape. It has no Files API: a video goes inline as a
`video_url` data URL. Measured 27/10 on Baseten: it reads sampled frames (about 1 per second, the review clock and the
captions read right, about 185 prompt tokens a frame at 360x640) but hears nothing (input_audio and the video's
soundtrack give 0 audio tokens and "I cannot hear audio"), so a review on it needs the heard-words fact.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import SPARK_MODEL, is_open_model, spark_cost
from ..ledger import Entry, Ledger, Timer
from . import schemas
from .api import chat_as_response, chat_create, dump_json, output_text, responses_create

# Reasoning effort of the two editorial calls that used to run at the model's default (30/10 probe, clones of 012 /
# 015 / 016 and 014): plan low 11-13 s / $0.012-0.016 vs the default 26-40 s / $0.03-0.05, the same kept words on
# 016 and 015; watch low 10.8 s vs 32.5 s on 016, 18.7 s vs 50.3 s on 014, the same shots and moments.
PLAN_EFFORT = "low"
WATCH_EFFORT = "low"
PARSE_RETRIES = 1  # an incomplete or unparsable answer is asked once more
PARSE_RETRY_TOKENS = 1.5


@dataclass
class SparkCall:
    name: str
    parsed: Any
    status: str
    usage: dict
    cost_usd: float
    seconds: float
    response_id: str


class Spark:
    def __init__(self, run_dir: Path, ledger: Ledger, model: str = SPARK_MODEL, log=print):
        self.dir = run_dir / "spark"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.model = model
        self.log = log

    # ----------------------------------------------------------------- generic call

    def call(self, name: str, prompt: str, schema: dict, file_id: str | None = None, max_output_tokens: int = 12000,
             reasoning_effort: str | None = None, stage: str | None = None, schema_name: str | None = None,
             video: Path | None = None, images: list[Path] | None = None,
             files: list[tuple[str, str]] | None = None) -> SparkCall:
        """`name` names the request/response files; `stage` the ledger entry (default spark.<name>). `file_id` is an
        uploaded file (Meta Files API, Spark 1.3); `files` several uploaded files, each (label, file id), the label
        sent as a text part right before its file (the pairwise judge's two cuts); `video` a local mp4 sent inline
        (Muse Spark Open only); `images` local JPEG / PNG stills sent inline as data URLs (either model)."""
        urls = [_data_url(p) for p in images or []]
        open_model = is_open_model(self.model)
        if open_model:
            if file_id or files:
                raise SystemExit(f"spark {name}: {self.model} has no Files API input; "
                                 "drop --plan-with-video or plan with muse-spark-1.3")
            content: list[dict] | str = prompt
            if video is not None:
                url = "data:video/mp4;base64," + base64.b64encode(video.read_bytes()).decode()
                content = [{"type": "text", "text": prompt}, {"type": "video_url", "video_url": {"url": url}}]
            if urls:
                content = ([{"type": "text", "text": prompt}] if isinstance(content, str) else content) + \
                    [{"type": "image_url", "image_url": {"url": u}} for u in urls]
            body: dict = {"model": self.model, "max_tokens": max_output_tokens,
                          "messages": [{"role": "user", "content": content}],
                          "response_format": {"type": "json_schema", "json_schema": {
                              "name": schema_name or name, "strict": True, "schema": schema}}}
            # a reasoning model: at the default effort a 200-token probe spent every token on reasoning and returned
            # no content (27/10); effort low answered in 0.8 s
            body["reasoning_effort"] = reasoning_effort or "low"
        else:
            if video is not None:
                raise SystemExit(f"spark {name}: {self.model} takes video as an uploaded file_id, not inline")
            content = [{"type": "input_text", "text": prompt}]
            if file_id:
                content.append({"type": "input_file", "file_id": file_id})
            for label, fid in files or []:
                content += [{"type": "input_text", "text": label}, {"type": "input_file", "file_id": fid}]
            content += [{"type": "input_image", "image_url": u} for u in urls]
            body = {"model": self.model, "max_output_tokens": max_output_tokens,
                    "input": [{"type": "message", "role": "user", "content": content}],
                    "text": {"format": {"type": "json_schema", "name": schema_name or name, "strict": True, "schema": schema}}}
            if reasoning_effort:
                body["reasoning"] = {"effort": reasoning_effort}
        dump_json(self.dir / f"{name}_request.json", _without_media(body))
        self.log(f"spark: {name} -> {self.model}{' + video' if file_id or video else ''}"
                 f"{f' + {len(files)} videos' if files else ''}"
                 f"{f' + {len(urls)} image(s)' if urls else ''} ...")
        total_cost, total_s = 0.0, 0.0
        for attempt in range(PARSE_RETRIES + 1):
            with Timer() as t:
                if open_model:
                    raw = chat_create(body)
                    resp = chat_as_response(raw)
                else:
                    resp = responses_create(body)
            dump_json(self.dir / f"{name}_response.json", raw if open_model else resp)
            usage = resp.get("usage") or {}
            cost = spark_cost(usage, self.model)
            total_cost, total_s = total_cost + cost, total_s + t.seconds
            status = resp.get("status", "?")
            text = output_text(resp)
            parsed, err = _parse_json(text)
            entry_status = status if parsed is not None else f"{status}/parse_error"
            self.ledger.add(Entry(stage=stage or f"spark.{name}", model=self.model, seconds=t.seconds, cost_usd=round(cost, 4),
                                  tokens_in=usage.get("input_tokens") or 0, tokens_out=usage.get("output_tokens") or 0,
                                  tokens_cached=(usage.get("input_tokens_details") or {}).get("cached_tokens") or 0,
                                  status=entry_status, note=f"response {resp.get('id', '?')}"))
            self.log(f"spark: {name}: {status}, {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out, "
                     f"${cost:.4f}, {t.seconds}s")
            if parsed is not None or attempt >= PARSE_RETRIES:
                break
            # one more try: an answer cut at the token limit gets PARSE_RETRY_TOKENS x the room (look.suggest came
            # back incomplete at 1500 tokens), an unparsable one the same call again
            shutil.copy2(self.dir / f"{name}_response.json", self.dir / f"{name}_response_try{attempt + 1}.json")
            key = "max_tokens" if open_model else "max_output_tokens"
            if status == "incomplete":
                body[key] = int(body[key] * PARSE_RETRY_TOKENS)
            self.log(f"spark: {name}: no JSON ({err}), asking once more"
                     + (f" with {body[key]} output tokens" if status == "incomplete" else ""))
        if parsed is None:
            raise RuntimeError(f"spark {name}: could not parse JSON ({err}); status={status}; head={text[:300]!r}")
        return SparkCall(name=name, parsed=parsed, status=status, usage=usage, cost_usd=round(total_cost, 4),
                         seconds=round(total_s, 2), response_id=resp.get("id", ""))

    # ----------------------------------------------------------------- the three editorial calls

    def watch(self, file_id: str, transcript_text: str, brief: str, duration_s: float,
              reasoning_effort: str | None = None) -> SparkCall:
        prompt = (
            "You are the assistant editor logging footage for a short-form video edit.\n"
            f"Brief from the producer: {brief}\n\n"
            f"You have the full video with its audio ({duration_s:.0f} s). Below is the diarized transcript produced by a "
            "speech model with exact turn timestamps; it is the authority for who said what and when. Do not transcribe "
            "speech yourself and do not quote words that are not in it.\n"
            "1) Log every visual shot with your best estimate of start and end seconds: shot type, what is on screen, any "
            "on-screen text, how many people are visible, and short noun phrases a segmentation model could track. "
            "A static card or still image is ONE shot however long it stays; only a real change of picture starts a new shot.\n"
            "2) Map each transcript speaker label to who they are and whether and where they appear on camera.\n"
            "3) List the concepts worth tracking for on-screen callouts (objects, props, logos; not 'person').\n"
            "4) List b-roll candidates: visual moments without speech that could cover a cut.\n"
            "5) List moments where the on-camera speaker moves out of centre or handles a prop, with seconds "
            "(leans, stands up, picks up or shows an object, points at something).\n\n"
            f"TRANSCRIPT:\n{transcript_text}")
        return self.call("watch", prompt, schemas.WATCH, file_id=file_id, reasoning_effort=reasoning_effort or WATCH_EFFORT)

    def plan(self, brief: str, transcript_text: str, watch: dict, target_s: float, file_id: str | None = None,
             reasoning_effort: str | None = None, words_per_s: float = 2.5, repair: str | None = None,
             name: str = "plan") -> SparkCall:
        """`words_per_s` is the speech rate measured by Voice on this clip (VoiceResult.speech_rate); 2.5 when unknown.
        `repair` (A4): what the deterministic plan check found wrong in an earlier paper edit, for one more try."""
        wps = words_per_s if words_per_s and words_per_s > 0 else 2.5
        shot_log = "\n".join(
            f"- {s['start_s']:.1f}-{s['end_s']:.1f}s {s['shot_type']}: {s['visual']}"
            + (f" | text: {s['on_screen_text']}" if s.get("on_screen_text") else "")
            for s in watch.get("visual_shots", []))
        moments = "\n".join(
            f"- {m['start_s']:.1f}-{m['end_s']:.1f}s: {m['what']}"
            for m in watch.get("movement_moments", []))
        moments_block = (
            "\nMoments where the speaker moves out of centre or handles a prop (visual beats worth keeping when the brief "
            f"allows; the camera reframe follows them):\n{moments}\n" if moments else "")
        speakers = "\n".join(f"- speaker {m['speaker']}: {m['who']} ({'on camera, ' + m['position'] if m['on_camera'] else 'off camera'})"
                             for m in watch.get("speaker_map", []))
        prompt = (
            "You are the editor building the paper edit for a short-form vertical video.\n"
            f"Brief: {brief}\n"
            f"Target runtime: aim for {target_s:.0f} to {target_s * 1.2:.0f} seconds of kept speech, never below {target_s * 0.8:.0f}. "
            "When the brief gives a maximum length, that maximum is a hard limit: the kept speech never runs over it, "
            "whatever the target says. "
            f"This speaker runs at about {wps:.1f} words per second (measured on this clip), so that is roughly "
            f"{target_s * wps:.0f} to {target_s * 1.2 * wps:.0f} words in total: "
            "count the words you keep and add spans until you are inside the range. Several spans may come from one long turn, "
            "each quoted as its own contiguous block in the order spoken.\n\n"
            f"Footage summary: {watch.get('summary', '')}\n\nShot log (visual, estimated times):\n{shot_log}\n{moments_block}\n"
            f"Speakers:\n{speakers}\n\n"
            "Rules for the paper edit:\n"
            "- Ordered spans to keep, strongest line first (the hook must land inside the first 1.5 seconds of speech).\n"
            "- Each span lies inside ONE transcript turn and quotes the exact contiguous words to keep, copied character for "
            "character from that turn's transcript, including repeated words and disfluencies. We cut on word boundaries "
            "taken from the audio, so a paraphrase or a cleaned-up quote cannot be used.\n"
            "- Every span starts where a sentence starts, or on a clause a viewer can follow with nothing before it, and "
            "ends where a sentence ends. Never open a span on words that lean on what came before (\"about a father who "
            "...\", \"it's been working with ...\", \"we're doing with the ...\") and never end one mid-thought (\"and was "
            "hoping\"): when the passage you want starts or ends mid-sentence, take the whole sentence or leave it out. "
            "This holds for the hook above all.\n"
            "- strength rates each span 1 to 5 (5 = the edit cannot do without it, 1 = the first span to cut when the "
            "edit runs long); the editor drops the weakest body span when the cut runs over.\n"
            "- Never keep greetings, audio checks or small talk "
            "('can you hear me', 'how are you'). Keep an interviewer question only when it is short and the answer "
            "needs it; the speaker's own lines fill the runtime first.\n"
            "- role says where the span sits in the edit: 'hook' for the first span only (the opening line); 'payoff' "
            "for the last span only, the line the video ends on (the ending the brief names, when it names one); 'cta' "
            "only for a closing call to action after the payoff; 'body' for every span in between. So an edit has one "
            "hook, one payoff and everything else body.\n"
            "- Give a 2 to 4 word callout for a span only when a concrete on-screen object or number deserves it, else empty.\n"
            "- If the brief asks for a line, a moment or a detail that is not in the transcript or the footage at all (a line "
            "never said, a take that does not exist), list it in brief_not_in_footage in the brief's own words and plan the "
            "best edit without it. Never stand in a different line for it. The transcript is machine-made and mishears "
            "names and words (\"Soundtracks me\" for \"SAM tracks me\", \"meta meta\" for \"four Meta\"): a line that is there "
            "in a misheard form is not missing, and a line you keep in the paper edit is never listed. Leave the list empty "
            "when everything is there.\n"
            "- Write the brief as brief_checks, one item per concrete requirement in the brief's own words. kind: "
            "'opening' (the line the video must open on), 'ending' (the line it must end on), 'keep' (a line or passage "
            "that must stay), 'drop' (something to leave out), 'length' (the runtime the brief allows: min_s and max_s in "
            "seconds), 'other' (anything else). For opening, ending, keep and drop, quote = the transcript's exact words "
            "it names (for a passage 'from X through Y', its first through its last words as the transcript has them), "
            "empty when they are not in the transcript. Unused: quote empty, min_s -1, max_s -1. Your paper edit must "
            "meet every item it can; they are checked against it word by word.\n"
            "- List likely_mishearings: names and terms the transcript probably misheard in the lines you keep. heard = "
            "the words exactly as the transcript has them (a contiguous run inside one kept span), written = what the "
            "speaker most likely said, why = your evidence. Only from evidence you have: a name or term the brief "
            "spells out, on-screen text in the shot log, or context that leaves no doubt (\"Soundtracks me\" in a line "
            "about tracking = \"SAM tracks me\", \"meta meta models\" where the brief says four Meta models). Never an "
            "ordinary word, a filler or a style change. A person confirms each one; until then the captions stay as "
            "heard. Empty when you have no such evidence.\n"
            "- Propose a short title card text (max 6 words), a caption style note and a one-sentence reframe note.\n"
            f"- estimated_runtime_s = total kept words / {wps:.1f}.\n\n"
            + (f"A first paper edit for this brief was checked word by word and failed these checks. Write a new paper "
               f"edit that fixes every one of them and keeps what was right:\n{repair}\n\n" if repair else "")
            + f"TRANSCRIPT (authoritative):\n{transcript_text}")
        return self.call(name, prompt, schemas.PLAN, file_id=file_id, max_output_tokens=16000,
                         reasoning_effort=reasoning_effort or PLAN_EFFORT, stage="spark.plan", schema_name="plan")

    def review(self, file_id: str | None, brief: str, facts: str, duration_s: float, name: str = "review",
               reasoning_effort: str | None = "low", max_output_tokens: int = 16000, video: Path | None = None,
               hears: bool = True) -> SparkCall:
        """Blind review: the rendered cut (with a review clock burned in), the brief, the rubric and the measured facts.
        Never the EDL, the plan or the transcript: the reviewer judges what a viewer gets. Effort low: at the default
        effort a 53.5 s cut burned all 10,000 output tokens on reasoning and returned nothing (26/10). `hears` False
        (Muse Spark Open, frames only) tells the reviewer to judge the sound from the facts, the heard words above all."""
        prompt = (
            "You are the reviewer of an automated video editor. You get the finished vertical cut, nothing else: judge it "
            "as a strict editor would before it goes out.\n"
            f"Brief from the producer: {brief}\n\n"
            f"The cut is {duration_s:.1f} s long. A small clock in the top-left corner (white on black, e.g. 12.3) shows the "
            "time of this cut in seconds; it was added for this review only, it is not part of the edit, do not score it. "
            "Read every t_s and t_end_s from that clock.\n\n"
            "Rubric, each criterion 0 to 10 (10 = perfect):\n"
            f"{RUBRIC}\n\n"
            f"Measured facts about this cut (deterministic, trust them over your impression):\n{facts}\n\n"
            + ("Listen at every cut point and watch every caption. " if hears else
               "You see sampled frames of the cut but cannot hear its sound: judge everything audible (clipped words, "
               "dead air, repeats, filler, whether captions match the speech) from the measured facts, above all the "
               "words heard in the cut, and watch every caption. ")
            + "Be strict and concrete: list every problem a viewer would "
            "notice, each with its time, and one fix operation from this list with its params:\n"
            f"{FIX_OP_GUIDE}\n"
            "Unused params: edge none, seconds 0, target_t_s -1, text empty, direction none. "
            "overall is 0 to 100. pass = true only if overall >= 80 and there is no blocker.")
        return self.call(name, prompt, schemas.REVIEW, file_id=file_id, max_output_tokens=max_output_tokens,
                         reasoning_effort=reasoning_effort, stage="spark.review", schema_name="review", video=video)

    def compare(self, file_a: str, file_b: str, brief: str, facts_a: str, facts_b: str, duration_a: float,
                duration_b: float, name: str = "compare", reasoning_effort: str | None = "low",
                max_output_tokens: int = 8000) -> SparkCall:
        """Pairwise judge (C2): two review copies of cuts of the same footage for the same brief, Cut A the first
        video, Cut B the second, each with its measured facts; which one goes out, and per criterion which is better.
        Blind like the review: no EDL, no scores, no word of which cut is newer. The caller asks twice with the two
        swapped (position bias, arXiv 2406.07791) and reads the labels back. Two input_file parts in one request work
        on Spark 1.3 (30/10: 23.5K tokens in for two 39 s cuts, both seen)."""
        prompt = (
            "You are the senior editor of an automated video editor. You get two finished vertical cuts of the same "
            "footage, made for the same brief: Cut A is the first video, Cut B the second. Watch both whole, with their "
            "sound, and decide which one you would send out. They may differ anywhere: the opening line, the order, a "
            "cut point, the captions, the framing, the length. The order they come in says nothing about which is "
            "better or newer.\n"
            f"Brief from the producer: {brief}\n\n"
            f"Cut A is {duration_a:.1f} s long, Cut B {duration_b:.1f} s. A small clock in the top-left corner of each "
            "shows its own cut time; it was added for this comparison only, do not judge it.\n\n"
            f"Rubric (the same one each cut was scored on alone):\n{RUBRIC}\n\n"
            "Measured facts (deterministic, trust them over your impression).\n"
            f"Cut A:\n{facts_a}\n\nCut B:\n{facts_b}\n\n"
            "For every criterion say which cut does it better: A, B, or tie when a viewer would not notice a "
            "difference. winner is the cut you would publish; tie only when you truly cannot choose. why: one or two "
            "sentences naming the differences that decided it, with cut times.")
        return self.call(name, prompt, schemas.COMPARE, files=[("Cut A:", file_a), ("Cut B:", file_b)],
                         max_output_tokens=max_output_tokens, reasoning_effort=reasoning_effort, stage="spark.compare",
                         schema_name="compare")


RUBRIC = "\n".join(f"- {c}: {d}" for c, d in {
    "hook_in_1_5s": "the first 1.5 s grab the viewer: the strongest line starts within 1.5 s. Silence, filler "
                    "('okay, so', 'um') or a slow warm-up in the opening fails it.",
    "brief_covered": "everything the brief asks for is in the cut, in an order that makes sense; nothing it forbids is in.",
    "no_cut_mid_word": "at every cut the last word before it and the first word after it are complete: no clipped "
                       "syllable, no word cut off, no half breath.",
    "captions_legible": "captions are readable, on screen long enough, inside the safe area and never over the face.",
    "captions_match_speech": "caption words are the words spoken (names spelled right), in sync with the voice.",
    "subject_framed": "the speaker's face stays fully in the 9:16 frame with sensible headroom, also when they move.",
    "callouts_anchored": "on-screen callouts sit next to what they name, appear while it is discussed, never cover the "
                         "face or a caption. Score 10 when the cut needs none and has none.",
    "pacing": "no dead air, long pauses, repeated takes or rambling; the cut keeps moving.",
    "ending": "the cut ends on a complete, deliberate closing line, not mid-thought.",
}.items())

FIX_OP_GUIDE = "\n".join(f"- {op}: {d}" for op, d in {
    "move_cut": "a cut at t_s is badly placed (mid-word, clipped, too early or late): edge = which side of the cut to "
                "change (out = the end of the passage before it, in = the start of the passage after it); seconds = how "
                "far to move it (+ later, - earlier), 0 to just snap it to the nearest word boundary.",
    "drop_span": "remove the passage spoken at t_s (a weak line, a repeat, filler).",
    "extend_span": "the passage at t_s is cut short: edge = which end to extend, seconds = roughly how much.",
    "swap_hook": "the opening is weak: target_t_s = the cut time of the line that should open the video instead. "
                 "With target_t_s -1 and t_s inside a title card or silence at the start, the opening card is dropped.",
    "reorder": "the passage at t_s belongs elsewhere: target_t_s = the cut time of the line it should come before.",
    "caption_fix": "the caption shown at t_s is wrong or badly placed: text = the words actually spoken when the "
                   "words are wrong, empty when only the position is wrong (e.g. over the face).",
    "retime_callout": "the callout at t_s appears at the wrong time or says the wrong thing: seconds = shift, text = "
                      "new text (empty to keep it).",
    "reframe_bias": "the speaker is badly framed around t_s: direction = which way the camera should move.",
    "none": "no edit can fix it (a note only).",
}.items())


def _data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _without_media(body: dict) -> dict:
    """The request as written to disk: an inline video or image data URL replaced by its size (a 40 s review copy is
    5 MB)."""
    msgs = body.get("messages")
    if msgs and not isinstance(msgs[0].get("content"), str):
        parts = []
        for c in msgs[0]["content"]:
            if c.get("type") in ("video_url", "image_url"):
                kind = "mp4" if c["type"] == "video_url" else "image"
                c = {"type": c["type"], c["type"]: {"url": f"<inline {kind}, {len(c[c['type']]['url'])} chars>"}}
            parts.append(c)
        return {**body, "messages": [{**msgs[0], "content": parts}]}
    inp = body.get("input")
    if inp and any(c.get("type") == "input_image" for c in inp[0].get("content") or []):
        parts = [{"type": "input_image", "image_url": f"<inline image, {len(c['image_url'])} chars>"}
                 if c.get("type") == "input_image" else c for c in inp[0]["content"]]
        return {**body, "input": [{**inp[0], "content": parts}]}
    return body


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def _parse_json(text: str) -> tuple[Any, str | None]:
    t = text.strip()
    if not t:
        return None, "empty output"
    t = _FENCE.sub("", t)
    try:
        return json.loads(t), None
    except json.JSONDecodeError as exc:
        return None, str(exc)
