from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from videosync.config import settings


def asr_enabled() -> bool:
    return bool(settings.asr_url.strip())


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _is_openai_transcriptions_url(url: str) -> bool:
    return "/v1/audio/transcriptions" in url


def asr_transcribe(*, audio_path: Path, language: str | None = None) -> dict[str, Any]:
    if not asr_enabled():
        raise RuntimeError("asr is not configured")

    base_url = settings.asr_url.strip()
    endpoint = settings.asr_endpoint.strip()

    if endpoint:
        url = endpoint if endpoint.startswith(("http://", "https://")) else _join_url(base_url, endpoint)
    else:
        # Default: OpenAI-compatible endpoint.
        url = base_url.rstrip("/") if _is_openai_transcriptions_url(base_url) else _join_url(base_url, "/v1/audio/transcriptions")

    timeout = httpx.Timeout(settings.asr_timeout_seconds)
    files = {"file": (audio_path.name, audio_path.read_bytes())}
    data: dict[str, Any] = {}

    if language:
        data["language"] = language

    model = settings.asr_model.strip()
    if model:
        data["model"] = model

    prompt = settings.asr_prompt.strip()
    if prompt:
        data["prompt"] = prompt

    if settings.asr_temperature is not None:
        data["temperature"] = settings.asr_temperature

    response_format = settings.asr_response_format.strip()
    if response_format:
        data["response_format"] = response_format

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, files=files, data=data)
        resp.raise_for_status()
        payload = resp.json()

    if not isinstance(payload, dict):
        return {"text": str(payload), "segments": []}

    if "text" not in payload and "transcript" in payload:
        payload["text"] = payload.get("transcript") or ""

    if "segments" not in payload:
        # Some servers return "chunks" or "segment" variants; normalize best-effort.
        segments = payload.get("chunks") or payload.get("segment") or []
        payload["segments"] = segments if isinstance(segments, list) else []

    if "text" not in payload:
        payload["text"] = ""

    return payload
