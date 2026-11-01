"""First cut on the hard clips (1/11): what the first review of 013, d1-nasa, d1-tedx and heli-01 still failed on,
each fixed before the first render (docs/notes/first-cut-hard_2026-11-01.md)."""
from montivo.engine import edl as E
from montivo.engine.edges import Words


def _words(turn: int, text: str, t0: float = 10.0, step: float = 0.4) -> list[dict]:
    return [{"turn_id": turn, "i": k, "w": w, "t_start": round(t0 + k * step, 3), "t_end": round(t0 + (k + 1) * step, 3)}
            for k, w in enumerate(text.split())]


def test_a_callout_shows_when_its_words_are_said():
    # heli-01 v1: "716 SAVES" came up at the span start, 7 s before "716 saves" is spoken (callouts_anchored 4)
    tw = _words(5, "Um our squadron's got a rich history it's been at Fairchild for over 50 years we passed 716 saves")
    w = Words(tw)
    s1 = {"id": "s1", "turn_id": 5, "word_from": 0, "word_to": len(tw) - 1, "t_in": tw[0]["t_start"],
          "t_out": tw[-1]["t_end"], "callout": "716 SAVES"}
    s0 = {"id": "s0", "turn_id": 5, "word_from": 0, "word_to": 3, "t_in": 10.0, "t_out": 11.6, "callout": "rich history"}
    edl = {"spans": [s0, s1], "title_card_text": "Huey", "title_style": "header"}
    E.anchor_callouts(edl, w)
    at = tw[17]["t_start"] - s1["t_in"]  # "716", 6.8 s in
    room = (s1["t_out"] - s1["t_in"]) - 0.3 - 2.0  # the callout keeps its 2 s on screen before the span ends
    assert abs(s1["callout_shift_s"] - min(room, at - 0.3)) < 0.01 and s1["callout_shift_s"] > 5
    assert "callout_shift_s" not in s0  # words not in the span: the span-start timing stays
    # a callout whose words the span never says keeps its time; a person's timing is never replaced
    s2 = dict(s1, callout="NVG HOIST", callout_shift_s=None)
    s3 = dict(s1, callout="716 SAVES", callout_shift_s=1.0)
    E.anchor_callouts({"spans": [s0, s2, s3]}, w)
    assert not s2.get("callout_shift_s") and s3["callout_shift_s"] == 1.0


def test_the_spark_plan_prompt_asks_for_a_real_hook_and_the_open_one_is_unchanged(tmp_path):
    from montivo.config import OPEN_MODEL
    from montivo.engine.spark import HOOK_RULE
    from montivo.ledger import Ledger
    from tests.test_plan_prompt import _Capture
    watch = {"summary": "s", "visual_shots": [], "speaker_map": []}
    sp = _Capture(tmp_path, Ledger(tmp_path / "ledger.json"), log=lambda *_: None)
    sp.plan("brief", "[turn 0] hi", watch, 40.0)
    assert HOOK_RULE in sp.captured[1] and "usually NOT the start of the recording" in sp.captured[1]
    op = _Capture(tmp_path, Ledger(tmp_path / "ledger2.json"), model=OPEN_MODEL, log=lambda *_: None)
    op.plan("brief", "[turn 0] hi", watch, 40.0)
    assert HOOK_RULE not in op.captured[1]
    assert sp.captured[1].replace(HOOK_RULE, "") == op.captured[1]


def test_a_false_start_inside_a_span_is_a_plan_problem_and_a_parallel_is_not():
    # d1-tedx v1: "we had we've had it we had enough" inside s6 (a brief_covered major the loop could not drop)
    edl = {"spans": [{"id": "s6", "words": "for 15 minutes we had we've had it we had enough and we left"},
                     {"id": "s4", "words": "everyone seemed to speak and everyone seemed to be looking down"},
                     {"id": "s2", "words": "Brussels sprouts all all of it"},
                     {"id": "s3", "words": "we stood over there and then over there I showed them"}]}
    found = E.restarts(edl)
    assert len(found) == 1 and found[0].startswith("s6 keeps a false start: \"we had we've had it we had\"")
    assert "we had enough" in found[0]
    heli = {"spans": [{"id": "s4", "words": "on call 48 weeks out of the out of the year up there"}]}
    assert E.restarts(heli)[0].startswith("s4 keeps a false start: \"out of the out of\"")


