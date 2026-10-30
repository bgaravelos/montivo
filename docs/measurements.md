# Montivo: measured behaviour the design rests on

What was measured while building, with the numbers, and the kill tests that set the design on the first night.
The [README](../README.md) has the summary table under "Measured results". Dates are 2026; run numbers name local run
folders, which stay out of the repository.

## Measurements

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
- Cost of this stage on a 150 s source, first measured with SAM's person track at 10 fps: about $0.56 and 3 minutes of
  wall time. Since 27/10 every concept runs at 5 fps on one upload (below), which brings a 150 s run with its review
  loop to about $0.48. SAM is one request per concept, so every extra concept costs the same again.
- Reframe on a moving subject (Billy's clip, 26/10): on a lean the head moves about 450 px at 1920 while the SAM person
  box centre moves 130 px (the box is torso plus arms), so a crop that centres the person box clips the face. The crop
  follows a separate "head" track at 5 fps instead, with the pan clamp at 450 px/s (1280 base) and an EMA of 0.25 s:
  on the 25 s lean block the head stayed fully inside the crop in 745 of 745 frames with no direction reversal, and the
  static NASA talking head still renders at one x.
- Render path (kill test (a) on the NASA clip): spans flagged contiguous render as one segment, every segment is
  snapped to whole source frames, video segments are joined by the concat demuxer with stream copy and the audio is
  cut from the source in one pass (atrim and concat; since 28/10 a 20 ms equal-power crossfade at every cut, and since
  29/10 one input per segment, so a clip moved earlier keeps its audio on ffmpeg 5.1 to 7.x). Re-transcribing the cut with Voice gives the
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
- Run 012 (26/10, fresh API run, `--target 45`, same clip and brief): the whole path in one `montivo run`, $0.4986 and
  242 s wall (Voice $0.006, watch $0.057, SAM head $0.125 + person $0.250, plan $0.050, Muse Image title $0.010 in
  12.6 s, render $0 in 30 s). Voice measured 2.14 words/s; the EDL came out at 52.0 s for a 45 to 54 s range, 9 spans,
  100% verbatim, and the hook keeps "Hi. I'm Billy. I run a small digital marketing agency in Athens" (run 011 lost it);
  it drops "for the Meta hackathon I'm building Montivo", so the name is first heard in the payoff. The new Voice output
  had the same three name mishearings (metal models, MuseSpark, News Voice), all caught, no "ready viewed" this time,
  and 5 new doubles (agent, between, me, the, afterwards), all removed. "it's an agent", "the cuts lands", "the vertical
  crops follows" and "score it against the rubric" came out identical in runs 011 and 012, so they are most likely what
  was said and stay uncorrected. 33 caption lines, all lower third, 0 over the face (head box bottom at most y 1036 of
  1920); 1 callout ("4 Meta Models", below the head on the person box). Caption lines ran across sentence ends at span
  joins ("work Muse Spark", "that matters Spark", "middle of one SAM"): a line now also breaks where the span changes,
  which gives 35 lines in `cut_v2.mp4` with every planned sentence starting its own line. Loudness -14.3 LUFS, -1.4 dBTP,
  LRA 3.6 LU (ebur128); cut 53.5 s with the 1.5 s title. On the lean the head stays inside the crop but sits in the left
  third for about a second at the fastest swing (pan clamp at 450 px/s).

- Review loop (evening 4, 26/10 night, runs 012 and 012-ktb): Spark 1.3 at the default reasoning effort burned all 10,000
  output tokens of a 53.5 s review on reasoning and returned nothing ($0.06); at effort low a review takes 15.5K tokens in
  (about 280 per second of video), 2.2K to 3.9K out, 17 to 31 s and $0.025 to $0.036, $0.012 to $0.016 when the same cut is
  reviewed again (video tokens cached). On run 012's cut_v1 the reviewer flagged the 1.5 s title card as a hook blocker in
  3 of 3 reviews (hook 2/10, overall 67 to 68); the loop dropped the card and cut_v2 passed at 82 (one patch, two reviews,
  $0.046). It never flagged that "Montivo" is first heard at 47 s. The first loop, before two guards existed, made the cut
  worse: "extra backstory at 4.5 to 7.5 s" dropped the whole hook span with "Hi, I'm Billy", and a stutter the reviewer
  imagined ("Muse Vocal gives", the caption reads "Muse Voice gives") dropped the timestamp line (68, 66, 68). Hence: a drop
  over part of a span trims only the words in the window at that end, the hook span is never dropped whole, and a pass
  scoring below the best stops the loop. The reviewer misreads captions at 720x1280 ("Musi Voice", "digital marketers");
  a caption_fix whose text equals the line is skipped, and minor issues are never applied.
- Kill test (b), reviewer (26/10 night): a bad v1 of run 012 with the first take "Okay. So, hi, I'm Billy..." as the hook
  (about 3 s of filler), span s2 cut inside "agent" (the 40 ms before the cut at -15.5 dB) and the line "Muse Spark watches"
  forced over the face. 3 of 3 blind reviews caught 2 of 3 with the timestamp inside the fault (hook at 0.0 s, caption at
  15.29 s): PASS by the rule, $0.081. The mid-word cut was caught 0 of 3 times (no_cut_mid_word 9 or 10/10): Spark does not
  hear a clipped word. The caption is caught because the deterministic caption fact names it; the hook from what Spark
  sees and hears. A control without the clock caught the same 2. The loop on the bad v1: trim the filler, un-force the
  caption, 58 to 84 and pass in two reviews ($0.044); the clipped "agent" stays. The audio level in the 40 ms before each
  cut separates it cleanly (good cuts on runs 012 and 012-ktb: -34.7 to -36.1 dB; the mid-word cut -15.5 dB), so a third
  measured fact would catch it.
- Third fact, cut edges (26/10 night): measured on the source audio (`audio.wav`) over 40 ms centred on each edge, because
  the render fades 20 ms at every join and a word that ends right at a tight cut is loud just before it without being
  clipped (a first version measured the render 20 to 60 ms before the cut and flagged the end of "company.", which is a
  clean cut into the next word). Clean edges on runs 012 and 012-ktb: -37.8 to -53.4 dB; inside "agent" -25.8 dB; 20 ms
  into the next word -31.3 dB; threshold -34 dB. Kill test (b) again with the three facts: 3 of 3 faults caught in 3 of 3
  runs, the mid-word cut at 12.17 s as a no_cut_mid_word blocker every time ($0.081). Run 012's clean cut gets no
  mid-word flag. Loops with the final code: run 012 v1 68 -> drop the title, trim the hook to "Hi. I'm Billy." with the
  edge moved into the gap (-25.1 -> -49.3 dB) -> 77 -> trim s2 to "It's a video editor where" -> 88, pass ($0.106; the
  dangling "where" is a weak edit the reviewer did not mind). The bad v1: 56 -> trim "Okay. So,", move the "company." end
  to the quiet point (-31.3 -> -40.6 dB), snap "agent" back onto its word, un-force the caption -> 72; the reviewer then
  asked to move the start 1 s earlier, which put "So," back, 69, and the regression guard kept v2 ($0.099). The same
  cut_v2 scored 83 and passed in an earlier loop: the reviewer varies by about 10 points on an identical cut. All
  evening: 45 review calls, $1.38.
- Edge pass (27/10, `engine/edges.py`): Voice puts the pauses inside words, so cuts on word boundaries opened in silence
  and every ending held 2.07 s after "cut out". Run 012's source: room tone at -51 dB (10th percentile of 10 ms frames),
  word onsets cross -45 dB within 10 to 20 ms, so speech is anything 6 dB over the floor, with dips under 60 ms bridged.
  Voice word edges were up to 1.6 s off at a passage start ("Hi." 25.50 to 27.18 s, heard from 27.13) and 0.4 s off
  inside one: "this" of the second take sits at 93.98 to 94.90 s in Voice, the speaker says it at 95.28 after the end of
  "again" and a 1.05 s pause, hence the fragment rule and the bound at the middle of the segment rather than the span.
  Measured on the source through the render's own segments (the rendered audio cannot be used: loudnorm runs in
  dynamic mode and lifts the room tone at the start of a cut from -48 to -20 dB): run 012 v1 9 edges moved, 52.0 -> 44.6 s;
  first speech 3.13 -> 1.63 s (1.5 s of it the title card), end hold 2.07 -> 0.31 s, dead air at the joins 3.12 ->
  0.98 s, the longest pause at a join 1.71 -> 0.27 s, the loudest cut edge -37.8 -> -47.9 dB (no loud edge). The bad
  v1 (012-ktb): 8 edges, 54.2 -> 47.6 s, first speech 0.42 -> 0.12 s, the "company." end pulled out of the next word
  (-31.3 -> -50.5 dB, fragment skipped); the deliberate cut inside "agent" is left alone (speech at the edge). Review
  loops on the tightened v1: run 012 76 -> drop the title -> 100, pass ($0.057; before the pass the same clip went
  68 -> 77 -> 88); the bad v1 65 -> trim "Okay. So,", snap "agent" back onto its word -> 85, pass ($0.061; before
  56 -> 72 -> 69). A first try of that loop showed that `trim_at` re-snaps both edges of a span, so a trim at the start
  re-opened the end the pass had tightened (the reviewer flagged it at -31.3 dB, 61 -> 78 -> 83): word-level patches now
  tighten both edges again. Spark has not flagged the ending or a late first word since.
