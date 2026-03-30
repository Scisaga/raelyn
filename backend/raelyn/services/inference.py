from __future__ import annotations

import base64
import io
import json
import uuid
import wave
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import AppConfig


INFERENCE_MODE_CONFIG_KEY = "inference_mode"
VOLCENGINE_INFERENCE_CONFIG_KEY = "volcengine_inference_config"

LOCAL_INFERENCE_MODE = "local"
VOLCENGINE_INFERENCE_MODE = "volcengine"
INFERENCE_MODES = {LOCAL_INFERENCE_MODE, VOLCENGINE_INFERENCE_MODE}

VOLCENGINE_LLM_PROVIDER = "volcengine_ark"
VOLCENGINE_ASR_PROVIDER = "volcengine_speech"
LOCAL_PROVIDER = "local"


@dataclass(frozen=True)
class EffectiveLlmConfig:
    mode: str
    provider: str
    source: str
    url: str
    model: str
    api_key: str
    headers_json: str
    timeout_seconds: int
    configured: bool


@dataclass(frozen=True)
class EffectiveAsrConfig:
    mode: str
    provider: str
    source: str
    url: str
    model: str
    timeout_seconds: int
    prompt: str
    temperature: float | None
    response_format: str
    app_key: str
    access_key: str
    resource_id: str
    configured: bool


def _with_session(session: Session | None, fn):
    if session is not None:
        return fn(session)
    with session_scope() as managed:
        return fn(managed)


def _get_app_config_value(session: Session, key: str) -> dict[str, Any] | str | None:
    getter = getattr(session, "get", None)
    if not callable(getter):
        return None
    try:
        item = getter(AppConfig, key)
    except Exception:
        return None
    return item.value if item else None


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= keep:
        return "*" * len(raw)
    return f"{'*' * max(4, len(raw) - keep)}{raw[-keep:]}"


def sanitize_config_value(key: str, value: Any) -> Any:
    if key != VOLCENGINE_INFERENCE_CONFIG_KEY or not isinstance(value, dict):
        return value
    masked = dict(value)
    for secret_key in ("api_key", "asr_app_key", "asr_access_key"):
        if isinstance(masked.get(secret_key), str):
            masked[secret_key] = mask_secret(masked.get(secret_key))
    return masked


def _normalize_inference_mode_value(value: Any) -> str:
    if isinstance(value, dict):
        raw = value.get("value")
    else:
        raw = value
    mode = str(raw or "").strip().lower()
    return mode if mode in INFERENCE_MODES else LOCAL_INFERENCE_MODE


def get_inference_mode(session: Session | None = None) -> tuple[str, str]:
    def _load(current: Session) -> tuple[str, str]:
        value = _get_app_config_value(current, INFERENCE_MODE_CONFIG_KEY)
        if value is None:
            return LOCAL_INFERENCE_MODE, "env"
        return _normalize_inference_mode_value(value), "app_config"

    try:
        return _with_session(session, _load)
    except Exception:
        return LOCAL_INFERENCE_MODE, "env"


