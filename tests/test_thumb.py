"""Offline: the designed cover (engine/thumb.py) on synthetic stills: text kept in the profile grid's 3:4 window and off
the head box, balanced headline lines, the highlight word found, the cutout's text left visible around the person,
SAM image records parsed into boxes, and the design loop with stand-ins for Spark (one fix applied, the best kept)."""
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw

from montivo.engine import spark as S
from montivo.engine import thumb as T

HEAD = [380.0, 820.0, 700.0, 1260.0]


def still(w=1080, h=1920):
    im = Image.new("RGB", (w, h), (200, 190, 170))
    ImageDraw.Draw(im).ellipse(HEAD, fill=(220, 170, 140))
    return im


def person_mask(w=1080, h=1920):
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.ellipse(HEAD, fill=255)
    d.rectangle((HEAD[0] - 150, HEAD[3] - 60, HEAD[2] + 150, h), fill=255)
    return m


def checks(info):
    return {c["id"]: c for c in info["checks"]}


def test_grid_window_matches_the_export_crop():
    assert T.grid_window(1080, 1920) == (0, 240, 1080, 1680)
    assert T.grid_window(1080, 1350) == (34, 0, 1046, 1350)
    assert T.grid_window(1080, 1080) == (135, 0, 945, 1080)


def test_wrap_balances_lines_and_finds_the_highlight():
    words = T.words_of("it checks its own edit")
    lines = T.wrap(words, 170, "inter-tight", 930, 3)
    assert lines and len(lines) <= 3 and sum(len(l) for l in lines) == 5
    assert all(T.line_w(l, T.font(170, "inter-tight")) <= 930 for l in lines)
    assert len(lines) == len(T.greedy([T.font(170, "inter-tight").getlength(w) for w in words], 40, 930))
    assert T.wrap(T.words_of("supercalifragilisticexpialidocious"), 190, "inter-tight", 600, 3) is None
    assert T.hi_index(words, "Edit") == 4 and T.hi_index(words, "edit.") == 4 and T.hi_index(words, "nope") is None


def test_headline_and_sticker_keep_off_the_face_and_inside_the_grid():
    for template in ("headline", "sticker"):
        img, info = T.render(still(), HEAD, template, "It checks its own edit", "edit")
        c = checks(info)
        assert img.size == (1080, 1920)
        assert c["in_grid"]["ok"] and c["off_face"]["ok"] and c["tile_size"]["ok"], (template, info["checks"])
        assert info["text_box"][3] < info["head_box"][1]  # the text ends above the head
        assert info["highlight"] == "EDIT"
        assert (info["head_box"][3] - info["head_box"][1]) > (HEAD[3] - HEAD[1])  # the face punched in


def test_feed_aspects_lay_out_too():
    for w, h in ((1080, 1350), (1080, 1080)):
        head = [w * 0.38, h * 0.45, w * 0.62, h * 0.75]
        im = Image.new("RGB", (w, h), (120, 120, 120))
        _, info = T.render(im, head, "headline", "Four models one edit", "one")
        assert checks(info)["in_grid"]["ok"], (w, h, info)


def test_frame_or_no_text_is_the_still_itself():
    im = still()
    out, info = T.render(im, HEAD, "headline", "   ")
    assert info["template"] == "frame" and out.tobytes() == im.tobytes()


def test_cutout_needs_its_mask_and_leaves_the_text_readable():
    try:
        T.render(still(), HEAD, "cutout", "Self reviewing AI")
        raise AssertionError("no mask, no cutout")
    except T.NeedMask:
        pass
    _, info = T.render(still(), HEAD, "cutout", "Self reviewing AI", "AI", mask=person_mask())
    c = checks(info)
    assert c["text_visible"]["ok"] and 0.72 <= info["text_visible"] < 1.0  # part of it runs behind the head
    assert info["word_visible"] >= T.WORD_VISIBLE  # and no word disappears behind it
    _, info = T.render(still(), HEAD, "cutout", "Self reviewing AI", "AI", mask=person_mask())
    assert info["word_visible"] >= T.WORD_VISIBLE  # a short last line centred over the head moves clear of it
    assert info["text_box"][3] > info["head_box"][1]  # behind the head, not above it
    assert "off_face" not in c