- Two-pass loudness and a minimum caption time (27/10, $0, re-rendered as cut_v2_polish.mp4 from copies of edl_v2,
  cut_v2 untouched). Single-pass loudnorm is a dynamic AGC: on the ktb cut_v1 it put the first 0.3 s at -22.6 dB, 6.7 dB
  under the speech median where the source has 17.8 dB (its gain on quiet frames reached +25 dB, 90th percentile, against
  +14 dB on speech), and it missed the target there (-15.1 LUFS) and on the ktb cut_v2 (-15.0 LUFS, -0.7 dBTP). On run
  012's cut_v2 it happened to stay flat (+15.3 dB everywhere, room tone 18.1 dB under speech, source 18.4): the -48 -> -31 dB
  "lift" noted for it is the overall gain, not pumping; the hiss heard there is the room tone of a -28.4 LUFS recording
  brought up 15 dB. Now: a measuring pass, then one fixed gain. The gain to -14 LUFS (+14.4 dB) would put the -8.4 dBTP
  peaks at +6 dBTP, so loudnorm alone would fall back to dynamic on every clip so far: the gain goes in front of a
  lookahead limiter at -2.0 dBFS run at 192 kHz (true peaks), the limited audio is measured again and the gain corrected
  for what the limiter took (2 tries on both runs, +15.06 / +14.94 dB), then loudnorm linear trims the rest. Four audio
  passes, about 1 s each. Results (ebur128 on the file): run 012 v2 -14.3 LUFS / -1.2 dBTP -> -14.0 / -1.3; ktb v2 -15.0 /
  -0.7 -> -14.0 / -1.6; ktb v1 -15.1 / -1.3 -> -14.0 / -1.6. Room tone against the speech median (10 ms frames, the source
  through the same segments as reference): first 0.3 s 012 v2 -18.4 source / -18.1 old / -18.1 new, ktb v2 -18.3 / -16.7 /
  -18.2, ktb v1 -17.8 / -6.7 / -17.8; the longest pause (1.0 s) -17.8 / -17.5 / -17.7 (012), -17.1 / -16.9 / -17.1 (ktb v2),
  -17.7 / -18.0 / -17.6 (ktb v1). The new gain is flat on quiet frames (+15.2 dB median and 90th percentile); the limiter
  takes more than 1 dB off 8 to 9% of the speech frames (5th percentile 1.6 to 1.9 dB). A/V lengths match (44.667 s).
