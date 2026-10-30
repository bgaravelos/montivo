"""The run's speed-ups that leave every output as it was: Voice starts on the wav during ingest, and the review prep
runs Voice on the cut and the review copy + upload beside the measured facts."""
from pathlib import Path

from montivo.ledger import Ledger


def test_ingest_hands_over_the_wav_before_the_proxy_and_the_upload(tmp_path, monkeypatch):
    from montivo.engine import ingest as I
    order = []
    probe = I.Probe(width=1920, height=1080, fps=30.0, duration=10.0, bytes=1, has_audio=True, codec="h264")
    monkeypatch.setattr(I, "ffprobe", lambda p: probe)
    monkeypatch.setattr(I, "standardise", lambda p, hflip=False: ([], None, None))
    monkeypatch.setattr(I, "make_wav", lambda src, dst, log=None: (order.append("wav"), Path(dst).write_bytes(b"w")))
    monkeypatch.setattr(I, "make_proxy", lambda src, dst, p, log=None: (order.append("proxy"), Path(dst).write_bytes(b"p"))
                        and probe)
    monkeypatch.setattr(I, "detect_shots", lambda proxy, log=None: (order.append("shots"), [])[1])
    monkeypatch.setattr(I, "upload_file", lambda p, mime: (order.append("upload"), {"id": "f", "upload_seconds": 1.0})[1])
    ing = I.ingest(tmp_path / "a.mp4", tmp_path / "out", Ledger(tmp_path / "ledger.json"), log=lambda m: None,
                   on_wav=lambda wav: order.append(f"on_wav {wav.name}"))
    assert order == ["wav", "on_wav audio.wav", "proxy", "shots", "upload"] and ing.file_id == "f"


def test_progress_keeps_the_ingest_step_while_voice_starts_on_the_wav():
    from montivo.server.runs import parse_log, progress
    lines = [(0.0, "montivo run: a.mp4 -> runs/x"), (6.0, "ingest: proxy 960x540 (9.6 MB), wav 3.3 MB in 5.97s"),
             (13.0, "voice: chunk 0 (69 s) streamed 971 events in 7.0s"),
             (13.0, "voice: 1 turns, 113 words, speakers ['A'], $0.0034, 7.0s"),
             (15.2, "ingest: uploaded proxy as f in 9.16s"), (22.7, "track: resampled proxy to 5 fps")]
    log = "\n".join(f"[{t:7.1f}s] {m}" for t, m in lines)
    p = progress(parse_log("\n".join(log.split("\n")[:4])))
    assert p["title"] == "Ingest" and not p["steps"]  # Voice ran during the upload: still the ingest step
    p = progress(parse_log(log))
    assert [s["title"] for s in p["steps"]] == ["Ingest"] and p["title"] == "Voice · SAM · Watch"
    assert p["detail"].startswith("Voice ✓")  # the early transcript counts once analysis shows
