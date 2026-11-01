"""Render vs live preview parity (audit, 1/11): the Live player's and the faders' levels against what the render's mix
does, the callout pills as the render draws them, and the style still past the source's last picture.

The web parts run in Node (type stripping and transforms, Node 22.7+): the TypeScript file is copied next to stubs for
what it imports, its relative imports given the .ts extension; skipped without such a Node."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "src"


def _node_ok() -> bool:
    if shutil.which("node") is None:
        return False
    out = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    try:
        major, minor = (int(x) for x in out.split(".")[:2])
    except ValueError:
        return False
    return (major, minor) >= (22, 7)


needs_node = pytest.mark.skipif(not _node_ok(), reason="needs Node 22.7+ (TypeScript transforms)")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def run_ts(tmp_path: Path, sources: dict[str, str], main: str) -> object:
    """Write `sources` ({file name: TypeScript}) into a module folder with every relative import given its .ts and
    "react" pointing at a stub, run `main` (TypeScript that prints one JSON value) and return what it printed."""
    d = tmp_path / "ts"
    d.mkdir(exist_ok=True)
    (d / "package.json").write_text('{"type": "module"}')
    (d / "react.ts").write_text("export const useEffect = () => {}; export const useRef = (x: unknown) => ({ current: x });"
                                "export const useState = (x: unknown) => [x, () => {}];")
    for name, text in sources.items():
        text = re.sub(r'from "\./([\w]+)"', r'from "./\1.ts"', text)
        (d / name).write_text(text.replace('from "react"', 'from "./react.ts"'))
    (d / "main.ts").write_text(main)
    proc = subprocess.run(["node", "--experimental-transform-types", "--no-warnings", str(d / "main.ts")],
                          capture_output=True, text=True, cwd=d)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def db(x: float) -> float:
    return 20 * math.log10(x)


# what a render of run 015 v5 with music at voice_db +6 really did to its two stems (least squares of the cut's audio
# on render/cut_vN.speech.m4a and .music.m4a, residual 0.0009): speech x0.987, bed x0.494; the render measured the
# speech 14.7 dB over the bed
RENDER_015_VOICE6 = {"speech_db": -0.12, "bed_db": -6.13, "margin_db": 14.7}


@needs_node
def test_the_faders_play_the_stems_at_the_renders_mix(tmp_path):
    """Moving a fader plays the two stems; they are the speech before the voice fader and the bed before the mix's
    loudness pass. Unity on both stems played a voice +6 version's bed 6 dB too loud the moment a fader moved."""
    main = """import { liveGains } from "./Mix.ts";
const base = { voice_db: 6, level_db: -18 };
const at = (m: { voice_db: number; level_db: number }) => liveGains(base, m, 14.7);
console.log(JSON.stringify({ same: at(base), bedUp: at({ voice_db: 6, level_db: -15 }), voiceDown: at({ voice_db: 0, level_db: -18 }),
  plain: liveGains({ voice_db: 0, level_db: -18 }, { voice_db: 0, level_db: -18 }, 17.1) }));"""
    got = run_ts(tmp_path, {"Mix.ts": (WEB / "Mix.ts").read_text(), "types.ts": "export type Version = any;"}, main)
    same = got["same"]
    assert db(same["voice"]) == pytest.approx(RENDER_015_VOICE6["speech_db"], abs=0.1)
    assert db(same["music"]) == pytest.approx(RENDER_015_VOICE6["bed_db"], abs=0.1)
    # each fader moves its stem against the other by its dB
    assert db(got["bedUp"]["music"]) - db(got["bedUp"]["voice"]) == pytest.approx(db(same["music"]) - db(same["voice"]) + 3, abs=1e-6)
    assert db(got["voiceDown"]["music"]) - db(got["voiceDown"]["voice"]) == pytest.approx(db(same["music"]) - db(same["voice"]) + 6, abs=1e-6)
    assert db(got["plain"]["voice"]) == pytest.approx(-0.085, abs=0.01)  # voice 0: the bed's share only
    assert db(got["plain"]["music"]) == pytest.approx(-0.085, abs=0.01)


@needs_node
def test_live_plays_the_speech_at_its_own_loudness_whatever_the_voice_fader(tmp_path):
    """The render normalises the mix after the voice gain: the export's speech stays at its loudness and the bed goes
    down by voice_db. Live used to add voice_db to the speech, 6 dB over the export at voice +6."""
    main = """import { mixLevels } from "./Live.ts";
const music = { track: "t", name: "T", url: "", gain_db: -2.4, level_db: -18, track_lufs: -15.6, duck_db: -6, voice_db: 6,
  ducked: [], attack_s: 0.15, release_s: 0.45, fade_in_s: 0.5, fade_out_s: 1.5 };
console.log(JSON.stringify({ v: mixLevels(15.6, music, null), moved: mixLevels(15.6, music, { level_db: -20, voice_db: -3 }),
  none: mixLevels(15.6, undefined, null) }));"""
    stubs = {"LiveOverlay.ts": "export class LiveOverlay {}", "Mix.ts": "export const audioCtx = () => null;",
             "types.ts": "export type DuckEdit = any; export type Version = any;"}
    got = run_ts(tmp_path, {"Live.ts": (WEB / "Live.ts").read_text(), **stubs}, main)
    # against the speech stem (= the edit audio at the loudness gain) the export plays the speech at -0.12 dB and the
    # bed at its stem level -6.13 dB: Live, which leaves out the bed's share of the mix, within 0.2 dB of both
    assert got["v"]["speech"] - 15.6 == pytest.approx(RENDER_015_VOICE6["speech_db"], abs=0.2)
    assert got["v"]["bed"] - (-2.4) == pytest.approx(RENDER_015_VOICE6["bed_db"], abs=0.2)
    assert got["moved"] == {"speech": 15.6, "bed": pytest.approx(-20 + 15.6 + 3)}
    assert got["none"] == {"speech": 15.6, "bed": 0}


