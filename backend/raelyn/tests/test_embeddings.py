from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services import embeddings
from raelyn.services.embeddings import EmbeddingOverBudgetError, EmbeddingTransientError


class _FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict:
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse, calls: list[dict]) -> None:
        self._response = response
        self._calls = calls

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, json: dict) -> _FakeResponse:
        self._calls.append({"url": url, "json": json})
        return self._response


class EmbeddingClientTests(unittest.TestCase):
    def test_embed_texts_aligns_response_by_index(self) -> None:
        calls: list[dict] = []
        response = _FakeResponse(
            200,
            {
                "data": [
                    {"index": 1, "embedding": [3, 4]},
                    {"index": 0, "embedding": [1, 2]},
                ]
            },
        )

        with patch.object(embeddings.settings, "embedding_url", "http://embedding.test"):
            with patch.object(embeddings.httpx, "Client", lambda timeout: _FakeClient(response, calls)):
                vectors = embeddings.embed_texts(["第一条", "第二条"])

        self.assertEqual(vectors, [[1.0, 2.0], [3.0, 4.0]])
        self.assertEqual(calls[0]["json"]["input"], ["第一条", "第二条"])

    def test_embed_texts_raises_over_budget_error(self) -> None:
        calls: list[dict] = []
        response = _FakeResponse(400, {"detail": "max_model_len exceeded"})

        with patch.object(embeddings.settings, "embedding_url", "http://embedding.test"):
            with patch.object(embeddings.httpx, "Client", lambda timeout: _FakeClient(response, calls)):
                with self.assertRaises(EmbeddingOverBudgetError):
                    embeddings.embed_texts(["超长文本"])

    def test_embed_texts_raises_transient_error_for_gateway_failure(self) -> None:
        calls: list[dict] = []
        response = _FakeResponse(502, {})

        with patch.object(embeddings.settings, "embedding_url", "http://embedding.test"):
            with patch.object(embeddings.httpx, "Client", lambda timeout: _FakeClient(response, calls)):
                with self.assertRaises(EmbeddingTransientError):
                    embeddings.embed_texts(["临时错误"])


if __name__ == "__main__":
    unittest.main()
