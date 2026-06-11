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

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, response: _FakeResponse, calls: list[dict]) -> None:
        self._response = response
        self._calls = calls

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, json: dict) -> _FakeResponse:
        self._calls.append({"method": "POST", "url": url, "json": json})
        return self._response

    def get(self, url: str) -> _FakeResponse:
        self._calls.append({"method": "GET", "url": url})
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
            with patch.object(embeddings.httpx, "Client", lambda **_kwargs: _FakeClient(response, calls)):
                vectors = embeddings.embed_texts(["第一条", "第二条"])

        self.assertEqual(vectors, [[1.0, 2.0], [3.0, 4.0]])
        self.assertEqual(calls[0]["json"]["input"], ["第一条", "第二条"])

    def test_check_embedding_health_uses_health_endpoint_and_ignores_environment_proxy(self) -> None:
        calls: list[dict] = []
        client_kwargs: list[dict] = []
        response = _FakeResponse(200, {"status": "ok"})

        def _client(**kwargs):
            client_kwargs.append(kwargs)
            return _FakeClient(response, calls)

        with patch.object(embeddings.settings, "embedding_url", "http://embedding.test"):
            with patch.object(embeddings.settings, "embedding_endpoint", "/v1/embeddings"):
                with patch.object(embeddings.httpx, "Client", _client):
                    result = embeddings.check_embedding_health()

        self.assertTrue(result["ok"])
        self.assertTrue(result["configured"])
        self.assertEqual(result["url"], "http://embedding.test/health")
        self.assertEqual(calls[0]["method"], "GET")
        self.assertEqual(calls[0]["url"], "http://embedding.test/health")
        self.assertEqual(client_kwargs[0]["trust_env"], False)

    def test_check_embedding_health_reports_unconfigured_service(self) -> None:
        with patch.object(embeddings.settings, "embedding_url", ""):
            result = embeddings.check_embedding_health()

        self.assertFalse(result["ok"])
        self.assertFalse(result["configured"])
        self.assertEqual(result["error"], "not configured")

    def test_embed_texts_raises_over_budget_error(self) -> None:
        calls: list[dict] = []
        response = _FakeResponse(400, {"detail": "max_model_len exceeded"})

        with patch.object(embeddings.settings, "embedding_url", "http://embedding.test"):
            with patch.object(embeddings.httpx, "Client", lambda **_kwargs: _FakeClient(response, calls)):
                with self.assertRaises(EmbeddingOverBudgetError):
                    embeddings.embed_texts(["超长文本"])

    def test_embed_texts_raises_transient_error_for_gateway_failure(self) -> None:
        calls: list[dict] = []
        response = _FakeResponse(502, {})

        with patch.object(embeddings.settings, "embedding_url", "http://embedding.test"):
            with patch.object(embeddings.httpx, "Client", lambda **_kwargs: _FakeClient(response, calls)):
                with self.assertRaises(EmbeddingTransientError):
                    embeddings.embed_texts(["临时错误"])

    def test_embed_texts_ignores_environment_proxy(self) -> None:
        calls: list[dict] = []
        client_kwargs: list[dict] = []
        response = _FakeResponse(200, {"data": [{"index": 0, "embedding": [1, 2]}]})

        def _client(**kwargs):
            client_kwargs.append(kwargs)
            return _FakeClient(response, calls)

        with patch.object(embeddings.settings, "embedding_url", "http://embedding.test"):
            with patch.object(embeddings.httpx, "Client", _client):
                embeddings.embed_texts(["文本"])

        self.assertEqual(client_kwargs[0]["trust_env"], False)


if __name__ == "__main__":
    unittest.main()
