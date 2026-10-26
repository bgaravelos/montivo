#!/usr/bin/env python3
"""Planner test: video + diarized transcript (Voice turns as text) + brief -> shot log + paper edit that references turn ids.
This is the real Second Cut WATCH/PLAN call. Timestamps for speech come from Voice; Spark decides what to keep."""
import argparse, json, time
from pathlib import Path
from common import client, save, spark_cost

ap = argparse.ArgumentParser()
ap.add_argument("--file-id", required=True)
ap.add_argument("--voice-json", type=Path, required=True)
ap.add_argument("--model", default="muse-spark-1.2")
ap.add_argument("--brief", default="A 30 to 40 second vertical teaser for social media: why launching from US soil again matters, keep the strongest personal line first.")
ap.add_argument("--effort", default=None)
ap.add_argument("--tag", default="nasa")
ap.add_argument("--no-schema", action="store_true", help="ask for JSON in the prompt instead of text.format")
args = ap.parse_args()

turns = json.loads(args.voice_json.read_text())["response"]["turns"]
transcript_text = "\n".join(f"[turn {t['turnId']} | {t['startMs']/1000:.2f}-{t['endMs']/1000:.2f}s | speaker {t.get('speaker','?')}] {t['transcript'].strip()}" for t in turns)

SCHEMA = {
  "type": "object", "additionalProperties": False,
  "properties": {
    "visual_shots": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
        "start_s": {"type": "number"}, "end_s": {"type": "number"},
        "shot_type": {"type": "string", "enum": ["title_card", "talking_head", "b_roll", "screen", "wide", "closeup", "black", "other"]},
        "visual": {"type": "string"}, "on_screen_text": {"type": "string"},
        "trackable_objects": {"type": "array", "items": {"type": "string"}, "description": "short noun phrases SAM could track, e.g. 'person', 'mission patch'"}},
        "required": ["start_s", "end_s", "shot_type", "visual", "on_screen_text", "trackable_objects"]}},
    "speaker_map": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
        "speaker": {"type": "string"}, "who": {"type": "string"}, "on_camera": {"type": "boolean"}}, "required": ["speaker", "who", "on_camera"]}},
    "paper_edit": {"type": "array", "description": "ordered spans to keep; each span is inside one turn and quotes the exact words kept", "items": {"type": "object", "additionalProperties": False, "properties": {
        "turn_id": {"type": "integer"}, "words": {"type": "string", "description": "verbatim words from that turn, contiguous"},
        "role": {"type": "string", "enum": ["hook", "body", "payoff", "cta"]}, "why": {"type": "string"}},
        "required": ["turn_id", "words", "role", "why"]}},
    "estimated_runtime_s": {"type": "number"},
    "title_card_text": {"type": "string"},
    "caption_style_note": {"type": "string"}
  },
  "required": ["visual_shots", "speaker_map", "paper_edit", "estimated_runtime_s", "title_card_text", "caption_style_note"]}

prompt = (f"You are the editor. Brief: {args.brief}\n\n"
          "You have the video with its audio, and below the diarized transcript with exact turn timestamps (authoritative for time).\n"
          "1) Log the visual shots with your best estimate of start/end seconds and what SAM could track.\n"
          "2) Map speakers to who they are and whether they are on camera.\n"
          "3) Build the paper edit: ordered spans, each inside one turn, quoting the exact contiguous words to keep (we cut on word boundaries from the audio). "
          "Keep it tight, strongest line first, total runtime inside the brief.\n\nTRANSCRIPT:\n" + transcript_text)
if args.no_schema:
    prompt += "\n\nReturn ONLY a JSON object matching this JSON schema, no prose, no code fence:\n" + json.dumps(SCHEMA)
body = dict(model=args.model, max_output_tokens=12000,
            input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": "input_file", "file_id": args.file_id}]}])
if not args.no_schema:
    body["text"] = {"format": {"type": "json_schema", "name": "paper_edit", "strict": True, "schema": SCHEMA}}
if args.effort:
    body["reasoning"] = {"effort": args.effort}
t0 = time.perf_counter()
with client() as api:
    r = api.responses.create(**body)
lat = time.perf_counter() - t0
u = r.usage.model_dump()
try:
    txt = r.output_text.strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1].rsplit("```", 1)[0]
    parsed = json.loads(txt); ok = True
except json.JSONDecodeError:
    parsed, ok = None, False
# verify every paper-edit span quotes words that exist in that turn
checks = []
if parsed:
    by_id = {t["turnId"]: t["transcript"] for t in turns}
    for s in parsed["paper_edit"]:
        src = by_id.get(s["turn_id"], "")
        checks.append({"turn_id": s["turn_id"], "role": s["role"], "verbatim": s["words"].strip().lower() in src.lower(), "n_words": len(s["words"].split())})
out = {"model": args.model, "effort": args.effort, "latency_s": round(lat, 1), "usage": u, "cost_usd": round(spark_cost(args.model, u), 4), "status": r.status,
       "parse_ok": ok, "span_checks": checks, "verbatim_rate": round(sum(c["verbatim"] for c in checks) / len(checks), 2) if checks else None, "result": parsed, "raw": None if ok else r.output_text[:3000]}
save(f"kt_a_planner_{args.model}_{args.tag}{'_noschema' if args.no_schema else ''}", out)
print(json.dumps({k: out[k] for k in ("latency_s", "cost_usd", "status", "parse_ok", "verbatim_rate")}), "input_tokens", u["input_tokens"], "output_tokens", u["output_tokens"])
if parsed:
    for s in parsed["visual_shots"]:
        print(f"  shot {s['start_s']:6.1f}-{s['end_s']:6.1f} {s['shot_type']:12s} {s['visual'][:60]} | track: {s['trackable_objects']}")
    print("  speakers:", parsed["speaker_map"])
    for s, c in zip(parsed["paper_edit"], checks):
        print(f"  edit turn {s['turn_id']} {s['role']:6s} verbatim={c['verbatim']} [{s['words'][:90]}]")
    print("  runtime:", parsed["estimated_runtime_s"], "| title:", parsed["title_card_text"])
