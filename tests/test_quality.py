"""Offline: batch F, what a viewer sees and hears in every cut (28/10). E13 the equal-power crossfade at every cut
(no dip, the frames' length kept), E12 caption lines that do not end on a function word (behind the look's
`line_breaks`, old EDLs keep the greedy lines)."""
import array
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from montivo.engine.captions import FUNCTION_WORDS, CWord, group_lines
from montivo.engine.edl import norm
from montivo.engine.graphics import CaptionStyle, normalize_look, restyle
from montivo.engine.look import with_style
from montivo.engine.render import AUDIO_RATE, Segment, cut_graph, handles, render_audio

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def pcm(path: Path) -> array.array:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    a = array.array("f")
    a.frombytes(raw)
    return a


def rms_db(x) -> float:
    return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(1, len(x))) + 1e-12)


def segs(*times, fps=30.0):
    return [Segment(index=i, t_in=a, t_out=b, span_ids=[f"s{i + 1}"], frames=round((b - a) * fps), fps=fps)
            for i, (a, b) in enumerate(times)]


# E13 ---------------------------------------------------------------------------------------------------------------

def test_handles_stay_inside_the_source_and_none_at_the_ends():
    s = segs((0.0, 1.0), (2.0, 3.0), (4.0, 4.995))
    assert handles(s, source_s=5.0) == [(0, 480), (480, 480), (480, 0)]
    assert handles(segs((0.004, 1.0), (1.5, 2.0)), source_s=2.0)[1] == (480, 0)
    assert handles(segs((0.0, 1.0), (0.004, 0.5)), source_s=1.002) == [(0, 96), (192, 0)]
    g = cut_graph(s, 5.0)
    assert sum("acrossfade=ns=960:c1=qsin:c2=qsin" in x for x in g) == 2 and g[-1].endswith("[cut]")
    assert cut_graph(segs((0.0, 1.0)), 1.0)[0].endswith("[cut]")


@needs_ffmpeg
def test_cuts_crossfade_without_a_dip_and_keep_the_frames_length(tmp_path):
    """Room tone (white noise) cut in three places: the joined audio is exactly as long as the frames, and no 5 ms
    window across a cut drops more than 2 dB under the tone (the old 20 ms fade out + fade in fell 20+ dB)."""
    src = tmp_path / "noise.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=6:c=white:r=48000:a=0.05:s=7",
                    "-ac", "2", str(src)], check=True)
    s = segs((0.5, 1.5), (2.0, 3.0), (3.5, 4.0), (5.0, 5.5))
    out, rec, _ = render_audio(src, s, tmp_path, loudnorm=False, source_s=6.0)
    x = pcm(out)
    total = sum(round(g.duration * AUDIO_RATE) for g in s)
    assert abs(len(x) - total) <= 1024  # AAC priming / padding only
    tone = rms_db(x[2400:40000])
    t = 0.0
    for g in s[:-1]:
        t += g.duration
        c = round(t * AUDIO_RATE)
        worst = min(rms_db(x[c + k:c + k + 240]) for k in range(-960, 960, 120))
        assert worst > tone - 2.0, (t, worst, tone)


# E12 ---------------------------------------------------------------------------------------------------------------

def timed(text: str, span: str = "s1", step: float = 0.28, pauses: dict[int, float] | None = None) -> list[CWord]:
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append(CWord(w=w, t_in=round(t, 3), t_out=round(t + step - 0.04, 3), span=span))
        t += step + (pauses or {}).get(i, 0.0)
    return out


def ends_on_function(lines) -> list[str]:
    return [" ".join(x.w for x in ln) for ln, nxt in zip(lines, lines[1:])
            if norm(ln[-1].w) in FUNCTION_WORDS and ln[-1].span == nxt[0].span]


def test_phrase_breaks_never_end_a_line_on_and_or_a():
    style = CaptionStyle()
    ws = timed("I run a small digital marketing agency in Athens and we edit short form videos every single day")
    greedy = group_lines(ws, style)
    assert "agency in Athens and" in [" ".join(x.w for x in ln) for ln in greedy]  # what 015 v5 shows
    phrase = group_lines(ws, style, breaks="phrase")
    assert not ends_on_function(phrase)
    assert [x.w for ln in phrase for x in ln] == [x.w for x in ws]
    assert all(1 <= len(ln) <= 4 for ln in phrase)
    raw = group_lines(timed("so raw footage in a reviewed cut out"), style, breaks="phrase")
    assert not ends_on_function(raw)


def test_phrase_breaks_take_the_pause_and_keep_names_whole():
    style = CaptionStyle()
    ws = timed("the reviewer scores the cut against the rubric", pauses={2: 0.3})  # a 0.3 s pause after "scores"
    lines = group_lines(ws, style, breaks="phrase")
    assert lines[0][-1].w == "scores" and not ends_on_function(lines)
    named = timed("then Muse Voice hears every word")
    named[1].src = named[2].src = "news voice"
    out = group_lines(named, style, max_words=2, breaks="phrase")
    assert ["Muse", "Voice"] in [[x.w for x in ln] for ln in out]


def test_old_looks_keep_greedy_and_a_style_edit_keeps_the_versions_breaks():
    assert normalize_look(None).line_breaks == "greedy"
    with pytest.raises(ValueError):
        normalize_look({"line_breaks": "smart"})
    new = {"line_breaks": "phrase"}
    assert restyle(new, {"preset": "bold_pop"}).line_breaks == "phrase"
    assert restyle(None, {"preset": "box", "line_breaks": "phrase"}).line_breaks == "greedy"
    edl = {"look": new, "spans": []}
    assert with_style(edl, normalize_look({"preset": "karaoke"}).to_json())["look"]["line_breaks"] == "phrase"