- Music bed (28/10, $0, a throwaway copy of run 015 v5, 37.9 s). The bed is ducked by a `volume` envelope from the Voice
  words in cut time (down 0.15 s before a word, back up 0.45 s after, pauses under 0.8 s stay down), not by a sidechain
  compressor: the duck is exact, the same on every render and testable offline. The speech is normalised first, the
  bed mixed in, the mix normalised again. Peaks: the first mix measured -1.95 dBTP as PCM and -0.5 dBTP as AAC (the
  encode overshot on a plosive at 0.97 s), so the render now measures the AAC file and runs the pass again with TP
  lowered by the overshoot: -2.2 dBTP after one retry. With the eight CC0 tracks at the defaults (-20 / -12), one version
  each: -14.0 LUFS, -1.7 to -2.0 dBTP, the speech 15.8 to 17.8 dB (RMS) over the bed as the render measures it (the
  speech stem against the bed alone); a first set of synthesised loops gave 14 to 17 dB and was dropped for its sound.
  Hand-checked in 3 s windows: 14 to 17 dB at -20, 11 to 14 dB at -18, 17 to 20 dB at -22. Run 015's cut has no pause of 0.8 s and no title card, so the bed stays down for the whole cut;
  it rises only in longer pauses and under a title card. The review listens to the speech stem the render keeps
  (`render/cut_vN.speech.m4a`) for silencedetect and Voice on the cut, since the bed would fill every silence gap, and
  tells the reviewer the music is there on purpose.
  Captions: "Hi." showed 0.15 s and "Meta models" 0.24 s on run 012 (0.24 s on ktb too). A line that would be up under
  0.4 s joins a neighbour of the same phrase when they fit on one line (4 words, frame width); neither could ("Hi." ends a
  sentence, "then the four" + "Meta models" is 5 words and "four Meta models" too wide), so both are held 0.4 s and the
  next line starts later ("I'm Billy." 0.18 -> 0.43 s, "do the work" 9.99 -> 10.15 s, still 0.71 and 0.64 s up); the EDL
  word times are clamped inside their line and the held lines carry `held_s`. No line under 0.4 s on either run now.
