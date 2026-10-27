"""Offline: `montivo run --resume` on a finished run folder. plan.json and edl_v1.json are reused when the brief, target
and planner are the ones the plan was made with; a changed input or --replan makes a new plan, after the old plan.json
and edl_v1.json are kept in superseded/ and any later version is named in a warning. No API call, no render."""
import json

import pytest

from montivo import run as R
from montivo.engine.spark import Spark, SparkCall

BRIEF = "a 40 s cut"
PLAN = {"paper_edit": [{"turn_id": 0, "words": "w0 w1 w2", "role": "hook", "why": "", "callout": ""},
                       {"turn_id": 0, "words": "w4 w5", "role": "payoff", "why": "", "callout": ""}],
        "estimated_runtime_s": 3.0, "title_card_text": "", "caption_style_note": "", "reframe_note": ""}


def folder(tmp_path, run_json=True):
    out = tmp_path / "run"
    out.mkdir()
    probe = {"width": 1920, "height": 1080, "fps": 30.0, "duration": 20.0, "bytes": 1, "has_audio": True, "codec": "h264"}
    (out / "ingest.json").write_text(json.dumps({"source": str(tmp_path / "clip.mp4"), "probe": probe,
                                                 "proxy": str(out / "proxy.mp4"), "proxy_probe": probe,
                                                 "wav": str(out / "audio.wav"), "file_id": "file-1", "upload_seconds": 0}))
    words = [{"w": f"w{k}", "t_start": 1 + 0.5 * k, "t_end": 1.4 + 0.5 * k, "turn_id": 0, "speaker": "A", "i": k}
             for k in range(8)]
    (out / "voice").mkdir()
    (out / "voice" / "voice.json").write_text(json.dumps({"turns": [{"turnId": 0, "startMs": 1000, "endMs": 5000,
                                                                     "speaker": "A", "transcript": ""}], "words": words}))
    (out / "watch.json").write_text(json.dumps({"summary": "", "visual_shots": [], "speaker_map": []}))
    (out / "plan.json").write_text(json.dumps(PLAN))
    if run_json:  # written by a run from before plan_inputs: brief, target and planner say what the plan was made with
        (out / "run.json").write_text(json.dumps({"brief": BRIEF, "target_s": 40.0, "model": "muse-spark-1.3",
                                                  "planner_model": "muse-spark-1.3"}))
    (out / "edl_v1.json").write_text(json.dumps({"version": 1, "runtime_s": 2.4, "spans": [], "unplaced": [],
                                                 "verbatim_rate": 1.0, "marker": "old v1"}))
    (out / "edl_v2.json").write_text(json.dumps({"version": 2, "marker": "patched from the old v1"}))
    return out


@pytest.fixture
def plans(monkeypatch):
    calls = []

    def fake_plan(self, brief, *a, **kw):
        calls.append(brief)
        return SparkCall(name="plan", parsed={**PLAN, "title_card_text": "new"}, status="completed", usage={},
                         cost_usd=0.03, seconds=1.0, response_id="r")

    monkeypatch.setattr(Spark, "plan", fake_plan)
    return calls


def go(out, tmp_path, **kw):
    opts = R.RunOptions(footage=tmp_path / "clip.mp4", brief=kw.pop("brief", BRIEF), out=out, resume=True,
                        skip_track=True, render=False, **kw)
    return R.run(opts)


def test_resume_reuses_the_plan_and_v1_when_nothing_changed(tmp_path, plans):
    out = folder(tmp_path)
    go(out, tmp_path)
    assert plans == []
    assert json.loads((out / "edl_v1.json").read_text())["marker"] == "old v1"
    assert not (out / "superseded").exists()
    log = (out / "run.log").read_text()
    assert "resume: same brief, target and planner as plan.json: plan reused" in log and "resume: edl_v1 from" in log
    assert json.loads((out / "run.json").read_text())["plan_inputs"]["brief"] == BRIEF
    go(out, tmp_path)  # run.json now has plan_inputs: still the same plan
    assert plans == []


@pytest.mark.parametrize("kw, why", [({"replan": True}, "--replan: a new plan"),
                                     ({"brief": "a 30 s cut"}, "brief changed since plan.json"),
                                     ({"target_s": 30.0}, "target_s changed since plan.json")])
def test_a_new_plan_keeps_the_old_v1_and_warns_about_later_versions(tmp_path, plans, kw, why):
    out = folder(tmp_path)
    go(out, tmp_path, **kw)
    assert len(plans) == 1
    log = (out / "run.log").read_text()
    assert f"plan: {why}" in log and "WARNING: edl_v2 were built on the previous plan" in log
    kept = next((out / "superseded").iterdir())
    assert json.loads((kept / "edl_v1.json").read_text())["marker"] == "old v1"
    assert json.loads((kept / "plan.json").read_text()) == PLAN
    assert json.loads((out / "plan.json").read_text())["title_card_text"] == "new"
    assert [s["role"] for s in json.loads((out / "edl_v1.json").read_text())["spans"]] == ["hook", "payoff"]
    assert json.loads((out / "edl_v2.json").read_text())["marker"] == "patched from the old v1"  # never touched
    assert json.loads((out / "run.json").read_text())["plan_inputs"]["brief"] == kw.get("brief", BRIEF)


def test_unrecorded_plan_inputs_make_a_new_plan(tmp_path, plans):
    out = folder(tmp_path, run_json=False)
    go(out, tmp_path)
    assert len(plans) == 1 and "plan: (not recorded) changed since plan.json" in (out / "run.log").read_text()


def test_new_voice_or_watch_means_a_new_plan():
    opts = R.RunOptions(footage=None, brief=BRIEF, out=None, resume=True)
    made = R.plan_inputs(opts)
    assert R.plan_reuse(opts, made, True, True)[0] is True
    assert R.plan_reuse(opts, made, True, False) == (False, "voice or watch is new: a new plan")
    assert R.plan_reuse(opts, made, False, True) == (False, "no plan.json yet: a new plan")
    opts.resume = False
    assert R.plan_reuse(opts, made, True, True) == (False, "a new plan")
