"""A2 + B5 (30/10): the gate's checks that need no cut run on the EDL before a render. v1 is fixed at $0 before the
first paid review (cut edges inside speech, captions forced over the head, the head out of the crop), and a review
patch is checked the same way before it is rendered and reviewed again."""
import copy

from montivo.engine import review as R


def _gate(reasons):
    return {"blocked": bool(reasons), "reasons": list(reasons), "issues": [{"check": "loud_edge"}] if reasons else []}


class FakeFix:
    def __init__(self, n):
        self.fingerprint = f"move_cut:s{n}:edge=out"


def test_preflight_fixes_until_the_checks_clear(monkeypatch):
    gates = iter([_gate(["cut at 3.00 s inside speech"]), _gate([])])
    monkeypatch.setattr(R, "measured_checks", lambda run_dir, edl, audio: next(gates))
    monkeypatch.setattr(R, "plan_fixes", lambda review, edl, words, audio, gate: ([FakeFix(2)], []))

    def apply(edl, fixes, words, audio):
        new = copy.deepcopy(edl)
        new["version"] = edl["version"] + 1
        new["moved"] = True
        return new, [{"op": "move_cut", "target": "s2", "fingerprint": fixes[0].fingerprint, "note": "valley"}]
    monkeypatch.setattr(R, "apply_fixes", apply)
    edl, rec = R.preflight(None, {"version": 1, "spans": []}, None, None, log=lambda m: None)
    assert edl["moved"] and edl["version"] == 1  # the fixes are part of v1, not a new version
    assert rec["clear"] and rec["fixes"][0]["target"] == "s2" and rec["rounds"] == 2


def test_preflight_stops_when_a_fix_repeats_or_nothing_applies(monkeypatch):
    monkeypatch.setattr(R, "measured_checks", lambda run_dir, edl, audio: _gate(["cut at 3.00 s inside speech"]))
    monkeypatch.setattr(R, "plan_fixes", lambda review, edl, words, audio, gate: ([FakeFix(2)], []))
    monkeypatch.setattr(R, "apply_fixes", lambda edl, fixes, words, audio: (
        dict(edl), [{"op": "move_cut", "target": "s2", "fingerprint": fixes[0].fingerprint, "note": "valley"}]))
    edl, rec = R.preflight(None, {"version": 1, "spans": []}, None, None, log=lambda m: None)
    assert not rec["clear"] and rec["left"] == ["cut at 3.00 s inside speech"] and len(rec["fixes"]) == 1
    assert "repeat" in rec["stop"]
