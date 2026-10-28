# Music beds

Four short instrumental loops the render can lay under a cut's speech (EDL `music`, `engine/music.py`,
`python -m montivo.human music RUN --track ID`). They are assets, not models: nothing is called at run time.

| id | name | mood | loop | source | licence |
|---|---|---|---|---|---|
| `low_light` | Low Light | lo-fi keys, 80 bpm | 24.0 s | synthesised by `build.py` | CC0 1.0 |
| `forward` | Forward | upbeat synth pluck, 112 bpm | 17.1 s | synthesised by `build.py` | CC0 1.0 |
| `still_air` | Still Air | ambient pads, no drums, 70 bpm | 27.4 s | synthesised by `build.py` | CC0 1.0 |
| `snap` | Snap | bouncy marimba and claps, 96 bpm | 20.0 s | synthesised by `build.py` | CC0 1.0 |

## Source and licence

Every track was made from scratch for Montivo during the hackathon (28/10/2026) by `build.py` in this folder:
FM electric piano and bells, additive pads, sine bass, a sine-sweep kick, noise hats and claps, written in plain
Python and seeded, so `python3 montivo/music/build.py` gives the same files again. No sample, loop, preset or
recording of anyone else is in them, and no AI music model made them.

The Montivo authors dedicate these four recordings and their compositions to the public domain under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/): copy, change and use them for anything,
no attribution needed.

## Files

- `<id>.m4a`: AAC 160 kbps, 48 kHz stereo, one seamless loop each (the pattern is rendered twice and the second pass
  kept, so its start carries the tails of the end).
- `tracks.json`: id, name, mood, bpm, loop length and the integrated loudness ffmpeg measured (`lufs`), which the
  render uses to set the bed's level.
