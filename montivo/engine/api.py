"""Thin httpx layer over the Meta developer API: Files, Responses (JSON and raw SSE) and the ASR stream.

Everything streams over plain httpx. The OpenAI SDK stream dropped mid-way on SAM twice during the kill tests,
raw SSE over httpx did not, so the whole engine uses one transport.

Every call backs off on 429 and 5xx (RETRY_STATUS) and on a dropped connection, honouring the server's Retry-After
(capped at RETRY_AFTER_MAX_S), else BACKOFF_S per attempt; a stream is retried only while it opens, before its first
event (a stream cut mid-way is the caller's: voice.py runs the chunk again, track.py keeps what came).
"""
from __future__ import annotations

import contextlib
import email.utils
import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from ..config import BASE_URL, OPEN_BASE_URL, api_key, open_api_key


RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
RETRIES = 3  # attempts after the first, for a 429 / 5xx / dropped connection
TIMEOUT_RETRIES = 1  # a read timeout is retried once at most: each one already waited the call's whole timeout
BACKOFF_S = (2.0, 5.0, 12.0)
RETRY_AFTER_MAX_S = 60.0
_sleep = time.sleep  # tests replace it


def _headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key()}", **extra}


def retry_wait(attempt: int, response: httpx.Response | None = None) -> float:
    """Seconds before attempt `attempt + 1`: the response's Retry-After (seconds or an HTTP date), capped at
    RETRY_AFTER_MAX_S, else BACKOFF_S for this attempt."""
    after = response.headers.get("retry-after") if response is not None else None
    if after:
        try:
            secs = float(after)
        except ValueError:
            try:
                secs = email.utils.parsedate_to_datetime(after).timestamp() - time.time()
            except (TypeError, ValueError):
                secs = None
        if secs is not None:
            return min(RETRY_AFTER_MAX_S, max(0.0, secs))
    return BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]


def _send(what: str, send: Callable[[], httpx.Response], retries: int = RETRIES) -> httpx.Response:
    """`send()` until it answers something other than a 429 / 5xx or a dropped connection, `retries` times at most
    (read timeouts TIMEOUT_RETRIES); raises RuntimeError with the last answer when it never does or on any other 4xx."""
    last = ""
    timeouts = 0
    for attempt in range(retries + 1):
        try:
            r = send()
        except httpx.TransportError as exc:
            last = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, httpx.ReadTimeout):
                timeouts += 1
                if timeouts > TIMEOUT_RETRIES:
                    break
            if attempt >= retries:
                break
            _sleep(retry_wait(attempt))
            continue
        if r.status_code in RETRY_STATUS and attempt < retries:
            last = f"{r.status_code}: {r.text[:300]}"
            _sleep(retry_wait(attempt, r))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{what} {r.status_code}: {r.text[:1000]}")
        return r
    raise RuntimeError(f"{what} failed after {attempt + 1} attempts: {last}")


def upload_file(path: Path, mime: str, purpose: str = "user_data") -> dict:
    """POST /files. Returns the file object (id, bytes, ...) plus upload_seconds."""
    t0 = time.perf_counter()

    def send() -> httpx.Response:
        with httpx.Client(timeout=httpx.Timeout(600, connect=30)) as http, path.open("rb") as fh:
            return http.post(f"{BASE_URL}/files", headers=_headers(), data={"purpose": purpose},
                             files={"file": (path.name, fh, mime)})
    data = _send("files", send).json()
    data["upload_seconds"] = round(time.perf_counter() - t0, 2)
    return data


def _post_json(url: str, headers: dict[str, str], body: dict, what: str, timeout_s: float, retries: int) -> dict:
    """POST a JSON body and return the JSON answer, with the back-off of _send."""
    def send() -> httpx.Response:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30)) as http:
            return http.post(url, headers={**headers, "Content-Type": "application/json"}, json=body)
    return _send(what, send, retries).json()


def responses_create(body: dict, timeout_s: float = 900, retries: int = RETRIES) -> dict:
    """POST /responses without streaming."""
    return _post_json(f"{BASE_URL}/responses", _headers(), body, "responses", timeout_s, retries)


def chat_create(body: dict, timeout_s: float = 900, retries: int = RETRIES) -> dict:
    """POST /chat/completions on the Open Weight host (Baseten, OpenAI-compatible) without streaming."""
    return _post_json(f"{OPEN_BASE_URL}/chat/completions", {"Authorization": f"Bearer {open_api_key()}"}, body,
                      "chat", timeout_s, retries)


