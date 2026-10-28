# Music beds

Eight instrumental loops the render can lay under a cut's speech (EDL `music`, `engine/music.py`,
`python -m montivo.human music RUN --track ID`). They are assets, not models: nothing is called at run time.

| id | name | mood | author | source page (file) | loop | licence |
|---|---|---|---|---|---|---|
| `chill_lofi` | Chill Lofi | lo-fi keys and soft drums | omfgdude (loop edit by qubodup) | [chill-lofi-inspired-loop-edit](https://opengameart.org/content/chill-lofi-inspired-loop-edit) (`chilllofir-loop.ogg`) | whole file | CC0 1.0 |
| `lofi_hip_hop` | Lofi Hip Hop | lo-fi hip hop beat | omfgdude | [lofi-hip-hop](https://opengameart.org/content/lofi-hip-hop) (`lofihiphop.ogg`) | 80.0 s from 0.29 s | CC0 1.0 |
| `funky_jam` | Funky Jam | funky hip hop jam | omfgdude | [funky-hip-hop-lofi-jam](https://opengameart.org/content/funky-hip-hop-lofi-jam) (`CVP701Jam_0.ogg`) | 60.7 s from 1.40 s | CC0 1.0 |
| `midnight_cruiser` | Midnight Cruiser | upbeat city-pop drive | Zane Little Music | [midnight-cruiser](https://opengameart.org/content/midnight-cruiser) (`midnight_cruiser_0.mp3`) | 76.0 s from 0.02 s | CC0 1.0 |
| `apple_cider` | Apple Cider | cozy acoustic | Zane Little Music | [apple-cider](https://opengameart.org/content/apple-cider) (`apple_cider.ogg`) | 55.8 s from 0.00 s | CC0 1.0 |
| `ukulele` | Island Ukulele | happy ukulele | Tarush Singhal | [happy-ukelele-island-surfing-theme](https://opengameart.org/content/happy-ukelele-island-surfing-theme) (`ukele.mp3`) | 80.0 s from 0.02 s | CC0 1.0 |
| `raspberry_jam` | Raspberry Jam | new jack swing, funky | congusbongus | [raspberry-jam](https://opengameart.org/content/raspberry-jam) (`raspberry_jam.ogg`) | 66.3 s from 0.01 s | CC0 1.0 |
| `cup_of_tea` | A Cup of Tea | mellow lo-fi | TAD | [lofi-compilation](https://opengameart.org/content/lofi-compilation) (`A_cup_of_tea_0.mp3`) | 68.6 s from 0.04 s | CC0 1.0 |

## Source and licence

Every track was published on [OpenGameArt.org](https://opengameart.org) by its author under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/) (public domain dedication); the licence was
read on each page above on 28/10/2026. CC0 asks for no
credit; the authors are named here and in the editor's Music panel anyway. Downloaded on Billy's go (28/10/2026);
no AI music model made them.

## How the loops are cut

`build.py DOWNLOADS_DIR` (the source files are not in the repo): leading silence trimmed, a loop length between 55 and
85 s picked where the onset envelope after it lines up best with the track's first 8 s (the beats in phase), and the
first 0.25 s of the loop crossfaded with what follows the loop point, so the loop opens like the track does and, looped,
runs on with no seam. Chill Lofi is qubodup's seamless loop edit, kept whole. AAC 160 kbps, 48 kHz stereo;
`tracks.json` holds each loop's length, the cut and the integrated loudness ffmpeg measured (`lufs`), which the
render uses to set the bed's level.
