"""Offline: a failed Muse Image call for new title words never leaves the old card passing for them (cloud review,
29/10: request.json was written before the call, run.title_card reads it as "this PNG shows these words", and the
retry reused the old PNG while the patch said "new card, $0.01")."""
import base64
import json

import pytest

from montivo import run as R
from montivo.engine import image as I
from montivo.ledger import Ledger

PNG = base64.b64encode(b"\x89PNG fake").decode()


class Answer:
    def __init__(self, ok=True):
        self.ok = ok

    def json(self):
        return {"data": [{"b64_json": PNG}], "usage": {}}


def test_a_failed_call_for_new_words_is_made_again_not_reused(tmp_path, monkeypatch):
    calls = []

    def send(what, fn):
        calls.append(what)
        if len(calls) == 2:
            raise RuntimeError("images: HTTP 500")
        return Answer()
    monkeypatch.setattr(I, "_send", send)
    monkeypatch.setenv("MODEL_API_KEY", "test")
    ledger = Ledger(tmp_path / "ledger.json")
    R.title_card(tmp_path, {"title_card_text": "Old words"}, ledger, log=lambda m: None)
    assert len(calls) == 1 and "Old words" in json.loads((tmp_path / "title" / "request.json").read_text())["prompt"]
    with pytest.raises(RuntimeError):
        R.title_card(tmp_path, {"title_card_text": "New words"}, ledger, log=lambda m: None)
    assert "Old words" in json.loads((tmp_path / "title" / "request.json").read_text())["prompt"]  # still the PNG's
    assert (tmp_path / "title" / "request.failed.json").is_file()
    R.title_card(tmp_path, {"title_card_text": "New words"}, ledger, log=lambda m: None)  # the retry calls again
    assert len(calls) == 3 and "New words" in json.loads((tmp_path / "title" / "request.json").read_text())["prompt"]
    assert not (tmp_path / "title" / "request.failed.json").exists()
