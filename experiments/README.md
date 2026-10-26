# Second Cut: day-1 kill tests (26/10/2026)

Standalone scripts, Python 3.12 venv in `.venv`. Set `MODEL_API_KEY` in the environment
(or in `experiments/.env`, which is git-ignored). Results land in `results/` as JSON.

| Script | Model | What it measures |
|---|---|---|
| `kt_a_spark_shotlog.py` | `muse-spark-1.2` / `1.3` | video + audio in, shot log as JSON schema; tokens, latency, cost, timestamp accuracy |
| `kt_b_sam_person.py` | `sam-3.1` | 30 s clip, "person"; frames, wall time, cost, mask format, track stability |
| `kt_c_voice_diarization.py` | `muse-voice-transcribe-1.0` | DIARIZATION turns, speakers, timestamps |
| `kt_d_image_titlecard.py` | `muse-image-1.0` | 1080x1920 title card; returned size, latency |

## Results (26/10/2026, public domain NASA interview clip 1280x720 + Duck and Cover 1951)

| Test | Verdict | Key numbers |
|---|---|---|
| (a) Spark shot log from video | PASS on `muse-spark-1.3` **with the Voice transcript supplied as text**; `1.2` + strict schema degenerated 3 of 5 runs; `1.3` alone fabricates speech | 1.3: 273 tokens per video second, 29 to 38 s, $0.06 to 0.07 per 150 s |
| (b) SAM 3.1 «person», 30 s 960 px 30 fps | PASS over curl/raw SSE (the OpenAI SDK stream dropped twice) | 900 frames in 37 to 43 s, $0.18; one stable id for a static subject, jitter p95 0.5 px; regex boxes 0.07 s vs official parser 5 min |
| (c) Voice DIARIZATION | PASS; streaming with `partialMode: DELTA` gives word-level deltas with `audioProcessedMs` | 150 s in 22.6 s buffered; 30 s in 7.1 s streaming; $0.0075 per 150 s |
| (d) Muse Image 1080x1920 | PASS, returns 1152x2048 PNG, perfect text | 11 s, $0.01 |
| (e) Review pass with rubric schema | PASS on 1.3 | 30 s clip = 8.3K tokens, 23 s, $0.025 |

Extra scripts: `kt_a_probe*.py` (what Spark receives), `kt_a_planner.py` (video + transcript → paper edit by turn id),
`kt_b_sam_curl.sh` + `kt_b_parse_sse.py` + `kt_b_fast_boxes.py` + `kt_b_draw.py` (SAM transport, parsing, visual check),
`kt_c_voice_stream.py` (word deltas), `kt_e_review.py` (rubric review). Logged spend: $0.76.
