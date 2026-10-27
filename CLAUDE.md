# Montivo: project instructions for Claude Code

This folder (`~/Documents/montivo`, public repo https://github.com/bgaravelos/montivo) is the only place the
hackathon project lives. Work here, not in `~/Documents/nocta-digital` (Nocta's agency work, off limits for this project).

## What this is
Montivo is Nocta Digital's entry for the Meta Global AI Developer Hackathon (build window 26/10 to 4/11/2026, kickoff 26/10 18:00 Athens,
submission on cerebralvalley.ai by Wed 4/11 evening, deadline Thu 5/11 04:00 Athens). An agent-first video editor:
Muse Spark 1.3 watches the footage and plans a paper edit, Muse Voice gives turns and word timings, SAM 3.1 tracks
the speaker for the 9:16 reframe and callouts, ffmpeg renders, then Spark reviews its own render against a rubric and
re-cuts (closed loop), with a chat-driven UI on top. Track: Indie. Bonus targets: Best Agentic, Best Creative, Most
Viral; Open Weight too: the Baseten and Meta credits arrived 26-27/10.

## Hard rules
- At run time the application calls Meta models only (`muse-spark-1.3`, `muse-voice-transcribe-1.0`, `sam-3.1`,
  `muse-image-1.0`). No other model provider in the product.
- All code was written from scratch during the hackathon. The concept comes from an earlier personal project of
  Billy's (also called Montivo); that code and repo no longer exist and nothing from it is used. Never copy code from
  `~/Documents/nocta-digital` either.
- Code, comments, README and commit messages in English. Replies to Billy in Greek with the professional terms in
  English, no em dashes. Billy dictates with Wispr Flow mixing Greek and English.
- `MODEL_API_KEY` lives in `~/.zshenv`; never print it, never commit it. `.env`, `runs/`, `footage/`, media files and
  `experiments/results/` are git-ignored and stay out of the repo.
- Spend: keep each evening under $5 and a 150 s run under $0.50. Every call is logged in `runs/<id>/ledger.json`.
- GitHub pushes to `main` are fine; creating repos, posting publicly or submitting needs Billy's explicit yes.
- Commit on Billy's go unless he said otherwise in the session; `git` identity is set repo-locally.

## Layout
```
montivo/           the engine package (config, ledger, run, cli, human; server/ = the UI API + static build; engine/{api,ingest,voice,track,spark,schemas,edl,reframe,
                   render,captions,graphics,image,review}.py)
tests/             offline tests on recorded Voice and SAM streams (make test)
experiments/       the 26/10 kill-test scripts (research, not product; results/ ignored)
docs/notes/        hand-over notes copied from the Claude Code sessions (git-ignored, read them first)
runs/              run folders (ignored); footage/ local clips (ignored)
```
`make dev` builds `.venv`; `.venv/bin/montivo run footage/clip.mp4 --brief "..." --out runs/003`; `--resume` reuses finished
stages of a run folder while iterating on prompts.

## State and plan
Evening 1 (26/10 night) done: `montivo run` goes ingest -> voice + track + watch -> plan -> `edl_v1.json` with a ledger.
Evening 2 (26/10 night, same session group) done: `engine/reframe.py` (9:16 crop path from the SAM track, sendcmd per
frame, keyframes in the EDL) and `engine/render.py` (frame-aligned segments from the source, audio cut in one pass,
concat by stream copy) give `cut_v1.mp4`; `montivo render runs/001` re-renders without the API; `--no-render` skips it.
Kill test (a) passed on the NASA clip (cut re-transcribed with Voice: span words in order, segment edges intact).
Measured facts that shape the code are in README.md ("Measured behaviour") and, in full, in `docs/notes/`.
Evening 3 (26/10 night) done offline on run 011: `engine/captions.py` + `engine/graphics.py` (Pillow PNG captions and
callouts, overlay in the segment encode: this ffmpeg has no libass/drawtext), `engine/image.py` (Muse Image title card,
`--no-title`), loudnorm -14 LUFS in `render_audio`; `montivo render runs/011 --out cut_v3.mp4`.
Run 012 (`--target 45`, fresh API run) verified evening 3 on new Voice output (commit `4952e84`).
Evening 4 (26/10 night) done, commit `3bfd99b`: `engine/review.py` (blind Spark review of the cut with a burned-in clock,
silencedetect and caption-vs-head facts, fix ops -> EDL patches, loop with guards), `montivo review runs/012`,
`montivo run --review`; a third fact (source audio level at both edges of every cut) on Billy's go: kill test (b) 3 of 3
in 3 of 3 (`experiments/kt_f_review_killtest.py`, `runs/012-ktb`). Edge pass (27/10, commit `a843346`):
`engine/edges.py` tightens every cut edge to the speech in `audio.wav` (in `build_edl`, after word-level review patches,
`montivo edges RUN` for old folders); loops 76 -> 100 (run 012) and 65 -> 85 (012-ktb). Render polish (27/10, commit
`1f530a9`): two-pass loudness in `render_audio` (measure, fixed gain + 192 kHz limiter when the peaks need it, loudnorm
linear; the mode goes in the EDL `render.loudnorm`) and caption lines up at least 0.4 s (`LINE_MIN_S`: merge with the same
phrase, else hold); `cut_v2_polish.mp4` in runs 012 / 012-ktb, -14.0 LUFS. Run 013 (27/10, commit `3737e32`): second
source `footage/usgs_uranium_miners_daughter.mp4` (public domain; `footage/nasa_exp72_thanksgiving.mp4` not run yet);
fixes: captions avoid only the followed head, a title-card issue drops the card, clipped edges widen to the gap; loop
62 -> 68 -> 72, no pass, $0.78. SAM at 5 fps for every concept (commit `432c45a`): person and head share one upload,
SAM $0.50 -> $0.33 on 166 s, a 150 s run about $0.48. API + UI (layout A, Review room, commit `b5f1f98`): `montivo serve` / `make ui`, `montivo/server/` (FastAPI + SSE,
reads run folders, engine as subprocess), `montivo/human.py` (drop span, re-plan -> next edl_vN, source human), `web/`
React/Vite built into `montivo/server/static` (`make web` after editing `web/src`); neutral dark, replay `?replay=4`,
Compare; test UI actions with `env -u MODEL_API_KEY`. Sample release `sample-012` not created yet (needs Billy's yes).
NLE redesign (27/10, commit `767e5f2`): `web/src/Editor.tsx` replaces `Room.tsx` (icon rail + panels, 9:16 viewer, inspector,
multi-track timeline with filmstrip / waveform / issue markers, J/K/L, export), trim by drag via `montivo.human trim`
(snaps to word boundaries, reviewer rules), `montivo/server/media.py` (filmstrip, peaks). Caption style is read-only (one
renderer style); Ask Montivo and "Review this version" not yet clicked with a key.
Cloud session (27/10 morning, branch `claude/nice-albattani-wsocse`): bundled Inter Tight 800 (`montivo/fonts/`, OFL) so
captions and tests match on every machine, pytest `pythonpath`, GitHub Actions `make test`; speaker -> head mapping
(`engine/speakers.py`, mouth-minus-eyes motion vs Voice word times, watch position as fallback, `speakers.json`,
`montivo speakers RUN`, $0): the render follows the talking speaker's head per span run; synthetic tests only so far.
Open Weight planner (same session): `--planner open` on `montivo run`, `montivo plan RUN --planner open` -> next edl_vN;
Muse Spark Open on Baseten as an OpenAI-compatible chat completion with the plan's strict schema, `BASETEN_API_KEY`
(already in `~/.zshenv`), model id exactly `meta-models/muse-spark-open-1026` (the key works only for it), a reasoning
model so the call sends `reasoning_effort` low unless `--plan-effort` says otherwise; offline tests only, the planner
path never called with the real key yet (Billy's 27/10 probe: HTTP 200, effort low answered in 0.8 s).
Second cloud round (27/10): README rebuilt for judges (why it is different, mermaid loop, measured results, try it,
the NLE editor, built during the hackathon, build log); English drafts of the submission form fields in Notion (subpage
of the Montivo page, waiting for Billy's yes); punch-ins (`engine/punch.py`, `--punch` on run and render, opt-in, stored
as EDL `punch` so the loop and edits keep it, `punch_ins` records them): 1.12x on words 3 dB over the cut's median level
or highlight words, 1.06x alternating on jump cuts, zoompan after the scale (crop cannot resize per frame, the graph
stalls), skipped when the head would leave the top or touch a caption; synthetic tests and a smoke render only.
Third cloud round (27/10, same branch, $0): demo video script and storyboard in Notion (subpage of the Montivo page,
waiting for Billy's yes; shots 7a/7b only if punch-ins / speaker mapping look good on the Mac). Punch-ins in the editor
(commit `36e137e`): every `punch_ins` entry has a `key` (`emphasis:<word>@<onset>`, `jump_cut:<span>`), the EDL's
`punch_skip` removes one while it keeps its place (budget, spacing, alternation), `python -m montivo.human punch RUN
--on|--off|--skip KEY` -> next edl_vN, POST `/api/runs/{id}/punch`, a switch in the cut inspector, pink markers on the
video track, a punch-in inspector with Remove; checked in Chromium on a synthetic run folder. Hook A/B (commit `eed0ef2`):
`engine/hook.py`, `montivo hook RUN [--edl] [--budget 0.10]` and `montivo review RUN --hook-ab`: B = A + one swap_hook
patch (challenger: the reviewer's swap_hook target, else a planner hook, else the first payoff, else the first new
passage), both reviewed blind, higher (pass, overall) kept, tie keeps A, record in `review/hook_ab_vA_vB.json` and B's
patch `result`; offline tests only, the review call never made for real.
Merge (27/10): the branch above is in `main`; a second cloud session had built the same two features on `main`
(commits `065420a`, `391a297`), the branch's versions were kept and only two pieces carried over: the EDL pin
`reframe_speaker_ids` {"A": "3"} in `render.load_speaker_ids` (replaces the mapped heads of that speaker, kept by
replan) and the v1-vs-new comparison of `montivo plan` (`plan_compare_v1_vN.json`, in the output and run log).
Open Weight A/B (27/10, `runs/012-open`, a clone of 012 so 012 stays clean for the sample release, $0.042): the first
real Baseten call worked (`edl_v3.json`, `cut_v3.mp4`, `plan_compare_v1_v3.json`): 9 spans, 45.3 s, verbatim 100 %,
same hook and ending, s2 "for the Meta Hackathon we are building Montivo" instead of "the editor it's an agent agent";
$0.012 / 6.8 s vs Spark 1.3 $0.050 / 35 s; blind review 92, pass (minor: the first caption drops "Hi,", 0.4 s over 45).
The open planner labels every body span `payoff` (the hook A/B would take s2 as a cold-open challenger).
Fourth review fact (27/10, $0.031): the caption timeline (`review.caption_timeline`, every line's text and cut times,
`facts.caption_timeline` in review_vN.json), because Spark samples about 1 fps and flagged cut_v3's 0.4 s "Hi." as
missing. Re-review of cut_v3 (`runs/012-open/review_v3.json`, the old one in `review_v3_no_timeline.json`): 88, pass,
no "Hi" issue; two new minor captions_match_speech: Voice wrote "cuts lands" and "vertical crops" (said singular?).
Next: the NASA clip (first real check of the speaker mapping), `montivo render runs/012 --punch --out cut_v2_punch.mp4` (first look at the punch-ins, then the switch and Remove in
`make ui` on a copy of 012), `montivo hook runs/012` (the first real hook A/B, about $0.03 with v2's review reused). The evening-by-evening table is in README.md.
Demo footage: Billy's own English talking-head clip, 60 to 90 s, expected in `~/Downloads` by 28/10; the NASA clip
(`footage/nasa_150s.mp4`, public domain) is regression only. Files under iCloud Drive cannot be read from Claude Code.

## Where the rest of the context is
- `docs/notes/` (copies of the session notes: kill tests and architecture, evening 1, fresh research and verdict).
- Notion page `405a4ee32031814ba38cfb1efd670918` (Kill test results, Architecture, Plan).
- Claude Code memory for this folder: `~/.claude/projects/-Users-billy-Documents-montivo/memory/`.
