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
  `experiments/results/` (the local kill-test results) are git-ignored and stay out of the repo.
- The pre-kickoff API test scripts (`experiments/`, started 26/10 17:30, four calls before 18:00) were removed from the
  repo on 27/10 and are disclosed in README "Before the kickoff"; never bring them or code from them back. Organizers
  (Cerebral Valley, Discord #questions, 27/10): that is fine, nothing else needed, but the 3-minute demo video must
  OPEN with one line stating what was pre-existing (the name and idea, the pre-kickoff API tests) and what was built
  this week.
- Spend: keep each evening under $5 and a 150 s run under $0.50. Every call is logged in `runs/<id>/ledger.json`.
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
Measured facts that shape the code are in README.md ("Measured behaviour") and, in full, in `docs/notes/`.
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
Demo footage: Billy's own English talking-head clip, 60 to 90 s, expected in `~/Downloads` by 28/10; the NASA clip
(`footage/nasa_150s.mp4`, public domain) is regression only. Files under iCloud Drive cannot be read from Claude Code.

## Montivo Board (live task board, every session)
Notion database "Montivo Board" under the project page, data source `collection://ca1d961f-09be-4b9a-bb90-875fbe8afab3`
(database `5cf24c9678414d9291a044acc150c283`). Billy tracks everything there until the submission, so every session,
local or cloud, keeps it current:
- First task of the session: query the board; take the matching card (or create one) and set Status `In progress`,
  Owner (`Claude session` / `Cloud session`), Area, Priority, Due, and Session = `claude://claude.ai/epitaxy/<local_id>`
  plus Session ID when the id is known (a session you start with start_session: create its card yourself with the id the
  tool returns); otherwise put the session title in Notes.
- New open items found while working (a follow-up, something that needs Billy's yes) get their own card: `Next` or
  `Backlog`, or `Waiting on Billy` with Owner `Billy` and what he has to do in Notes.
- At wrap-up: Status `Done` (with Commit and Cost) or `Waiting on Billy` / `Backlog` with one line in Notes on what is
  left. Card text in Greek with the professional terms in English, no em dashes. Never delete cards.
- Without the Notion connector (some cloud sessions): list the card changes in the final report instead.

## Where the rest of the context is
- `docs/notes/` (copies of the session notes: kill tests and architecture, evening 1, fresh research and verdict).
- Notion page `405a4ee32031814ba38cfb1efd670918` (Kill test results, Architecture, Plan).
- Claude Code memory for this folder: `~/.claude/projects/-Users-billy-Documents-montivo/memory/`.
