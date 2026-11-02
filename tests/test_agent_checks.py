"""Offline: agent.measured (B1), the review gate's $0 checks on the version Ask Montivo just made, read from the run
folder: a cut edge inside speech and a caption forced over the head are reported (four reasons at most, the rest
counted), a clean version reads clear, no source audio leaves the edge check out, a missing EDL skips the checks; and
keep_checks / checked_text carry them into the patch and the chat line."""
import json

from montivo import agent
from montivo.engine import review as R
from tests.test_edges import SynthAudio
from tests.test_review import make_edl

CLEAN = [(10.05, 11.15), (11.85, 12.65), (30.65, 32.05)]  # every cut edge of make_edl in a pause
LOUD = [(10.05, 11.15), (11.85, 13.2), (30.65, 32.05)]  # s2's out edge (the cut at 3.60 s) inside speech
OVER = {"id": "c2", "t_in": 2.7, "t_out": 3.6, "text": "w06 w07 w08", "spans": ["s2"], "box": [100, 600, 880, 120],
        "head_box": [300, 300, 780, 800], "placement": "auto"}
CLEAR = {"id": "c1", "t_in": 1.5, "t_out": 2.7, "text": "w00 w01 w02 w03", "spans": ["s1"],
         "box": [100, 1300, 880, 120], "head_box": [300, 300, 780, 800]}


def _version(tmp_path, monkeypatch, speech, captions, n=2):
    d = tmp_path / "runs" / "a1"
    d.mkdir(parents=True, exist_ok=True)
    edl, _ = make_edl()
    edl["captions"] = captions
    edl["patches"] = [{"from": n - 1, "source": "human", "action": "ask", "agent": {"message": "x"}, "fixes": []}]
    (d / f"edl_v{n}.json").write_text(json.dumps(edl))
    monkeypatch.setattr(R.Audio, "load", staticmethod(lambda run_dir: SynthAudio(speech) if speech else None))
    return d


def test_a_clean_version_reads_clear(tmp_path, monkeypatch):
    d = _version(tmp_path, monkeypatch, CLEAN, [CLEAR])
    logs = []
    c = agent.measured(d, 2, logs.append)
    assert c == {"ok": True, "reasons": [], "more": 0, "audio": True}
    assert logs == ["agent: checks on v2: clear"]
    assert agent.checked_text(c).startswith("Checked the new cut: no cut lands in speech")


def test_a_loud_edge_and_a_caption_over_the_head_are_reported(tmp_path, monkeypatch):
    d = _version(tmp_path, monkeypatch, LOUD, [CLEAR, OVER])
    c = agent.measured(d, 2, lambda *a: None)
    assert c["ok"] is False and c["audio"] is True and c["more"] == 0
    assert len(c["reasons"]) == 2 and c["reasons"][1] == "caption over the head at 2.70-3.60 s"
    # the source speech runs 11.85-13.2 s with no pause within 0.3 s of the 12.7 s edge
    assert c["reasons"][0] == ("cut at 3.60 s inside speech (out edge over -34.0 dB; speech runs on at the out edge, "
                               "no pause near it)")
    text = agent.checked_text(c)
    assert "cut at 3.60 s inside speech" in text and text.endswith("A review fix or an edit there would clear it.")
    # into the version's patch, where the chat's Fix it reads them
    agent.keep_checks(d, 2, c)
    assert json.loads((d / "edl_v2.json").read_text())["patches"][-1]["agent"]["checks"] == c


def test_a_placement_the_renderer_could_not_avoid_or_a_person_chose_is_not_held(tmp_path, monkeypatch):
    d = _version(tmp_path, monkeypatch, CLEAN, [{**OVER, "placement": "over_head"}, {**CLEAR, **{
        "head_box": [100, 1250, 980, 1500], "placement": "fixed"}}])
    assert agent.measured(d, 2, lambda *a: None)["ok"] is True


def test_more_than_four_reasons_are_counted(tmp_path, monkeypatch):
    caps = [{**OVER, "id": f"c{k}", "t_in": 1.5 + 0.5 * k, "t_out": 2.0 + 0.5 * k} for k in range(5)]
    d = _version(tmp_path, monkeypatch, LOUD, caps)
    c = agent.measured(d, 2, lambda *a: None)
    assert len(c["reasons"]) == 4 and c["more"] == 2  # one loud cut + five captions over the head
    assert "(and 2 more)" in agent.checked_text(c)


def test_without_source_audio_only_the_picture_is_checked(tmp_path, monkeypatch):
    d = _version(tmp_path, monkeypatch, None, [CLEAR])
    c = agent.measured(d, 2, lambda *a: None)
    assert c == {"ok": True, "reasons": [], "more": 0, "audio": False}
    assert "no cut" not in agent.checked_text(c)


def test_a_missing_version_skips_the_checks(tmp_path, monkeypatch):
    d = _version(tmp_path, monkeypatch, CLEAN, [])
    logs = []
    assert agent.measured(d, 7, logs.append) is None
    assert logs and logs[0].startswith("agent: checks on v7 skipped")
    agent.keep_checks(d, 2, None)  # nothing to keep: the EDL is untouched
    assert "checks" not in json.loads((d / "edl_v2.json").read_text())["patches"][-1]["agent"]
