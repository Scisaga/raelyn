from __future__ import annotations

import importlib
import sys
import unittest
from contextlib import ExitStack
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketDisconnect

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn import config
from raelyn.api.auth import is_valid_bearer_token


class _DummySession:
    def execute(self, *_args, **_kwargs):
        return None


@contextmanager
def _fake_health_session_scope():
    yield _DummySession()


def _load_app(stack: ExitStack, *, token: str):
    stack.enter_context(patch.object(config.settings, "api_bearer_token", token))
    stack.enter_context(patch("raelyn.db.init_db"))
    stack.enter_context(patch("raelyn.services.s3.s3_ensure_bucket"))
    stack.enter_context(patch("raelyn.recover_orphan_jobs.recover"))
    sys.modules.pop("raelyn.main", None)
    module = importlib.import_module("raelyn.main")
    return module.create_app()


class ApiAuthHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_is_public_when_token_disabled(self) -> None:
        with ExitStack() as stack:
            app = _load_app(stack, token="")
            stack.enter_context(patch("raelyn.api.health.session_scope", _fake_health_session_scope))
            stack.enter_context(patch("raelyn.api.health.s3_check_bucket", return_value={"ok": True, "bucket": "test", "error": None}))
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                system = await client.get("/api/system")
                health = await client.get("/api/health")

        self.assertEqual(system.status_code, 200)
        self.assertEqual(health.status_code, 200)

    async def test_api_requires_bearer_token_when_enabled(self) -> None:
        with ExitStack() as stack:
            app = _load_app(stack, token="testtoken")
            stack.enter_context(patch("raelyn.api.health.session_scope", _fake_health_session_scope))
            stack.enter_context(patch("raelyn.api.health.s3_check_bucket", return_value={"ok": True, "bucket": "test", "error": None}))
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                unauthorized_system = await client.get("/api/system")
                unauthorized_health = await client.get("/api/health")
                wrong_system = await client.get("/api/system", headers={"Authorization": "Bearer wrong"})
                authorized_system = await client.get("/api/system", headers={"Authorization": "Bearer testtoken"})
                client.cookies.set("raelyn_api_token", "testtoken")
                cookie_system = await client.get("/api/system")

        self.assertEqual(unauthorized_system.status_code, 401)
        self.assertEqual(unauthorized_health.status_code, 401)
        self.assertEqual(unauthorized_system.headers.get("WWW-Authenticate"), "Bearer")
        self.assertEqual(wrong_system.status_code, 401)
        self.assertEqual(authorized_system.status_code, 200)
        self.assertEqual(cookie_system.status_code, 200)

    async def test_cookie_token_with_special_characters_is_url_decoded(self) -> None:
        with ExitStack() as stack:
            app = _load_app(stack, token="raelyn@2o26%")
            stack.enter_context(patch("raelyn.api.health.session_scope", _fake_health_session_scope))
            stack.enter_context(patch("raelyn.api.health.s3_check_bucket", return_value={"ok": True, "bucket": "test", "error": None}))
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                client.cookies.set("raelyn_api_token", "raelyn%402o26%25")
                cookie_system = await client.get("/api/system")

        self.assertEqual(cookie_system.status_code, 200)

    def test_is_valid_bearer_token_rejects_non_ascii_without_raising(self) -> None:
        self.assertFalse(is_valid_bearer_token("你怀疑的API token", "testtoken"))


class ApiAuthWebSocketTests(unittest.TestCase):
    def test_ws_is_public_when_token_disabled(self) -> None:
        with ExitStack() as stack:
            app = _load_app(stack, token="")
            stack.enter_context(
                patch(
                    "raelyn.api.ws._query_job_stats",
                    return_value={"pending": 1, "running": 2, "succeeded_24h": 3, "failed": 4, "window_hours": 24},
                )
            )
            with TestClient(app) as client:
                with client.websocket_connect("/api/ws/job_stats") as ws:
                    payload = ws.receive_json()

        self.assertEqual(payload["type"], "job_stats")
        self.assertEqual(payload["pending"], 1)

    def test_ws_requires_query_token_when_enabled(self) -> None:
        with ExitStack() as stack:
            app = _load_app(stack, token="testtoken")
            stack.enter_context(
                patch(
                    "raelyn.api.ws._query_jobs",
                    return_value=[{"id": "job-1", "status": "running", "type": "demo"}],
                )
            )
            with TestClient(app) as client:
                with self.assertRaises(WebSocketDisconnect) as unauthorized:
                    with client.websocket_connect("/api/ws/jobs") as ws:
                        ws.receive_json()

                with self.assertRaises(WebSocketDisconnect) as wrong:
                    with client.websocket_connect("/api/ws/jobs?token=wrong") as ws:
                        ws.receive_json()

                with client.websocket_connect("/api/ws/jobs?token=testtoken") as ws:
                    payload = ws.receive_json()

        self.assertEqual(unauthorized.exception.code, 4401)
        self.assertEqual(getattr(unauthorized.exception, "reason", ""), "unauthorized")
        self.assertEqual(wrong.exception.code, 4401)
        self.assertEqual(payload["type"], "jobs")
        self.assertEqual(payload["jobs"][0]["id"], "job-1")


if __name__ == "__main__":
    unittest.main()
