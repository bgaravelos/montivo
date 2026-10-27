"""Offline: the plan prompt carries the measured speech rate and the movement moments from watch; nothing is sent."""
from pathlib import Path

from montivo.engine import schemas
from montivo.engine.spark import Spark
from montivo.ledger import Ledger


class _Capture(Spark):
    def call(self, name, prompt, schema, file_id=None, max_output_tokens=12000, reasoning_effort=None, **kw):  # noqa: D401
        self.captured = (name, prompt, schema)
        return None


def _spark(tmp_path: Path) -> _Capture:
    return _Capture(tmp_path, Ledger(tmp_path / "ledger.json"), log=lambda *_: None)


def test_plan_prompt_uses_measured_rate_and_moments(tmp_path):
    sp = _spark(tmp_path)
    watch = {"summary": "s", "visual_shots": [], "speaker_map": [],
             "movement_moments": [{"start_s": 82.5, "end_s": 83.5, "what": "leans left and lifts the laptop"}]}
    sp.plan("brief", "[turn 0] hi", watch, 40.0, words_per_s=2.0)
    _, prompt, _ = sp.captured
    assert "about 2.0 words per second" in prompt
    assert "roughly 80 to 96 words" in prompt
    assert "estimated_runtime_s = total kept words / 2.0" in prompt
    assert "82.5-83.5s: leans left and lifts the laptop" in prompt
    assert "2.5 words per second" not in prompt and "/ 2.5" not in prompt


def test_plan_prompt_default_rate_and_no_moments_block(tmp_path):
    sp = _spark(tmp_path)
    sp.plan("brief", "[turn 0] hi", {"summary": "s", "visual_shots": [], "speaker_map": []}, 40.0)
    _, prompt, _ = sp.captured
    assert "about 2.5 words per second" in prompt and "roughly 100 to 120 words" in prompt
    assert "Moments where the speaker moves" not in prompt


def test_watch_prompt_and_schema_ask_for_movement_moments(tmp_path):
    sp = _spark(tmp_path)
    sp.watch("file_x", "[turn 0] hi", "brief", 125.0)
    _, prompt, schema = sp.captured
    assert "5) List moments where the on-camera speaker moves out of centre or handles a prop, with seconds" in prompt
    assert "movement_moments" in schema["properties"] and "movement_moments" in schema["required"]
    assert schemas.WATCH["properties"]["movement_moments"]["items"]["required"] == ["start_s", "end_s", "what"]


def test_plan_prompt_and_schema_define_the_roles(tmp_path):
    # Muse Spark Open, given only the enum, labelled every body span payoff on run 012-open
    sp = _spark(tmp_path)
    sp.plan("brief", "[turn 0] hi", {"summary": "s", "visual_shots": [], "speaker_map": []}, 40.0)
    _, prompt, _ = sp.captured
    assert "'hook' for the first span only" in prompt and "'payoff' for the last span only" in prompt
    role = schemas.PLAN["properties"]["paper_edit"]["items"]["properties"]["role"]
    assert "first span only" in role["description"] and role["enum"] == ["hook", "body", "payoff", "cta"]
