"""Offline: the caption look (graphics.normalize_look and the presets), a fixed caption position and pinned callouts in
build_overlays, the preview's draw_at, the `style` human edit and the review loop keeping a person's placement."""
from types import SimpleNamespace

import pytest
from PIL import Image

from montivo import human
from montivo.engine import review as R
from montivo.engine.captions import CAPTION_TOP, build_overlays, fit_line
from montivo.engine.graphics import FONTS, PRESETS, CaptionStyle, Look, draw_caption, font_path, normalize_look
from tests.test_captions import BILLY


def scene(look=None, callout_xy=None):
    """test_captions' end-to-end scene: Billy's payoff line, one segment, a head high in the frame, one callout."""
    words = [{"w": w, "t_start": a, "t_end": b, "turn_id": 2, "i": 71 + k} for k, (w, a, b) in enumerate(BILLY)]
    span = {"id": "s6", "turn_id": 2, "word_from": 71, "word_to": 82, "t_in": 117.9, "t_out": 124.06, "duration": 6.16,
            "callout": "Raw in, cut out"}
    if callout_xy:
        span["callout_xy"] = callout_xy
    edl = {"source": {"width": 1920, "height": 1080, "fps": 30.0}, "spans": [span], **({"look": look} if look else {})}
    seg = SimpleNamespace(t_in=117.9, t_out=124.067, duration=6.1667, fps=30.0, span_ids=["s6"], frames=185, index=0)
    path = SimpleNamespace(xs=[640] * 185, crop_w=608, crop_h=1080)
    box = lambda b: {"source": {"width": 960, "height": 540}, "objects": {"0": [{"t": 117.0, "box": b}, {"t": 125.0, "box": b}]}}  # noqa: E731
    return edl, words, seg, path, box([400, 40, 540, 250]), box([200, 40, 700, 539])


def test_every_preset_and_font_normalizes_and_draws(tmp_path):
    for name in PRESETS:
        look = normalize_look({"preset": name})
        assert look.preset == name and font_path(look.font).endswith(FONTS[look.font][1])
        w, h = draw_caption(["Four", "Meta", "models"], 1, [False, True, False], CaptionStyle.from_look(look),
                            tmp_path / f"{name}.png")
        im = Image.open(tmp_path / f"{name}.png")
        assert im.size == (w, h) and im.getbbox() is not None
    assert normalize_look(None) == Look()  # no look: the marker render it always had


def test_a_look_is_checked():
    assert normalize_look({"preset": "box", "font": "anton", "top": 1100}).box == "#000000C8"
    for bad, msg in [({"preset": "neon"}, "unknown preset"), ({"colour": "#fff"}, "unknown look key"),
                     ({"text": "white"}, "colour"), ({"font": "comic"}, "unknown font"), ({"size": 400}, "between"),
                     ({"active": "blink"}, "active"), ({"position": "left"}, "position")]:
        with pytest.raises(ValueError, match=msg):
            normalize_look(bad)


def test_marker_style_is_the_old_style():
    st = CaptionStyle.from_look(Look())
    assert (st.size, st.stroke, st.space, st.pad_x, st.pad_y) == (84, 7, 22, 18, 10) and not st.upper


def test_fixed_position_and_pinned_callout(tmp_path):
    edl, words, seg, path, head, person = scene({"position": "fixed", "top": 600}, callout_xy=[50, 1880])
    fields, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path)
    assert all(c["placement"] == "fixed" and c["box"][1] == 600 for c in fields["captions"])
    call = fields["callouts"][0]
    assert call["placement"] == "pinned" and call["box"][0] == 50 and call["box"][1] == 1920 - call["box"][3]  # kept in frame
    assert fields["caption_style"]["look"]["top"] == 600


def test_auto_position_starts_from_the_looks_top(tmp_path):
    edl, words, seg, path, head, person = scene({"top": 1200})
    fields, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path)
    assert {c["box"][1] for c in fields["captions"]} == {1200}
    edl, *rest = scene()
    words, seg, path, head, person = rest
    fields, _ = build_overlays(edl, words, [seg], [path], head, person, tmp_path / "b")
    assert {c["box"][1] for c in fields["captions"]} == {CAPTION_TOP}


def test_draw_at_draws_only_what_is_on_screen(tmp_path):
    edl, words, seg, path, head, person = scene({"preset": "bold_pop"})
    full, ovs = build_overlays(edl, words, [seg], [path], head, person, tmp_path / "a")
    t = full["captions"][1]["words"][0]["t_in"] + 0.01
    part, one = build_overlays(edl, words, [seg], [path], head, person, tmp_path / "b", draw_at=t)
    assert part["captions"] == full["captions"] and len(one[0]) < len(ovs[0])
    assert all(o.t_in <= t < o.t_out for o in one[0]) and len(list((tmp_path / "b").glob("*.png"))) == len(one[0])
    assert all(len(c["words"]) <= 2 for c in full["captions"]) and full["captions"][0]["text"].isupper() is False


def test_an_over_wide_line_shrinks_to_fit():
    st = CaptionStyle.from_look(normalize_look({"preset": "bold_pop"}))
    small = fit_line("Four Meta models do all".split(), st)
    from montivo.engine.graphics import line_width
    assert small.size < st.size and line_width("Four Meta models do all".split(), small) <= st.max_w
    assert fit_line(["Hi."], st) is st