def test_an_edge_the_speaker_runs_on_across_goes_to_the_planner(monkeypatch):
    # d1-nasa: "Happy Thanksgiving!" (turn 9) starts while turn 8 still talks: no quiet point near the in edge, so
    # neither the preflight nor three loop passes could move it; only another plan can
    from montivo.engine import review as R
    from tests.test_review import FakeAudio
    tw8 = _words(8, "we are grateful for a nation that lets us live free", t0=5.0)
    tw9 = _words(9, "Happy Thanksgiving everyone from space", t0=20.0)
    w = Words(tw8 + tw9)
    spans = [{"id": "s1", "turn_id": 8, "speaker": "A", "role": "hook", "word_from": 0, "word_to": 10, "t_in": 5.0,
              "t_out": 9.4, "duration": 4.4, "words": " ".join(x["w"] for x in tw8)},
             {"id": "s2", "turn_id": 9, "speaker": "B", "role": "payoff", "word_from": 0, "word_to": 4, "t_in": 20.0,
              "t_out": 22.0, "duration": 2.0, "words": " ".join(x["w"] for x in tw9)}]
    edl = {"spans": spans, "source": {"fps": 30.0}}
    monkeypatch.setattr(R, "preflight", lambda run_dir, e, *a, **k: (e, {}))
    audio = FakeAudio((9.3, 9.6), (21.95, 22.4))  # quiet after s1, after s2; speech straight into s2's first word
    found = R.stuck_edges(None, edl, w, audio)
    assert len(found) == 1 and found[0].startswith("s2 opens on \"Happy Thanksgiving everyone from ...\"")
    quiet = FakeAudio((9.3, 9.6), (19.85, 20.05), (21.95, 22.4))
    assert R.stuck_edges(None, edl, w, quiet) == []


def test_a_studio_quiet_floor_does_not_turn_handling_noise_into_speech():
    # d1-nasa: floor -74 dB, speech about -31 dB; the floor rule (-68 dB) counted mic handling at -50 to -60 dB as
    # speech, so a 1.2 s pause stayed whole and the cut held 1.1 s of near silence after "Happy Thanksgiving!"
    import array
    import math
    from montivo.engine.edges import Audio

    class Clip(Audio):
        def __init__(self):
            self.rate = 8000
            self.pcm = array.array("h", [int(6 * math.sin(k * 1.7)) for k in range(20 * self.rate)])  # about -75 dB
            for a, b, amp in ((1.0, 5.0, 900), (6.2, 9.0, 900), (5.3, 5.4, 40)):  # speech -31 dB, a knock -58 dB
                for k in range(int(a * self.rate), int(b * self.rate)):
                    self.pcm[k] = int(amp * math.sin(k / 3))

    clip = Clip()
    speech = clip.level(2.0, 3.0)  # about -34 dB
    assert clip.floor_db() < -70
    assert abs(clip.threshold_db() - (speech - 20.0)) < 1.0  # 20 dB under the speech, not 6 over the floor
    runs = clip.speech_runs(0.5, 9.5)
    assert len(runs) == 2 and runs[0][1] < 5.1 and runs[1][0] > 6.1  # the knock at 5.3 s is not speech: one pause
    assert clip.level(5.3, 5.4) > clip.floor_db() + 6  # ... though the floor rule alone counted it


def test_knocks_after_the_last_word_are_not_speech():
    # d1-nasa s1: "us." (Voice 11.02-13.34) is said by 11.53; two mic knocks (0.10 and 0.07 s) at 12.69 and 13.08 kept
    # the out edge at 13.3, a 1.2 s pause in the cut and the first review's pacing major
    from montivo.engine import edges as G
    runs = [(10.0, 10.78), (10.87, 11.53), (12.69, 12.79), (13.08, 13.15)]
    assert G.without_knocks(runs, first=False) == runs[:2]
    assert G.without_knocks([(2.48, 2.52), (4.07, 4.31), (4.42, 4.9)], first=True) == [(4.07, 4.31), (4.42, 4.9)]
    # a short word close to the speech stays, and so does everything when no run is long enough to be a word
    assert G.without_knocks([(10.0, 11.5), (11.7, 11.8)], first=False) == [(10.0, 11.5), (11.7, 11.8)]
    assert G.without_knocks([(1.0, 1.05), (2.0, 2.08)], first=True) == [(1.0, 1.05), (2.0, 2.08)]
