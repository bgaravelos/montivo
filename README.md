# Montivo

An agent-first video editor built on Meta's models. You hand it raw footage and a one-line brief; the agents
watch it, pick the lines, cut on word boundaries, keep the speaker in a 9:16 frame, render, then review their own
cut against a rubric and re-cut until it passes.

| Role | Model | What it does |
|---|---|---|
| Eyes and judgement | **Muse Spark 1.3** | shot log and speaker map (`watch`), paper edit by turn id with verbatim words (`plan`), rubric review with fix ops (`review`) |
| Ears and clock | **Muse Voice Transcribe 1.0** | one streaming diarization call per clip: turns, speakers and word-level timings from the delta stream |
| Camera operator | **SAM 3.1** | tracks the person (and any callout concept) per frame over raw SSE; drives the 9:16 reframe and callout anchors |
| Art department | **Muse Image 1.0** | title card and thumbnail |
| Hands | ffmpeg | proxies, audio, trims, crop path, captions, loudness, H.264 |

Every runtime model is a Meta model. Every call's request and response JSON lands in the run folder next to a
ledger of tokens, dollars and seconds per stage.

## Status (evening 1 of 8, 27/10/2026)

Works end to end up to the paper edit:

```
montivo run footage.mp4 --brief "..." --out runs/001
  ingest   ffprobe, 960 px proxy at source fps, 24 kHz mono wav, one Files API upload reused by every Spark call
  voice    streaming DIARIZATION -> turns[] + words[] (lag-corrected, snapped to speechStart/speechEnd)   \
  track    SAM 3.1 "person" over httpx SSE, regex boxes -> track/person/tracks.json, masks kept opaque     | in parallel
  watch    Spark 1.3 + proxy + transcript -> shot log, speaker map, trackable concepts, b-roll            /
  plan     Spark 1.3 + brief + shot log + transcript -> paper edit (turn id + verbatim words)
  edl      spans mapped to Voice word boundaries -> edl_v1.json
```

Not yet: render, captions, reframe path, review loop, UI. See the plan at the end.

## Setup

Requirements: Python 3.12, ffmpeg and ffprobe on the PATH, a Meta developer API key.

```bash
make dev                      # python3.12 venv in .venv, installs montivo and pytest
export MODEL_API_KEY=...      # from dev.meta.ai, never committed
.venv/bin/montivo run footage.mp4 --brief "A 30 second vertical teaser: why launching from US soil again matters." --out runs/001
```

Useful flags: `--target 40` (seconds of speech to keep), `--concept "mission patch"` (extra SAM concepts, repeatable),
`--track-fps 10` (default: 10 fps for sources over 60 s, source fps below that), `--no-track`, `--plan-with-video`,
`--plan-effort low|medium|high`, `--resume` (reuse ingest, voice, watch and tracks already in the run folder while
iterating on the plan), `--language none`.

`make test` runs the offline tests (recorded Voice and SAM streams are parsed without network access).

## Run folder

```
runs/001/
  run.json  run.log  summary.json  ledger.json      what ran, every line logged, totals per stage
  ingest.json  proxy.mp4  audio.wav                 probe, 960 px proxy (uploaded once), 24 kHz mono wav
  voice/  request.json  events.jsonl  voice.json    raw stream, turns[] and words[]
  track/  track_source_10fps.mp4                    the clip SAM saw (resampled when the source is over 60 s)
          person/ request.json events.sse tracks.json masks.jsonl
  spark/  watch_request.json watch_response.json plan_request.json plan_response.json
  watch.json  plan.json  edl_v1.json                parsed outputs and the EDL on word boundaries
```

`edl_v1.json` spans carry `t_in`/`t_out` from the Voice words, `verbatim` (the planner quoted the transcript
exactly), `match_ratio` otherwise, and `contiguous_with_previous` when two spans join without a cut.

## Measured behaviour the design rests on