def chat_as_response(chat: dict) -> dict:
    """A chat completion in the Responses shape the rest of the engine reads: output_text parts, status, and usage
    with input_tokens / output_tokens / input_tokens_details.cached_tokens."""
    choice = (chat.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    finish = choice.get("finish_reason")
    usage = chat.get("usage") or {}
    return {"id": chat.get("id", ""), "model": chat.get("model", ""),
            "status": {"stop": "completed", "length": "incomplete"}.get(finish, finish or "?"),
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": usage.get("prompt_tokens") or 0, "output_tokens": usage.get("completion_tokens") or 0,
                      "input_tokens_details": {"cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0}}}


def output_text(response: dict) -> str:
    """Concatenate output_text parts of a Responses object (what the SDK calls output_text)."""
    parts: list[str] = []
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                parts.append(c["text"])
    return "".join(parts)


def iter_sse(response: httpx.Response) -> Iterator[tuple[str, str]]:
    """Yield (event, data) pairs from a text/event-stream response. Multi-line data is joined with newlines."""
    buf = ""
    for chunk in response.iter_text():
        buf += chunk
        while "\n\n" in buf or "\r\n\r\n" in buf:
            if "\r\n\r\n" in buf and (("\n\n" not in buf) or buf.index("\r\n\r\n") < buf.index("\n\n")):
                block, buf = buf.split("\r\n\r\n", 1)
            else:
                block, buf = buf.split("\n\n", 1)
            event, data = _parse_block(block)
            if data is not None:
                yield event, data
    if buf.strip():
        event, data = _parse_block(buf)
        if data is not None:
            yield event, data


def _parse_block(block: str) -> tuple[str, str | None]:
    event = "message"
    data_lines: list[str] = []
    for line in block.replace("\r\n", "\n").split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return event, None
    return event, "\n".join(data_lines)


@contextlib.contextmanager
def _open_stream(what: str, timeout_s: float, request: Callable[[], dict], retries: int = RETRIES) -> Iterator[httpx.Response]:
    """An open streaming response for POST `request()` (httpx.stream kwargs, built per attempt), with the back-off of
    _send while it opens: a 429 / 5xx answer or a connection that drops before the headers is tried again."""
    with contextlib.ExitStack() as stack:
        def send() -> httpx.Response:
            stack.close()
            http = stack.enter_context(httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30, read=timeout_s)))
            r = stack.enter_context(http.stream("POST", **request()))
            if r.status_code >= 400:
                r.read()
            return r
        yield _send(what, send, retries)


def stream_responses(body: dict, raw_sink: Path | None = None, timeout_s: float = 1800) -> Iterator[dict]:
    """POST /responses with stream=true over raw SSE. Yields each JSON event; writes the raw SSE to raw_sink."""
    body = {**body, "stream": True}
    sink = raw_sink.open("w") if raw_sink else None
    try:
        with _open_stream("responses stream", timeout_s, lambda: {
                "url": f"{BASE_URL}/responses", "json": body,
                "headers": _headers(**{"Content-Type": "application/json", "Accept": "text/event-stream"})}) as r:
            for event, data in iter_sse(r):
                if sink:
                    sink.write(f"event: {event}\ndata: {data}\n\n")
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
    finally:
        if sink:
            sink.close()


def stream_transcribe(wav: Path, request: dict, timeout_s: float = 1200) -> Iterator[dict]:
    """POST /asr/transcribe as multipart with Accept: text/event-stream. Yields each JSON event with t_wall added."""
    t0 = time.perf_counter()
    audio = wav.read_bytes()  # sent again as it is on a retry (a 10 min chunk is 29 MB)
    with _open_stream("asr", timeout_s, lambda: {
            "url": f"{BASE_URL}/asr/transcribe", "params": {"sessionId": f"montivo-{uuid.uuid4().hex[:10]}"},
            "headers": _headers(Accept="text/event-stream"),
            "files": {"request": (None, json.dumps(request), "application/json"), "audio": (wav.name, audio, "audio/wav")}}) as r:
        for _event, data in iter_sse(r):
            try:
                ev: Any = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                ev["t_wall"] = round(time.perf_counter() - t0, 3)
                yield ev


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to a hidden temp file next to `path`, then rename it over `path`: a reader (the server, the Modal
    Volume's commit every 30 s) sees the old file or the new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)  # mkstemp makes it 0600
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dump_json(path: Path, data: Any) -> None:
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
