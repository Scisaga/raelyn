from __future__ import annotations

import base64
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from raelyn.config import settings
from raelyn.services.inference import get_effective_asr_config

_ASR_TIMEOUT_MIN_REALTIME_FACTOR = 12
_ASR_TIMEOUT_OVERHEAD_SECONDS = 120
_ASR_TIMEOUT_MAX_SECONDS = 3300
_ASR_HEALTH_TIMEOUT_SECONDS = 2.0
_ASR_DEFER_DELAY_MIN_SECONDS = 5
_ASR_DEFER_DELAY_MAX_SECONDS = 900


@dataclass(frozen=True)
class AsrBackendDefer:
    reason: str
    delay_seconds: int


def asr_enabled() -> bool:
    return bool(get_effective_asr_config().configured)


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _local_asr_health_url(url: str) -> str:
    raw = str(url or "").strip()
    lowered = raw.lower()
    for suffix in ("/v1/audio/transcriptions", "/audio/transcriptions"):
        if lowered.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    return raw.rstrip("/") + "/health"


def _is_openai_transcriptions_url(url: str) -> bool:
    return "/v1/audio/transcriptions" in url


def resolve_asr_timeout_seconds(*, base_timeout_seconds: int, media_duration_seconds: int | None = None) -> int:
    base = max(1, int(base_timeout_seconds or 0))
    if not isinstance(media_duration_seconds, int) or media_duration_seconds <= 0:
        return base

    # 长视频的转写速度通常低于下载速度；这里按至少 12x realtime 估算，
    # 再预留固定上传/排队开销，但仍然限制在 worker 1 小时 lease 之内。
    estimated = math.ceil(media_duration_seconds / _ASR_TIMEOUT_MIN_REALTIME_FACTOR) + _ASR_TIMEOUT_OVERHEAD_SECONDS
    return max(base, min(_ASR_TIMEOUT_MAX_SECONDS, estimated))


def _coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bounded_asr_defer_delay(seconds: float | int | None) -> int:
    raw = _coerce_float(seconds, float(settings.asr_backend_capacity_defer_seconds or 30))
    return max(_ASR_DEFER_DELAY_MIN_SECONDS, min(_ASR_DEFER_DELAY_MAX_SECONDS, math.ceil(raw)))


def _asr_backend_defer_from_health(payload: dict[str, Any]) -> AsrBackendDefer | None:
    if payload.get("backend_ready") is False or payload.get("model_loaded") is False:
        return AsrBackendDefer(
            reason="asr backend is not ready",
            delay_seconds=_bounded_asr_defer_delay(payload.get("backend_queue_timeout_seconds")),
        )

    replicas = payload.get("backend_replicas")
    if not isinstance(replicas, list):
        return None

    ready_replicas = [item for item in replicas if isinstance(item, dict) and bool(item.get("ready"))]
    ready_count = _coerce_int(payload.get("backend_ready_count"), len(ready_replicas))
    ready_count = max(0, min(ready_count, len(ready_replicas) or ready_count))
    if ready_count <= 0:
        return AsrBackendDefer(
            reason="asr backend has no ready replica",
            delay_seconds=_bounded_asr_defer_delay(payload.get("backend_queue_timeout_seconds")),
        )

    # qwen3-asr-openai 的外层 router 允许每个 replica 保留多个 in-flight，
    # 但单个后端 worker 默认 MAX_CONCURRENT_TRANSCRIBE=1；调用侧按真实推理槽位保守放行。
    max_concurrent_per_replica = max(1, _coerce_int(payload.get("max_concurrent_transcribe"), 1))
    active_capacity = ready_count * max_concurrent_per_replica
    in_flight = sum(max(0, _coerce_int(item.get("in_flight"), 0)) for item in ready_replicas)
    queue_waiters = max(0, _coerce_int(payload.get("backend_queue_waiters"), 0))
    if queue_waiters > 0 or in_flight >= active_capacity:
        return AsrBackendDefer(
            reason=(
                "asr backend is busy "
                f"(in_flight={in_flight}, active_capacity={active_capacity}, queue_waiters={queue_waiters})"
            ),
            delay_seconds=_bounded_asr_defer_delay(payload.get("backend_queue_timeout_seconds")),
        )
    return None


