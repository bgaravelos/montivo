"""Spark: the Responses client for muse-spark-1.3 with strict JSON schemas, plus the watch / plan / review helpers.

Rules measured on 26/10/2026: always 1.3 (1.2 degenerates with a strict schema and video), always hand it the Voice
transcript as text (1.3 fabricates quotes when asked to transcribe), one uploaded proxy reused by every call.
Every call writes <name>_request.json and <name>_response.json and a ledger entry with tokens and dollars.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import SPARK_MODEL, spark_cost
from ..ledger import Entry, Ledger, Timer
from . import schemas
from .api import dump_json, output_text, responses_create


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
             reasoning_effort: str | None = None) -> SparkCall:
        content: list[dict] = [{"type": "input_text", "text": prompt}]
        if file_id:
            content.append({"type": "input_file", "file_id": file_id})
        body: dict = {"model": self.model, "max_output_tokens": max_output_tokens,
                      "input": [{"type": "message", "role": "user", "content": content}],
                      "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        dump_json(self.dir / f"{name}_request.json", body)
        self.log(f"spark: {name} -> {self.model}{' + video' if file_id else ''} ...")
        with Timer() as t:
            resp = responses_create(body)
        dump_json(self.dir / f"{name}_response.json", resp)
        usage = resp.get("usage") or {}
        cost = spark_cost(usage, self.model)
        status = resp.get("status", "?")
        text = output_text(resp)
        parsed, err = _parse_json(text)
        entry_status = status if parsed is not None else f"{status}/parse_error"
        self.ledger.add(Entry(stage=f"spark.{name}", model=self.model, seconds=t.seconds, cost_usd=round(cost, 4),
                              tokens_in=usage.get("input_tokens") or 0, tokens_out=usage.get("output_tokens") or 0,
                              tokens_cached=(usage.get("input_tokens_details") or {}).get("cached_tokens") or 0,
                              status=entry_status, note=f"response {resp.get('id', '?')}"))
        self.log(f"spark: {name}: {status}, {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out, "
                 f"${cost:.4f}, {t.seconds}s")
        if parsed is None:
            raise RuntimeError(f"spark {name}: could not parse JSON ({err}); status={status}; head={text[:300]!r}")
        return SparkCall(name=name, parsed=parsed, status=status, usage=usage, cost_usd=round(cost, 4), seconds=t.seconds,
                         response_id=resp.get("id", ""))

    # ----------------------------------------------------------------- the three editorial calls

    def watch(self, file_id: str, transcript_text: str, brief: str, duration_s: float) -> SparkCall:
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
            "4) List b-roll candidates: visual moments without speech that could cover a cut.\n\n"
            f"TRANSCRIPT:\n{transcript_text}")
        return self.call("watch", prompt, schemas.WATCH, file_id=file_id)

    def plan(self, brief: str, transcript_text: str, watch: dict, target_s: float, file_id: str | None = None,
             reasoning_effort: str | None = None) -> SparkCall:
        shot_log = "\n".join(
            f"- {s['start_s']:.1f}-{s['end_s']:.1f}s {s['shot_type']}: {s['visual']}"
            + (f" | text: {s['on_screen_text']}" if s.get("on_screen_text") else "")
            for s in watch.get("visual_shots", []))
        speakers = "\n".join(f"- speaker {m['speaker']}: {m['who']} ({'on camera, ' + m['position'] if m['on_camera'] else 'off camera'})"
                             for m in watch.get("speaker_map", []))
        prompt = (
            "You are the editor building the paper edit for a short-form vertical video.\n"
            f"Brief: {brief}\nTarget runtime: about {target_s:.0f} seconds of speech, never more than {target_s * 1.25:.0f}.\n\n"
            f"Footage summary: {watch.get('summary', '')}\n\nShot log (visual, estimated times):\n{shot_log}\n\n"
            f"Speakers:\n{speakers}\n\n"
            "Rules for the paper edit:\n"
            "- Ordered spans to keep, strongest line first (the hook must land inside the first 1.5 seconds of speech).\n"
            "- Each span lies inside ONE transcript turn and quotes the exact contiguous words to keep, copied character for "
            "character from that turn's transcript, including repeated words and disfluencies. We cut on word boundaries "
            "taken from the audio, so a paraphrase or a cleaned-up quote cannot be used.\n"
            "- Prefer spans that start and end on complete thoughts. Skip interviewer questions unless they carry the hook.\n"
            "- Give a 2 to 4 word callout for a span only when a concrete on-screen object or number deserves it, else empty.\n"
            "- Propose a short title card text (max 6 words), a caption style note and a one-sentence reframe note.\n\n"
            f"TRANSCRIPT (authoritative):\n{transcript_text}")
        return self.call("plan", prompt, schemas.PLAN, file_id=file_id, max_output_tokens=16000, reasoning_effort=reasoning_effort)

    def review(self, file_id: str, brief: str, edl_summary: str) -> SparkCall:
        prompt = (
            "You are the reviewer of an automated video editor.\n"
            f"Brief: {brief}\n\nThe cut you are watching was built from this edit decision list:\n{edl_summary}\n\n"
            "Watch the rendered cut with its audio and score it on each criterion 0 to 10 (10 = perfect). Be strict and "
            "concrete: every issue needs a timestamp, the span id it concerns when applicable, and a fix operation with "
            "arguments the editor can apply. If there are no captions or callouts, score those criteria 0 and say so. "
            "pass = true only if overall >= 80 and there is no blocker.")
        return self.call("review", prompt, schemas.REVIEW, file_id=file_id, max_output_tokens=8000)


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