def test_style_fixes():
    edl, *_ = scene()
    look, fixes = human.style_fixes(edl, {"preset": "box", "position": "fixed", "top": 1180}, {"s6": [10.4, 300]})
    assert look["preset"] == "box" and look["top"] == 1180
    assert [f["op"] for f in fixes] == ["look", "callout_pin"] and fixes[1]["args"]["xy"] == [10, 300]
    assert "preset 'marker' -> 'box'" in fixes[0]["note"]
    with pytest.raises(SystemExit, match="look: unknown preset"):
        human.style_fixes(edl, {"preset": "neon"}, {})
    with pytest.raises(SystemExit, match="has no callout"):
        human.style_fixes(edl, None, {"s9": [1, 1]})
    with pytest.raises(SystemExit, match="nothing to change"):
        human.style_fixes(edl, {"preset": "marker"}, {"s6": None})


def test_a_replan_keeps_pinned_callouts_by_text():
    prev = {"spans": [{"id": "s2", "callout": "Raw in, cut out", "callout_xy": [40, 300]}]}
    new = {"spans": [{"id": "s4", "callout": "raw in, cut out"}, {"id": "s5", "callout": "Other"}]}
    assert human.carry_callout_places(prev, new) == ["raw in, cut out"]
    assert new["spans"][0]["callout_xy"] == [40, 300] and "callout_xy" not in new["spans"][1]


def test_the_review_loop_keeps_a_fixed_caption_position():
    caps = {"over_face": [{"t_in": 1.0, "t_out": 2.0, "text": "hi there", "placement": "fixed"},
                          {"t_in": 3.0, "t_out": 4.0, "text": "moved", "placement": "below_head"}]}
    gate = R.gate_check(caps, [], {}, {})
    assert gate["reasons"] == ["caption over the head at 3.00-4.00 s"]
    from tests.test_review import issue, make_edl
    edl, words = make_edl()
    edl["captions"] = [{"id": "c1", "spans": ["s1"], "text": "w00 w01", "t_in": 1.5, "t_out": 2.1,
                        "words": [{"w": "w00", "t_in": 1.5, "t_out": 1.8}, {"w": "w01", "t_in": 1.8, "t_out": 2.1}],
                        "box": [300, 800, 400, 120], "head_box": [300, 700, 700, 1000], "placement": "fixed"}]
    fix, why = R.resolve(issue(1.8, "caption_fix"), 0, edl, words, R.cut_map(edl))
    assert fix is None and "a person placed the captions" in why
    edl["captions"][0]["placement"] = "lower_third"
    fix, _ = R.resolve(issue(1.8, "caption_fix"), 0, edl, words, R.cut_map(edl))
    assert fix is not None and fix.op == "caption_move"


def test_style_routes_check_the_body(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from montivo.server.app import create_app
    from tests.test_ui import _run_folder
    _run_folder(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    c = TestClient(create_app(tmp_path))
    info = c.get("/api/looks").json()
    assert [p["id"] for p in info["presets"]] == list(PRESETS) and info["default"]["preset"] == "marker"
    assert c.get("/api/runs/r1").json()["versions"][0]["look"]["preset"] == "marker"
    r = c.post("/api/runs/r1/style", json={"look": {"preset": "neon"}})
    assert r.status_code == 400 and "unknown preset" in r.json()["error"]
    assert c.post("/api/runs/r1/style", json={"callouts": {"s1": [1]}}).status_code == 400
    assert c.post("/api/runs/r1/style", json={}).status_code == 400
    assert c.post("/api/runs/r1/style", json={"look": {"preset": "box"}, "review": 1}).status_code == 409  # review needs the key
    assert c.post("/api/runs/r1/preview", json={"v": 9, "t": 1.0}).status_code == 404


def test_a_sentence_look_is_settled_and_kept_off_the_face(monkeypatch, tmp_path):
    from montivo.engine import look as L
    assert L.settle({"size": 300, "text": "yellow", "box": "", "top": 50}, Look())[1] == \
        ["size 300 -> 160", "top 50 -> 160", "text 'yellow' kept as '#FFFFFF'"]
    edl = {"captions": [{"head_box": [300, 825, 760, 1400]}, {"head_box": [310, 830, 770, 1390]}, {"head_box": [300, 800, 760, 1420]}]}
    assert L.face_band(edl) == (825, 1400) and "at or below 1436" in L.face_note(edl, 84)
    answer = {**Look().to_json(), "position": "fixed", "top": 1150, "text": "#FFC629", "why": "higher and yellow"}
    answer.pop("highlight_off")

    class FakeSpark:
        def __init__(self, *a, **k): pass
        def call(self, *a, **k): return SimpleNamespace(parsed=dict(answer))
    monkeypatch.setattr("montivo.engine.spark.Spark", FakeSpark)
    monkeypatch.setattr(L, "backdrop", lambda *a, **k: {"reads_as": "light", "mean_luma": 0.8})
    look, why, fixed = L.suggest(tmp_path, edl, "yellow and higher", ledger=None)
    assert look["position"] == "auto" and look["text"] == "#FFC629" and why == "higher and yellow"
    assert any("on the face" in f for f in fixed)
    assert set(L.schema()["required"]) == set(L.schema()["properties"])  # strict: every field required
