from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

import httpx

from raelyn.config import settings
from raelyn.services.usage import record_external_service_usage


class EmbeddingError(RuntimeError):
    pass


class EmbeddingOverBudgetError(EmbeddingError):
    pass


class EmbeddingTransientError(EmbeddingError):
    pass


@dataclass(frozen=True)
class EmbeddingSpec:
    model: str
    dim: int


def embedding_spec() -> EmbeddingSpec:
    return EmbeddingSpec(
        model=str(settings.embedding_model or "").strip() or "Qwen/Qwen3-Embedding-4B",
        dim=max(1, int(settings.embedding_dim or 1024)),
    )


def embedding_enabled() -> bool:
    return bool(str(settings.embedding_url or "").strip())


def validate_embedding_vector(vector: Any, spec: EmbeddingSpec | None = None) -> list[float]:
    expected = spec or embedding_spec()
    if not isinstance(vector, list):
        raise EmbeddingError("embedding response vector is not a list")
    if len(vector) != expected.dim:
        raise EmbeddingError(
            f"embedding response vector dimension {len(vector)} does not match configured dimension {expected.dim}"
        )
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError) as exc:
        raise EmbeddingError("embedding response vector contains a non-numeric value") from exc
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingError("embedding response vector contains NaN or infinity")
    if not any(value != 0.0 for value in values):
        raise EmbeddingError("embedding response vector is all zero")
    return values


def _embedding_endpoint_url() -> str:
    if not embedding_enabled():
        raise EmbeddingError("embedding service not configured")
    base_url = str(settings.embedding_url or "").rstrip("/")
    endpoint = str(settings.embedding_endpoint or "/v1/embeddings").strip() or "/v1/embeddings"
    return f"{base_url}{endpoint if endpoint.startswith('/') else f'/{endpoint}'}"


def _embedding_health_url() -> str:
    if not embedding_enabled():
        raise EmbeddingError("embedding service not configured")
    return f"{str(settings.embedding_url or '').rstrip('/')}/health"


def check_embedding_health() -> dict[str, Any]:
    spec = embedding_spec()
    if not embedding_enabled():
        return {
            "ok": False,
            "configured": False,
            "url": "",
            "error": "not configured",
            "provider": "local",
            "source": "env",
            "model": spec.model,
            "dim": spec.dim,
        }

    url = _embedding_health_url()
    try:
        with httpx.Client(timeout=httpx.Timeout(2.0), trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
        return {
            "ok": True,
            "configured": True,
            "url": url,
            "error": None,
            "provider": "local",
            "source": "env",
            "model": spec.model,
            "dim": spec.dim,
        }
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "url": url,
            "error": str(exc),
            "provider": "local",
            "source": "env",
            "model": spec.model,
            "dim": spec.dim,
        }


def _embedding_error_detail(response: httpx.Response, body: Any) -> str:
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("error") or body.get("detail") or "")
    return detail or response.text


def _raise_for_embedding_error(response: httpx.Response, body: Any) -> None:
    if response.status_code < 400:
        return
    detail = _embedding_error_detail(response, body)
    detail_lower = detail.lower()
    if "max_model_len" in detail_lower or "maximum context length" in detail_lower or "too long" in detail_lower:
        raise EmbeddingOverBudgetError(detail or "embedding input too long")
    if response.status_code in {502, 503, 504}:
        raise EmbeddingTransientError(detail or f"http {response.status_code}")
    raise EmbeddingError(detail or f"http {response.status_code}")


def embed_texts(texts: list[str], *, usage_operation: str | None = None) -> list[list[float]]:
    if not texts:
        return []

    spec = embedding_spec()
    payload: dict[str, Any] = {
        "input": [str(text or "") for text in texts],
        "model": spec.model,
        "dimensions": spec.dim,
    }
    timeout = max(5, int(settings.embedding_timeout_seconds or 120))
    endpoint_url = _embedding_endpoint_url()
    started_at = time.perf_counter()
    try:
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                response = client.post(endpoint_url, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise EmbeddingTransientError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(str(exc)) from exc

        try:
            body = response.json()
        except Exception:
            body = None

        _raise_for_embedding_error(response, body)

        if not isinstance(body, dict):
            raise EmbeddingError("embedding response is not json object")
        rows = body.get("data")
        if not isinstance(rows, list) or not rows:
            raise EmbeddingError("embedding response missing data")
        if len(rows) != len(texts):
            raise EmbeddingError(
                f"embedding response row count {len(rows)} does not match request count {len(texts)}"
            )

        vectors: list[list[float] | None] = [None] * len(texts)
        for row in rows:
            if not isinstance(row, dict):
                raise EmbeddingError("embedding response row is not json object")
            index = row.get("index")
            if not isinstance(index, int) or index < 0 or index >= len(texts):
                raise EmbeddingError("embedding response row index is invalid")
            vector = row.get("embedding")
            vectors[index] = validate_embedding_vector(vector, spec)

        if any(vector is None for vector in vectors):
            raise EmbeddingError("embedding response missing indexed vectors")
        result = [vector for vector in vectors if vector is not None]
    except Exception:
        if usage_operation:
            record_external_service_usage(
                service="embedding",
                operation=usage_operation,
                provider="local",
                model=spec.model,
                succeeded=False,
                duration_ms=round((time.perf_counter() - started_at) * 1000),
            )
        raise

    if usage_operation:
        record_external_service_usage(
            service="embedding",
            operation=usage_operation,
            provider="local",
            model=spec.model,
            succeeded=True,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )
    return result


def embed_text(text: str, *, usage_operation: str | None = None) -> list[float]:
    return embed_texts([text], usage_operation=usage_operation)[0]
