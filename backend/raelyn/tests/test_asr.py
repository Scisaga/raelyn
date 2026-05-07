from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from types import TracebackType
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.services.asr import _asr_backend_defer_from_health, asr_transcribe, inspect_asr_backend_defer
from raelyn.services.asr import resolve_asr_timeout_seconds
from raelyn.services.inference import EffectiveAsrConfig, check_asr_health, test_asr_connection


_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@contextmanager
def _blocked_proxy_env():
    previous = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    try:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class _LocalAsrServer:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.requests.append(("GET", self.path))
                if self.path == "/health":
                    _write_json(
                        self,
                        200,
                        {
                            "backend_ready": True,
                            "model_loaded": True,
                            "backend_ready_count": 1,
                            "max_concurrent_transcribe": 1,
                            "backend_queue_waiters": 0,
                            "backend_replicas": [{"ready": True, "in_flight": 0}],
                        },
                    )
                    return
                if self.path == "/":
                    _write_json(self, 200, {"ok": True})
                    return
                _write_json(self, 404, {"detail": "not found"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 0:
                    self.rfile.read(length)
                owner.requests.append(("POST", self.path))
                if self.path.endswith("/v1/audio/transcriptions"):
                    _write_json(self, 200, {"text": "hello", "segments": []})
                    return
                _write_json(self, 200, {"result": {"text": "hello", "utterances": []}})

            def log_message(self, _format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_LocalAsrServer":
        self._thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)


def _local_asr_config(url: str) -> EffectiveAsrConfig:
    return EffectiveAsrConfig(
        mode="local",
        provider="local",
        source="test",
        url=url,
        model="qwen3-asr",
        timeout_seconds=5,
        prompt="",
        temperature=None,
        response_format="",
        app_key="",
        access_key="",
        resource_id="",
        configured=True,
    )


def _volcengine_asr_config(url: str) -> EffectiveAsrConfig:
    return EffectiveAsrConfig(
        mode="volcengine",
        provider="volcengine_speech",
        source="test",
        url=url,
        model="bigmodel",
        timeout_seconds=5,
        prompt="",
        temperature=None,
        response_format="",
        app_key="app-key",
        access_key="access-key",
        resource_id="volc.bigasr.auc_turbo",
        configured=True,
    )


class AsrServiceTests(unittest.TestCase):
    def test_resolve_asr_timeout_keeps_base_timeout_for_short_or_unknown_media(self) -> None:
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=600, media_duration_seconds=None), 600)
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=600, media_duration_seconds=300), 600)

    def test_resolve_asr_timeout_grows_for_long_media(self) -> None:
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=600, media_duration_seconds=15705), 1429)

    def test_resolve_asr_timeout_preserves_explicitly_larger_base_timeout(self) -> None:
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=1800, media_duration_seconds=15705), 1800)

    def test_backend_capacity_guard_defers_when_replicas_are_full(self) -> None:
        defer = _asr_backend_defer_from_health(
            {
                "backend_ready": True,
                "model_loaded": True,
                "backend_ready_count": 2,
                "max_concurrent_transcribe": 1,
                "backend_queue_waiters": 4,
                "backend_queue_timeout_seconds": 30,
                "backend_replicas": [
                    {"ready": True, "in_flight": 2},
                    {"ready": True, "in_flight": 2},
                ],
            }
        )

        self.assertIsNotNone(defer)
        assert defer is not None
        self.assertEqual(defer.delay_seconds, 30)
        self.assertIn("queue_waiters=4", defer.reason)

    def test_backend_capacity_guard_allows_available_inference_slot(self) -> None:
        defer = _asr_backend_defer_from_health(
            {
                "backend_ready": True,
                "model_loaded": True,
                "backend_ready_count": 2,
                "max_concurrent_transcribe": 1,
                "backend_queue_waiters": 0,
                "backend_replicas": [
                    {"ready": True, "in_flight": 1},
                    {"ready": True, "in_flight": 0},
                ],
            }
        )

        self.assertIsNone(defer)

    def test_check_asr_health_ignores_environment_proxy(self) -> None:
        with _LocalAsrServer() as server:
            cfg = _local_asr_config(f"{server.base_url}/v1/audio/transcriptions")
            with _blocked_proxy_env():
                result = check_asr_health(cfg)

        self.assertTrue(result["ok"], result)
        self.assertIn(("GET", "/health"), server.requests)

    def test_capacity_guard_health_ignores_environment_proxy(self) -> None:
        with _LocalAsrServer() as server:
            cfg = _local_asr_config(f"{server.base_url}/v1/audio/transcriptions")
            with _blocked_proxy_env():
                with patch("raelyn.services.asr.get_effective_asr_config", return_value=cfg):
                    with patch.object(settings, "asr_backend_capacity_guard_enabled", True):
                        defer = inspect_asr_backend_defer()

        self.assertIsNone(defer)
        self.assertIn(("GET", "/health"), server.requests)

    def test_asr_transcribe_ignores_environment_proxy(self) -> None:
        with TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "audio.wav"
            audio_path.write_bytes(b"audio")

            with _LocalAsrServer() as server:
                cfg = _local_asr_config(server.base_url)
                with _blocked_proxy_env():
                    with patch("raelyn.services.asr.get_effective_asr_config", return_value=cfg):
                        result = asr_transcribe(audio_path=audio_path, language="zh")

        self.assertEqual(result["text"], "hello")
        self.assertIn(("POST", "/v1/audio/transcriptions"), server.requests)

    def test_volcengine_asr_connection_ignores_environment_proxy(self) -> None:
        with _LocalAsrServer() as server:
            cfg = _volcengine_asr_config(f"{server.base_url}/api/v3/auc/bigmodel/recognize/flash")
            with _blocked_proxy_env():
                result = test_asr_connection(cfg)

        self.assertTrue(result["ok"], result)
        self.assertIn(("GET", "/"), server.requests)
        self.assertIn(("POST", "/api/v3/auc/bigmodel/recognize/flash"), server.requests)


if __name__ == "__main__":
    unittest.main()
