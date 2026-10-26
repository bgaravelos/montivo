"""Offline: the two-pass loudness plan from a recorded loudnorm measuring pass (runs/012 cut_v2, 27/10). No media."""
from pathlib import Path

from montivo.engine.render import (LOUDNORM, limiter_chain, linear_ok, loudnorm_filter, normalize_plan, parse_loudnorm,
                                   target_lra)

FIRST = Path(__file__).resolve().parent / "fixtures" / "loudnorm_first_pass_012.log"
# the measuring passes after gain + limiter on the same audio, as ffmpeg printed them (input_i, input_tp, ...)
LIMITED = [{"input_i": -14.65, "input_tp": -2.0, "input_lra": 3.3, "input_thresh": -25.05, "target_offset": -0.18},
           {"input_i": -14.17, "input_tp": -2.0, "input_lra": 3.2, "input_thresh": -24.57, "target_offset": -0.28}]


def test_parse_recorded_first_pass():
    m = parse_loudnorm("ffmpeg banner\n" + FIRST.read_text() + "[out#0/null] size=N/A\n")
    assert m["input_i"] == -28.41 and m["input_tp"] == -8.42 and m["input_lra"] == 4.2 and m["input_thresh"] == -38.85
    assert m["normalization_type"] == "dynamic" and m["target_offset"] == 0.3
    assert parse_loudnorm("no json here") is None


def test_second_pass_filter_is_linear_with_the_measured_values():
    m = parse_loudnorm(FIRST.read_text())
    f = loudnorm_filter(11.0, m)
    assert f.startswith("loudnorm=I=-14.0:TP=-1.5:LRA=11:")
    assert "measured_I=-28.41:measured_LRA=4.2:measured_TP=-8.42:measured_thresh=-38.85:offset=0.3:linear=true" in f
    assert f.endswith(":print_format=json")
    assert "measured" not in loudnorm_filter(11.0)  # the measuring pass


def test_quiet_source_with_low_peaks_goes_linear_in_two_passes():
    m = {"input_i": -20.0, "input_tp": -9.0, "input_lra": 4.0, "input_thresh": -30.0, "target_offset": 0.1}
    assert linear_ok(m)  # +6 dB of gain puts the peak at -3 dBTP
    rec, chain = normalize_plan(m, measure=lambda pre: (_ for _ in ()).throw(AssertionError("no limiter pass")))
    assert rec["mode"] == "linear" and rec["passes"] == 2 and rec["limiter"] is None
    assert chain.startswith("loudnorm=") and "linear=true" in chain and chain.endswith("aresample=48000")


def test_peaks_over_tp_get_gain_and_limiter_then_linear():
    # runs/012: +14.4 dB to -14 LUFS would put the -8.42 dBTP peaks at +6: loudnorm alone falls back to dynamic
    first = parse_loudnorm(FIRST.read_text())
    assert not linear_ok(first)
    seen = []
    rec, chain = normalize_plan(first, measure=lambda pre: (seen.append(pre), LIMITED[len(seen) - 1])[1])
    assert rec["mode"] == "limiter+linear" and rec["passes"] == 4 and rec["limiter"]["tries"] == 2
    assert [p.split("volume=")[1].split("dB")[0] for p in seen] == ["14.41", "15.06"]  # corrected for what the limiter took
    assert rec["limiter"]["gain_db"] == 15.06 and rec["limiter"]["ceiling_dbtp"] == -2.0
    assert "alimiter=limit=0.7943" in chain and "latency=1" in chain and "aresample=192000" in chain
    assert "measured_I=-14.17" in chain and "linear=true" in chain


def test_dynamic_fallback_is_recorded_when_the_limiter_cannot_make_room():
    first = parse_loudnorm(FIRST.read_text())
    stuck = {"input_i": -16.0, "input_tp": -2.0, "input_lra": 3.0, "input_thresh": -26.0, "target_offset": 0.0}
    rec, chain = normalize_plan(first, measure=lambda pre: stuck)
    assert rec["mode"] == "dynamic" and rec["limiter"]["tries"] == 3 and rec["passes"] == 5


def test_lra_target_is_raised_over_the_measured_lra():
    assert target_lra({"input_lra": 4.2}) == LOUDNORM["LRA"]
    assert target_lra({"input_lra": 13.4}) == 15.0
    m = {"input_i": -20.0, "input_tp": -12.0, "input_lra": 13.4, "input_thresh": -30.0, "target_offset": 0.0}
    rec, chain = normalize_plan(m, measure=lambda pre: None)
    assert rec["lra_raised"] and "LRA=15:" in chain and rec["mode"] == "linear"


def test_limiter_ceiling_sits_under_tp():
    assert "volume=15.06dB,alimiter=limit=0.7943" in limiter_chain(15.06)  # -2.0 dBFS
