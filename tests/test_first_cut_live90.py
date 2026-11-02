"""First cut on the live 90 s upload of 2/11 (a raw talking-head take with retakes, cut off mid-sentence at 90 s): an
ending fix the recording cannot give, a stutter the speaker said twice, a cut short of the brief's length."""
from montivo.engine import review as R

FPS = 30.0


def _words(tid: int, text: str, t0: float, step: float = 0.3) -> list[dict]:
    return [{"w": w, "t_start": round(t0 + step * i, 3), "t_end": round(t0 + step * (i + 1), 3), "turn_id": tid, "i": i}
            for i, w in enumerate(text.split())]


def _span(sid: str, tw: list[dict], a: int, b: int, role: str = "body") -> dict:
    return {"id": sid, "order": 0, "turn_id": tw[0]["turn_id"], "speaker": "A", "role": role, "words": "",
            "t_in": tw[a]["t_start"], "t_out": tw[b]["t_end"], "duration": 0.0, "verbatim": True, "match_ratio": 1.0,
            "word_from": a, "word_to": b, "callout": "", "why": "", "contiguous_with_previous": False}


def _issue(t: float, op: str, criterion: str = "ending", sev: str = "blocker", **params) -> dict:
    p = {"edge": "none", "seconds": 0, "target_t_s": -1, "text": "", "direction": "none"}
    p.update(params)
    return {"t_s": t, "t_end_s": t, "criterion": criterion, "severity": sev, "what": "x", "fix_op": op, "params": p,
            "fix_args": ""}


def _edl(t0_words: list[dict], t1_words: list[dict], last: tuple[int, int]) -> tuple[dict, R.Words]:
    words = R.Words(t0_words + t1_words)
    edl = {"version": 1, "brief": "b", "source": {"fps": FPS, "width": 1920, "height": 1080}, "runtime_s": 0,
           "spans": [_span("s1", t0_words, 0, 4, "hook"), _span("s2", t1_words, *last, "payoff")],
           "captions": [], "callouts": [], "caption_overrides": []}
    for s in edl["spans"]:
        R.refresh_span(s, words)
    return edl, words


def test_an_ending_the_recording_cannot_extend_is_not_a_fix_and_tells_the_replan():
    # ui-1102-1902: the payoff "... like a reviewer would" with only "yeah" after it before the recording ends at 90 s;
    # the old extend_span added "yeah" (v2: "ends on filler yeah", 72 -> 72 -> 68)
    t0 = _words(0, "Montivo is a video editor that edits itself.", 10.0)
    t1 = _words(1, "then it renders the cut and watches its own render like a reviewer would yeah", 80.0)
    edl, words = _edl(t0, t1, (0, 13))
    end = R.cut_map(edl)[-1].cut_out
    fix, why = R.resolve(_issue(end - 0.2, "extend_span", edge="out", seconds=2.0), 0, edl, words, R.cut_map(edl))
    assert fix is None and why.startswith(R.NO_EXTEND) and "'yeah'" in why
    fixes, skipped = R.plan_fixes({"issues": [_issue(end - 0.2, "extend_span", edge="out", seconds=2.0)]}, edl, words)
    assert not fixes and skipped[0]["reason"].startswith(R.NO_EXTEND)
    # the loop then stops on "no applicable fix" (an escalation stop) and the re-plan is told what the recording lacks
    rv = {"review": {"issues": [_issue(end - 0.2, "extend_span", edge="out", seconds=2.0)]}, "skipped": skipped}
    notes = R.escalation_notes(rv)
    assert any(n.startswith("the recording has nothing after 'would'") for n in notes)


def test_a_turn_that_goes_on_is_still_extended():
    t0 = _words(0, "Montivo is a video editor that edits itself.", 10.0)
    t1 = _words(1, "then it renders the cut and watches its own render like a reviewer would and it scores it", 80.0)
    edl, words = _edl(t0, t1, (0, 13))
    end = R.cut_map(edl)[-1].cut_out
    fix, why = R.resolve(_issue(end - 0.2, "extend_span", edge="out", seconds=1.0), 0, edl, words, R.cut_map(edl))
    assert fix is not None and fix.op == "extend_span", why


