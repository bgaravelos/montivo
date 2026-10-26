"""Offline: captions from Voice word times (recorded NASA stream + Billy's run 011 words), placement, callouts, render
graph strings and the loudness parsers. No network, no media files."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from montivo.engine.captions import (CAPTION_TOP, CWord, apply_glossary, build_overlays, dedupe_words, group_lines,
                                     interp_box, place_callout, place_caption, span_words)
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
    words, f2 = apply_glossary(words)
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
                                      ("News", 5, 5.5), ("Voice,", 5.5, 6)]))
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


def test_overlay_graph_and_loudness_parsers():
    from montivo.engine.captions import Overlay
    from montivo.engine.render import _EBU, _LOUD_JSON, _overlay_graph
    args, graph = _overlay_graph("[base]", [Overlay(Path("a.png"), 10, 20, 0.0, 1.0),
                                            Overlay(Path("b.png"), 30, 40, 0.5, 3.0, fade_s=0.2)], 1)
    assert args == ["-i", "a.png", "-loop", "1", "-framerate", "30", "-t", "3.1000", "-i", "b.png"]
    assert "[base][1:v]overlay=x=10:y=20" in graph and "gte(t,0.0000)*lt(t,1.0000)" in graph
    assert "fade=t=in:st=0.5000:d=0.2:alpha=1" in graph and graph.endswith("format=yuv420p[v]")
    log = 'x\n[Parsed_loudnorm_0 @ 0x1]\n{\n\t"input_i" : "-28.53",\n\t"output_i" : "-14.06",\n\t"output_tp" : "-1.00"\n}\n'
    assert json.loads(_LOUD_JSON.findall(log)[-1])["output_i"] == "-14.06"
    summ = ("Summary:\n\n  Integrated loudness:\n    I:         -14.3 LUFS\n    Threshold: -25.2 LUFS\n\n  Loudness range:\n"
            "    LRA:         3.9 LU\n\n  True peak:\n    Peak:       -1.3 dBFS\n")
    assert _EBU.search(summ).groups() == ("-14.3", "3.9", "-1.3")
