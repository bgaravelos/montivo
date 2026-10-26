# Montivo

An agent-first video editor built on Meta's models. You hand it raw footage and a one-line brief; the agents
watch it, pick the lines, cut on word boundaries, keep the speaker in a 9:16 frame, render, then review their own
cut against a rubric and re-cut until it passes.

| Role | Model | What it does |
|---|---|---|
| Eyes and judgement | **Muse Spark 1.3** | shot log and speaker map (`watch`), paper edit by turn id with verbatim words (`plan`), rubric review with fix ops (`review`) |
| Ears and clock | **Muse Voice Transcribe 1.0** | one streaming diarization call per clip: turns, speakers and word-level timings from the delta stream |
| Camera operator | **SAM 3.1** | tracks the speaker's head (5 fps) for the 9:16 reframe and the person plus any callout concept per frame over raw SSE |
| Art department | **Muse Image 1.0** | title card and thumbnail |
| Hands | ffmpeg + Pillow | proxies, audio, trims, crop path, caption and callout overlays, loudness, H.264 |

Every runtime model is a Meta model. Every call's request and response JSON lands in the run folder next to a
ledger of tokens, dollars and seconds per stage.

## Status (evening 3 of 8)

Works end to end from raw footage to a finished 9:16 cut with captions, callouts, a title card and loudnorm:

```
montivo run footage.mp4 --brief "..." --out runs/001
  ingest   ffprobe, 960 px proxy at source fps, 24 kHz mono wav, one Files API upload reused by every Spark call
  voice    streaming DIARIZATION -> turns[] + words[] (lag-corrected, snapped to speechStart/speechEnd)   \
  track    SAM 3.1 "person" (10 fps) and "head" (5 fps) over httpx SSE, regex boxes -> track/<concept>/tracks.json | in parallel
  watch    Spark 1.3 + proxy + transcript -> shot log, speaker map, concept suggestions, b-roll, movement moments /
  plan     Spark 1.3 + brief + shot log + movement moments + measured speech rate + transcript -> paper edit (turn id + verbatim words)
  edl      spans mapped to Voice word boundaries -> edl_v1.json
  reframe  per segment: the head track (person as fallback), largest and most centred id -> smoothed 9:16 crop path (dead zone, EMA, pan clamp)
  title    Muse Image 1.0: the plan's title_card_text -> title/title_card.png (generated once, $0.01)
  captions Voice words of the kept spans -> de-doubled, glossary-corrected, 1 to 4 word lines, placed clear of the SAM head box;
           the plan's per-span callouts anchored to the SAM person box -> transparent PNGs (Pillow)
  render   ffmpeg from the SOURCE: 1.5 s title segment with a fade, then frame-aligned segments, sendcmd-driven crop, overlays
           burned in after the scale, 1080x1920 H.264; audio cut in one pass, 1.5 s of silence in front, one loudnorm pass
           (-14 LUFS) -> cut_v1.mp4, measured again with ebur128
montivo render runs/001 --out cut_v3.mp4   re-render from edl_v1.json + voice.json + tracks, ffmpeg only (the title card is
                                           generated once if missing, --no-title to skip it)
```

Not yet: review loop, UI. See the plan at the end.

## Setup

Requirements: Python 3.12, ffmpeg and ffprobe on the PATH, a Meta developer API key.

```bash
make dev                      # python3.12 venv in .venv, installs montivo and pytest
export MODEL_API_KEY=...      # from dev.meta.ai, never committed
.venv/bin/montivo run footage.mp4 --brief "A 30 second vertical teaser: why launching from US soil again matters." --out runs/001
```

The demo brief for Billy's own clip (`footage/billy_demo.mp4`, DJI selfie mode, so `--hflip`), verbatim:

```bash
.venv/bin/montivo run footage/billy_demo.mp4 --hflip --target 40 --out runs/011 --brief "35 to 45 s vertical reel: what Montivo is and why the review loop matters. Keep the hook (Hi, I'm Billy). Keep the line about the four Meta models through 'SAM tracks me so the vertical crop follows me when I move, like right now': that is the moment I move on camera, it must stay in the cut. Drop the Greek pre-roll, the pauses, the ums and the repeated take (keep only the second 'this is the part that matters'). End on 'Raw footage in, a reviewed cut out'."
```

