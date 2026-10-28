"""Image: the title card from muse-image-1.0 (POST /images/generations, $0.01 per image).

Measured 26/10 (kill test d): size "1080x1920" only sets the aspect ratio, the model returns 1152x2048 (exact 9:16) and
renders a short headline legibly; 11 s with reasoning_strength low and the tools off. The card is generated once per
run folder (title/title_card.png) and reused by every later render, so `montivo render` only calls the API when the
file is missing.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx

from ..config import BASE_URL, IMAGE_MODEL, PRICE, api_key
from ..ledger import Entry, Ledger
from .api import _send, dump_json

TITLE_PROMPT = (
    "Vertical 9:16 title card for a short social video. The headline text, exactly as written and spelled: \"{text}\". "
    "Set it large in a heavy geometric grotesk (like Inter Tight ExtraBold), tight letter spacing, off-white #F3F0E8, "
    "centred in the middle third, on one to three lines, with generous margins; put the single most important word on "
    "a flat marker-yellow #FFC629 highlight block with the word in near-black #141414. "
    "Background: flat near-black #141414 with a subtle film grain and one soft marker-yellow light streak. Clean, "
    "modern, high contrast, legible on a phone. No other text, no logos, no people, no borders.")


def generate_title_card(text: str, out_dir: Path, ledger: Ledger, log=print, size: str = "1080x1920") -> Path:
    """Generate the title card PNG for `text` into out_dir/title_card.png; request and response (image stripped) saved
    next to it, one ledger entry."""
    out_dir.mkdir(parents=True, exist_ok=True)
    body = {"model": IMAGE_MODEL, "prompt": TITLE_PROMPT.format(text=text), "n": 1, "size": size,
            "response_format": "b64_json", "output_format": "png", "reasoning_strength": "low",
            "tool_enablement": {"enable_image_search": False, "enable_web_search": False, "enable_shell": False}}
    dump_json(out_dir / "request.json", body)
    log(f"image: title card \"{text}\" -> {IMAGE_MODEL} ...")
    t0 = time.perf_counter()

    def send() -> httpx.Response:
        with httpx.Client(timeout=httpx.Timeout(300, connect=30)) as http:
            return http.post(f"{BASE_URL}/images/generations", headers={"Authorization": f"Bearer {api_key()}"}, json=body)
    try:
        data = _send("images", send).json()  # backs off on 429 / 5xx
    except RuntimeError as exc:
        ledger.add(Entry(stage="image.title", model=IMAGE_MODEL, seconds=round(time.perf_counter() - t0, 2), cost_usd=0.0,
                         status="http_error", note=str(exc)[:200]))
        raise
    seconds = round(time.perf_counter() - t0, 2)
    item = data["data"][0]
    raw = base64.b64decode(item["b64_json"])
    out = out_dir / "title_card.png"
    out.write_bytes(raw)
    meta = {k: v for k, v in data.items() if k != "data"}
    meta["data"] = [{k: v for k, v in d.items() if k != "b64_json"} for d in data["data"]]
    dump_json(out_dir / "response.json", meta)
    usage = data.get("usage") or {}
    ledger.add(Entry(stage="image.title", model=IMAGE_MODEL, seconds=seconds, cost_usd=PRICE["image"],
                     tokens_in=usage.get("input_tokens") or 0, tokens_out=usage.get("output_tokens") or 0,
                     note=f"title card \"{text}\", {len(raw) / 1e6:.1f} MB"))
    log(f"image: title card saved to {out} ({len(raw) / 1e6:.1f} MB, ${PRICE['image']:.2f}, {seconds}s)")
    return out
