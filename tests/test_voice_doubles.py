"""Voice's 40-80 ms duplicate words ("200 200", "C-130 C-130") are left out of the text a model reads (the planner's
transcript, Ask Montivo's context through voice.shown_words), while every word index stays as Voice gave it."""
from montivo.engine.edl import build_edl, locate
from montivo.engine.voice import Turn, VoiceResult, Word, artefact_doubles, shown_words


def _turn(*words):
    out, t = [], 10.0
    for k, (w, d) in enumerate(words):
        out.append(Word(w=w, t_start=round(t, 3), t_end=round(t + d, 3), turn_id=0, speaker="A", i=k))
        t += d
    return out


# heli-02 at 18.02 s: "200" 0.36 s then "200" 0.08 s; a stutter "and and" at 0.08 / 0.08 s stays
WORDS = _turn(("about", 0.3), ("200", 0.36), ("200", 0.08), ("tons", 0.3), ("and", 0.08), ("and", 0.08), ("then", 0.3),
              ("they", 0.8), ("they", 0.04), ("left.", 0.3))


def test_only_the_short_copy_goes():
    assert artefact_doubles(WORDS) == {2, 8}
    assert [w.w for w in shown_words(WORDS)] == ["about", "200", "tons", "and", "and", "then", "they", "left."]
    assert [w.i for w in shown_words(WORDS)] == [0, 1, 3, 4, 5, 6, 7, 9]  # indices untouched
    as_dicts = [{"w": w.w, "t_start": w.t_start, "t_end": w.t_end, "i": w.i} for w in WORDS]
    assert [w["i"] for w in shown_words(as_dicts)] == [0, 1, 3, 4, 5, 6, 7, 9]
    # a copy that is not shorter (015 "meta meta" = "Four Meta", 0.48 / 0.32 s) is not an artefact
    assert artefact_doubles(_turn(("meta", 0.48), ("meta", 0.32), ("models", 0.4))) == set()


def test_the_planner_reads_no_doubles_and_its_quote_still_lands():
    text = " ".join(w.w for w in WORDS)
    v = VoiceResult(turns=[Turn(turnId=0, startMs=10000, endMs=14000, speaker="A", transcript=text)], words=WORDS,
                    audio_s=20.0, chunks=[])
    assert "200 200" not in v.transcript_text() and "they they" not in v.transcript_text()
    assert "about 200 tons and and then they left." in v.transcript_text()
    assert locate("200 tons and", WORDS) == (1, 4, True, 1.0)  # the hidden copy sits inside the span
    assert locate("about 200 200 tons", WORDS) == (0, 3, True, 1.0)  # an old plan quoting the double still matches
    edl = build_edl({"paper_edit": [{"turn_id": 0, "words": "about 200 tons", "role": "hook"},
                                    {"turn_id": 0, "words": "then they left.", "role": "payoff"}]}, v, {},
                    {"fps": 30.0}, "b", {})
    assert [(s["word_from"], s["word_to"], s["verbatim"]) for s in edl["spans"]] == [(0, 3, True), (6, 9, True)]
    # a turn whose text is not its words joined keeps Voice's text
    v.turns[0].transcript = "something else"
    assert v.transcript_text().endswith("something else")


def test_ask_montivo_reads_no_doubles_either(tmp_path):
    import json

    from montivo import agent
    (tmp_path / "voice").mkdir()
    words = [{"w": w.w, "t_start": w.t_start, "t_end": w.t_end, "turn_id": 0, "speaker": "A", "i": w.i} for w in WORDS]
    (tmp_path / "voice" / "voice.json").write_text(json.dumps({"words": words}))
    span = {"id": "s1", "turn_id": 0, "word_from": 0, "word_to": 3}
    assert agent.numbered(tmp_path, span) == "0 about 1 200 3 tons"  # the cut op still names Voice's indices
    out = "\n".join(agent.footage(tmp_path, {"spans": [span]}))
    assert "they they" not in out and "4 and 5 and 6 then 7 they 9 left." in out