def _normalize_timeout(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return int(default)
    return parsed if parsed > 0 else int(default)


def _normalize_volcengine_config(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    return {
        "api_key": str(data.get("api_key") or "").strip(),
        "llm_model": str(data.get("llm_model") or "").strip(),
        "asr_model": str(data.get("asr_model") or "").strip(),
        "asr_app_key": str(data.get("asr_app_key") or "").strip(),
        "asr_access_key": str(data.get("asr_access_key") or "").strip(),
        "llm_timeout_seconds": _normalize_timeout(data.get("llm_timeout_seconds"), settings.volcengine_llm_timeout_seconds),
        "asr_timeout_seconds": _normalize_timeout(data.get("asr_timeout_seconds"), settings.volcengine_asr_timeout_seconds),
    }


def _volcengine_config_from_env() -> dict[str, Any]:
    return {
        "api_key": str(settings.volcengine_llm_api_key or "").strip(),
        "llm_model": str(settings.volcengine_llm_model or "").strip(),
        "asr_model": str(settings.volcengine_asr_model or "").strip(),
        "asr_app_key": str(settings.volcengine_asr_app_key or "").strip(),
        "asr_access_key": str(settings.volcengine_asr_access_key or "").strip(),
        "llm_timeout_seconds": _normalize_timeout(settings.volcengine_llm_timeout_seconds, 600),
        "asr_timeout_seconds": _normalize_timeout(settings.volcengine_asr_timeout_seconds, 600),
    }


def _build_local_llm_config(*, source: str) -> EffectiveLlmConfig:
    url = str(settings.llm_url or "").strip()
    model = str(settings.llm_model or "").strip()
    return EffectiveLlmConfig(
        mode=LOCAL_INFERENCE_MODE,
        provider=LOCAL_PROVIDER,
        source=source,
        url=url,
        model=model,
        api_key=str(settings.llm_api_key or "").strip(),
        headers_json=str(settings.llm_headers_json or "").strip(),
        timeout_seconds=_normalize_timeout(settings.llm_timeout_seconds, 600),
        configured=bool(url),
    )


def _build_volcengine_llm_config(volc: dict[str, Any], *, source: str) -> EffectiveLlmConfig:
    model = str(volc.get("llm_model") or "").strip()
    api_key = str(volc.get("api_key") or "").strip()
    url = str(settings.volcengine_llm_url or "").strip()
    return EffectiveLlmConfig(
        mode=VOLCENGINE_INFERENCE_MODE,
        provider=VOLCENGINE_LLM_PROVIDER,
        source=source,
        url=url,
        model=model,
        api_key=api_key,
        headers_json="",
        timeout_seconds=_normalize_timeout(volc.get("llm_timeout_seconds"), settings.volcengine_llm_timeout_seconds),
        configured=bool(url and model and api_key),
    )


def _build_local_asr_config(*, source: str) -> EffectiveAsrConfig:
    url = str(settings.asr_url or "").strip()
    return EffectiveAsrConfig(
        mode=LOCAL_INFERENCE_MODE,
        provider=LOCAL_PROVIDER,
        source=source,
        url=url,
        model=str(settings.asr_model or "").strip(),
        timeout_seconds=_normalize_timeout(settings.asr_timeout_seconds, 600),
        prompt=str(settings.asr_prompt or "").strip(),
        temperature=settings.asr_temperature,
        response_format=str(settings.asr_response_format or "").strip(),
        app_key="",
        access_key="",
        resource_id="",
        configured=bool(url),
    )


def _build_volcengine_asr_config(volc: dict[str, Any], *, source: str) -> EffectiveAsrConfig:
    app_key = str(volc.get("asr_app_key") or "").strip()
    access_key = str(volc.get("asr_access_key") or "").strip()
    model = str(volc.get("asr_model") or "").strip() or str(settings.volcengine_asr_model or "").strip()
    url = str(settings.volcengine_asr_url or "").strip()
    return EffectiveAsrConfig(
        mode=VOLCENGINE_INFERENCE_MODE,
        provider=VOLCENGINE_ASR_PROVIDER,
        source=source,
        url=url,
        model=model,
        timeout_seconds=_normalize_timeout(volc.get("asr_timeout_seconds"), settings.volcengine_asr_timeout_seconds),
        prompt="",
        temperature=None,
        response_format="",
        app_key=app_key,
        access_key=access_key,
        resource_id=str(settings.volcengine_asr_resource_id or "").strip() or "volc.bigasr.auc_turbo",
        configured=bool(url and app_key and access_key and model),
    )


def get_volcengine_config(session: Session | None = None) -> tuple[dict[str, Any], str]:
    env_config = _volcengine_config_from_env()

    def _load(current: Session) -> tuple[dict[str, Any], str]:
        value = _get_app_config_value(current, VOLCENGINE_INFERENCE_CONFIG_KEY)
        if value is None:
            return env_config, "env"
        runtime = _normalize_volcengine_config(value)
        merged = dict(env_config)
        for key, item in runtime.items():
            if isinstance(item, str):
                if item:
                    merged[key] = item
                continue
            merged[key] = item
        return merged, "app_config"

    try:
        return _with_session(session, _load)
    except Exception:
        return env_config, "env"


def get_effective_llm_config(session: Session | None = None) -> EffectiveLlmConfig:
    mode, mode_source = get_inference_mode(session)
    if mode == VOLCENGINE_INFERENCE_MODE:
        volc, source = get_volcengine_config(session)
        return _build_volcengine_llm_config(volc, source=source)
    return _build_local_llm_config(source=mode_source)


def get_effective_asr_config(session: Session | None = None) -> EffectiveAsrConfig:
    mode, mode_source = get_inference_mode(session)
    if mode == VOLCENGINE_INFERENCE_MODE:
        volc, source = get_volcengine_config(session)
        return _build_volcengine_asr_config(volc, source=source)
    return _build_local_asr_config(source=mode_source)


def build_inference_status(session: Session | None = None) -> dict[str, Any]:
    mode, mode_source = get_inference_mode(session)
    volc, volc_source = get_volcengine_config(session)
    return {
        "mode": mode,
        "mode_source": mode_source,
        "volcengine": {
            "source": volc_source,
            "api_key_masked": mask_secret(volc.get("api_key")),
            "api_key_present": bool(str(volc.get("api_key") or "").strip()),
            "llm_model": str(volc.get("llm_model") or "").strip(),
            "asr_model": str(volc.get("asr_model") or "").strip(),
            "asr_app_key_masked": mask_secret(volc.get("asr_app_key")),
            "asr_app_key_present": bool(str(volc.get("asr_app_key") or "").strip()),
            "asr_access_key_masked": mask_secret(volc.get("asr_access_key")),
            "asr_access_key_present": bool(str(volc.get("asr_access_key") or "").strip()),
            "llm_timeout_seconds": _normalize_timeout(volc.get("llm_timeout_seconds"), settings.volcengine_llm_timeout_seconds),
            "asr_timeout_seconds": _normalize_timeout(volc.get("asr_timeout_seconds"), settings.volcengine_asr_timeout_seconds),
        },
    }


def preserve_existing_volcengine_secrets(payload: dict[str, Any], session: Session | None = None) -> dict[str, Any]:
    next_value = _normalize_volcengine_config(payload)

    def _merge(current: Session) -> dict[str, Any]:
        existing_raw = _get_app_config_value(current, VOLCENGINE_INFERENCE_CONFIG_KEY)
        existing = _normalize_volcengine_config(existing_raw)
        for secret_key in ("api_key", "asr_app_key", "asr_access_key"):
            if not str(next_value.get(secret_key) or "").strip():
                next_value[secret_key] = str(existing.get(secret_key) or "").strip()
        return next_value

    return _with_session(session, _merge)


def _llm_health_check_url(url: str) -> str:
    lowered = url.lower()
    if "/api/generate" in lowered:
        return url.split("/api/generate", 1)[0].rstrip("/") + "/api/version"
    if "/chat/completions" in lowered:
        return url.split("/chat/completions", 1)[0].rstrip("/") + "/models"
    if "/completions" in lowered:
        return url.split("/completions", 1)[0].rstrip("/") + "/models"
    return url


def _headers_from_json(raw: str) -> dict[str, str]:
    if not str(raw or "").strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"headers_json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("headers_json must be a JSON object")
    headers: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        headers[str(key)] = str(value)
    return headers


def check_llm_health(config: EffectiveLlmConfig | None = None) -> dict[str, Any]:
    cfg = config or get_effective_llm_config()
    if not cfg.configured:
        return {
            "ok": False,
            "configured": False,
            "url": cfg.url,
            "error": "not configured",
            "provider": cfg.provider,
            "mode": cfg.mode,
            "source": cfg.source,
            "model": cfg.model,
        }
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    try:
        headers.update(_headers_from_json(cfg.headers_json))
    except ValueError as exc:
        return {
            "ok": False,
            "configured": True,
            "url": cfg.url,
            "error": str(exc),
            "provider": cfg.provider,
            "mode": cfg.mode,
            "source": cfg.source,
            "model": cfg.model,
        }
    check_url = _llm_health_check_url(cfg.url)
    try:
        with httpx.Client(timeout=httpx.Timeout(2.0), headers=headers) as client:
            resp = client.get(check_url)
            resp.raise_for_status()
        return {
            "ok": True,
            "configured": True,
            "url": check_url,
            "error": None,
            "provider": cfg.provider,
            "mode": cfg.mode,
            "source": cfg.source,
            "model": cfg.model,
        }
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "url": check_url,
            "error": str(exc),
            "provider": cfg.provider,
            "mode": cfg.mode,
            "source": cfg.source,
            "model": cfg.model,
        }


def _volcengine_asr_probe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _local_asr_health_url(url: str) -> str:
    raw = str(url or "").strip()
    lowered = raw.lower()
    for suffix in ("/v1/audio/transcriptions", "/audio/transcriptions"):
        if lowered.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    return raw.rstrip("/") + "/health"


def check_asr_health(config: EffectiveAsrConfig | None = None) -> dict[str, Any]:
    cfg = config or get_effective_asr_config()
    if not cfg.configured:
        return {
            "ok": False,
            "configured": False,
            "url": cfg.url,
            "error": "not configured",
            "provider": cfg.provider,
            "mode": cfg.mode,
            "source": cfg.source,
            "model": cfg.model,
        }
    if cfg.provider == LOCAL_PROVIDER:
        url = _local_asr_health_url(cfg.url)
        try:
            with httpx.Client(timeout=httpx.Timeout(2.0)) as client:
                resp = client.get(url)
                resp.raise_for_status()
            return {
                "ok": True,
                "configured": True,
                "url": url,
                "error": None,
                "provider": cfg.provider,
                "mode": cfg.mode,
                "source": cfg.source,
                "model": cfg.model,
            }
        except Exception as exc:
            return {
                "ok": False,
                "configured": True,
                "url": url,
                "error": str(exc),
                "provider": cfg.provider,
                "mode": cfg.mode,
                "source": cfg.source,
                "model": cfg.model,
            }

    probe_url = _volcengine_asr_probe_url(cfg.url)
    try:
        with httpx.Client(timeout=httpx.Timeout(2.0)) as client:
            resp = client.get(probe_url)
        ok = resp.status_code < 500
        error = None if ok else f"http {resp.status_code}"
    except Exception as exc:
        ok = False
        error = str(exc)
    return {
        "ok": ok,
        "configured": True,
        "url": probe_url,
        "error": error,
        "provider": cfg.provider,
        "mode": cfg.mode,
        "source": cfg.source,
        "model": cfg.model,
    }


def _tiny_silence_wav_base64() -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 1600)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def test_llm_connection(config: EffectiveLlmConfig | None = None) -> dict[str, Any]:
    cfg = config or get_effective_llm_config()
    base = check_llm_health(cfg)
    if not cfg.configured:
        return base

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    try:
        headers.update(_headers_from_json(cfg.headers_json))
    except ValueError as exc:
        base["ok"] = False
        base["error"] = str(exc)
        return base
    payload: dict[str, Any]
    path = urlparse(cfg.url).path.lower()
    if "/api/generate" in path:
        payload = {"model": cfg.model or "qwen2.5:7b", "prompt": "ping", "stream": False}
    elif "/chat/completions" in path:
        payload = {"model": cfg.model, "messages": [{"role": "user", "content": "ping"}], "stream": False}
    else:
        payload = {"model": cfg.model, "prompt": "ping", "stream": False}
    try:
        with httpx.Client(timeout=httpx.Timeout(min(cfg.timeout_seconds, 20)), headers=headers) as client:
            resp = client.post(cfg.url, json=payload)
            resp.raise_for_status()
        base["ok"] = True
        base["error"] = None
        return base
    except Exception as exc:
        base["ok"] = False
        base["error"] = str(exc)
        return base


