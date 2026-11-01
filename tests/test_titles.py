"""Suggest 3 hooks reads the cut as it is captioned, not as Voice heard it (bug audit 1/11)."""
from montivo.engine import titles


def test_the_title_prompt_reads_the_corrected_captions():
    edl = {"spans": [{"id": "s1", "words": "News Voice gives it every every word"}, {"id": "s2", "words": "bye now"}],
           "captions": [{"spans": ["s1"], "text": "Muse Voice gives it"}, {"spans": ["s1"], "text": "every word"}]}
    assert titles.said(edl) == "- Muse Voice gives it every word\n- bye now"  # s2 has no captions: its span words