def test_a_final_fragment_after_the_last_sentence_is_trimmed_off():
    # the recording stops mid-sentence: the last clip ends on its last complete sentence instead
    t0 = _words(0, "Montivo is a video editor that edits itself.", 10.0)
    t1 = _words(1, "It reviews its own cut. Then it renders the", 80.0)
    edl, words = _edl(t0, t1, (0, 8))
    end = R.cut_map(edl)[-1].cut_out
    fix, why = R.resolve(_issue(end - 0.1, "extend_span", edge="out", seconds=2.0), 0, edl, words, R.cut_map(edl))
    assert fix is not None and fix.op == "trim_span", why
    new, _ = R.apply_fixes(edl, [fix], words)
    assert new["spans"][-1]["words"] == "It reviews its own cut."
    # a middle clip is never trimmed for the ending: the fix is refused instead
    edl2, words2 = _edl(t0, t1, (0, 8))
    edl2["spans"] = edl2["spans"][::-1]
    edl2["spans"][0]["role"], edl2["spans"][1]["role"] = "hook", "payoff"
    edl2["spans"][1]["word_to"] = 4  # "Montivo is a video editor": the turn goes on after it
    t_mid = R.cut_map(edl2)[0].cut_out - 0.1
    fix2, why2 = R.resolve(_issue(t_mid, "extend_span", edge="out", seconds=2.0), 0, edl2, words2, R.cut_map(edl2))
    assert fix2 is None and why2.startswith(R.NO_EXTEND)


# --------------------------------------------------------------------------- a cut short of the brief's length

def _length_edl(kept: list[dict], runtime: float, **extra) -> dict:
    checks = [{"item": "35 to 45 s vertical reel", "kind": "length", "quote": "", "min_s": 35, "max_s": 45}]
    sp = {"id": "s1", "turn_id": kept[0]["turn_id"], "word_from": kept[0]["i"], "word_to": kept[-1]["i"],
          "words": " ".join(w["w"] for w in kept), "role": "hook"}
    return {"spans": [sp], "brief_checks": checks, "runtime_s": runtime, "unplaced": [], **extra}


def test_the_passages_a_short_cut_could_add_leave_out_retakes_fillers_and_echoes():
    from montivo.engine import edl as E
    t0 = _words(0, "um okay so I wanted to show you this thing Montivo is an Montivo is a video editor that edits "
                   "itself", 2.0)
    t1 = _words(1, "then it renders its own cut okay then it renders the cut and watches its own render", 60.0)
    w = R.Words(t0 + t1)
    edl = _length_edl(t0[13:], 20.0)  # s1 = "Montivo is a video editor that edits itself"
    edl["spans"].append({"id": "s2", "turn_id": 1, "word_from": 7, "word_to": 16, "role": "payoff",
                         "words": " ".join(x["w"] for x in t1[7:])})
    pieces = [" ".join(x["w"] for x in p) for _, p in E.unused_pieces(edl, w)]
    # "Montivo is an Montivo" and "then it renders its own cut" retake kept lines; the fillers alone are no passage
    assert pieces == ["um okay so I wanted to show you this thing"]
    lines = E.unused_passages(edl, w)
    assert len(lines) == 1 and lines[0].startswith("turn 0, 2.0-") and "wanted to show you" in lines[0]
    assert E.usable_unused_s(edl, w, None) == round(E.WORD_S * 7, 2)  # "um", "okay" and "so" are fillers


def test_a_length_only_fillers_and_retakes_could_reach_is_unreachable_and_not_repaired():
    # the live 90 s upload: 24 s planned, about 9.6 s of usable speech left for a 35 to 45 s brief; two repairs came back
    # 25.2 / 27.1 s or with a retake, and the reviewer called the length a blocker
    from montivo.engine import edl as E
    kept = _words(0, "Montivo is a video editor that edits itself", 30.0)
    edl = _length_edl(kept, 24.1, source_speech_s=82.8, source_usable_s=33.7)
    c = E.check_brief(edl)[0]
    assert c["status"] == "unreachable" and "33.7 s of usable speech" in c["found"]
    assert not any(p.startswith("length") for p in E.plan_check(edl, 40.0))
    assert E.length_short(edl) == 0.0
    edl["source_usable_s"] = 41.0  # enough usable speech left: missed, and the repair is told what is left out
    assert E.check_brief(edl)[0]["status"] == "missed" and E.length_short(edl) == 10.9
    del edl["source_usable_s"]  # an EDL from before keeps the old verdict
    assert E.check_brief(edl)[0]["status"] == "missed"


