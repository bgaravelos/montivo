"""Offline: cover suggestions (engine/cover.py) with a stand-in for Spark: the review's upload is reused, moments
outside the cut or on the title card are dropped, each kept one lands on a frame near Spark's time."""
import json
import subprocess
from types import SimpleNamespace

from montivo.engine import cover as C


def run_folder(tmp_path):
    d = tmp_path / "r1"
    d.mkdir()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=4",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "4", "-pix_fmt", "yuv420p", "-shortest",
                    str(d / "cut_v1.mp4")], check=True)
    (d / "edl_v1.json").write_text(json.dumps({"runtime_s": 4.0, "render": {"duration_s": 4.0, "title": {"seconds": 1.0}}}))
    (d / "review_v1.json").write_text(json.dumps({"file_id": "file-1", "clock": True, "cut": "runs/r1/cut_v1.mp4"}))
    return d


def test_cover_reuses_the_review_upload_and_settles(tmp_path, monkeypatch):
    d = run_folder(tmp_path)
    seen = {}

    class FakeSpark:
        def __init__(self, *a, **k): pass

        def call(self, name, prompt, schema, file_id=None, **k):
            seen.update(file_id=file_id, prompt=prompt)
            return SimpleNamespace(parsed={"moments": [{"t_s": 2.0, "why": "eyes open"}, {"t_s": 0.5, "why": "title"},
                                                       {"t_s": 9.0, "why": "past the end"}]}, cost_usd=0.02, seconds=3.0)
    monkeypatch.setattr("montivo.engine.spark.Spark", FakeSpark)
    monkeypatch.setattr(C, "upload_file", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no new upload")))
    rec = C.suggest(d, 1, ledger=None, log=lambda *a: None)
    assert seen["file_id"] == "file-1" and "first 1.0 s" in seen["prompt"]
    assert [m["why"] for m in rec["moments"]] == ["eyes open"] and abs(rec["moments"][0]["t_s"] - 2.0) <= 0.25
    assert json.loads((d / "cover_v1.json").read_text())["moments"][0]["spark_t_s"] == 2.0


def test_an_unreviewed_version_uploads_its_own_copy(tmp_path, monkeypatch):
    d = run_folder(tmp_path)
    (d / "review_v1.json").unlink()
    monkeypatch.setattr(C, "upload_file", lambda path, mime: {"id": "file-new"})
    assert C.video_for_spark(d, 1, log=lambda *a: None) == "file-new"
    assert (d / "review" / "cover_v1" / "cut_v1_review.mp4").is_file()


def test_cover_cli_prints_only_the_record_on_stdout(tmp_path, monkeypatch, capsys):
    """The server parses the CLI's stdout as JSON: the run log goes to stderr (the UI's cover buttons used to 500)."""
    from montivo import export
    from montivo.engine import thumb

    d = tmp_path / "r1"
    d.mkdir()

    def fake(kind):
        def f(run_dir, n, ledger, log, *rest):
            log(f"cover: {kind} for v{n}")
            return {"kind": kind}
        return f

    monkeypatch.setattr(C, "suggest", fake("suggest"))
    monkeypatch.setattr(thumb, "design", fake("design"))
    monkeypatch.setattr(thumb, "still_mask", lambda *a: tmp_path / "m.png")
    for flags, want in ((["--suggest-cover"], {"kind": "suggest"}), (["--design-cover"], {"kind": "design"}),
                        (["--cover-mask", "--cover-t", "1.5"], {"mask": str(tmp_path / "m.png")})):
        assert export.main([str(d), "--version", "1", *flags]) == 0
        out = capsys.readouterr()
        assert json.loads(out.out) == want
    assert "cover: design for v1" in (d / "run.log").read_text()


def test_suggest_keeps_the_design_record_and_never_settles_on_the_title(tmp_path, monkeypatch):
    d = run_folder(tmp_path)
    (d / "cover_v1.json").write_text(json.dumps({"version": 1, "moments": [], "design": {"template": "headline"}}))

    class FakeSpark:
        def __init__(self, *a, **k): pass

        def call(self, name, prompt, schema, file_id=None, **k):  # 1.05 s: just past the 1.0 s title card
            return SimpleNamespace(parsed={"moments": [{"t_s": 1.05, "why": "first words"}]}, cost_usd=0.02, seconds=3.0)
    monkeypatch.setattr("montivo.engine.spark.Spark", FakeSpark)
    # the sharpest frame is the earliest one, on the card: the old settle went back there
    monkeypatch.setattr(C, "frame", lambda cut, t, w=360: t)
    monkeypatch.setattr(C, "sharpness", lambda t: 10.0 - t)
    rec = C.suggest(d, 1, ledger=None, log=lambda *a: None)
    assert rec["moments"][0]["t_s"] >= 1.0 + C.TITLE_CLEAR_S - 1e-6
    saved = json.loads((d / "cover_v1.json").read_text())
    assert saved["design"] == {"template": "headline"} and saved["moments"][0]["why"] == "first words"


def test_export_out_outside_the_run_folder(tmp_path, monkeypatch):
    from montivo import export
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    seen = {}
    monkeypatch.setattr(export, "render_run", lambda run_dir, out_name, **k: seen.setdefault("out", out_name))
    out = tmp_path / "Desktop" / "exports" / "x-clean.mp4"
    out.parent.mkdir(parents=True)
    assert export.render_copy(run, {"spans": []}, out, captions=False) == out
    assert seen["out"] == str(out.resolve())
    seen.clear()
    inside = run / "exports" / "y.mp4"
    inside.parent.mkdir()
    export.render_copy(run, {"spans": []}, inside)
    assert seen["out"] == "exports/y.mp4"
