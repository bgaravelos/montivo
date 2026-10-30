# Montivo: project instructions for Claude Code

This folder (`~/Documents/montivo`, public repo https://github.com/bgaravelos/montivo) is the only place the
hackathon project lives. Work here and nowhere else. Private notes (task board, hosted address, background) are in
`CLAUDE.local.md`, which is git-ignored.

## What this is
Montivo is Billy's (official name Vasileios Gkaravelos) own Indie entry (as an individual) for the Meta Global AI Developer Hackathon (build window 26/10 to 4/11/2026, kickoff 26/10 18:00 Athens,
submission on cerebralvalley.ai by Wed 4/11 evening, deadline Thu 5/11 04:00 Athens). An agent-first video editor:
Muse Spark 1.3 watches the footage and plans a paper edit, Muse Voice gives turns and word timings, SAM 3.1 tracks
the speaker for the 9:16 reframe and callouts, ffmpeg renders, then Spark reviews its own render against a rubric and
re-cuts (closed loop), with a chat-driven UI on top. Track: Indie. Bonus targets: Best Agentic, Best Creative, Most
Viral; Open Weight too: the Baseten and Meta credits arrived 26-27/10.
Discord #questions answers (Cerebral Valley bot, 28/10): the track follows who enters, not the team name (Indie =
individuals; Startup = an incorporated company that can employ people, EIN or the local tax ID), picked on the submit
form and switchable until the deadline; prizes stack (one track prize plus any bonus prizes); Most Viral = likes +
reposts + comments on the one X post linked in the submission, counted through 8/11 EOD (views and hashtags do not
count, build-in-public posts are fine, it can go up any time during the event); the submission stays editable after
Submit until Wed 4/11 9:00 PM ET, early submission gives no edge. Kai | CV (organizer, 28/10): "once submissions are due, no
further edits are allowed" (so nothing pushed after the deadline counts: the repo is final by Thu 5/11 04:00 Athens);
the demo video should be 3:00, a few seconds over is not disqualified but they stop watching at 3:00; sped-up runs are
fine for the demo. Open Weight (Cerebral Valley bot, 28/10): hosted Muse models plus Muse Spark Open qualify for Best
Build on Meta Open Weight Models (mixing is encouraged), served on own hardware or partner inference like Baseten
(`meta-models/muse-spark-open-1026`, `https://inference.baseten.co/v1`); the demo and README must say which stage runs
on Spark Open and why (README "Open weight: Muse Spark Open", 28/10); the infrastructure partner verifies it in round 1.
OSS license is in the rules (a builder on Discord, 28/10): MIT in `LICENSE` (Vasileios Gkaravelos), fonts OFL, music CC0.
Track decision (Billy, 28/10, FINAL): Indie, as Billy's own entry as an individual (the organizer confirmed it in
#questions, 28/10). Every public credit (team name "Montivo", README, brand title/end cards, demo end card, submission
drafts, X post) names Vasileios Gkaravelos (Billy's official name) and no company; background in `CLAUDE.local.md`.
Event pages (read 28/10): Indie = "individual builders and students", judged on creativity, technical ambition and
whether it works; Startup = "teams and founders building a real product", judged on technical execution and whether it
holds up in production, and startups may build on existing products. Stage 2 rubric is the same for both tracks
(impact 30, Meta tooling 25, technical depth 20, demo 25), top 5 per track go to the live final on 9/11. Prizes: Indie
150/100/75/50/25k, Startup 250/150/75/50/25k. Most Viral asks for the project demo posted on X.

## Hard rules
- At run time the application calls Meta models only (`muse-spark-1.3`, `muse-voice-transcribe-1.0`, `sam-3.1`,
  `muse-image-1.0`). No other model provider in the product.
- All code was written from scratch during the hackathon. The concept comes from an earlier personal project of
  Billy's (also called Montivo); that code and repo no longer exist and nothing from it is used. Never copy code from
  Billy's other projects either.
- Code, comments, README and commit messages in English. Replies to Billy in Greek with the professional terms in
  English, no em dashes. Billy dictates with Wispr Flow mixing Greek and English.
- `MODEL_API_KEY` lives in `~/.zshenv`; never print it, never commit it. `.env`, `runs/`, `footage/`, media files and
  `experiments/results/` (the local kill-test results) are git-ignored and stay out of the repo.
- The pre-kickoff API test scripts (`experiments/`, started 26/10 17:30, four calls before 18:00) were removed from the
  repo on 27/10 and are disclosed in README "Before the kickoff"; never bring them or code from them back. Organizers
  (Cerebral Valley, Discord #questions, 27/10): that is fine, nothing else needed, but the 3-minute demo video must
  OPEN with one line stating what was pre-existing (the name and idea, the pre-kickoff API tests) and what was built
  this week.
- Spend: keep each evening under $5 and a 150 s run under $0.50. Every call is logged in `runs/<id>/ledger.json`.
  Experiment evenings (reviewer benchmark, Open Weight tests; Billy's go 27/10, paid from the free credits: Meta about
  $146, Baseten $100) may go up to $15; keep about $80 of the Meta credits for the hosted demo.
- GitHub pushes to `main` are fine; creating repos, posting publicly or submitting needs Billy's explicit yes.
- Commit on Billy's go unless he said otherwise in the session; `git` identity is set repo-locally.

## Layout
```
montivo/           the engine package (config, ledger, run, cli, human; server/ = the UI API + static build; engine/{api,ingest,voice,track,spark,schemas,edl,reframe,
                   render,captions,graphics,image,review}.py)
tests/             offline tests on recorded Voice and SAM streams (make test)
tools/             kt_f_review_killtest.py, kill test (b) of the review loop (26/10 23:55, uses montivo)
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
Measured facts that shape the code are in `docs/measurements.md` (the README links it) and, in full, in `docs/notes/`. The pipeline, every CLI flag and the run folder layout are in `docs/pipeline.md`; the README keeps what a judge needs.
Evening 3 (26/10 night) done offline on run 011: `engine/captions.py` + `engine/graphics.py` (Pillow PNG captions and
callouts, overlay in the segment encode: this ffmpeg has no libass/drawtext), `engine/image.py` (Muse Image title card,
`--no-title`), loudnorm -14 LUFS in `render_audio`; `montivo render runs/011 --out cut_v3.mp4`.
Run 012 (`--target 45`, fresh API run) verified evening 3 on new Voice output (commit `4952e84`).
Evening 4 (26/10 night) done, commit `3bfd99b`: `engine/review.py` (blind Spark review of the cut with a burned-in clock,
silencedetect and caption-vs-head facts, fix ops -> EDL patches, loop with guards), `montivo review runs/012`,
`montivo run --review`; a third fact (source audio level at both edges of every cut) on Billy's go: kill test (b) 3 of 3
in 3 of 3 (`tools/kt_f_review_killtest.py`, `runs/012-ktb`). Edge pass (27/10, commit `a843346`):
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
Compare; test UI actions with `env -u MODEL_API_KEY`. No sample release (Billy, 28/10): README "Try it" points only to the hosted demo.
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
the NLE editor, built during the hackathon, build log); English drafts of the submission form fields (private,
waiting for Billy's yes); punch-ins (`engine/punch.py`, `--punch` on run and render, opt-in, stored
as EDL `punch` so the loop and edits keep it, `punch_ins` records them): 1.12x on words 3 dB over the cut's median level
or highlight words, 1.06x alternating on jump cuts, zoompan after the scale (crop cannot resize per frame, the graph
stalls), skipped when the head would leave the top or touch a caption; synthetic tests and a smoke render only.
Third cloud round (27/10, same branch, $0): demo video script and storyboard (private,
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
no "Hi" issue; two new minor captions_match_speech ("cuts lands", "vertical crops") are reviewer false positives:
Billy confirmed he said the plurals and Voice heard them right, so the captions stay verbatim.
Punch-ins and hook A/B, first real runs (27/10, `runs/012-open`, $0.066): `python -m montivo.human punch --on --from 3`
-> v4 with 9 punch-ins (7 emphasis x1.12 incl. "Billy.", "four", "render", 2 jump cuts x1.06); frames checked: head
stays in frame, captions and callouts do not zoom; review v4 88, pass, same as v3 (subject_framed 10). `montivo hook`
(A = v4): challenger s2 "for the Meta Hackathon we are building Montivo" as the planner's payoff cold open -> v5 81
(hook 3, major swap_hook back to "Hi") vs v4 88 (hook 10): v4 kept, margin 7, not decisive. With the timeline fact the
reviewer now calls the 0.4 s "Hi." and "Meta models" lines too short to read (minor) instead of missing.
NASA clip, run 014 (27/10, `footage/nasa_exp72_thanksgiving.mp4`, 120 s, 4 astronauts in one shot, one handheld mic,
$0.36): Voice 4 speakers / 9 turns, 6 spans, 46 s cut. Speaker mapping wrong on all four: watch's speaker_map misnamed
A, B, C (true: A = woman floating middle = head 3, B = blue shirt lower right = head 0, C = orange shirt lower left =
head 1, D = upside-down on the ceiling = head 2, checked by who holds the mic per Voice turn and A's lips); mouth motion
too weak (heads 54-122 px at 5 fps, the mic over the mouth, upside-down mouth in the eyes band: D scored -0.36 on his
own head), only head 2 cleared 0.4, for C. edl_v2 = v1 + the pin {"A": "3", "B": "0", "C": "1", "D": "2"} -> cut_v2
follows the talking astronaut in every segment (first real use of the pin, $0).
Mic first (27/10, Billy's choice): when watch suggests a microphone, run.py tracks it on the head track's frames
(`track.reuse_source`, no new upload, $0.12) and `speakers.mic_votes` gives each speaker the head nearest the mic (box
gap / head height <= 1.5) on >= 60 % of their solo frames; still mic objects dropped (`held_mic`); then mouth motion with
upside-down heads flipped (`inverted_heads` from the person track), then position. `montivo run --resume` on 014 tracked
the mic in the run; centre distances still got A and D wrong (mic held low; a fixed fitting SAM called a mic), gaps +
the stillness rule got all four right (shares 86/100/80/99.5 %), `montivo speakers` + render -> cut_v1 follows the right
astronaut everywhere. The resume re-planned (9 spans, 48.8 s); the earlier state is in `runs/014/check/before_mic/`.
Run 014 total $0.52.
Punch-ins in the editor, first real use (27/10, `runs/012-open`, $0, Chromium in the app's browser pane, no key via
`.claude/launch.json` "montivo-ui"): v4's 9 pink markers, the punch-in inspector ("word", 1.12x, +4.3 dB), Remove ->
v6 with 8 (`punch_skip` emphasis:word@69.860, nothing took its place), the switch off -> v7 with none ("1 removed by
hand stay removed"), no console errors. Found: the editor opens on `review/loop.json`'s best (v3 here), ignoring the
human edits and the hook A/B's kept version that came after the loop. Fixed (`web/src/Editor.tsx` `kept`/`opening`): the
editor opens on the newest version a human edit made or the hook A/B kept, else the loop's best, else the newest;
checked in the browser on 012 (v2), 012-open (v7), a copy without v6/v7 (v4, was v3), 013 (v3), 014 (v1).
Three engine fixes (27/10, $0, commit `b919f38`): roles by position for any planner (`edl.normalize_roles` in `build_edl`: one `hook`
first, the `payoff` last, `cta` only after it, `body` between; the planner's label kept in `planner_role`, changes in
`role_fixes`; the plan prompt now defines each role; the hook A/B takes the last payoff of an old EDL); `run --resume`
reuses `plan.json` and `edl_v1.json` when brief, target and planner match `run.json` `plan_inputs` (old run.json: its
brief/target/planner) and voice and watch came from disk, `--replan` forces a plan, a new plan first copies plan.json and
edl_v1.json to `superseded/<time>/` and warns about later edl_vN; a one-word caption line under `LINE_MIN_S` joins the
next line of its span across end punctuation ("Hi. I'm Billy.", `runs/012-open/cut_v4_caps.mp4` from a copy
`edl_v4_caps.json`). Checks: a clean clone of the public repo passes `make dev` / `make test` and serves keyless, but
README "Try it" needs the `sample-012` release (not created); with a locally packed zip, 012 opens on v2 and a keyless
drop renders. `runs/012-demo` exists for live edits; replay 4x of 012: plan 20 s, cut_v1 32 s, review 76 at 41 s,
review 100 at 51.9 s (end).
Billy's demo clip, run 015 (27/10, $0.30): `DJI_20261027_192304_908_video.MP4` (4K, 600/19 fps, rotation -180, not
mirrored) re-encoded once to `footage/billy_demo_2710.mp4` (1920x1080, 30 fps, rotation baked, 69.0 s). He did not read
the 26/10 demo script: his own lines ("Every AI editor we tried stops at the first cut ... raw footage in, a reviewed cut
out") with one retake ("no wait sorry let me say that again"); the run's brief was the old one, so its "repeated take"
and "this is the part that matters" lines do not exist (v1 flagged it). The planner still cut it right: 8 spans, hook
"Hi. I'm Billy.", retake dropped, ends on the payoff; 38.7 s; the crop follows the lean at "like right now". Loop v1 68
(title card over 1.5 s of silence, dropped) -> v2 81 pass. Hand caption edit (same evening, $0): `python -m
montivo.human caption RUN --at CUT_S --text "..."` (repeatable) retexts the line on screen at that cut second with the
reviewer's `caption_text` patch -> next edl_vN; `captions.override_for` now merges every override of a line in order
(later text wins, a forced top stays) and its end is strict (an abutting next line's src_t used to match too). Billy
confirmed four Voice mishearings -> run 015 v3 / `cut_v3.mp4`: "we edit short form", "Four Meta models do all",
"watches its own render", "scores it against" (frames checked; "the Muse Spark" left as Voice heard it).
Editor gaps closed (27/10 evening, $0.11, commit `0b171cb`): the Captions panel retexts lines (double-click or the pencil, edits staged,
"Make Version N" -> POST `/api/runs/{id}/caption` -> `montivo.human caption`, one render for all; a corrected line
shows what Voice heard, `caption_lines[].heard`); a re-plan carries the caption texts to the new spans
(`human.carry_caption_text`, with `voice` = the words Voice heard, applied only where the new plan breaks the line the
same way) and the re-plan prompt now lists the current callouts (`edl_summary`: without them Spark dropped both of 015's
callouts on "make it about 30 seconds"); a later review keeps the agent loop's `review/loop.json` as
`review/loop_<time>.json`. First real clicks with the key: "Review this version" on 015 v3 -> 87 pass
(captions_match_speech 10, was 5 in v1; the only issue is the brief's "this is the part that matters", never said);
Ask Montivo on the sandbox `runs/015-ui` (v4 + two test caption edits) -> v5 7 spans 32.0 s, review 100 pass, captions
carried, callouts lost -> after the fix v6 kept both. `.claude/launch.json` "montivo-ui-key" serves WITH the key on 8767
(it spends: stop it after use). The evening-by-evening table is in README.md.
Brand (27/10 evening, $0, commit `4c17218`): Billy chose direction A, In/Out + v-tag, dark + marker yellow. `assets/brand/`
(guide in its README.md): symbol = in/out brackets with a marker V between them (reads M and [v]), 64 grid, 8-unit
brackets; wordmark lowercase Inter Tight 700 outlined with HarfBuzz kerning, -0.02 em; lockups dark/light/mono, stacked,
app icons (ink tile, marker tile), favicon; tokens.json (ink #141414, paper #F3F0E8, marker #FFC629, graphite, slate,
cut #FF5A4E, pass #5FD08E); compositions (GitHub social preview 1280x640, gallery thumbnail, title and end cards 16:9
and 9:16, brand sheet) from `src/compositions.html`; logo sting 2.4 s (`src/sting.mjs`, Playwright core + ffmpeg). All
generated: `python3 assets/brand/build.py --png` (fonttools, uharfbuzz, headless Chromium), which also copies favicon,
touch icon and lockup into `web/public`. Applied: editor tokens and logo (`web/src/styles.css`, Home, Editor, major
issues now orange #FF8A3D), render accent `graphics.ACCENT` = marker, Muse Image title prompt with the brand hexes (not
called yet). `.gitignore` lets brand PNG/MP4 and the UI icons in. GitHub social preview upload is manual (needs Billy).
UI backlog 1 + 2 (27/10 evening, $0, commit `89bfe80`; the 12-item list is in Claude Code memory `project-montivo-ui-backlog`): Loop film
(`web/src/Loop.tsx`, `#/run/<id>/loop`, Loop button in the top bar and Versions): every version left to right with a clean
poster (`/frame?clean=1`, from cut_vN not the review copy), blind score and delta, worst 3 notes (tick where fixed), and
between two versions the patch that made the second (reviewer fixes with the criterion score moved, human edit, re-plan
with planner / cost / time vs v1 from `plan_compare_v1_vN.json`, hook A/B verdict, a Compare button), "Every version / Agent only".
Hook A/B in the UI: POST `/api/runs/{id}/hook` (`montivo hook --edl edl_vN.json --budget 0.10`), "Try another hook" in
the review inspector (~$0.04 with A's review, ~$0.07 without), the job's chat line reads the CLI's JSON record, the
editor opens the kept version when the verdict lands. Planner choice: `PlannerPicker` (Spark 1.3 / Spark Open) on New
project (`--planner open`) and Ask Montivo (`human replan --model OPEN_MODEL`), `planner: open` needs
`BASETEN_API_KEY` (409 otherwise), `has_open_key` and `costs.plan_open` in the state; every version carries `planner`
(v1 from run.json, a re-plan from its patch's `model`) and `plan_compare`; the rail's plan stage names the run's planner.
Checked keyless in the browser on 012-open and 015; offline tests only for the actions, the hook button and an Open
re-plan never clicked with the keys yet.
Live progress (UI backlog 3, 27/10 evening, $0, commit `caf17d9`): `runs.progress` reads a job's own run.log lines
(the server keeps run.log's size at job start, `Job.log_pos`) into the step, detail (render "segment k of n" from the
seg lines, weighted by frames), the bar's floor/ceil from the checkpoints and the expected seconds (the run's ledger
entries of that stage and model, else `COST` hints `review_s`, `review_prep_s`, `image_s`, `render_s_per_frame`); SSE
`progress` events live and in replay (the clock runs at the replay's pace), `progress` on `/api/runs` for the Home
card (polls 2 s while a job runs); `web/src/Progress.tsx` (top bar, Assistant card, Home card). Engine: the render's
segments line carries the frame total, `review_render` logs a line when the prep starts. `replay_script` now keeps the
whole run process (015's replay used to stop at the loop's first `montivo render:` header). Checked keyless on 015
replay 4x and live punch jobs on a copy (deleted).
Home cards (UI backlog 4, 27/10 evening, $0, commit `ab89e65`): `runs.project_card` per run in `/api/runs` `card`:
the version the editor opens on (same rule as Editor.tsx `opening`), its title card text as the name, a poster time a
third into the cut after the title card (`/frame?clean=1`), its blind score (else the best, named) and every reviewed
version's score in order for the sparkline; EDL and review fields cached on mtime + size (list 28 ms cold, 3 ms warm).
Checked keyless: 13 cards, every poster 200.
Home sort, filter, archive (UI backlog 5, 27/10 evening, $0, commit `fc1ef25`): sort newest / best score / name and
filter all / reviewed / passed (kept in localStorage), search over name, run, footage, brief; POST
`/api/runs/{id}/archive` moves the folder and its side logs (`runs/<id>.*log`) to `runs/_archive/` (409 while a job
runs or on a name clash), GET `/api/archive`, POST `/api/archive/{id}/restore`; Undo in the toast, Restore under
"Archived". Never deletes. None of Billy's runs archived: checked keyless on a throwaway copy (removed).
UI batch A (backlog 6, 8, 12; 27/10 evening, $0, commit `6a985a9`): New project shows the clip (GET
`/api/footage/{name}`, `duration_s` from ffprobe cached on mtime + size), prices and times the run from its length
(`COST` run_fixed / run_per_s / run_wall_* / review_loop / hook_ab from runs 014 and 015), four brief presets,
punch-ins and hook A/B switches (`montivo run --hook-ab`, needs `--review`: `hook_ab` on the loop's best after the
loop, in run.py); safe-area guides on the viewer (`web/src/SafeArea.tsx`, Reels 14 / 35 / 6 %, TikTok and Shorts in px,
approximate, G); N / Shift+N to the next / previous reviewer issue; "opens here" vs "loop best" in Versions; Export
preset 720 (`media.small_copy`, 015 v4 2.9 MB from 26.7 MB). The score chip already opened the best review.
UI batch B (backlog 9, 10, 11; 27/10 evening, $0, commit `412e9ff`): three human ops in `montivo/human.py`, each
the next edl_vN rendered: `text` (callout_text per span, "" removes; title_off / title_on; title_text = new words, one
Muse Image call $0.01, so the server needs the key for it), `reorder` (the reviewer's reorder op, `before` None = to the
end, the cuts that now meet tightened, `roles_by_position` = edl.normalize_roles on dicts), `pin` (EDL
`reframe_speaker_ids`, head none drops it); POST `/api/runs/{id}/text|reorder|pin`, GET `/api/runs/{id}/heads`
(`runs.heads`: speakers with watch's "who", mapped ids; heads with their largest-box frame) and `/head/{id}.jpg`
(`media.head_thumb`). UI: Text panel staged edits, drag a clip body on the video track (MoveAsk -> EditDialog "move"),
Speakers panel (rail). Watch's "who" is shown as "watch's guess": on 014 it names the wrong astronauts. Checked keyless
with real renders on a throwaway copy of 014 (pin A -> head 0, s8 moved before s2, callouts + title off; removed).
UI batch C (backlog 7, transcript editing; 27/10 evening, $0, commit `d9c18de`): in the Transcript view click / drag /
Shift-click words, Backspace -> EditDialog "words" (words, seconds, a line per clip) -> one version: a whole clip ->
POST `/drop`, one clip edge -> `/trim`, anything else -> POST `/api/runs/{id}/cut` -> `python -m montivo.human cut RUN
--words s2:19-19 [...]` (`cut_plan`: drop_span / `trim_fix` / split into the kept word runs, new ids past every id in
the EDL and its punch_skip; callout stays on the first piece, caption overrides follow their source time). Found on
run 015: Voice's abutting word edges sit up to 0.3 s off, so the reviewer's settle (0.3 s) and the edge pass's widen
(0.4 s) brought deleted words back ("checks its | own work" one run of speech; the second "and" heard 0.25 s late) and
a trim re-opened the clip's other edge over an earlier delete. Fixed in human.py only (review.py was another session's):
`tighten_edges(keep_out=)` blocks the deleted words, `settle_split` puts a split edge left in speech on the dip within
0.1 s, `hold_trimmed_edges` (cut and trim, so timeline drags too) pulls a trimmed edge back when there is speech more
than 0.1 s past the boundary and keeps the other edge where it was. Checked keyless on a throwaway copy of 015 (split
"and and", 11 words over s3-s5, "every single day.", a whole clip; removed).
Reviewer benchmark + Spark Open reviewer (27/10 night, $5.02, commit `ff77959`, `docs/notes/montivo-reviewer-bench_2026-10-27.md`):
Muse Spark Open on Baseten sees `video_url` frames (~1 fps) but hears nothing; `Spark.call(video=)` sends the cut
inline, `review_render` gives an Open reviewer the heard-words facts (Voice on the cut, `dedupe_heard`,
`caption_heard_diff`); Spark 1.3's prompt unchanged. `tools/reviewer_bench.py` plants 8 fault types in 012 v2, 015 v3,
014 v1 (`runs/bench/`), 50 reviews per reviewer: Spark 1.3 82 % caught / 75 % as major, clean 88.5 pass 100 %, weak on
mid-word (2/6) and caption text (4/6), 30 % of faulty cuts still pass; Open 82 % / 75 %, $0.018 vs $0.032, but scores
15-20 lower and never passed a clean cut; heard facts do not help 1.3. The Open reviewer found 015 v3 opening with
"Okay, hi" (edge pass widened s1 into "Okay"; needs Billy's ear).
Deterministic checks + pass gate (27/10 night, $0.82, commit `b23604d`, `docs/notes/montivo-reviewer-bench_2026-10-27.md`
"Round 3"): `review.py` gives every reviewer two measured facts from Voice on the cut (now run for every review, $0.002
per 40 s): kept edge words not heard (`edge_words_unheard`: only the words nearest the cut, a mishearing that keeps the
word is not a miss, and only where the source audio at the edge is over the clip's speech threshold) and the head out
of the 9:16 crop (`head_frame_facts`, the crop re-planned as the render does, over 40 % outside); Spark 1.3 also gets
the caption-vs-heard diff, not the heard phrases. `gate_check` blocks the pass on a loud edge, an unheard edge word, a
caption forced over the head or the head out of frame for 0.5 s: `review.pass` is gated (`review.reviewer_pass` the
reviewer's own, `pass_gate` the reasons), each fault becomes a major fix `plan_fixes` applies, the loop ranks (gate
clear, overall); reframe_bias now moves every span of the segment. Re-review of 28 bench cuts (spark13): faults caught
as major 59 -> 82 % (mid_word 2 -> 4/6, the 2 misses are 012's inaudible plant; framing 4 -> 6/6; caption_text 4 -> 5/6;
weak_hook 4/4), clean 012/015 gate false alarms 0, one clean review failed on the brief's missing line + "we edit" / "we
did"; faulty cuts still passed 41 -> 36 %: the rest are majors the reviewer passes at >= 80 (its rule only stops on a
blocker). The UI reads `review.pass`, so a gated cut now shows no pass without the reason (UI follow-up).
Pass rule (27/10 night, $0, Billy's choice): `review.close_gate` also holds the pass while the reviewer has a blocker or
major open (`PASS_IGNORES` = brief_covered, since 015's brief asks for a line never said); `pass_gate` gains `checks`
(measured), `open_majors`, `reasons` = both; the loop ranks on `checks`; the bench re-scores stored reviews with the
current rule at $0. Round 3 re-scored: faulty cuts passing 36 -> 18 %, clean 012/015 pass stays 75 % (commit `a2b041c`).
Gate in the UI (27/10 night, $0): `runs._review` serves `reviewer_pass` and `gate` {blocked, reasons, checks,
open_majors, issues numbered after the reviewer's}, origin fixes keep `check`; `GateNote` (web/src/Inspector.tsx) shows
"gate" with each reason tagged measured / reviewer and the reviewer's own verdict, in the review inspector and on the
Loop film cards; "Measured checks" issue cards (tag "measured") under the notes; the Loop film's fix lines read
"measured · ..." for a gate fix. Checked keyless on a throwaway copy of 015 with a written pass_gate (removed) (commit `6a5be1d`).
Bench mid_word in the audio (27/10 night, $0.21): `tools/reviewer_bench.py` `fault_mid_word` cuts 45 % into the source
audio's speech run of the last word (`Audio.speech_runs`), audible on every base (edges -23.7 / -30.0 / -22.8 dB); the
old variant's files are in `runs/bench/<base>/round3/` with its manifest entry, so `--compare` scores old reviews on
their own window. spark13 x2: mid_word caught as major 4/6 -> 6/6, none passes; whole set: caught as major 82 -> 91 %,
faulty cuts passing 18 -> 9 %, clean pass 75 %.
Dropped-word edges (28/10, $0.0003): 015's cuts v1-v4 opened "Okay, hi." (Voice on the cut heard it): Voice put the
dropped "Okay." 0.8 s early (18.54-18.78, audio 18.76-19.14, "Hi" 19.58), its 0.36 s of sound was over FRAGMENT_MAX_S, so
the clipped in edge widened to 18.71 in front of it. `edges.onset/offset(dropped=)`: when the word past the edge is not
in the cut the sound at the edge may be that word, up to DROPPED_WORD_MAX_S 0.6 s, and runs FAINT_DB (3 dB) or less over
the threshold (breaths) do not break the pause after it; only 015 s1 moves on every run folder. `edges.reset_edges` puts
every edge the pass moved (and nothing since) back on its Voice boundary; `python -m montivo.human edges RUN` = reset +
the pass again -> next edl_vN ($0). 015 v5 (from v4): opens "Hi." at 0.15 s, 37.9 s. `runs/015-demo` = a clone of 015
at v5 for live clicks with the key (commit 19f4a59).
First clicks with the key (28/10, `runs/015-demo`, $0.09, montivo-ui-key stopped after): "Try another hook" on v5 ->
challenger s8 (payoff cold open) v6 69 / hook 2 vs v5 82 / hook 9, v5 kept, $0.068; Ask Montivo "make it about 30
seconds" with Spark Open, no review -> v7 8 clips 34.5 s, $0.011, plan 14 s, callouts kept; Text panel new title words
+ card on -> v8 with a Muse Image card in the brand colours (ink, paper, marker yellow, words exact), $0.01. No console
errors. Found: (1) v5's pass held by a reviewer major captions_match_speech on "Four" (Voice on the cut does not hear
it, the heard-diff fact says so; Billy confirmed the word: a false positive) -> 015 v5 has no pass; (2) Voice heard
"Four Meta" as "meta meta": the Open re-plan started s5 on the second "meta" and the hand caption "Four Meta models do
all" did not carry (the line breaks differently), v7 shows "meta models do all" while the audio keeps "Four".
Hand-checked captions (28/10, $0.031): `review.checked_captions(edl)` = the caption lines a person retexted (a
`caption_overrides` entry with why "human", mapped to the line through cut_map); `caption_timeline` marks them
`checked`, `caption_heard_diff` keeps them in the alignment but reports none of their words, the diff fact lists them
as checked against the recording, `close_gate` lets a reviewer captions_match_speech major on one (the gate's
`checked_captions`, cut seconds, +-0.3 s) not hold the pass, and `resolve` refuses a caption_text on one. Re-review of
015 v5 (`runs/015/review_v5.json`): 87, pass (reviewer's own pass too, captions_match_speech 10), gate clear; the only
issue is brief_covered (commit bba47b9).
Hosted demo (28/10, Billy: Modal, a $15 cap on the Meta credits): `montivo/server/hosted.py` switches on from the env only
(`MONTIVO_PASSWORD` sign-in page + cookie, every route but /api/health and /login answers 401 or goes to /login;
`MONTIVO_BUDGET_USD` = spend since the host saw each run, from the ledgers, baseline in `<root>/budget.json`, seeded runs
adopted; a paid action needs its estimate left, the $0 edits never; `MONTIVO_ONE_JOB`, `MONTIVO_MAX_FOOTAGE_S` 95,
`MONTIVO_MAX_UPLOAD_MB` 400; the samples cannot be archived); the state carries `budget` and `key_note` (shown instead of
"set MODEL_API_KEY", Home shows what is left). `modal_app.py`: Debian image + ffmpeg, Volume `montivo-data`, secret
`montivo-keys` (MODEL_API_KEY, BASETEN_API_KEY, MONTIVO_PASSWORD; where the password is: `CLAUDE.local.md`), one container,
4 CPUs, scale to zero, a thread commits the Volume every 30 s; `modal run modal_app.py::seed` unzips /data/seed/*.zip
(`python -m montivo.server.sample pack runs/015 --footage`) and adopts them, `::budget` prints the cap, `::smoke` renders a
$0 drop on a copy (this image: 015 v6 34.3 s, -14.0 LUFS, 22 s, same as the Mac). Live (address in `CLAUDE.local.md`):
(012 v2, 015 v5, $15 left). A judge's own copy (Billy's go): sign-in sets a `montivo_judge` id cookie per browser; the
first edit on a sample (adopted, no `ui/owner`) copies it to `runs/<id>-<judge>` (`app.own`, through `.copying/`, the
copy adopted so its ledger is not host spend, a chat line says so), every mutating route goes there and the editor
follows `job.run`; `/api/runs` shows each judge the samples (or their copy) and their own runs (`ui/owner`); a judge
may archive their copy, never a sample. Not tried yet: an upload and a paid run through the live UI (needs Billy's
sign-in, about $0.30) (commit e64c156).
First live upload (28/10, Billy, `runs/ui-1027-2252` on the Volume: a TEDx clip, 84 s, multi-camera with wide stage
shots, the speaker small in them): upload -> run -> loop in the container, 209 s, $0.32, v1 72 -> v2 76, no pass.
Found: the reviewer's caption_text on a line where Voice doubled a phrase across the break ("I wrote down wrote | down
the letters of") retexted the first line and left the second, so v2 showed the words twice and the reviewer flagged
it. `captions.covered` / `apply_overrides`: a retext takes over the next line's leading words it already says (aligned
against both lines' heard words, same span only; a next line left empty goes); none of the 192 retexted lines in the
local runs changes. `modal run modal_app.py::rereview --run ID --version N` re-renders cut_vN with today's code and runs
the loop (stop the web container first and after: it does not see another container's writes). Live re-review: v2's
lines fixed, captions_match_speech 10, 74 with one major (hook); the loop's swap_hook put the brief's ending first ->
v3 48, the regression guard kept v2; $0.07; host spend $0.40 of $15 (commit 32e3416).
Codex review fixes (28/10, $0, brief in `docs/notes/codex-review-brief_2026-10-27.md`): Codex reviewed the repo
read-only (11 findings), each checked in the code first, all fixed with tests (209 -> 220): uploads never replace a clip
(`app.free_name`: clip-2.mp4, written to a `.part` first, the size limit no longer deletes the old file; the UI takes
the returned path) and on the host each judge sees the seeded clips and only their own uploads (`footage/.owners.json`;
run ids get a free name too); ingest bakes a display rotation (`Probe.rotation`, `make_upright`, `source_upright.mp4`,
phone portrait clips); the loop's `rank` puts a passing version first and a new version takes `human.next_version`
(reviewing v1 of v1-v5 makes v6); `apply_fixes` checks each structural fix against the EDL the earlier fixes of the pass
made (`review.conflict`, a skipped fix logged `skipped`, a pass with nothing applied stops "no applicable fix"); the
dropped-word rule needs the dropped word's own Voice window mostly quiet (`edges.displaced`, `DROPPED_HEARD` 0.5: a short
first word said where Voice put it stays) and edge searches stay inside the source (`Audio.inside`, `quietest` clamps);
Voice turn ids count only kept turns (unique across chunks); `run --resume` refuses other footage, another hflip or
missing track concepts (commit 36540de). No edge moves on any run folder (old vs new edge pass compared on all 16). README: the guard in
the one-liner, the "16 editors we looked at", the pass gate bullet, the benchmark and the TEDx upload rows, the first
two rows marked as scored before the gate, hook A/B and punch-ins with their real runs. Deployed to Modal 28/10 (smoke: 015
v6 34.3 s, -14.0 LUFS); the TEDx clip is deleted from the Volume (Billy, 28/10) and its run moved to runs/_archive/ui-1027-2252, so its
$0.40 stays in the cap; a judge now edits, archives, lists and restores only their own projects (commit abb969c) and reads only
the samples and their own (one middleware over /api/runs/<id>/..., another judge's run answers like a missing one, commit b780b56).
Caption styles + canvas (28/10, $0): EDL `look` (graphics.Look/normalize_look: 6 presets marker/clean/box/karaoke/
bold_pop/minimal, 5 OFL fonts in montivo/fonts + licenses/, colours, outline, shadow, box, words per line, case, active
word pill/color/karaoke/none, highlight_add/off, position auto|fixed + top), callouts in the look's font/colours, span
`callout_xy` pins a callout (placement "pinned"); a fixed caption is placement "fixed": gate_check skips it, resolve
refuses caption_move on it; an over-wide line shrinks to fit (`captions.fit_line`); replan carries look + pins (by callout
text). `python -m montivo.human style RUN --look JSON --callout s5=X,Y|auto`, POST /style, GET /api/looks, POST /preview
(`engine/look.py`: source frame cropped by `render.plan_paths` + build_overlays draw_at, 0.4 s, ui/cache/preview/).
UI: Style panel (rail), preset cards on real frames, preview still on the paused viewer, drag handles (StyleCanvas: the
caption up/down -> fixed, a callout anywhere -> pinned, the face area shaded, snaps to centre / safe area / clear of the
face). Checked keyless on a throwaway copy of 015 (callout pinned above the head, v6 rendered in 19 s; removed).
Export page (28/10, $0): `web/src/Export.tsx` replaces the Export dialog (cover frame + grid 3:4 crop, file name,
destination full / 720, include video / clean / srt / vtt / cover / audio / transcript, settings in localStorage);
`python -m montivo.export RUN --version N --out DIR --include ... [--preset 720] [--cover-t S]` (clean = render_run with
captions=False from a copy of the EDL, own ledger, no log, title only when title_ready; nothing in the run folder
changes; zip when more than one file); POST /export/bundle (ui/cache/exports/<key>/, last 3 kept, one at a time, 409
for clean while a job runs), GET /export/check -> `server/preflight.py` (review + gate, loudness, format, length vs
Reels/Shorts 3 min and TikTok 10 min, safe area per app from SafeArea.tsx zones, right column only in the lower half,
captions over the face). On 015 v5 the pre-flight says 25 of 28 lines meet TikTok's caption (bottom 1480).
Style by prompt (28/10, $0.026 incl. one incomplete call): `montivo.human style RUN --prompt "..."` / POST /style
{"prompt"} / the Style panel's Describe it box -> `look.suggest`: one muse-spark-1.3 call (strict schema of every Look
field + why, reasoning low, 6000 output tokens: 1500 came back incomplete), facts = current look, `look.backdrop` (luma
behind the caption rows), `look.face_note` (median head rows from the EDL); `settle` clamps / keeps bad colours,
a fixed top on the face -> auto. 015-style v8: upper, 2 words, yellow, auto from 1200, off the face; $0.008-0.010 each.
Saved looks (28/10, $0): GET /api/looks `saved`, POST /api/looks/saved {name, look} (same name replaces, 24 max),
DELETE /api/looks/saved/{id}; `<root>/looks/saved.json` (git-ignored; per judge on the hosted demo); "Your looks" row
+ Save look in the Style panel.
Spark cover (28/10, $0.018): `engine/cover.py` (prompt on the review copy with the clock, the review's file_id reused
else a new review copy uploaded, moments outside the cut or on the title dropped, `settle` = sharpest frame +-0.25 s by
edge variance in the face band), `python -m montivo.export RUN --version N --suggest-cover` -> `cover_vN.json`,
GET/POST /export/cover, "Let Muse Spark pick" + moment chips on the Export page. 015 v5: 1.9 s "Hi. I'm Billy.",
18.4 s "Four Meta models" callout, 22.5 s.
Feed 4:5 and Square 1:1 (28/10, $0): `engine/frame.py` (EDL `target.aspect` 9:16 | 4:5 1080x1350 | 1:1 1080x1080,
`frame_for(edl)`, `with_aspect`); `plan_paths` / `review.crop_paths` pass the aspect to `reframe.plan_segment` (crop full
height, narrower; a source narrower than the aspect keeps its width, `crop_size` cuts the height and `SegmentPath.crop_y`
puts the head centre 0.4 down, `crop_top`); render, title card, zoompan, look.frame_at/preview take the frame; captions
scale every stored y by h / 1920 (`Frame.y`: CAPTION_TOP, look top, forced top, pinned callout), x kept, and a feed frame
keeps a 6 % margin (`Frame.band`) instead of the Reels band (with it every 4:5 line of 015 v5 went above the head), plus
an `above_head` callout spot on feed frames only. 9:16 is bit-identical (015 v5 framemd5 of every frame + audio md5,
previews, EDL fields before/after). `python -m montivo.export RUN --version N --aspect 4:5|1:1` renders `video` and
`clean` again from an EDL copy (`export.render_copy`, files `-4x5` / `-1x1`), the cover is `look.preview` in that frame,
the grid crop the 3:4 middle (`grid_crop`); POST /export/bundle and /preview take `aspect`, GET /export/check `aspect`
(`preflight.checks`: feed format row, one safe-area info row instead of the app rows, the face rows from
`look.layout`, no render). Export page: Feed 4:5 and Square 1:1 cards, cover still per aspect, grid bands at the sides.
Checked keyless in the browser pane on a throwaway copy of 015 (removed): 4:5 28 lines below the head, callouts above it;
1:1 27 below, 1 above; the 1:1 bundle in 12 s.
Music bed (28/10, $0, merged from branch `music-bed`): EDL `music` = {"track", "level_db" -20, "duck_db" -12} (absent = none,
`engine/music.py` normalize_music); eight CC0 tracks from OpenGameArt in `montivo/music/` (chill_lofi, lofi_hip_hop,
funky_jam, midnight_cruiser, apple_cider, ukulele, raspberry_jam, cup_of_tea; AAC .m4a loops 55-97 s, `tracks.json` with
LUFS, author, source page; README there). Downloaded on Billy's go (28/10, he rejected a first set of synthesised loops;
FreePD.com is closed); `montivo/music/build.py DOWNLOADS_DIR` cuts them (lead-in trimmed, loop point by onset-envelope
match to the first 8 s, 0.25 s crossfade; Chill Lofi is a loop edit kept whole). No model. `render_audio` keeps the normalised speech as a
float WAV, renders the bed (looped, gain to level_db, a `volume` expression ducking along the Voice words in cut time:
-0.15 s attack, +0.45 s release, pauses < 0.8 s stay ducked; audio.wav speech runs without words), mixes, normalises the
mix again and measures the AAC file's own true peak (the first mix: -1.95 dBTP PCM, -0.5 AAC), re-running with a lower TP
up to 2 times (`render.loudnorm` encoded_tp / tp_retries, the speech pass in `loudnorm.speech`); `render.music` records the
bed, the ducked stretches and `measured` (speech vs bed RMS: margin_db). The speech stem stays as
`render/<cut>.speech.m4a`: `review.speech_audio` gives it to silencedetect and Voice on the cut, and the facts tell the
reviewer the music is on purpose. `python -m montivo.human music RUN --track ID [--level] [--duck] | --off` -> next
edl_vN; review patches and hook keep it (deep copy), replan carries it. GET `/api/music`, `/api/music/{id}` (the loop for
the preview), POST `/api/runs/{id}/music`; the Music panel in the rail (`web/src/Music.tsx`). Run 015 copy (deleted):
one version per track at -20 / -12: -14.0 LUFS, -1.7 to -2.0 dBTP, speech 15.8-17.8 dB over the bed; 015's cut never
pauses 0.8 s, so the bed stays ducked throughout. Checked keyless in the browser (track change, music off, previews);
Billy listened to all eight under his 015 cut and kept all eight (commits f1dd64b, 2a47ff5). Merged into main as 8322446 and
deployed to Modal 28/10 with the 4:5 / 1:1 exports (`modal run modal_app.py::smoke --music midnight_cruiser`: 37.9 s,
-14.0 LUFS / -1.7 dBTP, speech 17.1 dB over the bed, same as the Mac; the plain drop smoke 34.3 s, -14.0 / -1.7); host
spend unchanged at $0.40 of $15. After a redeploy the old web container keeps serving (its SSE `/events` streams
never close) and, with max_containers=1, the new version never starts: every request stayed Pending from 12:54 on
28/10. After every `modal deploy`: `modal container list`, then `modal container stop` the web container that started
before the deploy. The judge copy on a sample now reads the body first and copies in a thread (a11fa77): the first
edit used to fail with a 408 while the 13 s copy of 015 ran.
Chat, transport, icons (28/10, $0, Billy's pick from the mockup in `docs/notes/ui-chat-transport-icons_2026-10-28.html`):
the Assistant reads as a chat (`web/src/Assistant.tsx`: your bubbles right, Montivo / Reviewer avatars, tool lines folded
into one "Applied 2 fixes" row, version cards with poster / Open / Compare, review cards with the score ring and the worst
note as a seek link), the agent's live work in a tray glued above the input (step, clock vs expected, bar, steps done,
Stop; idle = the pipeline checklist folded), one composer box (context pills, planner menu, Review toggle, send that turns
into Stop, suggestions from the last review); ProgressCard is gone. Transport (`Player.tsx`): Inter tabular timecode
`00:13.60`, click for frames `00:13:18` (localStorage), one icon family in a pill, right side speed 1/1.5/2/0.5, loop the
cut, mute (M), safe area (the "Safe area" chip shows only while on), full screen (F). Icons: one stroke set
(`icons.tsx`, `CRIT_ICON` per criterion), track headers, Timeline / Transcript tabs, clip tools (loop, Ask, Delete) in
the timeline bar, inspector heads and sections. No eye / lock / split / snap: nothing in the engine backs them. Checked
keyless on 015 and replay 4x; the composer and suggestions with a key not clicked yet.
Waiting view (28/10, $0, commits `b9674af` then `4ac96b0`, Billy's pick A from
`docs/notes/wait-mark-options_2026-10-28.html`): while no version has a cut (a new project's first run, or a replay
before v1) the viewer is `web/src/Waiting.tsx`: the Montivo mark doing the logo sting's motion on a loop (in/out points
close in, the V fills down, holds, opens), the running step's title with a shimmer and one sentence on what the step
does (`STEPS`, keyed by `runs.progress` stage). Billy rejected the first version (footage, captions and chips behind):
no video there. Edits on a project with a cut keep the player. `modal container stop` needs `-y` from Claude Code.
New-deploy check (28/10, $0, commit `9d74697`): `web/src/main.tsx` reads index.html every minute and when the tab comes
back into view; a bundle name other than its own reloads the page at the next route change and shows a Reload pill until
then (none on the Vite dev server). Live as Modal v12 = 15d14e0 + 9d74697, built in a clean worktree: the Cover maker
(b214930) is on main but NOT deployed yet; the next `modal deploy` from main ships it. A tab loaded before v12 has no
check and needs one manual reload.
Stale page after deploys, the real cause (28/10, commit `6d15e28`): Modal's image stamps every file 1970-01-01 and
index.html is always 1066 bytes, so Starlette's ETag never changed and a revalidating browser got 304 and kept its old
page (and old bundle) through reloads. `WebFiles` now never answers 304 for HTML and drops ETag / Last-Modified. Live as
Modal v13 (15d14e0 + 9d74697 + 6d15e28), then v14 (14:26, + 4ac96b0's web/src), both still without the Cover maker. Before `modal container stop` after a deploy,
check no job is running (the newest runs' run.log on the Volume): stopping v12's container cut Billy's ui-1028-1209 in
its review_v1 call (cut_v1 kept, no review).
Cover maker (28/10, $0.011, Billy's pick A + D + E after research in `docs/notes/cover-maker-*_2026-10-28`): the cover was
a raw frame of cut_vN with that second's half caption. `engine/thumb.py`: templates `frame` (clean still), `headline`
(face punched in, ink gradient, headline with a marker word), `sticker` (tilted marker sticker, edges darkened), `cutout`
(giant Anton headline behind the head, the person laid back over it from SAM 3.1's mask of that one still,
`still_mask`, one image request, lossless mask, cached in ui/cache/cover/), laid out around the caption layout's head
box, text kept in the 3:4 grid window and off the face; measured checks (in grid, off face, cap height on a 130 px
tile, cutout text visible >= 72 %, 2-6 words). `design` = the agent: cover moments (cover.suggest, reused), 18 clean
stills around them in a numbered contact sheet -> Spark 1.3 picks the still + 3 headlines + template (`Spark.call(images=)`,
inline data URLs, works on 1.3), render, Spark checks the cover and its tile, one fix (shorter text / other template /
other still). `python -m montivo.export --design-cover | --cover-mask --cover-t S | --cover-design JSON`; POST
/cover/design, /cover/mask, /cover/render ($0), bundle `cover_design`. UI `web/src/CoverMaker.tsx` in the Export page.
First real design (015-demo v5): still 1.54 s smiling, "AI That Fixes Itself", headline, pass at once, $0.011.
The mask decodes with Meta's `meta-sam-parser==0.1.0` (Billy's yes, 28/10; in pyproject and modal_app). First real
mask (015-demo v5 at 1.54 s, $0.010, 2.4 s): one person, clean edge; the cutout keeps every word at least 80 % visible
(`WORD_VISIBLE`: "Self-reviewing AI" used to hide "AI" whole behind the head while 89 % of the pixels showed).
Deployed to Modal 28/10 (commit b214930 + the smoke option): `modal run modal_app.py::smoke --cover` renders the
newest version's headline and sticker covers on a copy ($0) and imports the parser; old web container stopped.
Ask Montivo agent (28/10, $0.013): the chat used to send every message to `human.replan` (the plan schema has no music,
look or punch fields, so "put music" only re-planned). `montivo/agent.py` (`python -m montivo.agent RUN --message ...`,
POST `/api/runs/{id}/ask`, the composer now posts there): one muse-spark-1.3 call (strict json_schema, no video,
$0.0035-0.0052 on 015) reads the message next to the version (`agent.context`: clips with cut seconds and callouts,
caption lines, title, look, music, punch, the last review) and picks ops from a closed list (music_on/off, style preset or
look_prompt, punch, caption, callout, title_text/on/off, drop, reorder, replan, review); `check` validates ids and
orders them (caption first, a replan drops the clip ops), `run_ops` runs the human edits with `human.DEFER` set
(unrendered edl_vN each), `fold` folds the chain into one version with one patch (`action` the op's own or "ask",
`agent` {message, reply, router, ops, done, skipped}), rendered once. A question gets a reply and no version (chat line
kind "ask" with `after`). Without the key (or past the hosted budget) `keywords` reads music / track names, punch,
preset names, title on/off, $0, no review. UI: the agent's message, reply and steps in the chat, "asked Montivo".
Checked on a throwaway copy of 015 (removed): "Βάλε μουσική, κάτι χαλαρό, και κάνε τα captions πιο έντονα" -> Chill Lofi
+ Bold pop in one render (22.8 s); a score question answered with no version; keyless in the browser: ukulele + karaoke.
Commit 4f4d601, deployed to Modal 28/10 14:56 (the last job, 015-dd516021 v9, had finished; the old container was
already stopped); smoke 015 v6 34.3 s, -14.0 LUFS / -1.7 dBTP; host spend $2.46 of $15. Not yet asked live with the key.
Music track + live faders (28/10, $0, Billy's ask, commit 13f29a3): the timeline has two audio lanes, Voice (the cut's
audio, or its speech stem when there is music) and Music (the ducked bed, its track name opens the Music panel; "Add
music" when none), both waveforms on the voice's scale. Each head has a fader: Voice = new EDL `music.voice_db` (-12..+6,
kept only when not 0, applied to the speech before the mix), Music = `music.level_db`. Moving one plays the version's
two stems through Web Audio gains on the video's clock (`web/src/Mix.ts` `useLiveMix`: video volume 0, restart on
seek / speed / drift over 80 ms) with the -14 LUFS normalisation simulated (`liveGains`, from the render's margin), so
raising the voice brings the bed down as the export will; "Make Version N" -> POST /music {voice_db, mix: true} ->
`montivo.human music --voice`. With no music the faders are off (one track, nothing to balance). The render now keeps
the bed as `render/cut_vN.music.m4a` (`render.music` `music_stem`, `voice_db`); older versions get it rebuilt into
ui/cache/ on the first GET `/api/runs/{id}/stem?v=N&track=voice|music` (`render.music_stem`, bit-identical to the
render's); GET /peaks takes `track`. Checked keyless on a throwaway copy of 015 (removed): live stems start at the seek
point, restart at 1.5x, v7 = level -16 / voice +3, -14.0 LUFS / -1.8 dBTP, margin 16.9 -> 15.9 dB. Deployed to Modal
28/10 15:15 (no job was running; old container stopped); smoke `--music midnight_cruiser` 37.9 s, -14.0 / -1.7 dBTP,
margin 17.1 dB, same as the Mac. The live faders not yet moved on the hosted demo.
UX batch A (28/10, $0, commit `3a1557e`, research in `docs/notes/ux-research_2026-10-28.md`): under 900 px the editor is
a review mode (player, review, chat, export; no rail or timeline) and Home one column; review notes say the fix in
words (`edit.fixText`, the op in the tooltip), LUFS / facts / review cost in a Details fold, the chat's review card
names the weakest criterion; timeline clips show the caption text (`edit.spanText`), not what Voice heard; the tab
title carries a job's % and a done mark when it ends hidden, plus a browser notification if allowed
(`Progress.useJobSignals`, `askToNotify` on run / ask / review / hook); the waiting view says the tab can close and its
loop can pause (WCAG 2.2.2); on the hosted demo Home opens with a Start here card and the composer shows the budget left.
Checked keyless in the browser pane at 1440 and 375 px; the tab title seen on a live $0 job (5 % -> 80 % -> done).
Engine hygiene (28/10, $0, commit `efba5bf`, side session, batch 1 of `docs/notes/engine-editing-research_2026-10-28.md`):
x264 veryfast (015 v5 render 18.4 -> 9 s, 26 -> 18 MB), every segment and the title tv-range BT.709 and tagged
(`render.source_color`, EDL `render.color`), overlays to YUV with BT.709; glossary and highlight words per run in the EDL
(`glossary`, `highlight`; new runs empty, older EDLs keep the demo words; punch-ins follow the look's highlight_add/off);
atomic writes (`.part` + rename: dump_json, ledger, cut_vN.mp4, budget.json); back-off on 429 / 5xx with Retry-After,
Voice chunk rerun on a broken stream, SAM asked again when no frame came, Spark one retry on incomplete / bad JSON;
`review_loop` writes loop.json when a pass fails; ingest ffmpeg stderr reaches run.log. Throwaway re-render in
`runs/015-hygiene/cut_v5_hygiene.mp4`.
Brief vs footage (UX A3, 28/10, $0.096, commit `6fd517f`): the plan schema has `brief_not_in_footage`; `build_edl` keeps
it as `brief_missing` after `edl.brief_missing` drops any item whose content words are mostly in the kept spans (the
planner reads Voice's transcript: on 015 it listed "SAM tracks me" and "four Meta models", misheard but kept); the
reviewer gets it as a fact about the source (`facts_text(not_in_source=)`), the chat says it once under the planned
version. On a copy of 015 (removed): re-plan + review 100 pass, no brief_covered major. Old EDLs have no list, so 015
itself still shows the major until a re-plan. UX batch A, engine hygiene and A3 deployed to Modal 28/10 15:52 (no job
was running, last run.log write 15:19; the 15:15 web container stopped); smoke drop on 015: 34.3 s, -14.0 LUFS /
-1.7 dBTP, render 17.2 s (was 22 s), 16.6 MB. The new UI on the live URL not yet opened (needs the sign-in).
Restore (UX B1, 28/10, $0, commit `685c263`): `python -m montivo.human restore RUN --from N` copies edl_vN forward as the
next version, renders it and carries review_vN to it (`carried_from`), POST `/restore`; a Restore button on older version
cards in the chat and under the selected version in Versions. Checked on a copy of 015: v3 -> v6 in ~9 s, 87 carried.
Share links (UX B4, 28/10, $0, commit `a34a521`, `montivo/server/share.py`): POST `/api/runs/{id}/share {version}` ->
`/s/<token>` (`<root>/shares.json`, git-ignored, the same version keeps its token): a read-only page with the cut, score,
the loop's score history, criteria, top 3 notes (click to play), Open Graph / Twitter tags and a 720 px poster; the token
reaches only that version's video and poster; `/s/` is an open path on the hosted demo, creating a link needs the
sign-in (checked with a local hosted server: /s 200, video Range 206, /api 401). Share button in the top bar. No
Unshare yet.
Editing batch 2 (28/10, $0, side session, commits `373b049` + `5a03755`): split at the playhead (S / Cmd+K, `human
split --span --word`, POST /split; same render, the second piece selected), snapping on trim / move (playhead, clip
edges, issue markers; N toggles, Shift free), the 8 editor bugs of the engine research, multi-select (Shift / Cmd click,
Backspace -> one /cut), keys Up/Down prev/next cut, [ ] select left/right, Cmd+A, next issue `.` / `,`, mute Shift+M,
undo position kept in `ui/current.json` (localStorage on hosted samples), a fork after undo is said aloud.
Segment cache (engine batch 3, 28/10, $0, side session, commit `bd0778a`): `render/cache/` keyed on the ffmpeg args with
inputs replaced by their content (source path+mtime+size, sendcmd text, overlay PNG bytes, the ffmpeg build), hard
links on a hit, LRU of about 4 renders, `MONTIVO_SEGCACHE=0` turns it off; segments over 8 s split into 4 s chunks (not
punch-in segments), parallel encodes (4 workers on the Mac, 2 on Modal, x264 threads fixed per machine and in the key),
audio in its own thread and cached; `-movie_timescale 90000` fixes a sub-ms shift at every concat edge. 015 v5 on the
Mac: cold 8.8 s, unchanged 0.85 s, one caption edit 2.4-2.7 s (was ~10 s); cached = uncached byte for byte.
Export cover fix (28/10, commit `de893e3`, made in the day-three video session, committed on Billy's deploy go): the
cover CLI branches send the run log to stderr, so stdout is the one JSON record the server parses ("Design it with Muse
Spark" answered 500 although the cover was made). B1, B4, batch 2, batch 3 and this fix deployed to Modal 28/10 16:33
on Billy's explicit go (the auto-mode classifier refuses `modal deploy` without it), from a clean worktree of main at
de893e3 (the checkout had uncommitted work of another session); no job was running (last run.log 16:21), the 16:00 web
container stopped; /s/nope 404 and /api/runs 401 live; smoke drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP, render 9.0 s
cold on Modal (was 17.2 s), 16.7 MB. Next batches: `docs/notes/next-batches_2026-10-28.md` (C chat, D add back / pauses / overlays,
E publish pack, F quality).
Batch C, chat as a collaborator (28/10, $0.034, side session, commits `d577d5a` + `7e24b75`): Ask Montivo previews a
structural or > $0.02 change (re-plan, drop, reorder, a new `cut` op for words inside a clip) as a chat card ("8 -> 6
clips, about 25 s, s3 goes") with cost and time, Apply / Try something else (one more router call, ~$0.005-0.009);
proposals wait in `ui/pending/<id>.json`, a re-plan is planned once, on Apply; music, style, captions apply at once.
Selection = scope (multi-selected clips, Transcript words, a caption line), ops outside it refused. A message typed
during a job waits in a queue and goes on the newest cut when the job ends ("Send now" stops the job); a failed or
stopped job gets "Try again" and "Nothing was charged." when the ledger did not grow. Unshare: GET /shares, DELETE
/share/{token} (403 on a hosted sample, commit `37514fd`). Publish pack: `python -m montivo.export --post-text` (one
Spark call, ~$0.01) -> `post_vN.json` with title, description, hashtags for Reels, TikTok, Shorts, LinkedIn; "Post text"
tabs + Copy on the Export page, `post.txt` in the zip. Left: a stopped render leaves an edl_vN without a cut in the run.
Batch D, edits (28/10, ~$0.08, side session, commits `a7e8ac6` + `a897aa1`): `human insert RUN --words TURN:FROM-TO
[--before sN | --end]` / POST /insert puts back an unused passage (source order or before a clip, every word once, two
words min, joins a neighbour at the dip); the Transcript's "Not in the cut" words (Insert / At the playhead) and ghost
clips ("Bring it back"). `human pauses` / POST /pauses: pauses found in the audio (Voice words abut), cut at the nearest
word edge, no word lost; 012 v2: 2 pauses (1.01, 0.90 s), -1.31 s; a "N pauses, -X s" button, marks on the video track;
the reviewer may extend a dramatic pause back. B-roll stills with Muse Image (`human overlay`, POST /overlay, EDL
`overlays[]`, placements full / card / corner, under the captions with a short fade, tied to their words, a B-roll track
with a prompt box; move / resize / placement / remove $0); Ask Montivo adds one only when asked. 015 v5 without overlays
renders byte for byte as before. Left: the Text track still shows a callout a full still hides; the Style preview
ignores stills. UI build of both batches: commit `492d324`. Deployed to Modal 28/10 17:18 on Billy's go, from a clean
worktree of main at 57298c0 (the last job, a hook A/B on 012-dd516021, ended 16:52; the 16:33 container stopped); live
/s/nope 404, /api/runs and /shares 401 without the cookie; smoke drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP, render 8.2 s.
Batch F, picture and sound (28/10, $0, commits `fe4c27b`, `6c5dbe5`, `4d36bdc`, `441387e`): E13 every cut
is a 20 ms equal-power crossfade centred on it over 10 ms handles (`render.cut_graph`, length kept): the deepest 2 ms
window at 015 v5's cuts 26-31 dB under the room tone -> 10-12 dB (uncut room tone dips 9-22), -14.0 LUFS. E12 the
look's `line_breaks` greedy | phrase (`captions.phrase_breaks`, cheapest breaks: no line ending on and / a / in, pauses
preferred); build_edl writes phrase into new EDLs, old ones keep greedy, a style edit keeps the version's
(`graphics.restyle`); function-word line ends 015 5 -> 0, 012 7 -> 0, 014 11 -> 3, 013 12 -> 3. E8 zero-phase Gaussian
(sigma 0.13 s) per window, 1 px x, `crop exact=1`: max pan accel 7200 -> 1800 px/s^2 (015), 9000 -> 1800 (012), framing
unchanged. E10 a window the head track misses follows the person track, else the previous centre, then the middle
(`SegmentPath.fallbacks`; `render.reframe_inputs` / `plan_paths` now plan for render, review and look.py alike).
E11 `ingest.detect_shots` (scdet 10 on the proxy; talking heads peak 3.3, cuts ~28) -> ingest.json and EDL
`source.shots`, pieces split at cuts, no hold across one; dissolves are not caught. E9 `ingest.standardise`: VFR, odd
fps (DJI 600/19 -> 30), > 60 fps, 10-bit, HDR (zscale + hable where it exists, HLG via colorspace here, PQ refused
without zscale; `modal run modal_app.py::smoke` prints which). E15 an end-to-end `render()` test and `human.drop`
(dropping the hook used to write an identical version, now refused). 015 v5: E12 / E13 leave the video bit-identical,
E8 changes 368 of 1138 frames (the pans), E10 / E11 change nothing on any local run. 384 tests. Deployed to Modal
28/10 17:51 on Billy's go, from a clean worktree of main at 8a69087 (the last job, ui-1028-1227's review loop, ended
17:42; the 17:18 container stopped); the image HAS zscale (HDR HLG / PQ tone-mapped live); smoke drop on 015: 34.3 s,
-14.0 LUFS / -1.7 dBTP, render 8.6 s; /s/nope 404, /api/runs 401, /api/health 200.
Debugging day 1 (run 28/10 evening, $1.67, commits 7245d63 2203aac 91b7a1a 1d82215 7a45609 2a90982, findings in
`docs/notes/debugging-day1_2026-10-29.md`): `tools/combo_check.py` ($0) runs chains of human edits through the CLI on
an APFS clone of `runs/016` and checks every version (edl + cut, duration within 2 frames a segment, -14 LUFS / TP <= -1,
every word once and none deleted coming back, untouched fields, segment cache = `MONTIVO_SEGCACHE=0`, exports 9:16 / 4:5
/ 1:1 with music and a placeholder B-roll): 6 chains, 44 versions, 0 failures after the fixes, cache byte-identical everywhere. Fixed with tests (384 -> 409):
a stopped or failed job's unrendered edl_vN moves to `stopped/` (server `set_aside_unrendered`; `human._finish` and the
review loop drop an EDL whose render failed); a re-plan keeps a carried caption text when its line starts a word later
(anchor in the middle of what the new plan keeps, splice into a longer line: 015-demo v7 reads "Four Meta models do all"
again) and keeps an old EDL's greedy line breaks; insert never widens into unselected words; split keeps an override by
its word; cut refuses a refused drop; Apply refuses a proposal made on an older version; a look from words goes first
in an Ask Montivo chain; the signed judge cookie (`<id>.<HMAC>`), archived ledgers keyed `_archive/<id>` (a second copy
after archiving the first spent uncounted), Try again priced by its args, paid calls in requests one at a time, no job
while an export renders; segment audio padded to its exact length (`apad`, normal renders md5-identical); camera PCM to
ALAC in the upright copy; E11 also takes a cut over 5 and 25x the clip's own p95 (the TEDx stage scored 7.5-8.9); edges
never widen or settle into another span's source time in any turn (NASA turns played 0.2-0.44 s twice, 014 too) and a
join on the same source second is not a gate cut. Real runs (today's code, `runs/d1-nasa`, `d1-usgs`, `d1-tedx`; TEDx
clip pulled from the Volume): no pass (NASA 58 -> 66, after the fixes 62: the astronauts talk over each other at turn
changes, not a sample for run 017; USGS 68; TEDx 62 -> 70). Open for day 3: the clean export's punch-ins and pip, an
upload quota per judge (needs Billy's number); Nice ones on the task board. The judge cookie change needs the deploy; the
iPhone HDR clip was not in ~/Downloads (day 2).
Debugging day 2 (28/10 evening, $1.12, commits ac581c4 7c0d9ba d442e5a f5c96c7 c6e9b4c 2a3115f 898c7e8 fdb04c5
544ae75, findings in `docs/notes/debugging-day2_2026-10-29.md`): the judge checklist walked in a local hosted-mode
server (password, budget, ONE_JOB, limits, key) over clones of 016 and 014, two judges at 1440 and 375 px; 410 -> 419
tests. Fixed: (Must, live on Modal) look.preview / look.layout read a `concept` Batch F stopped setting: Style
preview, every cover render and the 4:5 / 1:1 pre-flight answered 500; (Must, live) under 900 px body's overflow
hidden reached the viewport, so nothing scrolled on a phone; a drop that tightens the next clip's in edge into its
first word lost that line's hand text (CWord.t0, "Four Meta"); an edit refused by ONE_JOB first copied the sample;
the upright copy is encoded veryfast and no taller than 1920 (90 s iPhone 4K HDR: 479 s; 10 s clip 30 -> 9.4 s); a
phrase Voice heard twice back to back shows once ("You have You have"); a version shows its music only once the
render recorded the bed (stems 404'd in between); a refused $0 edit says why in the chat. Uploads (Billy's pick):
MONTIVO_MAX_UPLOAD_MB 1000 on Modal, the state carries `limits`, Home checks size and length before the upload, the
server refuses a Content-Length over the limit unread. Billy's iPhone clips: the audio is the RØDE lav (L = R, mono
over USB-C); clip 1 tone-maps clean (skin right, captions under the face, run 70 -> 74); tips take 2 (future 017) is
160.5 s with a second ending and Greek at the end, so the sample needs a trim to 90 s, and its planner cut dropped the
payoff (28 s). Not deployed: day 3 ships the fixes with Billy's go.
Debugging day 3 (28/10 night, $0, commits 5819589 4ada665 6950547, findings in `docs/notes/debugging-day3_2026-10-29.md`):
the clean export (no captions) now lays the caption lines out without drawing them (`build_overlays(draw_captions=
False)`, render_run always loads the Voice words, the clean EDL records 0 lines), so its punch-ins, pip and callouts
match the video (012-open v4: 9 / 9 punch-ins, both callouts, same pixels outside the captions); an upload over
MONTIVO_MAX_FOOTAGE_S is probed and refused before it lands (no quota slot), `/api/health` names no root on the host; a
new project's brief bubble reads the job's chat line until run.json lands, no suggestions before v1. The chat queue
was never lost (localStorage per project, sent on return); phrase break on "I", Text track / Style preview vs stills
Parked. 419 -> 423 tests; combo_check on a fresh clone of 016: 44 versions + 6 exports, 0 failures; 016 v3 re-rendered
at HEAD and at 8a69087 (then live): every frame and the audio identical (vs the stored 17:22 cut_v3: 373 frames of the
22.8-35.2 s pan differ from E8, the audio from E13). Deployed on Billy's go in this session (the classifier refuses a
go relayed from another session) as Modal v22 = 6950547 at 22:26, from a clean worktree; no web container was running
before it; live /api/health 200 (no root, upload 1000 MB), /api/runs 401, /s/nope 404, host spend $2.72 of $15; smoke
drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP, render 8.9 s; `--music midnight_cruiser` 37.9 s, -14.0 / -1.7, margin 17.1
dB; `--cover` headline and sticker pass every check (zscale yes). Billy checked it live (28/10): phone scroll on Home
and the review mode, Style preview (desktop only: the rail is hidden under 900 px) and Export cover work. Tag
`freeze-2026-10-31` on c545d70 (his go): from here only what breaks the demo changes.
Second Codex review (28/10 night, $0, brief `docs/notes/codex-review-brief-2_2026-10-28.md`, read-only, partial: it
did not get to paid-action admission, Ask Montivo chains or stopped renders): 3 musts, each checked in the code and fixed
with a test that fails on the old code (commit `34e876b`, 426 tests): judge cookies are signed with a server secret
(`hosted.judge_key`: MONTIVO_JUDGE_SECRET, else `<root>/judge.key`, made once, 0600, git-ignored), not the password every
judge knows; the upload's ffprobe runs in a worker thread with a 20 s timeout (`UPLOAD_PROBE_S`, a hung probe used to
hold the event loop); `share.LOCK` around every shares.json write (an Unshare during a Share came back). README: caption
fixes are in the Captions panel too. Deployed on Billy's go at 23:33 from a clean worktree (last job 012-f41e1e7a ended
23:22; the 22:26 container stopped); `judge.key` made on the Volume, so every judge cookie from before is void (new id at
the next request; Billy's own test copies no longer show for him); live /api/health 200, /api/runs 401, /s/nope 404,
host spend $2.79 of $15; smoke drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP, render 8.3 s.
Codex follow-up review (28/10 night, $0, brief `docs/notes/codex-review-brief-3_2026-10-28.md`: budget admission,
Ask Montivo chains, stopped renders): 3 musts + 2 shoulds, each checked and fixed with a test (commit `3b903fe`, 432
tests): request paid calls (cover, post text, mask) and engine jobs share one slot (`State.paid`, `refuse_beside_paid_call`;
on the host none beside any job; the mask route now waits for a job on its run); jobs run in their own process group,
Stop SIGTERMs the group, `end_group` SIGKILLs it after 5 s and the slot frees only then (the ffmpeg encodes used to keep
writing into render/); a stopped paid job holds up to $0.05 on the hosted budget (`stop_hold`, `Budget.hold`,
budget.json `held`); the hosted server sets aside at start the unrendered edl_vN past each run's newest cut (a container
stop never ran the job's clean-up); Ask Montivo refuses a re-plan while part of the cut is selected. Deployed on Billy's
go at 23:46 (no job since 23:22; the 23:33 container stopped); live /api/health 200, /api/runs 401, /s/nope 404, host spend
$2.79 of $15; smoke drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP, render 10.1 s.
Cloud review patches (29/10 evening, $0 Meta, commit `6f2f180`): of the 23 patches the cloud sessions left in their notes, 20
went in after a check on the Mac: render reads one input per segment, so a clip moved earlier keeps its audio on
ffmpeg 7.x (a reorder of 015 on a static 7.1: the old code failed, measured_I -inf; now audio = video; on 9.0 015 v5
audio and frames byte-identical, same render time); ingest refuses cover-art audio, no-duration files and evens an
odd height (old code: a 90000 fps "video", KeyError, ffmpeg exit 187); hosted review passes 0..3, key / budget checked
before a sample copy, priced stop holds, Ask fallback on the last cents, exact cents, nan / inf 400; title card cache
after success; SSE streams end after 300 s and reconnect, serve stops 5 s after SIGTERM; `modal run
modal_app.py::smoke --reorder` (passed on Modal with the new image); `constraints.txt` (make dev, Modal image);
UI: Music lane, Space on buttons, modal focus + Esc, contrast, reduced motion, 375 px bar, no agency name in the Style placeholder; CLI
--from checks. Each new test fails on the old code. Left out: 1a (cover / post / share on a sample would answer "make
any edit first"), 3d / 3e (iOS only, no Xcode here). Still on ffmpeg 7.1, old and new: a clip that runs past the end
of the audio stream and is moved first renders no audio (rare). Deployed on Billy's go 29/10 at c0f2046 (no
container was running before it); Modal's image has ffmpeg 5.1.9 (Debian 12); live /api/health 200, /api/runs 401,
/s/nope 404, host spend $2.79 of $15; smoke drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP, render 9.8 s; smoke
--reorder: audio 37.333 s = video, the moved clip -15.8 dB.
Live preview editing (29-30/10, $0, Billy: into the demo and the submission despite the freeze; merged as `5876f4d`): the editor plays a version or a draft EDL from an edit proxy without a render. `ingest.make_edit_proxy`
(720 px short side, keyframe every 0.5 s, at most 30 fps, video only; the audio apart as 48 kHz FLAC) made once per source
into `ui/cache/edit/`; `engine/preview.py` `preview_data` lays out with the render's own code (plan_paths segments and
crop x per source frame, build_overlays captions and callouts not drawn, broll.place stills, plan_punches zoom,
music.speech_intervals for the bed, the speech's loudness gain), 5 ms without graphics, 27-41 ms with; GET
`/api/runs/{id}/preview?v=N`, `/draft/preview` (plus the draft's `version_entry`, runs.version_entry now shared with
runs.versions), `/api/fonts/{key}`, `/filmstrip` and `/peaks` with `draft=1`. `web/src/Live.ts` LiveMedia: audio-led
(Web Audio, one source per segment, gain into a limiter, the looped bed with its duck curve), two `<video>`s swapped
in the frame callback with the next one pre-rolled 0.15 s before each cut, the crop and punch-ins as CSS transforms,
`LiveOverlay.ts` HTML graphics; the Player drives it through its usual transport (`live` = URL or data). Live /
Rendered switch on the transport (localStorage `montivo.live`, `#/run/<id>?live=1`). In Live the structural edits
(split, delete, trim, move, word cuts, insert, pauses) go to the draft (`montivo/server/draft.py`, pure ops in
`human.OPS`, 4-33 ms an edit, per judge on the hosted demo) with no dialog, cmd-Z is the draft's undo, the top bar
offers Discard and Make Version N (Billy: commit by button only, one render, no auto-commit); captions, style, music,
review and Ask wait for the version. `tools/live_check.mjs RUN` (headless Google Chrome, needs `montivo serve`): on a
015 clone no freeze, picture vs audio p95 0.5-0.98 frame, seek-to-play 21-48 ms; never measure in the hidden browser
pane (animation frames at 4 Hz). Open: Safari and listening (Billy, `runs/015-live` v8 has Chill Lofi), pitch at
1.5x / 2x, HTML caption stroke a little heavier than Pillow's. Notes: `docs/notes/live-preview-phase0_2026-10-29.md`.
The editor opens in Live (Billy, 30/10): a Live | Rendered switch above the viewer, V toggles it (`7abf85f`). Deployed
to Modal 30/10 00:11 on Billy's go at `a9786a1` (no job since 28/10 23:22; the 00:07 web container stopped); live
/api/health 200, /api/runs 401, /s/nope 404; `modal run modal_app.py::smoke --live` (new): edit media for 015 v5 in
6.2 s (23.7 MB, keyframes every 0.5 s), preview data 183 ms cold, a draft drop 94 ms; smoke drop on 015: 34.3 s,
-14.0 LUFS / -1.7 dBTP, render 11.5 s. The first Live open of each sample on the Volume makes its edit media (~6 s).
Timeline flicker (30/10, $0, commit `17b7593`, Billy's screen recording of the hosted demo): at fit-to-width the canvas
came out a pixel wider than the rounded clientWidth, a horizontal scrollbar pushed a vertical one into `.tl-body`, the
lanes narrowed, the fit shrank, every frame (a draft edit changed the duration and set it off). Fit leaves a pixel spare,
`.tl-body` has `scrollbar-gutter: stable`, the filmstrip and waveform keep the last media until the draft's new ones load.
Reproduced only with scrollbars shown (Playwright headless passes `--hide-scrollbars`: `ignoreDefaultArgs`): old 29 of 30
frames changed, new 0 over 1225 width x height cases. Deployed 30/10 17:11 on Billy's go (no job since 00:25; the 17:09
container stopped); /api/health 200, /api/runs 401, /s/nope 404; smoke drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP.
Linked voice, draft music, music level (30/10, $0, commit `29f3b65`, Billy's asks after the flicker fix): the Voice lane
draws one piece per clip (the video track's gaps and corners) that selects, drags and opens the menu of its clip; the
bed stays one lane (Premiere / Resolve / CapCut keep music on its own track; J/L cuts would need per-span audio in/out in
render and Live, after the freeze). A draft's version entry had no music (`runs._music` needs a render): `runs.draft_music`,
GET /peaks?draft=1&track=music (`media.draft_bed_peaks`, the loop shaped like Live.ts bedGain), faders locked in a draft
until Make Version. Music default -20 / -12 -> -18 / -6 (`music.LEVEL_DB`, `DUCK_DB`): 015 is ducked the whole cut, so the
old default sat 17.1 dB under the voice and the fader's top 7.1; Billy chose 9 dB by ear from 17 / 12 / 9 / 7 (files in
`runs/_listen-music/`); with Ukulele the new default measures 7.5 dB. The Music fader and the mix bar show the bed under
the voice (the render's margin_db moved by both faders): a mix commit at -16 / -12 predicted -13, measured 13.1.
514 tests. Deployed 30/10 17:33 on Billy's go (no job since 00:25; the 17:11 container stopped); /api/health 200,
/api/runs 401, /s/nope 404; smoke drop on 015: 34.3 s, -14.0 LUFS / -1.7 dBTP; `--music midnight_cruiser` at the new
default: 37.9 s, -14.0 / -1.6 dBTP, speech 9.1 dB over the bed (was 17.1).
Montivo loader (30/10, $0, commit `5ec2449`, design in `docs/notes/montivo-loader_2026-10-30.html`): `MarkLoader`
(`web/src/icons.tsx`) = the brand mark on a 1.6 s loop (brackets close in, a marker V passes through the V shape, no
rotation) in every loading spot: top bar, Home card, chat tray and log fold, the running pipeline row, Export, Cover
maker, Style, the Loading texts; brackets take the text colour, the V turns ink on a marker button, reduced motion keeps
a fade; `.spin` removed; the Waiting sting, rail dot, Live light and new-version notice unchanged. Deployed 30/10 on
Billy's go (no container was running before it); /api/health 200, /api/runs 401, /s/nope 404; smoke drop on 015:
34.3 s, -14.0 LUFS / -1.7 dBTP, render 8.0 s.
Demo footage: Billy's own English talking-head clip, 60 to 90 s, expected in `~/Downloads` by 28/10; the NASA clip
(`footage/nasa_150s.mp4`, public domain) is regression only. Files under iCloud Drive cannot be read from Claude Code.

## Task board (every session)
Billy tracks every task on a private board until the submission; every session keeps it current. Where it is and
how to update it: `CLAUDE.local.md`. A session without that file (a cloud session): list the task changes in the
final report instead.

## Where the rest of the context is
- `docs/notes/` (copies of the session notes: kill tests and architecture, evening 1, fresh research and verdict).
- `CLAUDE.local.md` (private: the task board, the planning pages, the hosted address).
- Claude Code memory for this folder: `~/.claude/projects/-Users-billy-Documents-montivo/memory/`.