FAKE_DOM = """
class El {
  tagName: string; style: Record<string, string> & { setProperty?: unknown } = {} as any; dataset: Record<string, string> = {};
  children: El[] = []; parent: El | null = null; className = ""; textContent = ""; src = "";
  classList = { add: (c: string) => { this.className += " " + c; } };
  constructor(tag: string) { this.tagName = tag; (this.style as any).setProperty = (k: string, v: string) => { (this.style as any)[k] = v; }; }
  append(...cs: El[]) { for (const c of cs) { c.parent = this; this.children.push(c); } }
  replaceChildren() { this.children = []; }
}
(globalThis as any).document = { createElement: (t: string) => new El(t), fonts: { add() {} } };
(globalThis as any).FontFace = class { load() { return Promise.resolve(this); } };
const effective = (el: El, k: string): string => { for (let e: El | null = el; e; e = e.parent) if ((e.style as any)[k]) return (e.style as any)[k]; return ""; };
"""


@needs_node
def test_callouts_keep_their_case_and_fade_like_the_render(tmp_path):
    """graphics.draw_callout draws a callout (and the title header) as written, whatever the look's case; the overlay
    fades in and out over captions.CALLOUT_FADE_S. Live took the upper-case look's uppercase into the pills (wider than
    their box) and popped them on and off."""
    main = FAKE_DOM + """
import { LiveOverlay } from "./LiveOverlay.ts";
const look = { font: "anton", text: "#FFFFFF", stroke: "#000000", active: "color", active_fg: "#000000", active_bg: "#FFC629",
  highlight: "", box: "", shadow: false, upper: true, callout_bg: "#FFC629", callout_fg: "#000000" };
const data: any = { frame: { width: 1080, height: 1920 }, look, fonts: {}, stills: [], title: null,
  captions: [{ id: "c1", t_in: 0, t_out: 2, box: [100, 1300, 600, 160], placement: "lower_third", size: 124, stroke: 9, space: 33,
    pad_x: 27, pad_y: 15, ascent: 100, descent: 30, words: [{ w: "HELLO", t_in: 0, t_out: 2, hl: false }] }],
  callouts: [{ span: "s2", text: "Four Meta models", t_in: 1, t_out: 3, box: [300, 400, 500, 111], size: 58, ascent: 50, descent: 20, fade_s: 0.2 },
             { span: "title", text: "Raw In, Reviewed Out", t_in: 0, t_out: 2.2, box: [80, 200, 900, 118], size: 64, ascent: 55, descent: 20, fade_s: 0.2 }] };
const ov = new LiveOverlay(data);
const root = ov.root as unknown as El;
const line = root.children.find((e) => e.className === "lo-line")!;
const pills = root.children.filter((e) => e.className === "lo-callout");
const opacity = (t: number) => { ov.apply(t); return pills.map((p) => [p.style.display, Number(p.style.opacity ?? 1)]); };
console.log(JSON.stringify({ line: effective(line, "textTransform"), pills: pills.map((p) => effective(p, "textTransform")),
  at: [opacity(1.1), opacity(2.0), opacity(2.9)] }));"""
    edit = (WEB / "edit.ts").read_text()
    got = run_ts(tmp_path, {"LiveOverlay.ts": (WEB / "LiveOverlay.ts").read_text(), "edit.ts": edit,
                            "types.ts": "export type Issue = any; export type Span = any; export type Version = any; export type Word = any;",
                            "Live.ts": "export type LiveData = any; export type LiveLook = any;"}, main)
    assert got["line"] == "uppercase"  # the caption words are the look's (preview.py already shows them in capitals)
    assert got["pills"] == ["none", "none"]
    callout, header = zip(*got["at"])  # at 1.1, 2.0 and 2.9 s: (display, opacity) of each pill
    assert [d for d, _ in callout] == ["flex"] * 3
    assert [round(o, 2) for _, o in callout] == [0.5, 1.0, 0.5]  # 0.1 s into a 0.2 s fade in, held, 0.1 s before the end
    assert header[0] == ["flex", 1.0] and header[1][0] == "flex" and header[1][1] == pytest.approx(1.0) and header[2][0] == "none"


@needs_ffmpeg
def test_the_style_still_past_the_last_picture_holds_it(tmp_path):
    """A clip whose audio runs on past its picture (015's source: video 68.50 s, audio 68.96 s): the render holds the
    last frame there (tpad); look.frame_at (the Style preview, the covers) asked ffmpeg for a frame past the end and
    got none (PIL: cannot identify image file)."""
    from montivo.engine import look
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=2.5", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    edl = {"source": {"path": str(src), "width": 640, "height": 360, "fps": 30.0, "duration_s": 2.5},
           "spans": [{"id": "s1", "turn_id": 0, "role": "hook", "t_in": 1.5, "t_out": 2.5, "speaker": "A"}]}
    run = tmp_path / "run"
    run.mkdir()
    inside = look.frame_at(run, edl, 0.2)  # source 1.7 s: a picture there
    past = look.frame_at(run, edl, 0.8)  # source 2.3 s: past the last frame (1.967 s)
    assert inside.size == past.size == (1080, 1920)
    last = look.frame_at(run, edl, 0.47)  # source 1.967 s: the last frame itself
    assert past.tobytes() == last.tobytes()