def inspect_asr_backend_defer() -> AsrBackendDefer | None:
    if not settings.asr_backend_capacity_guard_enabled:
        return None

    cfg = get_effective_asr_config()
    if cfg.provider != "local" or not cfg.configured:
        return None

    health_url = _local_asr_health_url(cfg.url)
    try:
        with httpx.Client(timeout=httpx.Timeout(_ASR_HEALTH_TIMEOUT_SECONDS), trust_env=False) as client:
            resp = client.get(health_url)
    except httpx.HTTPError:
        return AsrBackendDefer(
            reason="asr backend health check failed",
            delay_seconds=_bounded_asr_defer_delay(None),
        )

    if resp.status_code >= 500:
        return AsrBackendDefer(
            reason=f"asr backend health returned http {resp.status_code}",
            delay_seconds=_bounded_asr_defer_delay(None),
        )
    if resp.status_code >= 400:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return _asr_backend_defer_from_health(payload)


def asr_transcribe(
    *,
    audio_path: Path,
    language: str | None = None,
    media_duration_seconds: int | None = None,
) -> dict[str, Any]:
    if not asr_enabled():
        raise RuntimeError("asr is not configured")

    cfg = get_effective_asr_config()
    timeout_seconds = resolve_asr_timeout_seconds(
        base_timeout_seconds=cfg.timeout_seconds,
        media_duration_seconds=media_duration_seconds,
    )
    payload: Any
    if cfg.provider == "local":
        base_url = cfg.url.strip()
        endpoint = str(settings.asr_endpoint or "").strip()
        if endpoint:
            url = endpoint if endpoint.startswith(("http://", "https://")) else _join_url(base_url, endpoint)
        else:
            # Default: OpenAI-compatible endpoint.
            url = base_url.rstrip("/") if _is_openai_transcriptions_url(base_url) else _join_url(base_url, "/v1/audio/transcriptions")
        timeout = httpx.Timeout(timeout_seconds)
        files = {"file": (audio_path.name, audio_path.read_bytes())}
        data: dict[str, Any] = {}
        if language:
            data["language"] = language
        if cfg.model:
            data["model"] = cfg.model
        if cfg.prompt:
            data["prompt"] = cfg.prompt
        if cfg.temperature is not None:
            data["temperature"] = cfg.temperature
        if cfg.response_format:
            data["response_format"] = cfg.response_format

        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(url, files=files, data=data)
            resp.raise_for_status()
            payload = resp.json()
    else:
        timeout = httpx.Timeout(timeout_seconds)
        headers = {
            "Content-Type": "application/json",
            "X-Api-App-Key": cfg.app_key,
            "X-Api-Access-Key": cfg.access_key,
            "X-Api-Resource-Id": cfg.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        audio_base64 = base64.b64encode(audio_path.read_bytes()).decode("utf-8")
        request_payload: dict[str, Any] = {
            "user": {"uid": cfg.app_key},
            "audio": {"data": audio_base64},
            "request": {"model_name": cfg.model},
        }
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(cfg.url, json=request_payload, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                utterances = result.get("utterances")
                payload = {
                    "text": str(result.get("text") or "").strip(),
                    "segments": (
                        [
                            {
                                "start": item.get("start_time"),
                                "end": item.get("end_time"),
                                "text": str(item.get("text") or ""),
                                "words": item.get("words"),
                            }
                            for item in utterances
                            if isinstance(item, dict)
                        ]
                        if isinstance(utterances, list)
                        else []
                    ),
                    "raw": payload,
                }

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
