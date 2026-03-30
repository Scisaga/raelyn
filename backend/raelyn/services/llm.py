from __future__ import annotations

import ipaddress
import json
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from raelyn.services.inference import get_effective_llm_config


def _as_int(value: Any) -> int:
    try:
        n = int(value)
    except Exception:
        return 0
    return n if n > 0 else 0


def llm_enabled() -> bool:
    return bool(get_effective_llm_config().configured)


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
    cfg = get_effective_llm_config()
    headers: dict[str, str] = {}

    api_key = cfg.api_key.strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    raw = cfg.headers_json.strip()
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


def _extract_llm_usage(payload: Any, *, mode: str) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 1}

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0

    usage = payload.get("usage")
    if isinstance(usage, dict):
        input_tokens = _as_int(
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("prompt_token_count")
            or usage.get("input_token_count")
        )
        output_tokens = _as_int(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("completion_token_count")
            or usage.get("output_token_count")
        )
        total_tokens = _as_int(usage.get("total_tokens") or usage.get("total_token_count"))

    if mode == "ollama_generate":
        input_tokens = input_tokens or _as_int(payload.get("prompt_eval_count"))
        output_tokens = output_tokens or _as_int(payload.get("eval_count"))

    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "call_count": 1,
    }


def llm_generate(*, prompt: str, think: bool | str | None = None) -> dict[str, Any]:
    if not llm_enabled():
        raise RuntimeError("llm is not configured")

    cfg = get_effective_llm_config()
    url = cfg.url.strip()
    mode = _llm_mode(url)
    timeout = httpx.Timeout(cfg.timeout_seconds)
    model = cfg.model.strip()

    if mode == "ollama_generate":
        payload: dict[str, Any] = {"model": model or "qwen2.5:7b", "prompt": prompt, "stream": False}
        if think is not None:
            payload["think"] = think
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
            return {
                "text": str(data.get("response", "")).strip(),
                "usage": _extract_llm_usage(data, mode=mode),
            }
        return {"text": str(data).strip(), "usage": _extract_llm_usage(data, mode=mode)}
    if mode == "openai_chat":
        return {"text": _extract_openai_chat_content(data).strip(), "usage": _extract_llm_usage(data, mode=mode)}
    return {"text": _extract_openai_completion_text(data).strip(), "usage": _extract_llm_usage(data, mode=mode)}


def llm_generate_markdown(*, prompt: str, think: bool | str | None = None) -> str:
    return str(llm_generate(prompt=prompt, think=think).get("text", "")).strip()
