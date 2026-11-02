"""Fix batch, 2/11: a clip moved away from a shortened pause and back keeps the pause short, a word a retext leaves
behind never opens a line of another look, a person's break or another retext, and the keyword router reads on / off
by the word nearest each feature."""
import json

from montivo import agent, human
from montivo.engine.captions import CWord, apply_overrides
from tests.test_bug_audit_human import _take


def test_a_clip_moved_away_from_a_shortened_pause_and_back_keeps_the_pause_short():
    # 012 v2: pauses 44.61 -> 43.30 s, s4 to the end and back before s5 -> 44.46 s (the join gave the pause back)
    edl, words, audio = _take()
    s1, s2 = edl["spans"][:2]
    s1["t_out"], s2["t_in"] = 11.1, 11.3  # what `pauses` leaves: the same words, 0.2 s of the pause skipped
    for s in edl["spans"]:
        human.refresh_span(s, words)
    away, _ = human.step(edl, words, audio, {"op": "reorder", "args": {"span": "s2", "before": None}})
    back, rec = human.step(away, words, audio, {"op": "reorder", "args": {"span": "s2", "before": "s3"}})
    a, b = back["spans"][:2]
    assert [s["id"] for s in back["spans"]] == ["s1", "s2", "s3"]
    assert (a["t_out"], b["t_in"]) == (away["spans"][0]["t_out"], away["spans"][2]["t_in"])  # the edges as they were
    assert a["t_out"] < b["t_in"] and "joined" not in rec.fixes[0]["note"]
    # a take parted by the move is marked on both clips, and rejoined (the 015 case) only by that mark
    edl, words, audio = _take()
    away, _ = human.step(edl, words, audio, {"op": "reorder", "args": {"span": "s2", "before": None}})
    by = {s["id"]: s for s in away["spans"]}
    assert (by["s1"].get("take_out"), by["s2"].get("take_in")) == ("s2", "s1")
    back, _ = human.step(away, words, audio, {"op": "reorder", "args": {"span": "s2", "before": "s3"}})
    assert back["spans"][0]["t_out"] == back["spans"][1]["t_in"]
    assert "take_out" not in back["spans"][0] and "take_in" not in back["spans"][1]


def _lines(spans=("s1",) * 12):
    said = "one two three four five six seven eight nine ten eleven twelve".split()
    ws = [CWord(w=w, t_in=round(10 + 0.4 * k, 3), t_out=round(10.35 + 0.4 * k, 3), span=sp)
          for k, (w, sp) in enumerate(zip(said, spans))]
    return ws, [ws[k:k + 3] for k in range(0, 12, 3)]


def _text(lines):
    return [" ".join(w.w for w in ln) for ln in lines]


RETEXT = {"span": "s1", "src_t": 11.21, "until": 11.95, "text": "FOUR FIVE", "why": "human"}  # words 3-4, "six" left


def test_a_word_a_retext_leaves_behind_opens_the_next_line_only_in_its_own_look():
    ws, lines = _lines()
    assert _text(apply_overrides(lines, [RETEXT], own=set(), breaks=[])[0])[1:3] == ["FOUR FIVE", "six seven eight nine"]
    # the next line is s2's, in a look of its own: "six" stayed in s1's look, 4 words over max_words 3
    ws, lines = _lines(("s1",) * 6 + ("s2",) * 6)
    out = _text(apply_overrides(lines, [RETEXT], own={"s2"}, breaks=[])[0])
    assert out == ["one two three", "FOUR FIVE", "six", "seven eight nine", "ten eleven twelve"]


def test_a_word_a_retext_leaves_behind_never_undoes_a_break_or_joins_another_retext():
    ws, lines = _lines()
    brk = [{"span": "s1", "src_t": 12.4}]  # a person started a line at "seven"
    assert _text(apply_overrides(lines, [RETEXT], own=set(), breaks=brk)[0])[2:4] == ["six", "seven eight nine"]
    old = {"span": "s1", "src_t": 12.41, "text": "SEVEN EIGHT NINE", "voice": "seven eight nine", "why": "human"}
    out = _text(apply_overrides(lines, [old, RETEXT], own=set(), breaks=[])[0])
    assert out == ["one two three", "FOUR FIVE", "six", "SEVEN EIGHT NINE", "ten eleven twelve"]  # "six" was not shown


def test_the_keyword_reader_takes_the_on_off_word_nearest_each_feature():
    edl = {"spans": [{"id": "s1", "role": "hook", "t_in": 0.0, "t_out": 1.0, "words": "hi"}]}
    ops = lambda msg: [(o["op"], o["on"]) if o["op"] == "punch" else o["op"]  # noqa: E731
                       for o in agent.keywords(edl, msg)["ops"]]
    assert ops("turn the music off and the title on") == ["music_off", "title_on"]
    assert ops("no punch-ins, music on") == ["music_on", ("punch", False)]
    assert ops("add music without punch-ins") == ["music_on", ("punch", False)]
    assert ops("make the subtitles bigger") == []
    assert json.dumps(ops("Βάλε μουσική χωρίς ζουμ"), ensure_ascii=False) == '["music_on", ["punch", false]]'