def test_a_short_spark_plan_gets_the_unused_passages_as_a_problem():
    from montivo.run import short_passages
    t0 = _words(0, "I wanted to show you this thing I have been working on Montivo is a video editor", 2.0)
    w = R.Words(t0)
    edl = _length_edl(t0[11:], 24.1, source_usable_s=40.0)
    found = short_passages(edl, w)
    assert len(found) == 1 and found[0].startswith("length: 10.9 s short") and "I wanted to show you" in found[0]
    edl["runtime_s"] = 36.0
    assert short_passages(edl, w) == []


# --------------------------------------------------------------------------- a word said twice

def _stutter_turn() -> list[dict]:
    # "Montivo is ... is a video editor that edits itself.": a pause between the two copies of "is" (10.8-11.2 s)
    tw = _words(0, "Montivo is is a video editor that edits itself.", 10.0, step=0.4)
    for w in tw[2:]:
        w["t_start"], w["t_end"] = round(w["t_start"] + 0.4, 3), round(w["t_end"] + 0.4, 3)
    return tw


def test_a_word_said_twice_with_a_pause_between_is_a_stutter():
    from montivo.engine import edl as E
    tw = _stutter_turn()
    edl = {"spans": [{"id": "s1", "turn_id": 0, "word_from": 0, "word_to": 8, "role": "hook"}]}
    assert E.stutters(edl, R.Words(tw), {0: {2}}) == [("s1", 1, 2)]
    assert E.stutters(edl, R.Words(tw), {}) == []  # the copies abut: Voice doubles as often as speakers (015 "meta meta")
    very = _stutter_turn()
    very[1]["w"] = very[2]["w"] = "very"
    assert E.stutters(edl, R.Words(very), {0: {2}}) == []  # "very very" is said on purpose
    echo = _stutter_turn()
    echo[2]["t_start"] = round(echo[1]["t_end"] + 0.05, 3)  # Voice's 40 ms echo copy: the captions drop it already
    echo[2]["t_end"] = round(echo[2]["t_start"] + 0.04, 3)
    assert E.stutters(edl, R.Words(echo), {0: {2}}) == []


def test_v1_cuts_a_stutter_copy(monkeypatch, tmp_path):
    from montivo import run
    from tests.test_review import FakeAudio
    tw = _stutter_turn()
    t1 = _words(1, "it reviews its cut.", 30.0, step=0.4)
    w = R.Words(tw + t1)
    sp = {"id": "s1", "order": 0, "turn_id": 0, "speaker": "A", "role": "hook", "word_from": 0, "word_to": 8,
          "t_in": 10.0, "t_out": tw[8]["t_end"], "duration": 0.0, "verbatim": True, "match_ratio": 1.0, "callout": "",
          "why": "", "contiguous_with_previous": False, "words": ""}
    sp2 = {**sp, "id": "s2", "role": "payoff", "turn_id": 1, "word_from": 0, "word_to": 3, "t_in": 30.0, "t_out": 31.6}
    edl = {"version": 1, "brief": "b", "source": {"fps": FPS, "width": 1920, "height": 1080}, "spans": [sp, sp2],
           "runtime_s": 0.0, "captions": [], "callouts": [], "caption_overrides": []}
    for s in edl["spans"]:
        R.refresh_span(s, w)
    monkeypatch.setattr(R, "stuck_edges", lambda *a, **k: [])
    monkeypatch.setattr(run, "pause_marks", lambda words, audio: {0: {2}})
    # quiet before "Montivo", in the stutter's pause and after each clip
    audio = FakeAudio((9.8, 10.0), (10.82, 11.18), (14.0, 14.4), (29.8, 30.0), (31.6, 32.0))
    new = run.cut_stutters(tmp_path, edl, w, audio, log=lambda *_: None)
    kept = " ".join(s["words"] for s in new["spans"])
    assert kept.startswith("Montivo is a video editor") and kept.count(" is ") == 1, kept
    # the first copy would leave "Montivo" a one-word clip, so the second goes: "Montivo is | a video editor"
    assert new["v1_stutters"][0]["word"] == 2 and new["version"] == 1 and len(new["spans"]) == 3
    monkeypatch.setattr(R, "stuck_edges", lambda run_dir, e, *a: ["x"] * len(e["spans"]))  # more stuck edges: kept
    assert run.cut_stutters(tmp_path, edl, w, audio, log=lambda *_: None) is edl
