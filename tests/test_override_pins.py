"""A caption override from before `until` (the reviewer's caption_text, an early hand edit) keeps the words it was
written for when a look, a glossary or an edit breaks the lines elsewhere (1/11: 015 v5 in Bold pop showed "scores it
against | it's | against the"; 018 v5 after a glossary lost "brief" into "and NewSpark")."""
import copy
import json
from pathlib import Path

from montivo.engine.look import layout

SAID = "the Muse Spark watches on render score it it's against the rubric and fixes what's wrong it's a loop".split()


def _run(tmp_path: Path, said=SAID) -> tuple[Path, dict]:
    run = tmp_path / "r1"
    (run / "voice").mkdir(parents=True)
    words = [{"w": w, "t_start": round(10.0 + 0.4 * k, 3), "t_end": round(10.35 + 0.4 * k, 3), "turn_id": 0, "i": k}
             for k, w in enumerate(said)]
    (run / "voice" / "voice.json").write_text(json.dumps({"words": words}))
    end = words[-1]["t_end"] + 0.05
    edl = {"version": 1, "source": {"width": 1080, "height": 1920, "fps": 30.0},
           "spans": [{"id": "s1", "turn_id": 0, "word_from": 0, "word_to": len(said) - 1, "t_in": 9.95, "t_out": end,
                      "duration": round(end - 9.95, 3), "role": "hook"}],
           "look": {"line_breaks": "greedy"}, "glossary": {}, "highlight": [], "patches": []}
    return run, edl


def _save(run: Path, edl: dict, v: int, parent: dict | None = None, **change) -> dict:
    new = copy.deepcopy(parent or edl)
    new.update(change)
    new["version"] = v
    if parent is not None:
        new["patches"] = [*parent.get("patches", []), {"from": parent["version"]}]
    (run / f"edl_v{v}.json").write_text(json.dumps(new))
    return new


def _lines(run: Path, edl: dict) -> list[str]:
    return [c["text"] for c in layout(run, edl)["captions"]]


def test_a_hand_fix_keeps_its_words_under_a_look_with_fewer_words_per_line(tmp_path):
    run, v1 = _run(tmp_path)
    _save(run, v1, 1)
    assert "score it it's against" in _lines(run, v1)
    ov = {"span": "s1", "src_t": round(10.0 + 0.4 * 6 + 0.01, 3), "text": "scores it against", "why": "human"}
    v2 = _save(run, v1, 2, v1, caption_overrides=[ov])
    assert "scores it against" in _lines(run, v2)
    for preset in ("bold_pop", "karaoke", "minimal", "box", "clean", "marker"):
        v3 = _save(run, v1, 3, v2, look={"preset": preset, "line_breaks": "greedy"})
        got = _lines(run, v3)
        flat = " ".join(got).split()
        assert "scores it against" in got, (preset, got)  # the fix whole, on its own line
        assert flat.count("against") == 1 and "it's" not in flat[flat.index("scores"):flat.index("rubric")], (preset, got)
        assert " ".join(flat) == " ".join(SAID).replace("score it it's against", "scores it against"), (preset, got)


def test_a_reviewer_fix_after_a_glossary_keeps_the_word_before_it(tmp_path):
    # 018 v4 -> v5: the glossary moved the phrase breaks, the line now started at "brief" and the retext ate it; the
    # reviewer's text, written before the glossary, also kept the mishearing the glossary fixes
    said = "and a short brief and new NewSpark plans the cut".split()
    run, v1 = _run(tmp_path, said)
    v1["look"] = {"line_breaks": "phrase"}
    _save(run, v1, 1)
    ov = {"span": "s1", "src_t": round(10.0 + 0.4 * 4 + 0.01, 3), "text": "and NewSpark", "why": "review"}
    v2 = _save(run, v1, 2, v1, caption_overrides=[ov])
    assert "and NewSpark" in _lines(run, v2)
    v3 = _save(run, v1, 3, v2, glossary={"new NewSpark": "Muse Spark", "NewSpark": "Muse Spark"})
    got = _lines(run, v3)
    assert "and Muse Spark" in got and "brief" in " ".join(got).split(), got
    assert " ".join(got) == "and a short brief and Muse Spark plans the cut", got


def test_a_reviewer_fix_whose_words_changed_is_dropped(tmp_path):
    run, v1 = _run(tmp_path)
    _save(run, v1, 1)
    ov = {"span": "s1", "src_t": round(10.0 + 0.4 * 6 + 0.01, 3), "text": "scores it against", "why": "review"}
    v2 = _save(run, v1, 2, v1, caption_overrides=[ov])
    # a later version ends the clip after "it": "it's against" is gone, the reviewer's text would show a word not said
    spans = copy.deepcopy(v2["spans"])
    spans[0]["word_to"], spans[0]["t_out"] = 7, round(10.0 + 0.4 * 7 + 0.37, 3)
    v3 = _save(run, v1, 3, v2, spans=spans)
    assert _lines(run, v3)[-1] == "render score it", _lines(run, v3)  # the old rule retexted it: "render" went too
    # a person's text stays (the old rule: it applies while its line holds its time)
    v4 = _save(run, v1, 4, v1, caption_overrides=[{**ov, "why": "human"}])
    v5 = _save(run, v1, 5, v4, spans=spans)
    assert _lines(run, v5)[-2:] == ["render", "scores it against"], _lines(run, v5)


def test_lines_that_do_not_re_break_lay_out_as_before(tmp_path):
    # the pin is the line the override was written for: without a new break the layout is the old one, the next
    # line's words the text covers included ("we have we've had | it we had enough" -> "we had enough")
    said = "so we have we've had it we had enough of the rain".split()
    run, v1 = _run(tmp_path, said)
    _save(run, v1, 1)
    ov = {"span": "s1", "src_t": round(10.0 + 0.4 * 1 + 0.01, 3), "text": "we had enough", "why": "review"}
    lines = [["so"], ["we", "have", "we've", "had"], ["it", "we", "had", "enough"], ["of", "the", "rain"]]
    v1["look"] = {"line_breaks": "greedy", "size": 60}
    at = lambda k, **kw: {"span": "s1", "src_t": round(10.0 + 0.4 * k, 3), **kw}  # noqa: E731
    v2 = _save(run, v1, 2, v1, caption_breaks=[at(1), at(4, join=True), at(5), at(8, join=True), at(9)])
    assert [ln.split() for ln in _lines(run, v2)] == lines
    v3 = _save(run, v1, 3, v2, caption_overrides=[ov])
    unpinned = copy.deepcopy(v3)
    unpinned["patches"] = []  # no versions to look back on: the old rule
    assert _lines(run, v3) == _lines(run, unpinned) == ["so", "we had enough", "of the rain"]
    v4 = _save(run, v1, 4, v3, look={"preset": "bold_pop", "line_breaks": "greedy"})
    got = _lines(run, v4)
    assert "we had enough" in got and " ".join(got) == "so we had enough of the rain", got
