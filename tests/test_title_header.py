"""A1 (30/10): new EDLs show the title over the opening speech (`title_style` "header") instead of a Muse Image card in
silence before the first word, which failed the hook in the first review of 9 of 9 runs. Older EDLs keep the card."""
import pytest

from montivo import human
from montivo.engine import edl as E
from montivo.engine.captions import HEADER_S, build_overlays, place_header
from montivo.engine.review import facts_text
from tests.test_look import scene


def test_old_edls_keep_the_card_and_new_ones_get_the_header():
    old = {"title_card_text": "Raw in, cut out"}
    assert E.title_style(old) == "card" and E.title_card_on(old) and not E.title_header_on(old)
    new = {**old, "title_style": "header"}
    assert E.title_header_on(new) and not E.title_card_on(new)
    assert not E.title_on({**new, "title_card": False}) and not E.title_on({"title_style": "header", "title_card_text": ""})


def test_build_edl_writes_the_header_style():
    from montivo.engine.voice import VoiceResult
    voice = VoiceResult.from_json({"turns": [{"turnId": 0, "speaker": "A", "transcript": "hi there friend",
                                               "startMs": 0, "endMs": 1500}],
                                    "words": [{"w": w, "t_start": a, "t_end": a + 0.4, "turn_id": 0, "speaker": "A", "i": k}
                                              for k, (w, a) in enumerate([("hi", 0.0), ("there", 0.5), ("friend", 1.0)])]})
    plan = {"paper_edit": [{"turn_id": 0, "words": "hi there friend", "role": "hook"}], "title_card_text": "Hi"}
    edl = E.build_edl(plan, voice, {}, {"duration_s": 2.0}, "brief", {})
    assert edl["title_style"] == "header" and E.title_header_on(edl)


def test_the_header_sits_over_the_opening_speech_clear_of_the_head(tmp_path):
    edl, words, seg, path, head, person = scene()
    edl.update({"title_card_text": "Raw footage in, reviewed cut out", "title_style": "header"})
    fields, overlays = build_overlays(edl, words, [seg], [path], head, person, tmp_path)
    h = fields["title_header"]
    assert (h["t_in"], h["t_out"]) == (0.0, HEADER_S) and not h["over_face"] and h["placement"] != "overlap"
    x, y, w, hh = h["box"]
    assert x >= 0 and x + w <= 1080
    assert any(o.png.name == "title_header.png" and o.t_in == 0.0 for o in overlays[0])
    call = fields["callouts"][0]  # the first clip's callout comes once the title is gone
    assert call["t_in"] >= h["t_out"]
    edl.pop("title_style")  # an old EDL: no header, and no new field in what the render writes back
    fields, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path)
    assert "title_header" not in fields


def test_place_header_goes_above_the_head_else_the_top_else_under_it():
    assert place_header(900, 116, [300, 819, 760, 1414], [])[2] == "above_head"
    assert place_header(900, 116, [300, 360, 760, 900], [])[2] == "top"  # no room above the head under the top UI
    assert place_header(900, 116, [300, 260, 760, 1000], [])[2] == "below_head"


def test_the_text_op_switches_the_style_and_new_header_words_cost_nothing():
    edl = {"spans": [{"id": "s1", "callout": ""}], "title_card_text": "Old words", "title_style": "header"}
    fixes = human.text_fixes(edl, {}, "New words", None)
    assert fixes[0]["op"] == "title_text" and "$0)" in fixes[0]["note"]
    fixes = human.text_fixes(edl, {}, None, None, style="card")
    assert fixes[0]["op"] == "title_style" and fixes[0]["args"] == {"style": "card"}
    assert "Muse Image" in human.text_fixes({**edl, "title_style": "card"}, {}, "New words", None)[0]["note"]
    with pytest.raises(SystemExit):
        human.text_fixes(edl, {}, None, None, style="header")  # already the header: nothing to change


def test_the_reviewer_is_told_the_header_is_the_title():
    caps = {"lines": 0, "over_face": [], "min_clearance_px": None}
    text = facts_text([], caps, 30.0, title_header={"text": "Raw in", "t_in": 0.0, "t_out": 2.2})
    assert '"Raw in"' in text and "not a callout" in text and "speech already runs" in text
