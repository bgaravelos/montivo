import math

from montivo.engine.reframe import (ReframeParams, centre_samples, crop_size, pick_track, plan_segment, resample,
                                    smooth_centres)

SRC = {"width": 1280, "height": 720, "fps": 30.0}
P = ReframeParams()


def recs(cx: float, t0: float, t1: float, fps: float = 10.0, w: int = 400, h: int = 480):
    out = []
    n = int(round((t1 - t0) * fps)) + 1
    for k in range(n):
        out.append({"f": k, "t": round(t0 + k / fps, 3), "box": [int(cx - w / 2), 40, int(cx + w / 2), 40 + h]})
    return out


def track(objects: dict):
    return {"track_fps": 10.0, "source": {"width": 960, "height": 540}, "objects": objects}


def test_crop_size_is_even_and_full_height():
    assert crop_size(1280, 720) == (404, 720)
    assert crop_size(1920, 1080) == (608, 1080)
    assert crop_size(1080, 1920) == (1080, 1920)


def test_dead_zone_ignores_small_jitter():
    crop_w = 404
    targets = [640 + (10 if k % 2 else -10) for k in range(50)]
    path = smooth_centres(targets, 0.1, crop_w, 1280, P)
    assert len(set(path)) == 1 and path[0] == 630  # held at the first target, 8 % of 404 = 32 px of dead zone


def test_big_move_is_speed_limited_and_settles():
    crop_w = 404
    targets = [300.0] * 5 + [900.0] * 100
    path = smooth_centres(targets, 0.1, crop_w, 1280, P)
    steps = [b - a for a, b in zip(path, path[1:])]
    assert max(steps) <= P.max_pan_px_s * 0.1 + 1e-9
    assert all(s >= 0 for s in steps)  # never overshoots back
    assert abs(path[-1] - 900) <= P.dead_zone_frac * crop_w + 1e-9


def test_centre_is_clamped_inside_the_frame():
    crop_w = 404
    assert smooth_centres([5.0] * 10, 0.1, crop_w, 1280, P) == [202.0] * 10
    assert smooth_centres([1275.0] * 10, 0.1, crop_w, 1280, P) == [1078.0] * 10


def test_centre_samples_interpolates_gaps_and_holds_edges():
    r = [{"f": 0, "t": 0.0, "box": [50, 0, 150, 100]}, {"f": 10, "t": 1.0, "box": [150, 0, 250, 100]}]
    cs = centre_samples(r, 0.0, 2.0, 10.0, 2.0)  # scale x2: 100 -> 200, 200 -> 400
    assert cs[0] == 200 and abs(cs[5] - 300) < 1e-9 and cs[10] == 400 and cs[-1] == 400 and len(cs) == 21


def test_resample_to_source_fps():
    out = resample([0.0, 10.0, 20.0], 10.0, 30.0, 0.2)
    assert len(out) == 6 and abs(out[1] - 10 / 3) < 1e-9 and out[3] == 10.0


def test_pick_track_prefers_large_centred_and_covering():
    objs = {"small_edge": recs(80, 0, 10, w=100, h=100), "big_centre": recs(480, 0, 10),
            "big_but_gone": recs(480, 0, 1, w=500, h=500)}
    assert pick_track(objs, 2.0, 8.0, 10.0, 960, 540) == "big_centre"
    assert pick_track({"gone": recs(480, 0, 1)}, 50.0, 60.0, 10.0, 960, 540) is None


def test_plan_segment_follows_the_track():
    seg = plan_segment(2.0, 4.0, SRC, track({"1": recs(346, 0, 10)}), span_ids=["s1"])
    assert seg.track_id == "1" and seg.crop_w == 404 and seg.crop_h == 720
    assert len(seg.xs) == 60 and all(x % 2 == 0 for x in seg.xs)
    # proxy cx 346 -> source 461.3, minus half the crop = 259.3 -> even 260
    assert seg.xs[0] == 260 and len(set(seg.xs)) == 1
    assert seg.keyframes[0] == {"t": 2.0, "t_seg": 0.0, "x": 260, "spans": ["s1"]}
    assert seg.sendcmd_text().startswith("0.0000 crop x 260;\n0.0167 crop x 260;")


def test_plan_segment_falls_back_to_centre_crop():
    for tr in (None, track({}), track({"0": recs(480, 0, 1)})):
        seg = plan_segment(50.0, 51.0, SRC, tr)
        assert seg.track_id is None and set(seg.xs) == {438} and len(seg.xs) == 30


def test_plan_segment_pan_inside_frame():
    objs = {"1": recs(100, 0, 3) + recs(860, 3.1, 6)}
    seg = plan_segment(0.0, 6.0, SRC, track(objs))
    assert min(seg.xs) >= 0 and max(seg.xs) <= 1280 - 404
    assert seg.xs[0] < seg.xs[-1]  # it panned right
    per_frame = [abs(b - a) for a, b in zip(seg.xs, seg.xs[1:])]
    assert max(per_frame) <= math.ceil(P.max_pan_px_s / 30) + 2
