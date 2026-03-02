from __future__ import annotations

import ipaddress
import json
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from raelyn.config import settings


def llm_enabled() -> bool:
    return bool(settings.llm_url.strip())


def _llm_mode(url: str) -> Literal["ollama_generate", "openai_chat", "openai_completions"]:
    path = urlparse(url).path.lower()
    if "/api/generate" in path:
        return "ollama_generate"
    if "/chat/completions" in path:
        return "openai_chat"
    if "/completions" in path:
        return "openai_completions"
    raise ValueError("LLM_URL must include /api/generate, /chat/completions, or /completions")


def _is_private_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        host = ""
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}

    api_key = settings.llm_api_key.strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    raw = settings.llm_headers_json.strip()
    if raw:
        try:
            extra = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM_HEADERS_JSON is not valid JSON: {e}") from e
        if not isinstance(extra, dict):
            raise ValueError("LLM_HEADERS_JSON must be a JSON object")
        for k, v in extra.items():
            if v is None:
                continue
            headers[str(k)] = str(v)

    return headers


def _extract_openai_chat_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    msg = first.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if content is not None:
            return str(content)
    text = first.get("text")
    if isinstance(text, str):
        return text
    if text is not None:
        return str(text)
    return ""


def _extract_openai_completion_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    text = first.get("text")
    if isinstance(text, str):
        return text
    if text is not None:
        return str(text)
    msg = first.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if content is not None:
            return str(content)
    return ""


def llm_generate_markdown(*, prompt: str) -> str:
    if not llm_enabled():
        raise RuntimeError("llm is not configured")

    url = settings.llm_url.strip()
    mode = _llm_mode(url)
    timeout = httpx.Timeout(settings.llm_timeout_seconds)
    model = settings.llm_model.strip()

    if mode == "ollama_generate":
        payload: dict[str, Any] = {"model": model or "qwen2.5:7b", "prompt": prompt, "stream": False}
    elif mode == "openai_chat":
        if not model:
            raise RuntimeError("LLM_MODEL is required for /chat/completions endpoints")
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}
    else:
        if not model:
            raise RuntimeError("LLM_MODEL is required for /completions endpoints")
        payload = {"model": model, "prompt": prompt, "stream": False}

    headers = {"Content-Type": "application/json", **_headers()}
    # Avoid accidentally routing private/localhost endpoints through environment proxies.
    trust_env = not _is_private_host(url)
    with httpx.Client(timeout=timeout, headers=headers, trust_env=trust_env) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    if mode == "ollama_generate":
        if isinstance(data, dict):
            return str(data.get("response", "")).strip()
        return str(data).strip()
    if mode == "openai_chat":
        return _extract_openai_chat_content(data).strip()
    return _extract_openai_completion_text(data).strip()
