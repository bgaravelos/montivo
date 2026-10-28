"""Offline: captions from Voice word times (recorded NASA stream + Billy's run 011 words), placement, callouts, render
graph strings and the loudness parsers. No network, no media files."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from montivo.engine.captions import (CAPTION_TOP, DEMO_GLOSSARY, LINE_MIN_S, CWord, Line, apply_glossary, build_overlays, dedupe_words,
                                     group_lines, hold_short_lines, interp_box, merge_short_lines, place_callout,
                                     place_caption, span_words)
from montivo.engine.graphics import CaptionStyle
from montivo.engine.voice import fold_events

REC = Path(__file__).resolve().parent / "fixtures" / "voice_stream_nasa30.json"
STYLE = CaptionStyle()

# runs/011 voice.json, turn 2 words 71..82 (Billy's payoff line as Voice heard it, 26/10)
BILLY = [("that's", 117.9, 118.38), ("that's", 118.38, 118.7), ("Montivo", 118.7, 119.34), ("raw", 119.34, 119.98),
         ("footage", 119.98, 120.22), ("in", 120.22, 120.46), ("in", 120.46, 120.5), ("a", 120.5, 120.54),
         ("ready", 120.54, 121.06), ("viewed", 121.06, 121.58), ("cut", 121.58, 121.74), ("out", 121.74, 124.06)]


def cw(items, span="s6"):
    return [CWord(w=w, t_in=a, t_out=b, span=span) for w, a, b in items]


def test_dedupe_and_glossary_on_billys_payoff_line():
    words, f1 = dedupe_words(cw(BILLY))
    words, f2 = apply_glossary(words, DEMO_GLOSSARY)
    assert " ".join(w.w for w in words) == "that's Montivo raw footage in a reviewed cut out"
    assert [(f["kind"], f["from"], f["to"]) for f in f1 + f2] == [
        ("dedupe", "that's that's", "that's"), ("dedupe", "in in", "in"), ("glossary", "ready viewed", "reviewed")]
    kept = words[0]
    assert (kept.t_in, kept.t_out) == (117.9, 118.7)  # the kept word covers both copies
    rev = next(w for w in words if w.w == "reviewed")
    assert (rev.t_in, rev.t_out) == (120.54, 121.58) and rev.src == "ready viewed"


def test_dedupe_keeps_repeats_with_a_real_gap():
    words, fixes = dedupe_words(cw([("go", 1.0, 1.3), ("go", 1.6, 1.9), ("Go!", 1.9, 2.1)]))
    assert [w.w for w in words] == ["go", "go"] and len(fixes) == 1  # 0.3 s apart stays, the abutting one goes


def test_glossary_names_and_punctuation():
    words, fixes = apply_glossary(cw([("four", 0, 1), ("metal", 1, 2), ("models.", 2, 3), ("MuseSpark", 3, 5),
                                      ("News", 5, 5.5), ("Voice,", 5.5, 6)]), DEMO_GLOSSARY)
    assert [w.w for w in words] == ["four", "Meta", "models.", "Muse", "Spark", "Muse", "Voice,"]
    assert words[3].t_in == 3 and words[4].t_out == 5 and words[3].t_out == words[4].t_in
    assert len(fixes) == 3


def test_lines_are_1_to_4_words_and_never_split_a_name():
    ws = cw([(w, i * 0.3, i * 0.3 + 0.3) for i, w in enumerate("plans the cut Muse Voice gives every word".split())])
    ws[3].src = ws[4].src = "News Voice"
    lines = group_lines(ws, STYLE)
    assert all(1 <= len(ln) <= 4 for ln in lines)
    assert [w.w for ln in lines for w in ln] == [w.w for w in ws]
    assert not any(ln[-1].w == "Muse" for ln in lines)


def test_pause_breaks_a_line_and_short_tail_borrows_a_word():
    ws = cw([("rubric", 0, .4), ("and", .4, .5), ("recuts", .5, 1.1), ("it's", 1.1, 1.6), ("a", 1.6, 1.8),
             ("loop", 1.8, 2.0), ("next", 3.0, 3.4)])
    lines = [[w.w for w in ln] for ln in group_lines(ws, STYLE)]
    assert lines == [["rubric", "and", "recuts"], ["it's", "a", "loop"], ["next"]]


def test_span_boundary_starts_a_new_line():
    # runs/012 s3 + s4 render as one segment; Voice gives no punctuation, before this the line read "work Muse Spark"
    ws = cw([("Meta", 0, .3), ("models", .3, .6), ("do", .6, .8), ("the", .8, .9), ("work", .9, 1.2)], span="s3")
    ws += cw([("Muse", 1.2, 1.5), ("Spark", 1.5, 1.8), ("watches", 1.8, 2.2)], span="s4")
    wide = CaptionStyle(max_w=4000)  # only the span rule may break here, whatever font the machine falls back to
    lines = [[w.w for w in ln] for ln in group_lines(ws, wide)]
    assert lines[-1] == ["Muse", "Spark", "watches"]
    assert all(len({w.span for w in ln}) == 1 for ln in group_lines(ws, wide))  # the short-line borrow stays in its span


def test_short_line_joins_its_phrase_when_it_fits():
    # a 0.15 s first line ("Hi" with no end punctuation) and the next line of the same phrase: one line
    lines = merge_short_lines([cw([("Hi", 27.03, 27.18)], "s1"), cw([("I'm", 27.18, 27.9), ("Billy", 27.9, 28.14)], "s1")], STYLE)
    assert [[w.w for w in ln] for ln in lines] == [["Hi", "I'm", "Billy"]]
    # the line before when it is the one that fits
    lines = merge_short_lines([cw([("then", 0, .5), ("the", .5, 1.0)]), cw([("four", 1.0, 1.2)]),
                               cw([("models", 1.2, 1.6), ("do", 1.6, 1.8), ("the", 1.8, 2.0), ("work", 2.0, 2.5)])], STYLE)
    assert [[w.w for w in ln] for ln in lines] == [["then", "the", "four"], ["models", "do", "the", "work"]]


def test_one_word_line_joins_the_next_across_a_sentence_end():
    # runs/012-open: "Hi." held to 0.4 s alone was too short to read (the reviewer, with the caption timeline)
    hi = [cw([("Hi.", 27.03, 27.18)], "s1"), cw([("I'm", 27.18, 27.9), ("Billy.", 27.9, 28.14)], "s1")]
    assert [[w.w for w in ln] for ln in merge_short_lines(hi, STYLE)] == [["Hi.", "I'm", "Billy."]]
    # only forwards, only a one-word line, only when it fits and there is no pause
    two = [cw([("Hi", 27.0, 27.05), ("there.", 27.05, 27.18)], "s1"), cw([("I'm", 27.18, 27.9)], "s1")]
    assert len(merge_short_lines(two, STYLE)) == 2
    long = [cw([("Hi.", 27.03, 27.18)], "s1"), cw([("I", 27.18, 27.3), ("am", 27.3, 27.5), ("Billy", 27.5, 27.7),
                                                  ("Garavelos.", 27.7, 28.2)], "s1")]
    assert len(merge_short_lines(long, STYLE)) == 2
    paused = [cw([("Hi.", 27.03, 27.18)], "s1"), cw([("I'm", 27.9, 28.3), ("Billy.", 28.3, 28.6)], "s1")]
    assert len(merge_short_lines(paused, STYLE)) == 2


def test_short_line_stays_apart_across_a_sentence_end_span_or_full_line():
    # a two-word sentence under the minimum does not join the one before across its end punctuation
    end = [cw([("I'm", 26.0, 26.5), ("Billy.", 26.5, 27.0)], "s1"), cw([("Hi", 27.03, 27.1), ("all", 27.1, 27.18)], "s1"),
           cw([("folks", 27.9, 29.0)], "s1")]
    assert [len(ln) for ln in merge_short_lines(end, STYLE)] == [2, 2, 1]
    spans = [cw([("Hi", 27.03, 27.18)], "s1"), cw([("I'm", 27.18, 27.9)], "s2")]
    assert len(merge_short_lines(spans, STYLE)) == 2  # never across a span boundary
    full = [cw([("then", 0, .5), ("the", .5, .8), ("four", .8, 1.0)]), cw([("Meta", 1.0, 1.1), ("models", 1.1, 1.24)]),
            cw([("do", 1.24, 1.4), ("the", 1.4, 1.56), ("work", 1.56, 2.0)])]
    assert len(merge_short_lines(full, STYLE)) == 3  # 5 words either way: held instead (below)


def test_short_line_is_held_and_the_next_line_starts_later():
    # runs/012 cut_v2 after the edge pass: "Hi." 0.03-0.18, "I'm Billy." from 0.18
    a = Line(words=cw([("Hi.", 27.03, 27.18)]), segment=0, t_in=0.03, t_out=0.18, word_times=[(0.03, 0.18)])
    b = Line(words=cw([("I'm", 27.18, 27.9), ("Billy.", 27.9, 28.14)]), segment=0, t_in=0.18, t_out=1.14,
             word_times=[(0.18, 0.9), (0.9, 1.14)])
    held = hold_short_lines([a, b], seg_end=4.767)
    assert held == [0.25, 0.0] and (a.t_in, a.t_out) == (0.03, round(0.03 + LINE_MIN_S, 3))
    assert b.t_in == a.t_out and b.word_times[0] == (0.43, 0.9)  # words clamped inside their line
    # never past the segment end
    c = Line(words=cw([("out", 0, 1)]), segment=0, t_in=5.0, t_out=5.1, word_times=[(5.0, 5.1)])
    assert hold_short_lines([c], seg_end=5.2) == [0.1] and c.t_out == 5.2
    # never leaves the next line under the minimum: a stays short then
    d = Line(words=cw([("so", 0, 1)]), segment=0, t_in=0.0, t_out=0.1, word_times=[(0.0, 0.1)])
    e = Line(words=cw([("yes", 0, 1)]), segment=0, t_in=0.1, t_out=0.45, word_times=[(0.1, 0.45)])
    assert hold_short_lines([d, e], seg_end=9.0) == [0.0, 0.05] and e.t_in == 0.1  # e, short itself, is held


@pytest.mark.skipif(not REC.is_file(), reason="recorded stream not present")
def test_recorded_voice_stream_to_caption_lines():
    turns, words = fold_events(json.loads(REC.read_text())["events"])
    t = turns[2]
    n = t.n_words
    spans = [{"id": "s1", "turn_id": t.turnId, "word_from": 0, "word_to": n - 1}]
    ws = span_words(spans, [w.__dict__ for w in words])["s1"]
    assert " ".join(w.w for w in ws) == t.transcript
    ws, _ = dedupe_words(ws)
    lines = group_lines(ws, STYLE)
    assert all(1 <= len(ln) <= 4 for ln in lines)
    flat = [w for ln in lines for w in ln]
    assert all(a.t_in <= b.t_in for a, b in zip(flat, flat[1:]))


def test_interp_box():
    recs = [{"t": 0.0, "box": [0, 0, 10, 10]}, {"t": 1.0, "box": [10, 10, 20, 20]}]
    assert interp_box(recs, 0.5) == [5, 5, 15, 15]
    assert interp_box(recs, 5.0) is None


def test_caption_moves_off_the_head():
    assert place_caption(800, 137, [300, 100, 780, 900]) == (CAPTION_TOP, "lower_third")
    y, where = place_caption(800, 137, [300, 400, 780, 1350])  # head low in frame: push below it
    assert where == "below_head" and y > 1350
    y, where = place_caption(800, 137, [300, 1000, 780, 1550])  # no room below: go above
    assert where == "above_head" and y + 137 < 1000


def test_callout_sits_on_the_box_and_clear_of_the_head():
    x, y, where = place_callout(500, 110, [100, 600, 1000, 1900], [300, 620, 780, 1000], [])
    assert where == "box_top" and y + 110 <= 600
    x, y, where = place_callout(500, 110, [0, 132, 1043, 1916], [290, 132, 809, 923], [[100, 1300, 980, 1437]])
    assert where == "box_below_head" and y >= 923 and y + 110 < 1300


def test_build_overlays_end_to_end(tmp_path):
    words = [{"w": w, "t_start": a, "t_end": b, "turn_id": 2, "i": 71 + k} for k, (w, a, b) in enumerate(BILLY)]
    edl = {"source": {"width": 1920, "height": 1080, "fps": 30.0},
           "spans": [{"id": "s6", "turn_id": 2, "word_from": 71, "word_to": 82, "t_in": 117.9, "t_out": 124.06,
                      "duration": 6.16, "callout": "Raw in, cut out"}]}
    seg = SimpleNamespace(t_in=117.9, t_out=124.067, duration=6.1667, fps=30.0, span_ids=["s6"], frames=185)
    path = SimpleNamespace(xs=[640] * 185, crop_w=608, crop_h=1080)
    head = {"source": {"width": 960, "height": 540}, "objects": {"0": [{"t": 117.0, "box": [400, 40, 540, 250]},
                                                                     {"t": 125.0, "box": [400, 40, 540, 250]}]}}
    person = {"source": {"width": 960, "height": 540}, "objects": {"0": [{"t": 117.0, "box": [200, 40, 700, 539]},
                                                                       {"t": 125.0, "box": [200, 40, 700, 539]}]}}
    fields, ovs = build_overlays(edl, words, [seg], [path], head, person, tmp_path, title_s=1.5)
    caps = fields["captions"]
    assert " ".join(c["text"] for c in caps) == "that's Montivo raw footage in a reviewed cut out"
    assert caps[0]["t_in"] == 1.5  # after the title card
    assert all(not c["over_face"] for c in caps)
    assert {f["to"] for f in fields["caption_corrections"]} == {"that's", "in", "reviewed"}
    assert any(c["corrections"] for c in caps)
    assert len(fields["callouts"]) == 1 and 2.0 <= fields["callouts"][0]["t_out"] - fields["callouts"][0]["t_in"] <= 3.0
    # one PNG per word state plus the callout, all inside the segment, files on disk
    assert len(ovs[0]) == 9 + 1 and all(0 <= o.t_in < o.t_out <= seg.duration + 1e-6 and o.png.is_file() for o in ovs[0])
    assert all(c["t_out"] - c["t_in"] >= LINE_MIN_S - 1e-6 for c in caps)
    assert all(c["t_in"] <= w["t_in"] <= w["t_out"] <= c["t_out"] for c in caps for w in c["words"])


def test_overlay_graph_and_loudness_parsers():
    from montivo.engine.captions import Overlay
    from montivo.engine.render import _EBU, _LOUD_JSON, _overlay_graph
    args, graph = _overlay_graph("[base]", [Overlay(Path("a.png"), 10, 20, 0.0, 1.0),
                                            Overlay(Path("b.png"), 30, 40, 0.5, 3.0, fade_s=0.2)], 1, 59.94)
    # the fading overlay loops at the segment's rate (was a fixed 30: the fade stepped every other frame at 59.94)
    assert args == ["-i", "a.png", "-loop", "1", "-framerate", "59.94", "-t", "3.1000", "-i", "b.png"]
    assert "[base][o0]overlay=x=10:y=20" in graph and "gte(t,0.0000)*lt(t,1.0000)" in graph
    assert "fade=t=in:st=0.5000:d=0.2:alpha=1" in graph
    # every PNG goes to YUV with BT.709 limited range and the output is tagged so
    assert graph.count("scale=out_color_matrix=bt709:out_range=tv,format=yuva420p") == 2
    assert graph.endswith("format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709[v]")
    log = 'x\n[Parsed_loudnorm_0 @ 0x1]\n{\n\t"input_i" : "-28.53",\n\t"output_i" : "-14.06",\n\t"output_tp" : "-1.00"\n}\n'
    assert json.loads(_LOUD_JSON.findall(log)[-1])["output_i"] == "-14.06"
    summ = ("Summary:\n\n  Integrated loudness:\n    I:         -14.3 LUFS\n    Threshold: -25.2 LUFS\n\n  Loudness range:\n"
            "    LRA:         3.9 LU\n\n  True peak:\n    Peak:       -1.3 dBFS\n")
    assert _EBU.search(summ).groups() == ("-14.3", "3.9", "-1.3")


def test_captions_keep_clear_of_the_head_the_crop_follows(tmp_path):
    # run 013: SAM's head concept also tracked passers-by; a union of every head object covered the frame and put
    # every caption over the head. Only the followed head counts (the largest one when the crop follows no head).
    from montivo.engine.captions import SAFE_BOTTOM
    words = [{"w": w, "t_start": a, "t_end": b, "turn_id": 2, "i": 71 + k} for k, (w, a, b) in enumerate(BILLY)]
    edl = {"source": {"width": 1920, "height": 1080, "fps": 30.0},
           "spans": [{"id": "s6", "turn_id": 2, "word_from": 71, "word_to": 82, "t_in": 117.9, "t_out": 124.06,
                      "duration": 6.16, "callout": ""}]}
    seg = SimpleNamespace(t_in=117.9, t_out=124.067, duration=6.1667, fps=30.0, span_ids=["s6"], frames=185)
    path = SimpleNamespace(xs=[640] * 185, crop_w=608, crop_h=1080, track_id="0")
    ts = [117.0 + 0.5 * k for k in range(17)]  # interp_box needs records closer than 1.2 s
    keep = [{"t": t, "box": [400, 40, 540, 250]} for t in ts]
    stray = [{"t": t, "box": [0, 0, 960, 540]} for t in ts]  # bigger than the speaker
    head = {"source": {"width": 960, "height": 540}, "objects": {"0": keep, "1": stray}}
    fields, _ = build_overlays(edl, words, [seg], [path], head, None, tmp_path / "a", callouts=False,
                               reframe_concept="head")
    assert fields["captions"] and all(c["head_box"] and not c["over_face"] for c in fields["captions"])
    # following the person concept, the largest head counts: here the stray box, so no clear spot is left and the
    # line goes as low as the bottom UI allows instead of onto the mouth at the default height
    fields, _ = build_overlays(edl, words, [seg], [path], head, None, tmp_path / "b", callouts=False,
                               reframe_concept="person")
    c = fields["captions"][0]
    assert c["placement"] == "over_head" and c["box"][1] == SAFE_BOTTOM - c["box"][3] > CAPTION_TOP


def test_bundled_font_wins(monkeypatch):
    from montivo.engine.graphics import BUNDLED_FONT, font_path
    monkeypatch.delenv("MONTIVO_FONT", raising=False)
    assert BUNDLED_FONT.is_file() and font_path() == str(BUNDLED_FONT)


def _line(words, t0, span="s2", step=0.2):
    return [CWord(w=w, t_in=round(t0 + k * step, 3), t_out=round(t0 + (k + 1) * step, 3), span=span)
            for k, w in enumerate(words.split())]


def test_a_retext_takes_the_next_lines_words_it_already_says():
    from montivo.engine.captions import apply_overrides, covered
    # live run ui-1027-2252: Voice doubled "wrote down" across the break; the reviewer's text is the whole phrase
    a, b, c = _line("I wrote down wrote", 15.0), _line("down the letters of", 15.8), _line("the station and we", 16.6)
    assert covered(a, "I wrote down the letters of", b) == 4
    ov = [{"span": "s2", "src_t": 15.01, "text": "I wrote down the letters of", "why": "review"}]
    lines, forced = apply_overrides([a, b, c], ov)
    assert [" ".join(w.w for w in ln) for ln in lines] == ["I wrote down the letters of", "the station and we"]
    assert lines[0][0].t_in == 15.0 and lines[0][-1].t_out == pytest.approx(16.6) and forced == {}
    assert lines[0][0].src == "I wrote down wrote down the letters of"  # what Voice heard, for the Captions panel
    # the words between can go too: "we have we've had | it we had enough" -> "we had enough" takes the whole next line
    a, b = _line("we have we've had", 23.0, "s4"), _line("it we had enough", 23.8, "s4")
    assert covered(a, "we had enough", b) == 4
    # part of the next line: it keeps the rest and starts later
    a, b = _line("so after about after", 57.3, "s4"), _line("about 20 30 minutes we", 58.1, "s4")
    lines, _ = apply_overrides([a, b], [{"span": "s4", "src_t": 57.31, "text": "so after about 20 30 minutes"}])
    assert [" ".join(w.w for w in ln) for ln in lines] == ["so after about 20 30 minutes", "we"]
    assert lines[1][0].t_in == pytest.approx(58.9)


def test_a_retext_of_its_own_words_leaves_the_next_line_alone():
    from montivo.engine.captions import apply_overrides, covered
    # run 015's hand edits: the corrected words are the line's own
    a, b = _line("meta models do all", 36.6, "s5"), _line("the work Muse Voice", 37.4, "s5")
    assert covered(a, "Four Meta models do all", b) == 0
    a, b = _line("we did short form", 24.9), _line("videos every", 25.7)
    assert covered(a, "we edit short form", b) == 0
    assert covered(_line("and I said", 1.0), "and I said the", _line("the end", 1.6, span="s3")) == 0  # another span
    lines, forced = apply_overrides([a, b], [{"span": "s2", "src_t": 25.71, "top": 500}])
    assert [" ".join(w.w for w in ln) for ln in lines] == ["we did short form", "videos every"] and list(forced) == [1]


def test_dedupe_drops_a_phrase_voice_heard_twice_back_to_back():
    # bug bash 28/10 (tips take 2, the future sample 017): Voice heard "You have You have one second." where the audio
    # says "you have" once (silence 22.5-23.2 s, speech from 23.3 s); the cut opened on the caption "You have You"
    words, fixes = dedupe_words(cw([("You", 23.19, 23.19), ("have", 23.19, 23.58), ("You", 23.58, 23.66),
                                    ("have", 23.66, 23.78), ("one", 23.78, 23.9), ("second.", 23.9, 24.46)], "s1"))
    assert " ".join(w.w for w in words) == "You have one second."
    assert (words[0].t_in, words[1].t_out) == (23.19, 23.78) and words[0].t_out == words[1].t_in
    assert [(f["from"], f["to"], f["src_t"]) for f in fixes] == [("You have You have", "You have", 23.19)]
    # a phrase said again after a real pause stays: that is a retake the cut may keep
    words, fixes = dedupe_words(cw([("so", 1.0, 1.2), ("we", 1.2, 1.4), ("so", 1.9, 2.1), ("we", 2.1, 2.3)]))
    assert [w.w for w in words] == ["so", "we", "so", "we"] and fixes == []
