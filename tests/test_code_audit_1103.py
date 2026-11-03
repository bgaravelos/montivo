"""The code audit of 3/11 (docs/code-audit-2026-11-03.md): another judge's ui/ files through the generic file route,
new title words with the card asked off, the 400 a bad request gets, the share poster written in place."""
import json

import pytest
from fastapi.testclient import TestClient

import montivo.server.app as A
from montivo import human
from montivo.server import share
from tests.test_hosted import fake_jobs, hosted_env  # noqa: F401 (fixture)
from tests.test_hosted_security import host, signed_in
from tests.test_ui import _human_run, _run_folder


def test_the_file_route_never_serves_a_judges_ui_files_on_the_host(tmp_path, hosted_env):
    d = host(tmp_path, hosted_env)
    private = d / "ui" / "draft" / "ffff0000" / "state.json"
    private.parent.mkdir(parents=True)
    private.write_text('{"private": "another judge\'s draft"}')
    (d / "ui" / "labels").mkdir()
    (d / "ui" / "labels" / "ffff0000.json").write_text("{}")
    (d / "cut_v1.mp4").write_bytes(b"\0" * 64)
    other = d / "ui" / "cache" / "cover" / "judges" / "ffff0000" / "c.jpg"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"jpeg")
    (d / "ui" / "cache" / "edit").mkdir(parents=True)
    (d / "ui" / "cache" / "edit" / "proxy.mp4").write_bytes(b"\0")
    c = signed_in(tmp_path)
    assert c.get("/api/runs/r1/file/ui/draft/ffff0000/state.json").status_code == 404
    assert c.get("/api/runs/r1/file/ui/labels/ffff0000.json").status_code == 404
    assert c.get("/api/runs/r1/file/ui/../ui/draft/ffff0000/state.json").status_code == 404
    assert c.get("/api/runs/r1/file/ui/cache/cover/judges/ffff0000/c.jpg").status_code == 404
    mine = d / "ui" / "cache" / "cover" / "judges" / c.cookies.get("montivo_judge").split(".")[0] / "c.jpg"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"jpeg")
    assert c.get(f"/api/runs/r1/file/{mine.relative_to(d).as_posix()}").status_code == 200
    assert c.get("/api/runs/r1/file/ui/cache/edit/proxy.mp4").status_code == 200  # Live's edit media
    assert c.get("/api/runs/r1/file/cut_v1.mp4").status_code == 200  # the shared media stay


def test_the_file_route_serves_ui_files_locally(tmp_path, hosted_env):
    d = _run_folder(tmp_path)
    fake_jobs(hosted_env)
    (d / "ui").mkdir(exist_ok=True)
    (d / "ui" / "note.json").write_text("{}")
    assert TestClient(A.create_app(tmp_path)).get("/api/runs/r1/file/ui/note.json").status_code == 200


@pytest.mark.parametrize("was_on", [False, True])
@pytest.mark.parametrize("card", [False, True, None])
def test_new_title_words_keep_the_card_state_asked_for(tmp_path, monkeypatch, was_on, card):
    d = _human_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    edl.update(title_card=was_on, title_card_text="Old", title_style="header")
    new, _ = human.text_edit(edl, human._words(d), None, title="New", title_card=card)
    assert new["title_card_text"] == "New"
    assert new["title_card"] is (card is not False)


def test_a_card_asked_off_is_not_priced_as_a_new_image(tmp_path, monkeypatch):
    d = _human_run(tmp_path, monkeypatch)
    edl = json.loads((d / "edl_v1.json").read_text())
    edl.update(title_card=False, title_card_text="Old", title_style="card")
    fixes = human.text_fixes(edl, {}, "New", False, None, None)
    assert [f["op"] for f in fixes] == ["title_text"] and "$0.01" not in fixes[0]["note"]


def test_a_bad_request_says_why_under_error(tmp_path, hosted_env):
    _run_folder(tmp_path)
    fake_jobs(hosted_env)
    r = TestClient(A.create_app(tmp_path)).post("/api/runs/r1/drop", json={"span": "s1", "from": "invalid"})
    assert r.status_code == 400 and r.json()["error"].startswith("bad request:")


def test_the_share_poster_is_renamed_into_place_when_whole(tmp_path, monkeypatch):
    out = tmp_path / "poster.jpg"
    calls = []

    def run(args, **k):
        calls.append(args[-1])
        assert args[-1] != str(out)  # ffmpeg writes a part file, never the poster itself
        if len(calls) == 1:
            open(args[-1], "wb").write(b"jpeg")
            return type("R", (), {"returncode": 0})()
        open(args[-1], "wb").write(b"half")  # a failed encoder leaves a half file
        return type("R", (), {"returncode": 1})()

    monkeypatch.setattr(share.subprocess, "run", run)
    src = tmp_path / "cut.mp4"
    src.write_bytes(b"\0")
    share.poster_file(src, 1.0, out)
    assert out.read_bytes() == b"jpeg"
    out.unlink()
    share.poster_file(src, 1.0, out)
    assert not out.exists() and not list(tmp_path.glob("*.part.jpg"))
