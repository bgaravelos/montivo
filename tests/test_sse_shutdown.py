"""Offline: an open live /events stream never holds the server (cloud review, 29/10: SIGTERM took 60 s and more with
one tab open, and after a `modal deploy` the old web container kept serving). A live stream ends after STREAM_MAX_S
(the browser reconnects) and `montivo serve` gives open streams SHUTDOWN_GRACE_S after a SIGTERM."""
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx
from fastapi.testclient import TestClient

import montivo.server.app as A
from tests.test_ui import _run_folder


def test_a_live_stream_ends_after_its_lifetime_and_asks_for_a_reconnect(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(A, "STREAM_MAX_S", 1.0)
    _run_folder(tmp_path)
    c = TestClient(A.create_app(tmp_path))
    t0 = time.monotonic()
    with c.stream("GET", "/api/runs/r1/events") as r:
        body = "".join(r.iter_text())
    assert time.monotonic() - t0 < 5
    assert body.startswith("retry: 2000\n") and "event: hello" in body


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sigterm_stops_montivo_serve_with_a_stream_open(tmp_path):
    _run_folder(tmp_path)
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "BASETEN_API_KEY")}
    proc = subprocess.Popen([sys.executable, "-c", "import sys; from montivo.server.app import main; "
                             "sys.exit(main(sys.argv[1:]))", "--root", str(tmp_path), "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        opened = threading.Event()

        def tab():  # a browser tab: reads the stream for as long as it lasts
            try:
                with httpx.stream("GET", f"{url}/api/runs/r1/events", timeout=None) as r:
                    for _ in r.iter_text():
                        opened.set()
            except httpx.HTTPError:
                pass
        threading.Thread(target=tab, daemon=True).start()
        assert opened.wait(10)
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=A.SHUTDOWN_GRACE_S + 10)  # the old server was still up after 90 s
        assert time.monotonic() - t0 < A.SHUTDOWN_GRACE_S + 5
    finally:
        if proc.poll() is None:
            proc.kill()
