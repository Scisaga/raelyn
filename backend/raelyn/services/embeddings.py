from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from raelyn.config import settings


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
        model=str(settings.embedding_model or "").strip() or "Qwen/Qwen3-Embedding-8B",
        dim=max(1, int(settings.embedding_dim or 1024)),
    )


def embedding_enabled() -> bool:
    return bool(str(settings.embedding_url or "").strip())


def _embedding_endpoint_url() -> str:
    if not embedding_enabled():
        raise EmbeddingError("embedding service not configured")
    base_url = str(settings.embedding_url or "").rstrip("/")
    endpoint = str(settings.embedding_endpoint or "/v1/embeddings").strip() or "/v1/embeddings"
    return f"{base_url}{endpoint if endpoint.startswith('/') else f'/{endpoint}'}"


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


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    spec = embedding_spec()
    payload: dict[str, Any] = {
        "input": [str(text or "") for text in texts],
        "model": spec.model,
        "dimensions": spec.dim,
    }
    timeout = max(5, int(settings.embedding_timeout_seconds or 120))
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(_embedding_endpoint_url(), json=payload)
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

    vectors: list[list[float] | None] = [None] * len(texts)
    for row in rows:
        if not isinstance(row, dict):
            raise EmbeddingError("embedding response row is not json object")
        index = row.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(texts):
            raise EmbeddingError("embedding response row index is invalid")
        vector = row.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError("embedding response missing vector")
        vectors[index] = [float(value) for value in vector]

    if any(vector is None for vector in vectors):
        raise EmbeddingError("embedding response missing indexed vectors")
    return [vector for vector in vectors if vector is not None]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