Useful flags: `--target 40` (seconds of speech to keep), `--concept "mission patch"` (extra SAM concepts, repeatable;
SAM is one request per concept, so each one costs about $0.25 per 125 s at 10 fps, and the concepts `watch` suggests are
only logged as `watch suggests: ...` with that price, never tracked on their own), `--hflip` (mirror the footage once
before everything else: selfie / mirror-mode clips where on-screen text reads backwards; writes `<run>/source_hflip.mp4`
with libx264 crf 18 and the audio copied, and that file is the source for the proxy, the wav, SAM and the render, so no
box or crop is ever flipped afterwards; recorded in `ingest.json` and in the EDL's `source`),
`--track-fps 10` (default: 10 fps for sources over 60 s, source fps below that), `--reframe-concept head` and
`--reframe-fps 5` (the track the crop follows; `none` falls back to the person track), `--no-track`, `--plan-with-video`,
`--plan-effort low|medium|high`, `--resume` (reuse ingest, voice, watch and tracks already in the run folder while
iterating on the plan), `--language none`, `--no-render` (stop at `edl_v1.json`), `--no-title` (no Muse Image title card,
saves $0.01), `--no-captions`. `montivo render runs/001 [--edl ...] [--source ...] [--out cut_v3.mp4] [--no-title]
[--no-captions] [--no-callouts] [--no-loudnorm]` renders an EDL again from the run folder's `voice/voice.json` and tracks;
it calls no model, except Muse Image once when `title/title_card.png` is missing or was made for another title text.
Captions use the first font found of `MONTIVO_FONT`, `~/Library/Fonts/InterTight-800.ttf`, Arial Black, Arial Bold,
DejaVu Sans Bold (recorded in the EDL's `caption_style.font_path`).

`make test` runs the offline tests (recorded Voice and SAM streams are parsed without network access).

## Run folder

```
runs/001/
  run.json  run.log  summary.json  ledger.json      what ran, every line logged, totals per stage (render is $0)
  ingest.json  proxy.mp4  audio.wav                 probe, 960 px proxy (uploaded once), 24 kHz mono wav
  source_hflip.mp4                                  only with --hflip: the mirrored source every later stage reads
  voice/  request.json  events.jsonl  voice.json    raw stream, turns[] and words[]
  track/  track_source_10fps.mp4                    the clip SAM saw (resampled when the source is over 60 s)
          person/ head/ request.json events.sse tracks.json masks.jsonl   (head: track_source_5fps.mp4)
  spark/  watch_request.json watch_response.json plan_request.json plan_response.json
  watch.json  plan.json  edl_v1.json                parsed outputs and the EDL on word boundaries
  title/  request.json response.json title_card.png   Muse Image call (image stripped from the response) and the card (1152x2048)
  render/ seg_00_title.mp4 seg_NN.mp4 seg_NN_crop.cmd audio.m4a concat.txt   title segment, one video segment per cut with its crop
          overlays/ cap_NNN_K.png callout_sN.png    path, the audio in one piece; one PNG per caption line and active word, per callout
  cut_v1.mp4                                        1080x1920 H.264 + AAC, the segments joined by stream copy
```

`edl_v1.json` spans carry `t_in`/`t_out` from the Voice words, `verbatim` (the planner quoted the transcript
exactly), `match_ratio` otherwise, and `contiguous_with_previous` when two spans join without a cut. After a render it
also carries `reframe` (crop keyframes every 0.5 s and whenever the camera starts or stops moving), `captions` (one entry
per line: text, cut times per word with what Voice heard where a correction applied, the box in output pixels, the head
box it avoided, `placement` lower_third / below_head / above_head / over_head, and its `corrections`), `callouts` (text,
times, box, the person and head boxes, placement), `caption_corrections` (every dedupe and glossary fix) and `render`
(segments with their frame counts, the track followed, the crop x range and overlay count; the title segment; the
loudnorm figures and the ebur128 measurement of the finished file).

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
- Cost of this stage on a 150 s source: Voice $0.01, SAM person at 10 fps $0.30 plus head at 5 fps $0.15, Spark watch
  $0.07, Spark plan (text only) $0.03, about $0.56 and 3 minutes of wall time ($0.47 on Billy's 125 s clip). SAM is
  one request per concept, so every extra concept costs the same again.
- Reframe on a moving subject (Billy's clip, 26/10): on a lean the head moves about 450 px at 1920 while the SAM person
  box centre moves 130 px (the box is torso plus arms), so a crop that centres the person box clips the face. The crop
  follows a separate "head" track at 5 fps instead, with the pan clamp at 450 px/s (1280 base) and an EMA of 0.25 s:
  on the 25 s lean block the head stayed fully inside the crop in 745 of 745 frames with no direction reversal, and the
  static NASA talking head still renders at one x.
- Render path (kill test (a) on the NASA clip): spans flagged contiguous render as one segment, every segment is
  snapped to whole source frames, video segments are joined by the concat demuxer with stream copy and the audio is
  cut from the source in one pass (atrim + 20 ms fades + concat filter). Re-transcribing the cut with Voice gives the
  span words in order with the first and last word of every segment intact. Joining per-segment AAC instead adds about
  47 ms of audio per segment (encoder padding) and the lip sync drifts by that much at every cut, so the audio is not
  cut per segment.
- Planner fill: with "aim for 40 seconds" alone Spark kept 20 s; told the range (target to 1.2x, never below 0.8x),
  the speech rate (2.5 words per second) and that one long turn may yield several spans, it kept 34.5 s and 32.8 s on
  two runs ($0.04 per plan call). It still pads with the interviewer's question when the speaker's own lines run short.
  On Billy's clip the same 2.5 constant made a 40 s target come out at 50.7 s (Spark's own estimate was 47.6 s): Billy
  speaks slower, 2.3 words/s measured by Voice over the spoken seconds of the turns. The plan prompt now takes the
  rate Voice measured on the clip (`VoiceResult.speech_rate`, 2.5 only when unknown) for the word range and the
  `estimated_runtime_s` rule. Run 011 on the same clip: Voice measured 2.09 words/s, the EDL came out at 47.7 s for a
  40 to 48 s range (was 50.7 s).
- Watch on Billy's clip (26/10) logged 2 shots for 125 s and no motion, so the planner dropped the one block where he
  moves (60.6 to 85.4 s). Watch now also returns `movement_moments` (speaker out of centre or handling a prop, with
  seconds) and the plan prompt lists them under the shot log. Run 011: watch returned the 82.5 to 85.6 s side-to-side
  lean among 5 moments and the plan kept that block (the brief asked for it too). `--hflip` on the same run: the EDL
  and the render read `source_hflip.mp4`, the head track stayed one id, the head was centred at all 9 checked points
  of the cut including the lean. Run cost $0.49 (hflip and render are $0).

- Finishing on run 011 (26/10, `montivo render runs/011 --out cut_v3.mp4`, ffmpeg only after one $0.01 Muse Image call):
  this ffmpeg build (Homebrew 9.0.2) has neither libass nor freetype, so `subtitles` and `drawtext` are unavailable;
  captions and callouts are Pillow PNGs burned in with `overlay` inside the segment encode, so the video is still
  encoded once. 29 caption lines, 96 word-state PNGs and 1 callout, render 25 s for a 49.3 s cut (the plain cut took 20 s). Voice
  doubled 3 words in the kept spans ("its its", "that's that's", "in in", each with 0 s gap) and misheard 4 names; all 7
  corrections are in the EDL. The SAM head box bottom stayed above y 971 of 1920 in every line, so all 29 lines kept the
  lower-third default (top at 1300) and none is over the face; the callout could not sit on top of the person box (the
  box starts 132 px from the frame top, inside the platform UI) and went inside the box under the head. Loudness:
  source -28.5 LUFS; loudnorm (dynamic, one pass) at -1.0 dBTP gave -0.8 dBTP after the AAC encode, so it now aims at
  -1.5 and the file measures -14.3 LUFS integrated, -1.3 dBTP, LRA 3.9 LU with ebur128. Muse Image returned the title
  "Raw In, Reviewed Cut Out" spelled correctly in 8.9 s.

## Plan

| Evening | Deliverable |
|---|---|
| 27/10 | engine clients, run folder, ledger, CLI (this); then two kill tests on the render path, see below |
| 28/10 | done early (26/10 night): reframe path, ffmpeg trim/concat, 9:16 crop following the track, `cut_v1.mp4`, `montivo render`; from here every run uses Billy's own 60 to 90 s English talking-head clip, the NASA clip is regression only |
| 29/10 | done early (26/10 night): captions from word times, callouts on tracks, Muse Image title card, loudnorm; speaker-to-person mapping still open |
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
