from __future__ import annotations

from typing import Any

import httpx

from videosync.config import settings


def ollama_enabled() -> bool:
    return bool(settings.ollama_url.strip())


def ollama_generate_markdown(*, prompt: str) -> str:
    if not ollama_enabled():
        raise RuntimeError("ollama is not configured")
    url = settings.ollama_url.rstrip("/") + "/api/generate"
    timeout = httpx.Timeout(settings.ollama_timeout_seconds)
    model = settings.ollama_model.strip() or "qwen2.5:7b"
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", "")).strip()

