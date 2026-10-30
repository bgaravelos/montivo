# Montivo: pipeline, commands and run folder

The reference behind the [README](../README.md): every stage of a run, the CLI and the files a run leaves.

## Pipeline

Works end to end from raw footage to a finished 9:16 cut with captions, callouts, a title card and loudnorm, then
reviews its own cut blind and re-cuts:

```
montivo run footage.mp4 --brief "..." --out runs/001
  ingest   ffprobe, 960 px proxy at source fps, 24 kHz mono wav, one Files API upload reused by every Spark call
  voice    streaming DIARIZATION -> turns[] + words[] (lag-corrected, snapped to speechStart/speechEnd)   \
  track    SAM 3.1 "person" and "head" at 5 fps (one upload) over httpx SSE, regex boxes -> track/<concept>/tracks.json | in parallel
  watch    Spark 1.3 + proxy + transcript -> shot log, speaker map, concept suggestions, b-roll, movement moments /
  speakers (2+ voices, 2+ heads) mouth-minus-eyes motion of every SAM head vs the Voice word times of every speaker
           -> speakers.json (head ids per speaker; watch's "left/right/centre" as the fallback), $0, ffmpeg + Pillow
  plan     Spark 1.3 + brief + shot log + movement moments + measured speech rate + transcript -> paper edit (turn id + verbatim words);
           the same call names the words Voice probably misheard (likely_mishearings: from the brief, on-screen text or
           context) -> EDL glossary_suggestions, offered in the chat and the Captions panel ("Did you say ...?", Accept =
           the glossary edit), never applied unasked
  edl      spans mapped to Voice word boundaries, then every cut edge tightened to the speech in the source audio
           (0.1 s before the onset, 0.15 s after the offset, 0.3 s at the very end) -> edl_v1.json
  reframe  per segment: the head track (person as fallback), the talking speaker's head when speakers.json maps one, else the
           largest and most centred id -> smoothed 9:16 crop path (dead zone, EMA, pan clamp)
  title    Muse Image 1.0: the plan's title_card_text -> title/title_card.png (generated once, $0.01)
  captions Voice words of the kept spans -> de-doubled, glossary-corrected, 1 to 4 word lines up at least 0.4 s each, placed
           clear of the SAM head box;
           the plan's per-span callouts anchored to the SAM person box -> transparent PNGs (Pillow)
  punch    (--punch) zooms on the loudest content words and an alternating scale across jump cuts, about the head
  render   ffmpeg from the SOURCE: 1.5 s title segment with a fade, then frame-aligned segments, sendcmd-driven crop, overlays
           burned in after the scale, 1080x1920 H.264; audio cut in one pass, 1.5 s of silence in front, two-pass loudness
           (measure, then one fixed gain to -14 LUFS, a 4x oversampled limiter when the peaks need it) -> cut_v1.mp4,
           measured again with ebur128; with EDL `music` a bundled loop under the normalised speech, ducked along the
           Voice words in cut time, the mix normalised again (-14 LUFS, true peak of the AAC file under -1 dBTP)
  review   (--review) Spark 1.3 watches cut_vN blind -> rubric scores + fix ops -> EDL patch -> edl_vN+1 -> render cut_vN+1, repeat
montivo render runs/001 --out cut_v3.mp4   re-render from edl_v1.json + voice.json + tracks, ffmpeg only (the title card is
                                           generated once if missing, --no-title to skip it)
montivo review runs/001                    the review loop on a finished run folder: review cut_v1, patch, render cut_v2, review ...
montivo edges runs/001                     the edge pass on an EDL built before it existed ($0; the old EDL kept under check/)
montivo speakers runs/001                  which SAM head is which Voice speaker ($0) -> speakers.json; the next render follows it
montivo plan runs/001 --planner open       a fresh paper edit from the run's brief, voice and watch on Muse Spark Open (Baseten)
                                           -> the next edl_vN / cut_vN beside Spark 1.3's v1 (the Open Weight A/B, one call)
montivo hook runs/001 [--edl edl_v2.json]  hook A/B: the next version opens on another line, both reviewed blind, the better
                                           one kept (review/hook_ab_vA_vB.json); `montivo review ... --hook-ab` runs it on
                                           the loop's best version
```