- Spark 1.3 sees about one frame per second plus the audio, with no timestamps, so time comes from Voice, never
  from Spark. 1.3 fabricates quotes when asked to transcribe, so every Spark call gets the Voice transcript as text.
  Spark 1.2 degenerates with a strict JSON schema and video input; Montivo uses 1.3 only.
- Voice streaming (`partialMode: DELTA`, `emitAudioProgress: true`) emits word deltas stamped with
  `audioProcessedMs` on an 80 ms grid about 0.5 s after the audio. Word start = delta time minus 0.5 s, word end =
  next delta, first and last word snap to `speechStart`/`speechEnd` within 0.4 s. Checked by re-transcribing the
  cut spans: boundaries land on the right words, with up to about 0.3 s of slop inside continuous speech.
- SAM 3.1 must be streamed over raw SSE (httpx or curl): the OpenAI SDK stream dropped mid-way twice. Boxes come
  out of the text with a regex in milliseconds; the official parser needs minutes for the masks, so masks are kept
  as opaque payloads per frame and decoded only where a head anchor is needed. Ids are stable while the object stays
  in frame and do not reset on cuts.
- Cost of this stage on a 150 s source: Voice $0.01, SAM at 10 fps $0.30, Spark watch $0.07, Spark plan
  (text only) $0.03, about $0.41 and 3 minutes of wall time.

## Plan

| Evening | Deliverable |
|---|---|
| 27/10 | engine clients, run folder, ledger, CLI (this); then two kill tests on the render path, see below |
| 28/10 | EDL builder with reframe path, ffmpeg trim/concat, 9:16 crop following the track: first `cut_v1.mp4`; from here every run uses Billy's own 60 to 90 s English talking-head clip, the NASA clip is regression only |
| 29/10 | captions from word times, callouts on tracks, Muse Image title card, loudnorm, speaker-to-person mapping |
| 30/10 | blind review pass, fix ops, loop guards (3 passes, budget, repeated fixes): v1 to v3 on three sources |
| 31/10 | FastAPI + SSE, React shell: upload, brief, run, live log, preview, review panel |
| 1/11 | the two human actions in the loop first: "drop this span" and "re-run with this instruction" (typed, or spoken through Voice); reorder, caption edit and reframe nudge only after those |
| 2/11 to 4/11 | demo video, README for judges, buffer, submission |

Design rules fixed on 26/10:

- **Blind reviewer.** The review pass gets only the rendered mp4, the brief and the rubric, never the EDL. It also gets two
  deterministic facts as text: the silence gaps ffmpeg `silencedetect` finds in the render, and where the caption boxes sit
  relative to the SAM face boxes.
- **Kill test (a), render path:** Billy's clip, cuts on Voice word boundaries, 9:16 reframe from the SAM track. Pass when no cut
  lands inside a word and the pan does not jitter. If it fails: an 80 ms audio crossfade on every cut and snapping only to
  `speechEnd`/`speechStart`.
- **Kill test (b), reviewer:** a deliberately bad v1 with three known faults (a cut mid-word, a caption over the face, a weak
  hook). The review must catch 2 of 3 with a timestamp within 1 s, 3 runs out of 3.
- Track: Indie. Bonus targets: Best Agentic, Best Creative, Most Viral. Open Weight (Muse Spark Open on Baseten) only if the
  credits arrive by 31/10.

## Where it comes from

The concept comes from Montivo, an earlier personal project of Billy Gkaravelos (a chat-driven video editor). All the
code in this repository was written during the Meta Global AI Developer Hackathon (27/10 to 4/11/2026) from scratch:
nothing is shared with that earlier editor, and at run time the application calls Meta models only.

`experiments/` holds the kill-test scripts from 26/10 (kickoff day) that measured the API behaviour listed above; they
are research, not part of the product, and nothing in `montivo/` imports them. Their raw results stay out of the repo.

## Licence

MIT. Built by Nocta Digital (Athens).
