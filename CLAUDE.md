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
Discord #questions answers (Cerebral Valley bot, 28/10): the track follows who enters, not the team name (Indie =
individuals; Startup = an incorporated company that can employ people, EIN or the local tax ID), picked on the submit
form and switchable until the deadline; prizes stack (one track prize plus any bonus prizes); Most Viral = likes +
reposts + comments on the one X post linked in the submission, counted through 8/11 EOD (views and hashtags do not
count, build-in-public posts are fine, it can go up any time during the event); the submission stays editable after
Submit until Wed 4/11 9:00 PM ET, early submission gives no edge. Still open: which repo commit is judged, Open Weight
with one Spark Open stage.
Track decision (Billy, 28/10): Nocta Digital is a Greek OE (general partnership, can employ people), so by the bot's rule
it fits Startup; Billy prefers Indie, which holds only if the entry is his own as an individual, not the agency's
(team name, README credit and brand cards still say Nocta Digital: see the Board card «Αποφάσεις form»).

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
`montivo-keys` (MODEL_API_KEY, BASETEN_API_KEY, MONTIVO_PASSWORD; the password is on the Board card), one container,
4 CPUs, scale to zero, a thread commits the Volume every 30 s; `modal run modal_app.py::seed` unzips /data/seed/*.zip
(`python -m montivo.server.sample pack runs/015 --footage`) and adopts them, `::budget` prints the cap, `::smoke` renders a
$0 drop on a copy (this image: 015 v6 34.3 s, -14.0 LUFS, 22 s, same as the Mac). Live: https://bgaravelos--montivo-web.us-east.modal.run
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
