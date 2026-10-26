# Montivo: project instructions for Claude Code

This folder (`~/Documents/montivo`, public repo https://github.com/bgaravelos/montivo) is the only place the
hackathon project lives. Work here, not in `~/Documents/nocta-digital` (Nocta's agency work, off limits for this project).

## What this is
Montivo is Nocta Digital's entry for the Meta Global AI Developer Hackathon (build window 27/10 to 4/11/2026,
submission on cerebralvalley.ai by Wed 4/11 evening, deadline Thu 5/11 04:00 Athens). An agent-first video editor:
Muse Spark 1.3 watches the footage and plans a paper edit, Muse Voice gives turns and word timings, SAM 3.1 tracks
the speaker for the 9:16 reframe and callouts, ffmpeg renders, then Spark reviews its own render against a rubric and
re-cuts (closed loop), with a chat-driven UI on top. Track: Indie. Bonus targets: Best Agentic, Best Creative, Most
Viral; Open Weight only if the Baseten credits arrive by 31/10.

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
montivo/           the engine package (config, ledger, run, cli; engine/{api,ingest,voice,track,spark,schemas,edl,reframe,
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
phrase, else hold); `cut_v2_polish.mp4` in runs 012 / 012-ktb, -14.0 LUFS. Next: the loop on more sources, then the API + UI. The evening-by-evening table is in README.md.
Demo footage: Billy's own English talking-head clip, 60 to 90 s, expected in `~/Downloads` by 28/10; the NASA clip
(`footage/nasa_150s.mp4`, public domain) is regression only. Files under iCloud Drive cannot be read from Claude Code.

## Where the rest of the context is
- `docs/notes/` (copies of the session notes: kill tests and architecture, evening 1, fresh research and verdict).
- Notion page `405a4ee32031814ba38cfb1efd670918` (Kill test results, Architecture, Plan).
- Claude Code memory for this folder: `~/.claude/projects/-Users-billy-Documents-montivo/memory/`.
