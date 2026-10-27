"""Offline: regex parse of the head of a recorded SAM SSE stream (first 40 frames of 900, 26/10/2026)."""
import json
from pathlib import Path

import pytest

from montivo.engine.track import parse_line

SSE = Path(__file__).resolve().parent / "fixtures" / "sam_stream_nasa30_head.sse"


def test_parse_line_box_and_mask():
    line = "<12f>0<|box;x1=443;y1=41;x2=959;y2=538;w=960;h=540|><|mask;x=0;y=0;data=498,517,!!ab|>3<|box;x1=1;y1=2;x2=3;y2=4;w=960;h=540;c=0.87|>"
    frame, recs = parse_line(line)
    assert frame == 12
    assert recs[0][0] == "0" and recs[0][1] == (443, 41, 959, 538) and recs[0][5].startswith("x=0;y=0;data=498,517,")
    assert recs[1][0] == "3" and recs[1][4] == 0.87 and recs[1][5] is None
    assert parse_line("plain text") is None


@pytest.mark.skipif(not SSE.is_file(), reason="recorded SSE not present")
def test_parse_recorded_stream():
    text = []
    for block in SSE.read_text().replace("\r\n", "\n").split("\n\n"):
        payload = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
        if payload and payload != "[DONE]":
            e = json.loads(payload)
            if e.get("type") == "response.output_text.delta":
                text.append(e["delta"])
    frames = 0
    recs = 0
    for line in "".join(text).split("\n"):
        p = parse_line(line)
        if p:
            frames += 1
            recs += len(p[1])
    assert frames == 40 and recs == 40


def test_every_concept_is_tracked_at_the_head_tracks_rate():
    # person and head share one 5 fps upload and one batch (person at 10 fps was half of run 013's SAM bill)
    from montivo.config import REFRAME_TRACK_FPS, TRACK_RESAMPLE_FPS
    from montivo.engine.track import track_fps
    assert TRACK_RESAMPLE_FPS == REFRAME_TRACK_FPS == 5.0
    assert track_fps(29.97) == 5.0 and track_fps(59.94) == 5.0  # short clips too: no more 30 fps SAM under 60 s
    assert track_fps(29.97, 10.0) == 10.0 and track_fps(4.0) == 4.0  # --track-fps wins; never above the source
