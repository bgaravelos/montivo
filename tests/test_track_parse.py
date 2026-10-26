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
