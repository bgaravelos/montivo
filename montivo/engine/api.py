"""Thin httpx layer over the Meta developer API: Files, Responses (JSON and raw SSE) and the ASR stream.

Everything streams over plain httpx. The OpenAI SDK stream dropped mid-way on SAM twice during the kill tests,
raw SSE over httpx did not, so the whole engine uses one transport.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from ..config import BASE_URL, OPEN_BASE_URL, api_key, open_api_key


def _headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key()}", **extra}


def upload_file(path: Path, mime: str, purpose: str = "user_data") -> dict:
    """POST /files. Returns the file object (id, bytes, ...) plus upload_seconds."""
    t0 = time.perf_counter()
    with httpx.Client(timeout=httpx.Timeout(600, connect=30)) as http:
        with path.open("rb") as fh:
            r = http.post(f"{BASE_URL}/files", headers=_headers(), data={"purpose": purpose},
                          files={"file": (path.name, fh, mime)})
    r.raise_for_status()
    data = r.json()
    data["upload_seconds"] = round(time.perf_counter() - t0, 2)
    return data


def _post_json(url: str, headers: dict[str, str], body: dict, what: str, timeout_s: float, retries: int) -> dict:
    """POST a JSON body and return the JSON answer. Retries on transport errors and 5xx."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30)) as http:
                r = http.post(url, headers={**headers, "Content-Type": "application/json"}, json=body)
            if r.status_code >= 500 and attempt < retries:
                last = httpx.HTTPStatusError(f"{r.status_code}: {r.text[:300]}", request=r.request, response=r)
                time.sleep(2 + 3 * attempt)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{what} {r.status_code}: {r.text[:1000]}")
            return r.json()
        except (httpx.TransportError, httpx.ReadError) as exc:
            last = exc
            if attempt >= retries:
                break
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"{what} failed after {retries + 1} attempts: {last}")


def responses_create(body: dict, timeout_s: float = 900, retries: int = 1) -> dict:
    """POST /responses without streaming. Retries once on transport errors and 5xx."""
    return _post_json(f"{BASE_URL}/responses", _headers(), body, "responses", timeout_s, retries)


def chat_create(body: dict, timeout_s: float = 900, retries: int = 1) -> dict:
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


def stream_responses(body: dict, raw_sink: Path | None = None, timeout_s: float = 1800) -> Iterator[dict]:
    """POST /responses with stream=true over raw SSE. Yields each JSON event; writes the raw SSE to raw_sink."""
    body = {**body, "stream": True}
    sink = raw_sink.open("w") if raw_sink else None
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30, read=timeout_s)) as http, http.stream(
                "POST", f"{BASE_URL}/responses", headers=_headers(**{"Content-Type": "application/json", "Accept": "text/event-stream"}),
                json=body) as r:
            if r.status_code >= 400:
                r.read()
                raise RuntimeError(f"responses stream {r.status_code}: {r.text[:1000]}")
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
    with wav.open("rb") as fh, httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30, read=timeout_s)) as http, http.stream(
            "POST", f"{BASE_URL}/asr/transcribe", params={"sessionId": f"montivo-{uuid.uuid4().hex[:10]}"},
            headers=_headers(Accept="text/event-stream"),
            files={"request": (None, json.dumps(request), "application/json"), "audio": (wav.name, fh, "audio/wav")}) as r:
        if r.status_code >= 400:
            r.read()
            raise RuntimeError(f"asr {r.status_code}: {r.text[:1000]}")
        for _event, data in iter_sse(r):
            try:
                ev: Any = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                ev["t_wall"] = round(time.perf_counter() - t0, 3)
                yield ev


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
