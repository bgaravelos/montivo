"""The clean export (debugging day 3, a code-review finding of day 1): the version without burned-in captions must be
the same picture as the version minus its caption PNGs. Without captions the render used to load no Voice words and
lay out no caption lines, so the punch-ins lost their emphasis words and gained the jump-cut zooms the captions had
blocked, the pip still went to another corner and the callouts moved into the room the captions had taken."""
import json
from types import SimpleNamespace

from montivo.engine.captions import build_overlays
from tests.test_captions import BILLY
from tests.test_segcache import edl_for, needs_ffmpeg, source, words  # noqa: F401 (fixture)


def test_a_layout_without_caption_pngs_keeps_every_box(tmp_path):
    ws = [{"w": w, "t_start": a, "t_end": b, "turn_id": 2, "i": 71 + k} for k, (w, a, b) in enumerate(BILLY)]
    edl = {"source": {"width": 1920, "height": 1080, "fps": 30.0},
           "spans": [{"id": "s6", "turn_id": 2, "word_from": 71, "word_to": 82, "t_in": 117.9, "t_out": 124.06,
                      "duration": 6.16, "callout": "Raw in, cut out"}]}
    seg = SimpleNamespace(t_in=117.9, t_out=124.067, duration=6.1667, fps=30.0, span_ids=["s6"], frames=185)
    path = SimpleNamespace(xs=[640] * 185, crop_w=608, crop_h=1080)
    head = {"source": {"width": 960, "height": 540}, "objects": {"0": [{"t": 117.0, "box": [400, 40, 540, 250]},
                                                                     {"t": 125.0, "box": [400, 40, 540, 250]}]}}
    shown, ovs = build_overlays(edl, ws, [seg], [path], head, None, tmp_path / "a", title_s=1.5)
    clean, bare = build_overlays(edl, ws, [seg], [path], head, None, tmp_path / "b", title_s=1.5, draw_captions=False)
    assert clean == {**shown, "caption_style": clean["caption_style"]} and clean["captions"]
    assert [o.png.name for o in bare[0]] == ["callout_s6.png"]
    assert [(o.x, o.y, o.t_in, o.t_out) for o in bare[0]] == [(o.x, o.y, o.t_in, o.t_out) for o in ovs[0]
                                                               if o.png.name.startswith("callout")]
    assert not list((tmp_path / "b").glob("cap_*.png"))


@needs_ffmpeg
def test_the_clean_render_plans_punch_ins_and_stills_on_the_captioned_layout(tmp_path, source, monkeypatch):  # noqa: F811
    from montivo.engine import render as R
    from montivo.run import render_run
    seen = []

    def plan(edl, segments, paths, ws, audio, head, captions, title_s=0.0):
        seen.append((len(ws), [c["text"] for c in captions]))
        return {}, []
    monkeypatch.setattr(R, "plan_punches", plan)
    run = tmp_path / "run"
    (run / "voice").mkdir(parents=True)
    (run / "voice" / "voice.json").write_text(json.dumps({"words": words()}))
    for name, captions in (("edl_shown.json", True), ("edl_clean.json", False)):
        (run / name).write_text(json.dumps({**edl_for(source), "punch": True, "runtime_s": 2.45}))
        render_run(run, edl_path=run / name, out_name=name.replace("edl", "cut").replace(".json", ".mp4"),
                   captions=captions, title=False, loudnorm=False, log=lambda *a, **k: None)
    assert len(seen) == 2 and seen[0] == seen[1] and seen[0][0] == 6 and seen[0][1]
    clean = json.loads((run / "edl_clean.json").read_text())
    assert clean["captions"] == [] and clean["render"]["captions"] == 0  # nothing burned in, nothing claimed
    assert not list((run / "render" / "overlays").glob("cap_*.png"))
