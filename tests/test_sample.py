"""The judges' sample zips: pack, check (size + sha256), unpack only runs/<id> and footage/. No network: the download
reads a local file through a stubbed urlopen."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from montivo.server import sample
from montivo.server.sample import Sample, SampleError


def make_run(root: Path, rid: str = "099") -> Path:
    d = root / "runs" / rid
    (d / "review").mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"id": rid}))
    (d / "edl_v1.json").write_text(json.dumps({"source": {"path": "footage/clip.mp4"}}))
    (d / "cut_v1.mp4").write_bytes(b"cut")
    (d / "review" / "loop.json").write_text("{}")
    (d / "notes.txt").write_text("not packed")
    (root / "footage").mkdir()
    (root / "footage" / "clip.mp4").write_bytes(b"source clip")
    return d


@pytest.fixture
def packed(tmp_path, monkeypatch):
    src = tmp_path / "src"
    run = make_run(src)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    z = sample.pack(run, tmp_path / "out" / "montivo-sample-099.zip", footage=True)
    s = Sample("099", "test", z.stat().st_size, sample.sha256(z))
    monkeypatch.setitem(sample.SAMPLES, "099", s)
    return z, s


def test_pack_keeps_the_run_and_its_clip(packed):
    z, s = packed
    names = set(zipfile.ZipFile(z).namelist())
    assert names == {"runs/099/run.json", "runs/099/edl_v1.json", "runs/099/cut_v1.mp4", "runs/099/review/loop.json",
                     "footage/clip.mp4"}
    assert z.with_name(z.name + ".sha256").read_text() == f"{s.sha256}  {z.name}\n"


def test_pack_refuses_a_key_in_the_files(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    monkeypatch.setenv("MODEL_API_KEY", "secret-value-123")
    (run / "run.json").write_text('{"auth": "Bearer secret-value-123"}')
    with pytest.raises(SampleError, match="run.json"):
        sample.pack(run, tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


def test_fetch_from_a_file_a_folder_and_file_url(packed, tmp_path):
    z, _ = packed
    for i, src in enumerate([str(z), str(z.parent), z.as_uri()]):
        root = tmp_path / f"judge{i}"
        root.mkdir()
        [run] = sample.fetch("099", src, root)
        assert run == root / "runs" / "099"
        assert (run / "cut_v1.mp4").read_bytes() == b"cut"
        assert (root / "footage" / "clip.mp4").read_bytes() == b"source clip"
        assert not (root / "runs" / ".fetch-099").exists()


def test_the_sample_id_comes_from_the_file_name():
    assert sample.guess_id("dist/montivo-sample-017.zip") == "017"
    assert sample.guess_id("~/Downloads") is None


def test_a_wrong_zip_unpacks_nothing(packed, tmp_path, monkeypatch):
    z, s = packed
    root = tmp_path / "judge"
    root.mkdir()
    monkeypatch.setitem(sample.SAMPLES, "099", Sample("099", "", s.bytes, "0" * 64))
    with pytest.raises(SampleError, match="sha256"):
        sample.fetch("099", str(z), root)
    monkeypatch.setitem(sample.SAMPLES, "099", Sample("099", "", s.bytes + 1, s.sha256))
    with pytest.raises(SampleError, match="bytes"):
        sample.fetch("099", str(z), root)
    assert not (root / "runs").exists() and not (root / "footage").exists()
    sample.fetch("099", str(z), root, verify=False)  # a zip you packed yourself
    assert (root / "runs" / "099" / "run.json").is_file()


@pytest.mark.parametrize("name", ["../evil", "/abs/file", "runs/012/run.json", "runs/099/../../x", "footage/a/b.mp4",
                                  "footage/.owners.json", "other/file", "runs\\099\\x"])
def test_entries_outside_the_run_and_footage_fail_the_zip(tmp_path, name):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("runs/099/run.json", "{}")
        zf.writestr(name, "x")
    with pytest.raises(SampleError, match="unexpected entry"):
        sample.unpack(z, "099", tmp_path / "root")
    assert not (tmp_path / "root").exists()


def test_a_symlink_entry_fails_the_zip(tmp_path):
    z = tmp_path / "link.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("runs/099/run.json", "{}")
        info = zipfile.ZipInfo("runs/099/link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(SampleError):
        sample.unpack(z, "099", tmp_path / "root")


def test_a_zip_without_the_run_is_not_a_sample(tmp_path):
    z = tmp_path / "empty.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("footage/clip.mp4", "x")
    with pytest.raises(SampleError, match="run.json"):
        sample.unpack(z, "099", tmp_path / "root")


def test_an_existing_run_stays_unless_forced(packed, tmp_path):
    z, _ = packed
    root = tmp_path / "judge"
    root.mkdir()
    sample.fetch("099", str(z), root)
    run = root / "runs" / "099"
    (run / "edl_v2.json").write_text("{}")  # the judge's own edit
    sample.fetch("099", str(z), root)
    assert (run / "edl_v2.json").is_file()
    sample.fetch("099", str(z), root, force=True)
    assert not (run / "edl_v2.json").exists() and (run / "run.json").is_file()
    [old] = (root / "runs" / "_archive").iterdir()
    assert (old / "edl_v2.json").is_file()  # archived, never deleted


def test_a_clip_already_there_is_kept(packed, tmp_path):
    z, _ = packed
    root = tmp_path / "judge"
    (root / "footage").mkdir(parents=True)
    clip = root / "footage" / "clip.mp4"
    clip.write_bytes(b"other clip!")  # same size as the packed one
    sample.fetch("099", str(z), root)
    assert clip.read_bytes() == b"other clip!"
    clip.write_bytes(b"short")
    sample.fetch("099", str(z), root, force=True)
    assert clip.read_bytes() == b"source clip"


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_is_checked_before_it_counts(packed, tmp_path, monkeypatch):
    z, s = packed
    data = z.read_bytes()
    seen = []
    monkeypatch.setattr(sample.urllib.request, "urlopen", lambda req, timeout: seen.append(req.full_url) or FakeResponse(data[:-10]))
    root = tmp_path / "judge"
    root.mkdir()
    with pytest.raises(SampleError, match="incomplete"):
        sample.fetch("099", None, root)
    assert seen == [s.url] and s.url.endswith("/releases/download/sample-099/montivo-sample-099.zip")
    assert not list((root / "dist").iterdir())  # the truncated .part is gone
    monkeypatch.setattr(sample.urllib.request, "urlopen", lambda req, timeout: FakeResponse(data))
    sample.fetch("099", None, root)
    assert (root / "dist" / s.file).is_file() and (root / "runs" / "099" / "run.json").is_file()
    # a second fetch uses the checked zip in dist/ and downloads nothing
    monkeypatch.setattr(sample.urllib.request, "urlopen", lambda *a, **k: pytest.fail("downloaded again"))
    sample.fetch("099", None, root, force=True)


def test_the_published_samples_are_pinned():
    assert sample.DEFAULT in sample.SAMPLES and sample.RELEASE == sample.SAMPLES[sample.DEFAULT].url
    for s in sample.SAMPLES.values():
        assert s.bytes > 0 and len(s.sha256) == 64, f"sample {s.id}: set its size and sha256 after packing"


def test_cli_reports_errors_without_a_traceback(tmp_path, capsys):
    assert sample.main(["fetch", "--sample", "nope", "--root", str(tmp_path)]) == 1
    assert "no sample 'nope'" in capsys.readouterr().err
    assert sample.main(["fetch", "--from", str(tmp_path / "missing.zip"), "--root", str(tmp_path)]) == 1