- Run 013, a second source (27/10): USGS "Chapter 2: Uranium Miner's Daughter" (public domain, 166 s, 1280x720
  29.97 fps, a close-up interview in a noisy atrium, a second speaker in the cold open, chapter and end cards, the
  speaker's name burned in as a lower third). `--target 40 --review`: Voice 3 speakers, 431 words, 3.08 words/s; the plan
  kept only her, 8 spans, 100% verbatim, 42.3 s, the cards and the other speaker left out. $0.69 for the run, over the
  $0.50 rule: SAM $0.50 (person 10 fps $0.33, head 5 fps $0.17) for 166 s. First loop 56 -> 54, stop on regression, and
  it showed three faults Billy's clip never hit: (1) every caption "over the head" (41 of 41): captions and callouts
  kept clear of the union of every head object, and SAM's head concept also tracked passers-by, so the union covered the
  frame; now only the head the crop follows counts (the largest head when the crop follows the person). (2) The title
  card stayed through two reviews that flagged it as a blocker, because the reviewer named a target line and the patcher
  swapped that line to the front; an issue inside the card now drops the card. (3) Four cuts clipped words (-21.5 to
  -33.8 dB at the edge): a fast speaker, and Voice ends words early. The edge pass now also widens a clipped edge (40 ms
  around it over the speech threshold) outward to the first quiet stretch within 0.4 s, never into a word another span
  keeps: 4 edges, -21.5 -> -40.9, -27.8 -> -37.9, -26.3 -> -40.1, -33.2 -> -36.0 dB. With a -39.6 dB room the absolute
  -34 dB of the third fact sat inside room tone; an edge is now loud over max(-34, the clip's speech threshold, at most
  -30). Second loop on the fixed v1: 62 -> drop the card -> 68 -> s6 opens -> 72, max passes, no pass ($0.094); no
  clipped-word issue in any of the three reviews. Still open: the head box of a speaker with long hair reaches the bottom
  of the frame, so no caption spot clears it; the fallback now puts the line as low as the bottom UI allows (y 1483,
  under the chin) instead of on the mouth (y 1300), and the reviewer rates it minor. The source's own lower third is
  cropped to "EGOS" in 9:16 and the reviewer files it under callouts, where no fix applies. Run total $0.784.
- SAM at 5 fps for every concept (27/10): run 013 paid $0.50 for SAM on 166 s, $0.33 of it the person track at 10 fps,
  a box that only anchors callouts and backs up the reframe. Dropping every other record of the 10 fps person tracks of
  runs 012 and 013 moves the largest person box by a median 0.1 px and 1.2 / 0.8 px at the 95th percentile (proxy 960 px,
  2,647 frames over the kept spans), with no frame where one has a box and the other none. Person and head now share
  one 5 fps upload and one batch: SAM $0.375 -> $0.25 on run 012 (125 s), $0.50 -> $0.33 on run 013, so a 150 s run
  comes to about $0.48 with the review loop. Clips under 60 s, which SAM used to see at the source rate (30 fps, $0.36
  per concept per minute), are resampled too.

- **A faster and smarter first cut (30/10).** Every first cut on the Mac (9 of 9) failed its first review on the 1.5 s
  title card that played in silence before the first word, so every pass cost a second review (016: 72 -> 88 for
  ~95 s and $0.07, the only fix the card dropped). Changed: the title over the opening speech (`title_style` header),
  plan and watch at reasoning effort low (plan 26-40 s / $0.03-0.05 -> 11-13 s / $0.012-0.016 on 012, 015, 016 with
  the same kept words on 015 and 016; watch 32.5 -> 10.8 s on 016, 50.3 -> 18.7 s on 014), Voice started on the wav
  during the upload and the plan beside SAM, the review prep in parallel (016 v3: 19.2 -> 10.7 s, facts identical),
  the brief as a checklist with a plan check and one repair call, and a $0 preflight of the measured checks before
  every render. A fresh run of the 016 clip passed its first review at 88 in 106.6 s wall and $0.22 (before: pass at
  200 s, $0.31). First-cut benchmark on seven runs re-planned from their stored Voice, watch and tracks ($0.56):
  first review mean 65.9 -> 78.7, passed at the first review 0 -> 2 (015 88, 016 100), title-card hook issues 7 -> 0;
  012 82 -> 100 at the second; 013, d1-nasa, d1-tedx and heli-01 still do not pass (close-up captions over the head,
  overlapping speakers, an opening line the reviewer finds weak, a cut edge the preflight cannot clear). Notes:
  `docs/notes/review-and-first-cut-research_2026-10-30.md`.

## Design rules fixed on 26/10

- **Blind reviewer.** The review pass gets only the rendered mp4, the brief and the rubric, never the EDL. It also gets
  deterministic facts as text: the silence gaps ffmpeg `silencedetect` finds in the render, where the caption boxes sit
  relative to the SAM face boxes, (since 26/10 night, Billy's call after kill test (b)) the source audio level at both
  edges of every cut and (since 27/10) the caption timeline.
- **Kill test (a), render path:** Billy's clip, cuts on Voice word boundaries, 9:16 reframe from the SAM track. Pass when no cut
  lands inside a word and the pan does not jitter. If it fails: an 80 ms audio crossfade on every cut and snapping only to
  `speechEnd`/`speechStart`.
- **Kill test (b), reviewer:** a deliberately bad v1 with three known faults (a cut mid-word, a caption over the face, a weak
  hook). The review must catch 2 of 3 with a timestamp within 1 s, 3 runs out of 3.
