# Evaluation

Montivo is an agent that grades its own work, so most of what it does is measured: the blind reviewer, the first cut,
the chat router, Monti, and the choice of which stages run on Muse Spark Open. This folder is the index of those
measurements. The harnesses live in [`tools/`](../tools) and the offline test suite in [`tests/`](../tests); each
harness says at its top what it measures, how it scores and what a round costs.

The benchmarks run on finished run folders, which hold footage and stay out of the repository, so the results below
come from the author's runs and are reproducible with your own footage and a key. Every number comes from the run's
ledger or the harness's stored results. The benchmarks are small development samples: the prompts and the checks were
refined on the same clips, so they say how the agent does on these cases, not a general error rate.

## Benchmarks that call the models

| Harness | What it measures | Latest result | Cost of a round |
|---|---|---|---|
| [`reviewer_bench.py`](../tools/reviewer_bench.py) | the blind reviewer: eight fault types (cut inside a word, caption over the face, weak hook, head out of frame, wrong caption ...) planted one at a time into finished cuts of three runs, clean cuts as the control, each reviewed blind | round 3, with the measured checks and the pass gate: 91 % of the planted faults caught as blocker or major, 9 % of faulty cuts still pass, 3 of 4 clean reviews pass, 4.3 points SD between two reviews of one cut. Round 1: Spark 1.3 and Spark Open both 82 % caught, Spark Open never passed a clean cut, so the reviewer stays on Spark 1.3 | $0.80 (round 3), $5.02 (round 1, three reviewers) |
| [`first_cut_bench.py`](../tools/first_cut_bench.py) | the first cut: a fresh plan, v1 and the review loop on clones of finished runs (first review score and pass, the loop's best, cost, wall time) | hard set (8 clips): first review 73.2 to 80.1, first-review passes 0/8 to 2/8, loop passes 0/8 to 3/8; regression set (6 clips): 90.3 to 94.3, loop passes 4/6 to 6/6 | about $0.10 to $0.45 a clip |
| [`chat_bench.py`](../tools/chat_bench.py) | Ask Montivo's router: 38 messages in English and Greek on three runs, the edits a person would expect and their arguments (clip, word range, track, cut second) | 22/38 before the router read the recording and the chat, 38/38 after; on 1/11 Spark 1.3 37/38, Spark Open 33/38, so the router stays on Spark 1.3 | about $0.005 a message |
| [`monti_bench.py`](../tools/monti_bench.py) | Monti, the help gecko: 24 "where is / how do I" questions in English and Greek, the target, the panel and the hand-off to Ask Montivo | 24/24 on Spark 1.3 and 24/24 on Spark Open; the $0 keyword fallback 21/24 | about $0.013 a question (Spark 1.3), $0.0035 (Spark Open) |
| [`open_stages_bench.py`](../tools/open_stages_bench.py) | which calls that need no sound can move to Muse Spark Open: titles, post text, a look from words, the cover pick; measured checks plus a blind pairwise judge run on both models | Monti and the look from words moved to Spark Open (equal quality, lower cost); titles, post text, cover and router stay on Spark 1.3. Full table in the README's [Open weight](../README.md#open-weight-muse-spark-open) section | $1.15 Baseten + $1.71 Meta for the round |
| [`kt_f_review_killtest.py`](../tools/kt_f_review_killtest.py) | kill test (b) from the first night: three faults planted in one cut, does the blind reviewer catch each with a timestamp | 3 of 3 faults in 3 of 3 runs; the loop took the cut from 65 to 85 | $0.08 |

## Checks that cost nothing

| Harness | What it checks | Latest result |
|---|---|---|
| [`tests/`](../tests) (`make test`) | the engine, the human edits, the server and the hosted limits, on recorded Voice and SAM streams; no key, no network; runs in CI on every push | 862 tests pass |
| [`combo_check.py`](../tools/combo_check.py) | chains of human edits through the CLI on a clone of a run, with invariants after every version: duration, -14 LUFS and the true peak, every kept word once and none deleted coming back, untouched fields, the segment cache byte-identical to a cold render, exports at 9:16, 4:5 and 1:1 | 49 versions on run 017, 0 failures |
| [`live_check.mjs`](../tools/live_check.mjs) | the Live preview in headless Chrome: picture against the audio clock at every cut, freezes, seek-to-play latency | no freeze; picture vs audio p95 0.5 to 0.98 frame; seek to play 21 to 48 ms |

## How the agent measures itself at run time

The same idea runs inside every project, not only in these harnesses. Each version gets a blind review against a
rubric, and its pass needs measured checks the reviewer cannot overrule: Muse Voice re-transcribes the cut to find
clipped edge words, the head is checked inside the crop, captions against the face. The loop keeps the best version,
and a pairwise judge decides the hook A/B and guards against regressions. Each check and how it was measured is in
[`docs/measurements.md`](../docs/measurements.md); the loop is in the README under
[How the loop works](../README.md#how-the-loop-works).