def test_asr_connection(config: EffectiveAsrConfig | None = None) -> dict[str, Any]:
    cfg = config or get_effective_asr_config()
    base = check_asr_health(cfg)
    if not cfg.configured:
        return base
    if cfg.provider == LOCAL_PROVIDER:
        return base
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Key": cfg.app_key,
        "X-Api-Access-Key": cfg.access_key,
        "X-Api-Resource-Id": cfg.resource_id,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1",
    }
    payload = {
        "user": {"uid": cfg.app_key},
        "audio": {"data": _tiny_silence_wav_base64()},
        "request": {"model_name": cfg.model},
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(min(cfg.timeout_seconds, 20))) as client:
            resp = client.post(cfg.url, json=payload, headers=headers)
        status_code = str(resp.headers.get("X-Api-Status-Code") or "").strip()
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(f"{resp.status_code}: {resp.text}", request=resp.request, response=resp)
        # 20000000=成功，20000003=静音音频；测试静音样本时两者都可接受。
        if status_code and status_code not in {"20000000", "20000003"}:
            message = str(resp.headers.get("X-Api-Message") or resp.text or "asr test failed").strip()
            raise RuntimeError(message)
        base["ok"] = True
        base["error"] = None
        return base
    except Exception as exc:
        base["ok"] = False
        base["error"] = str(exc)
        return base


def build_effective_llm_config_from_payload(payload: dict[str, Any]) -> EffectiveLlmConfig:
    mode = _normalize_inference_mode_value(payload.get("mode"))
    if mode != VOLCENGINE_INFERENCE_MODE:
        return _build_local_llm_config(source="env")
    volc = _normalize_volcengine_config(payload.get("volcengine"))
    return _build_volcengine_llm_config(volc, source="request")


def build_effective_asr_config_from_payload(payload: dict[str, Any]) -> EffectiveAsrConfig:
    mode = _normalize_inference_mode_value(payload.get("mode"))
    if mode != VOLCENGINE_INFERENCE_MODE:
        return _build_local_asr_config(source="env")
    volc = _normalize_volcengine_config(payload.get("volcengine"))
    return _build_volcengine_asr_config(volc, source="request")
