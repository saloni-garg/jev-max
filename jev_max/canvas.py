"""Canvas fallback: when the element table has no usable target
(shadow-piercing failed, canvas UI, cross-origin iframe region),
ground a natural-language instruction to viewport coordinates with a
vision model, then click with the real mouse.
"""

from __future__ import annotations

import base64
import json
import os
import re

import httpx


class GroundingError(Exception):
    pass


def ground(screenshot: bytes, instruction: str,
           api_key: str | None = None, model: str | None = None,
           base_url: str | None = None, timeout: float = 45.0) -> tuple[int, int]:
    """Return (x, y) in 0-1000 normalized viewport coordinates."""
    api_key = api_key or os.getenv("TEXT_MODEL_API_KEY", "")
    if not api_key:
        raise GroundingError("TEXT_MODEL_API_KEY is required for canvas grounding")
    model = model or os.getenv("VISION_MODEL_NAME") or os.getenv("TEXT_MODEL_NAME", "inception/mercury-2.5")
    base_url = base_url or os.getenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    b64 = base64.b64encode(screenshot).decode()

    prompt = (
        "Locate the UI element described below in this screenshot. "
        "Reply with ONLY a JSON object like {\"x\": 512, \"y\": 300}, where x and y "
        "are integers in 0-1000 normalized viewport coordinates pointing at the "
        "center of the element. If it is not visible, reply {\"x\": -1, \"y\": -1}.\n"
        f"Element: {instruction}"
    )
    r = httpx.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            "temperature": 0.1,
            "max_tokens": 60,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{[^{}]*\}", content)
    if not m:
        raise GroundingError(f"could not parse grounding response: {content[:120]}")
    try:
        pt = json.loads(m.group(0))
        x, y = int(pt["x"]), int(pt["y"])
    except (KeyError, ValueError, TypeError):
        raise GroundingError(f"bad grounding coordinates: {content[:120]}")
    if x < 0 or y < 0:
        raise GroundingError("element not visible in screenshot")
    return max(0, min(1000, x)), max(0, min(1000, y))


def to_pixels(x1000: int, y1000: int, width: int, height: int) -> tuple[int, int]:
    return int(x1000 / 1000 * width), int(y1000 / 1000 * height)
