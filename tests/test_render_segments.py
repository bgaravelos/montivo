from montivo.engine.render import frame_align, merge_segments


def span(i, turn, t_in, t_out, contiguous=None):
    s = {"id": f"s{i}", "turn_id": turn, "role": "body", "t_in": t_in, "t_out": t_out}
    if contiguous is not None:
        s["contiguous_with_previous"] = contiguous
    return s


def test_contiguous_spans_merge_into_one_segment():
    spans = [span(1, 5, 112.78, 115.5), span(2, 9, 131.74, 137.98), span(3, 9, 137.98, 144.06, True),
             span(4, 9, 144.06, 149.42)]  # s4 has no flag (older EDL) but is the same turn with no gap
    segs = merge_segments(spans)
    assert [s.span_ids for s in segs] == [["s1"], ["s2", "s3", "s4"]]
    assert (segs[1].t_in, segs[1].t_out) == (131.74, 149.42)


def test_gap_or_other_turn_starts_a_new_segment():
    spans = [span(1, 9, 10.0, 12.0), span(2, 9, 13.0, 15.0), span(3, 7, 15.0, 16.0), span(4, 7, 16.5, 17.0, False)]
    segs = merge_segments(spans)
    assert [s.span_ids for s in segs] == [["s1"], ["s2"], ["s3"], ["s4"]]


def test_frame_align_snaps_to_whole_frames():
    segs = frame_align(merge_segments([span(1, 5, 112.78, 115.5)]), 30.0)
    s = segs[0]
    assert s.frames == 82 and abs(s.t_in - 112.766667) < 1e-6 and abs(s.t_out - 115.5) < 1e-6
    assert abs(s.duration - 82 / 30) < 1e-4