def test_long_headlines_are_flagged():
    _, info = T.render(still(), HEAD, "headline", "one two three four five six seven eight")
    assert not checks(info)["words"]["ok"]


def test_parse_sam_image_records():
    text = ("<0f>3<|box;x1=10;y1=20;x2=109;y2=219;w=480;h=270;c=0.97|><|mask;x=0;y=0;data=50,25,~abc|>"
            "4<|box;x1=300;y1=40;x2=399;y2=139;w=480;h=270|><|mask;x=0;y=0;data=10,10,!xyz|>")
    recs = T.parse_masks(text, 2.0)
    assert [r["id"] for r in recs] == ["3", "4"]
    assert recs[0]["box"] == [20.0, 40.0, 220.0, 440.0] and recs[0]["c"] == 0.97
    assert (recs[0]["mh"], recs[0]["mw"], recs[0]["payload"]) == (50, 25, "~abc") and recs[1]["payload"] == "!xyz"


def test_spark_images_go_inline_and_stay_out_of_the_request_file(tmp_path):
    p = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(p)
    url = S._data_url(p)
    assert url.startswith("data:image/jpeg;base64,")
    body = {"input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "x"},
                                                                       {"type": "input_image", "image_url": url}]}]}
    kept = S._without_media(body)["input"][0]["content"]
    assert kept[0]["text"] == "x" and kept[1]["image_url"].startswith("<inline image,")
    chat = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": url}}]}]}
    assert S._without_media(chat)["messages"][0]["content"][1]["image_url"]["url"].startswith("<inline image,")


def test_design_applies_one_fix_and_keeps_the_best(tmp_path, monkeypatch):
    d = tmp_path / "r1"
    d.mkdir()
    (d / "edl_v1.json").write_text(json.dumps({"runtime_s": 10.0, "render": {"duration_s": 10.0},
                                               "captions": [{"text": "we edit short form"}]}))
    (d / "cover_v1.json").write_text(json.dumps({"moments": [{"t_s": 3.0, "why": "a"}, {"t_s": 7.0, "why": "b"}]}))
    (d / "run.json").write_text(json.dumps({"brief": "a reel about Montivo"}))
    monkeypatch.setattr(T, "frame_at", lambda run, edl, t, plan=None: still())
    monkeypatch.setattr(T, "head_at", lambda run, edl, t, key=None: HEAD)
    calls = []

    class FakeSpark:
        def __init__(self, *a, **k): pass

        def call(self, name, prompt, schema, images=None, **k):
            calls.append((name, [p.name for p in images or []], prompt))
            if name.startswith("cover_design"):
                return SimpleNamespace(cost_usd=0.01, seconds=2.0, parsed={
                    "still": 4, "still_why": "mouth closed", "template": "headline", "template_why": "calm",
                    "headlines": [{"text": "The editor that checks its own work", "highlight": "checks", "why": "x"},
                                  {"text": "AI edits AI checks", "highlight": "checks", "why": "y"}]})
            first = sum(1 for c in calls if c[0].startswith("cover_check")) == 1
            return SimpleNamespace(cost_usd=0.002, seconds=1.0, parsed={
                "readable_small": 4 if first else 8, "face": 8, "clear_of_face": True, "fits_video": 8, "pass": not first,
                "fix": "shorter_text" if first else "none", "better_text": "", "note": "too long" if first else "ok"})
    monkeypatch.setattr("montivo.engine.spark.Spark", FakeSpark)
    rec = T.design(d, 1, ledger=None, log=lambda *a: None)
    des = rec["design"]
    assert calls[0][1] == ["candidates.jpg"] and "we edit short form" in calls[0][2] and "a reel about Montivo" in calls[0][2]
    assert [c[0] for c in calls] == ["cover_design_v1", "cover_check_v1_1", "cover_check_v1_2"]
    assert des["candidates"][3]["n"] == 4 and des["final"]["t_s"] == des["candidates"][3]["t_s"]
    assert des["final"]["text"] == "AI edits AI checks" and des["pass"]  # the shorter headline after the fix
    assert len(des["attempts"]) == 2 and not des["attempts"][0]["pass"]
    assert (d / "cover" / "v1" / "attempt2.jpg").is_file() and (d / "cover" / "v1" / "attempt2_tile.jpg").is_file()
    assert json.loads((d / "cover_v1.json").read_text())["design"]["cost_usd"] == 0.014