The review loop: the reviewer gets the cut (scaled to 720x1280 with a cut-time clock burned in at the top left), the brief,
the rubric and four measured facts, never the EDL: the silence gaps ffmpeg `silencedetect` finds in the cut, every
caption box against the SAM head box, the speech level of the source audio right at both edges of every cut (added
after kill test (b): Spark never hears a clipped word) and every caption line's text and cut times (added 27/10: the
0.4 s "Hi." opening run 012-open's cut_v3 fell between Spark's frames and was flagged missing; with the timeline the
same cut drew no such issue). It names times in the cut, not spans; `engine/review.py` maps each time back to a
span, a cut point, a caption line or a callout and turns blocker and major issues into EDL patches (`drop_span`,
`extend_span`, `move_cut`, `swap_hook`, `reorder`, `caption_fix`, `retime_callout`, `reframe_bias`; a title card the
reviewer flags is dropped with `title_card: false`). Guards: at most 3 reviews, a dollar budget checked before each review
(`--budget`, default $0.30), at most 4 fixes per pass, the same fix proposed twice stops the loop, a pass that scores
below the best so far stops it (unless the pairwise judge prefers it in both orders, see Hook A/B), the hook span is
never dropped whole, and a drop over part of a span trims only the words
in the reviewer's window at that end. The fixes of one pass are applied in turn and each is checked against the EDL the
earlier ones made (a move next to a span another fix dropped, or a drop that would leave one span, is skipped and
recorded), and a new version always takes the next free number, so reviewing an old version never overwrites a later one. Word-level edits land on a sentence end when one is a word away and then settle on
the quietest 40 ms of the source audio within 0.3 s (Voice words abut, so only the audio shows the gap) and are then
tightened to the speech like v1; a clipped word is fixed by moving its edge to that quiet point, never by removing words.
Escalation (C4, 30/10): when the loop stops without a pass on a stop no fix of the cut gets past (no applicable fix, the
same fix twice, a regression, a measured fault the fixes cannot clear) and the best version still has a blocker or
major open, it re-plans once from that version with the reviewer's open notes and the measured faults as the editor's
note (`human.replan`, Muse Spark Open when `BASETEN_API_KEY` is set, else Spark 1.3 at effort low, the run's target),
preflights and renders it and reviews it once; it is kept only when its gated rank beats the best. The budget covers
it (about $0.03 for the plan plus one review); loop.json `escalation` records the notes, the planner, the costs, the
score and whether it was kept; its patch says `source: review`, `action: escalate`. First real one, a clone of d1-nasa
(stuck at 62 on "same fix twice"): the Spark Open re-plan scored 56 and was not kept ($0.059).

The editor on top of this is described in the README, under "The editor".

## Commands and flags

Useful flags: `--target 40` (seconds of speech to keep), `--concept "mission patch"` (extra SAM concepts, repeatable;
SAM is one request per concept, so each one costs about $0.125 per 125 s at 5 fps, and the concepts `watch` suggests are
only logged as `watch suggests: ...` with that price, never tracked on their own), `--hflip` (mirror the footage once
before everything else: selfie / mirror-mode clips where on-screen text reads backwards; writes `<run>/source_hflip.mp4`
with libx264 crf 18 and the audio copied, and that file is the source for the proxy, the wav, SAM and the render, so no
box or crop is ever flipped afterwards; recorded in `ingest.json` and in the EDL's `source`),
`--track-fps 10` (default 5 fps for every concept, the head track's rate, so person and head share one upload), `--reframe-concept head` and
`--reframe-fps 5` (the track the crop follows; `none` falls back to the person track), `--no-track`, `--plan-with-video`,
`--plan-effort low|medium|high`, `--punch` (punch-in zooms, see below), `--review` (the review loop after cut_v1; `--review-passes 3`, `--review-budget 0.30`), `--resume` (reuse ingest, voice, watch and tracks already in the run folder, and `plan.json` + `edl_v1.json` while
the brief, target and planner are the ones the plan was made with; `--replan` forces a new plan, and a new plan first
keeps the old `plan.json` and `edl_v1.json` in `superseded/` and names any later `edl_vN` built on it in a warning), `--language none`, `--no-render` (stop at `edl_v1.json`), `--no-title` (no Muse Image title card,
saves $0.01), `--no-captions`. `montivo render runs/001 [--edl ...] [--source ...] [--out cut_v3.mp4] [--no-title]
[--no-captions] [--no-callouts] [--no-loudnorm] [--punch | --no-punch]` renders an EDL again from the run folder's `voice/voice.json` and tracks;
it calls no model, except Muse Image once when `title/title_card.png` is missing or was made for another title text.
`montivo review runs/001 [--edl edl_v1.json] [--passes 3] [--budget 0.30] [--no-clock] [--no-apply]` runs the loop on a
finished run folder (only Spark review calls, about $0.03 each, $0.012 when the same cut is reviewed again and the video
tokens come from the cache); `--passes 1` or `--no-apply` reviews once and writes the proposed fixes without patching.
`montivo edges runs/001 [--edl edl_v1.json] [--dry-run]` tightens an EDL that was built before the edge pass existed: it
rewrites the EDL in place (the old one goes to `check/<name>_before_edges.json`), clears the render fields so the next
`render` or `review` renders it again, and logs every move; a second run moves nothing.
Open Weight planner: `montivo run ... --planner open` writes the paper edit with Muse Spark Open on Baseten Model APIs
(`config.OPEN_MODEL`, default `meta-models/muse-spark-open-1026`, `MONTIVO_OPEN_MODEL` / `MONTIVO_OPEN_BASE_URL` override;
`BASETEN_API_KEY` in the environment, checked before any spend; a reasoning model, so the call asks for effort low
unless `--plan-effort` says otherwise: at the default effort a short probe spent every token on reasoning). The plan is the one stage that needs no video, so the
same prompt and strict JSON schema go out as an OpenAI-compatible chat completion (`response_format` json_schema),
the answer is read back in the Responses shape and logged in the ledger like any Spark call; watch and review stay on
Spark 1.3. `montivo plan runs/001 --planner open` does it on a finished run folder: a fresh plan from the run's own
brief (blind to the current cut), the next `edl_vN` with a `replan` patch that names the model, rendered, so the UI's
Compare shows the two planners side by side; `--review 3` runs the loop on it. The A/B numbers (spans, runtime,
verbatim rate, unplaced passages, the hook, what each plan call cost and took) go to `plan_compare_v1_vN.json`, the
run log and the command's output. Measured on a copy of run 012 (27/10): Muse Spark Open placed 9 spans, 45.3 s, every
one verbatim, nothing unplaced, the same hook and ending as Spark 1.3 and 8 of its 9 passages (it opened the body on
"for the Meta Hackathon we are building Montivo" instead of the stumbling "the editor it's an agent agent"), for $0.012
in 6.8 s against Spark 1.3's $0.050 in 35 s; the blind reviewer gave that first cut 92 and a pass (Spark 1.3's v1 had
76 before the loop, its reviewed v2 100). It marked every body span `payoff` (the prompt gave only the label names),
so the hook A/B took a middle line as the punchline; the plan prompt now defines each role and `build_edl` sets them by
position for any planner (one `hook` first, the `payoff` last, `cta` only after it, `body` between; a changed label
keeps the planner's in `planner_role` and `role_fixes`).

Hook A/B (`engine/hook.py`, `montivo hook runs/001 [--edl edl_vN.json] [--budget 0.20]`, or `montivo review runs/001
--hook-ab [--hook-budget 0.20]` after the loop): version A is the EDL as it is, version B the next free version with one
`swap_hook` patch through the reviewer's own patch path (the challenger moved to the front, every other span in A's order,
the old opener kept as a body span), rendered and reviewed blind; then the pairwise judge decides (`review.pairwise`,
30/10): one Spark 1.3 call sees both review copies (two `input_file` parts, Cut A then Cut B, each with its measured
facts), twice with the two swapped, both at once; B is kept only when both calls pick it, a split or a tie keeps A
(`review/compare_vA_vB.json`, about $0.07 and 17 s on two 38 s cuts). The two scores stay on record (`by_score`); with
no budget left for the judge, or an open-model reviewer, the higher (pass, overall) decides as before. The review
loop's regression guard asks the same judge when a version ranks below the best before it only by its score: the loop
goes on from it (`preferred_over`) only when it wins both orders.
The challenger is chosen deterministically: the line the reviewer asked to open with (a `swap_hook` target in this run's
reviews, newest first), else another span the planner marked `hook`, else the planner's `payoff`, the ending (a cold open),
else the first span after the opener that starts a new passage. A's review is reused when `cut_vA` was already
reviewed (then the A/B is one review, about $0.03; two reviews about $0.06 on a 45 s cut), the budget is checked for
every review still needed before the first one, a challenger already compared against the same A is not tried again,
and nothing is overwritten. `decisive` = the judge's two calls agreed (on an A/B without the judge: the margin reaches
the reviewer's 10-point noise). Both versions, the challenger and why, both scores with the hook criterion, the
winner and the reason go into `review/hook_ab_vA_vB.json` and into B's patch entry (`source: hook_ab`, `result`); the
editor labels B "hook A/B" and the Assistant says which version was kept. Real runs so far: on run 012-open the
challenger (the payoff as a cold open) scored 81 against A's 88 (hook 3 against 10), on run 015 69 against 82; both
times A was kept.

Speaker to head mapping (`engine/speakers.py`, run step and `montivo speakers runs/001`): with two or more voices and two
or more heads, the microphone comes first. When watch saw one, the run tracks it with SAM on the head track's frames
(one request, about $0.12 per 120 s); per frame the head whose box is nearest the mic's box (the gap over the head's
height, within 1.5) holds it, and a speaker gets the head that holds it on at least 60 % of the frames where only that
speaker talks; a mic object that never moves is dropped (SAM also called a fixed fitting a microphone). For the
speakers and heads the mic leaves, every head box gets a mouth-minus-eyes motion series on the 5 fps grid SAM saw (mean absolute frame
difference in the lower 40 % of the box minus the 10 to 45 % band, so a nod or a camera move cancels out), compared per
speaker between the frames where only that speaker talks (Voice word times) and the frames where they are silent; a
head goes to the speaker whose effect size is at least 0.4 and 0.2 over the runner-up, two heads on screen together
never share one, and a speaker left without a head takes the one at the position watch's speaker map names. The
render then plans each segment in pieces, one per speaker run, each following that speaker's head (`pieces` in the
EDL's `render.segments`); captions keep clear of the head followed at that moment. Off-camera voices and single-speaker
clips keep the plain rule. A human pin in the EDL, `reframe_speaker_ids` {"A": "3"} (Voice label -> head id), replaces
the mapped heads of that speaker while the pinned id is in frame, with or without speakers.json, and survives a re-plan.
A head upside down (an
astronaut on the ceiling) is found from the person track (the head sits at the bottom edge of its body's box) and its
mouth and eyes bands are flipped. First real check, the NASA Expedition 72 Thanksgiving message (run 014, 120 s, four
astronauts in one shot, one handheld microphone passed around): without the mic all four voices went to the wrong head.
Watch's speaker map named the wrong person for three voices, and mouth motion carried too little (heads 54 to 122 px
wide at 5 fps, the mic over the mouth, the upside-down astronaut's own voice at -0.36 before the flip, +0.36 after).
With the mic ($0.12 more) all four map right, checked against who holds it in every Voice turn: shares 86, 100, 80 and
99.5 %. Distances between box centres had given the first voice to the wrong astronaut (the mic was held low, nearer the
next head down), and the fixed fitting the second; gaps and the stillness rule fixed both. A human pin,
`reframe_speaker_ids`, still wins over all of it.
Punch-ins (`engine/punch.py`, `--punch` on `run` and `render`, opt-in for now): a 1.12x zoom on the words the speaker
leans on (a word's level in the source audio at least 3 dB over the median of the kept words, 0.2 s or longer, four
letters or more, not a stop word; a word on the run's highlight list at any level), at most one per 6 s of cut and
4 s apart, ramping in over 0.12 s to the word's onset, held 0.35 s past it and out over 0.25 s; and a 1.06x base scale
on a segment that opens on the head at the place and size where the previous one closed (a jump cut), so takes
of the same shot alternate. The zoom is about the median centre of the followed head, so the face grows where it is;
an event is skipped when the zoomed head would leave the top of the frame or touch a caption on screen. ffmpeg's crop
cannot change size per frame (the graph stalls), so the zoom is a `zoompan` after the scale and before the overlays:
captions and callouts never zoom. The EDL records `punch` (kept by the review loop and the editor's edits) and
`punch_ins` (kind, word, level over the median, cut times, zoom, anchor, and a `key` such as `emphasis:Montivo@47.120`
or `jump_cut:s4` that names it across versions). `python -m montivo.human punch runs/012 --on|--off` makes the next
version with the punch-ins on or off, `--skip KEY` the next version without that one: the key goes into the EDL's
`punch_skip`, which the review loop, trims, drops and re-plans carry forward, and a skipped punch-in keeps its place
(its slot in the one-per-6 s budget and the 4 s spacing, its turn in the jump-cut alternation), so removing one never
makes another appear elsewhere. First real use (27/10, run 012-open): 9 punch-ins (7 on emphasis, 2 on jump cuts),
the head stayed in frame and the captions did not zoom in the frames checked, the blind review 88 and a pass as
without them; in the editor, Remove and then the switch off made v6 with 8 and v7 with none.
Captions use `MONTIVO_FONT` when set, else the bundled `montivo/fonts/InterTight-800.ttf` (Inter Tight, SIL OFL 1.1, a
static wght 800 instance of the Google Fonts variable font), so line breaks and pixels match on every machine; Arial
Black, Arial Bold and DejaVu Sans Bold are fallbacks only (the path is recorded in the EDL's `caption_style.font_path`).
The look is a person's choice (EDL `look`, `engine/graphics.py`): six presets (marker, clean, box, karaoke, bold pop,
minimal), five bundled OFL fonts (Inter Tight, Montserrat Black, Anton, Poppins Bold, DM Serif Display; licences in
`montivo/fonts/licenses/`), colours, outline, shadow, a box behind the line, words per line, case, how the spoken word
shows (pill, colour, karaoke, none), extra highlighted words, and the caption position: auto (off the face, from the
look's height) or fixed (every line at one height). Callouts take the look's font and colours, and a person can pin
one where they dragged it (span `callout_xy`). `python -m montivo.human style RUN --look '{"preset": "box"}'
[--callout s5=120,300 | s5=auto]` makes the next version ($0); the review loop and its pass gate leave a fixed position
and a pinned callout where the person put them, and a re-plan carries both. The editor's Style panel shows each preset
on a real frame of the cut (and the person's own saved looks, kept for every project in `looks/`, one file per judge on the hosted demo) and previews every change on the paused viewer with the same Pillow code
(`engine/look.py`, about 0.4 s per still): what it shows is the new version's frame.
A look can also be asked for in words (`--prompt "bold yellow, one word at a time, higher"`, the Style panel's
Describe it box): one Muse Spark call with a strict schema of the look's fields (about $0.008, 8-12 s) gets the current
look, how light the picture is behind the caption rows (mean luma of the source frames under them) and the rows the
speaker's head covers, and answers the whole look with one line of why; out-of-range numbers are clamped, a bad colour
keeps the old one, and a fixed height that would sit on the face becomes auto (on run 015 its first answer put the
words on the mouth at y 1150 before the face rows were in the prompt and the guard existed).

The editor: `make ui` (or `.venv/bin/montivo serve [--port 8765] [--root .]`). It reads the run folders and starts the
engine as a subprocess for every paid call and every render (`montivo run ... --review`, `montivo review`, `python -m
montivo.human drop|trim|replan|punch`); it imports engine modules only for quick $0 work in the request (the Style
panel's stills, covers, stems, pre-flight). The React build is committed in `montivo/server/static`, so it needs Python only; after editing
`web/src`, `make web` rebuilds it (Node 18+). `python -m montivo.human drop runs/012 --span s3 [--from 2] [--review 1]`
and `python -m montivo.human replan runs/012 --instruction "..." [--from 2] [--review 1]` (and `python -m montivo.human
trim runs/012 --span s3 --edge out --word 12`, `python -m montivo.human punch runs/012 --on|--off|--skip KEY`) are the
editor's actions from the shell: the next version number is one past the highest in the folder, so nothing is overwritten; drop goes
through the reviewer's own `drop_span` patch and tightens the two edges that now meet ($0 without `--review`); replan is
one Spark plan call (about $0.04) on the stored Voice words and shot log with the instruction added to the brief, the
title card kept as it was, then the edge pass and the render. For a word Voice misheard, `python -m montivo.human caption
runs/015 --at 18.5 --text "Four Meta models do all" [--at ... --text ...]` gives the caption line on screen at that cut
second the words actually spoken (the reviewer's `caption_text` patch, timing kept, $0); in the editor, the Captions panel does the same (double-click a line or the pencil, then Make Version N).

## Run folder

```
runs/001/
  run.json  run.log  summary.json  ledger.json      what ran, every line logged, totals per stage (render is $0)
  ingest.json  proxy.mp4  audio.wav                 probe, 960 px proxy (uploaded once), 24 kHz mono wav
  source_hflip.mp4                                  only with --hflip: the mirrored source every later stage reads
  voice/  request.json  events.jsonl  voice.json    raw stream, turns[] and words[]
  track/  track_source_5fps.mp4                     the clip SAM saw, 5 fps, one upload for every concept
          person/ head/ request.json events.sse tracks.json masks.jsonl
  spark/  watch_request.json watch_response.json plan_request.json plan_response.json
  watch.json  plan.json  edl_v1.json                parsed outputs and the EDL on word boundaries
  title/  request.json response.json title_card.png   Muse Image call (image stripped from the response) and the card (1152x2048)
  render/ seg_00_title.mp4 seg_NN.mp4 seg_NN_crop.cmd audio.m4a concat.txt   title segment, one video segment per cut with its crop
          overlays/ cap_NNN_K.png callout_sN.png    path, the audio in one piece; one PNG per caption line and active word, per callout
  cut_v1.mp4                                        1080x1920 H.264 + AAC, the segments joined by stream copy
  review_v1.json                                    the review of cut_v1: facts, scores, issues, the fixes planned and skipped (why)
  review/review_v1/ cut_v1_review.mp4 clock/        the 720x1280 copy Spark watched with its clock frames; review/loop.json
  spark/review_v1_request.json review_v1_response.json
  edl_v2.json  cut_v2.mp4  review_v2.json ...       the patched EDL (`patches`: what changed and why), its render, its review
```

`edl_v1.json` spans carry `t_in`/`t_out` from the Voice words, `verbatim` (the planner quoted the transcript
exactly), `match_ratio` otherwise, and `contiguous_with_previous` when two spans join without a cut; `edges` records
every edge the edge pass moved (from, to, the speech onset or offset, a neighbour's fragment skipped, the level before and
after) and `edge_tuned` the edges the review patcher must not snap back; `edge_pass` holds the noise floor, the speech
threshold and the totals. After a render it
also carries `reframe` (crop keyframes every 0.5 s and whenever the camera starts or stops moving), `captions` (one entry
per line: text, cut times per word with what Voice heard where a correction applied, the box in output pixels, the head
box it avoided, `placement` lower_third / below_head / above_head / over_head, and its `corrections`), `callouts` (text,
times, box, the person and head boxes, placement), `caption_corrections` (every dedupe and glossary fix) and `render`
(segments with their frame counts, the track followed, the crop x range and overlay count; the title segment; the
loudnorm figures and the ebur128 measurement of the finished file).
