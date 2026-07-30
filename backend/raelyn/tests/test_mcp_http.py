from __future__ import annotations

import importlib
import json
import sys
import unittest
from contextlib import ExitStack
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from raelyn import config


class _DummySession:
    def execute(self, *_args, **_kwargs):
        return None


@contextmanager
def _fake_health_session_scope():
    yield _DummySession()


class McpHttpTests(unittest.IsolatedAsyncioTestCase):
    def _load_app(
        self,
        stack: ExitStack,
        *,
        mcp_allowed_hosts: str = "",
        mcp_allowed_origins: str = "",
    ):
        stack.enter_context(patch.object(config.settings, "api_bearer_token", "apitoken"))
        stack.enter_context(patch.object(config.settings, "mcp_base_path", "/mcp"))
        stack.enter_context(patch.object(config.settings, "mcp_dns_rebinding_protection_enabled", True))
        stack.enter_context(patch.object(config.settings, "mcp_allowed_hosts", mcp_allowed_hosts))
        stack.enter_context(patch.object(config.settings, "mcp_allowed_origins", mcp_allowed_origins))
        stack.enter_context(patch.object(config.settings, "asr_url", ""))
        stack.enter_context(patch.object(config.settings, "llm_url", ""))
        stack.enter_context(patch.object(config.settings, "embedding_url", ""))
        stack.enter_context(patch("raelyn.db.init_db"))
        stack.enter_context(patch("raelyn.services.s3.s3_ensure_bucket"))
        stack.enter_context(patch("raelyn.recover_orphan_jobs.recover"))
        sys.modules.pop("raelyn.main", None)
        module = importlib.import_module("raelyn.main")
        return module.create_app()

    def _load_app_without_mcp(self, stack: ExitStack):
        stack.enter_context(patch.object(config.settings, "api_bearer_token", ""))
        stack.enter_context(patch.object(config.settings, "mcp_base_path", "/mcp"))
        stack.enter_context(patch.object(config.settings, "mcp_dns_rebinding_protection_enabled", True))
        stack.enter_context(patch.object(config.settings, "mcp_allowed_hosts", ""))
        stack.enter_context(patch.object(config.settings, "mcp_allowed_origins", ""))
        stack.enter_context(patch.object(config.settings, "asr_url", ""))
        stack.enter_context(patch.object(config.settings, "llm_url", ""))
        stack.enter_context(patch.object(config.settings, "embedding_url", ""))
        stack.enter_context(patch("raelyn.db.init_db"))
        stack.enter_context(patch("raelyn.services.s3.s3_ensure_bucket"))
        stack.enter_context(patch("raelyn.recover_orphan_jobs.recover"))
        sys.modules.pop("raelyn.main", None)
        module = importlib.import_module("raelyn.main")
        return module.create_app()

    async def test_health_is_public_and_mcp_requires_bearer_token(self) -> None:
        with ExitStack() as stack:
            app = self._load_app(stack)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                health = await client.get("/mcp/health")
                unauthorized = await client.post("/mcp")
                unauthorized_api = await client.get("/api/system")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True, "service": "raelyn-mcp"})
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers.get("WWW-Authenticate"), "Bearer")
        self.assertEqual(unauthorized_api.status_code, 401)

    async def test_mcp_is_not_mounted_when_token_disabled(self) -> None:
        with ExitStack() as stack:
            app = self._load_app_without_mcp(stack)
            stack.enter_context(patch("raelyn.api.health.session_scope", _fake_health_session_scope))
            stack.enter_context(patch("raelyn.api.health.s3_check_bucket", return_value={"ok": True, "bucket": "test", "error": None}))
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                health = await client.get("/mcp/health")
                endpoint = await client.post("/mcp")
                api_health = await client.get("/api/health")

        self.assertEqual(health.status_code, 404)
        self.assertEqual(endpoint.status_code, 404)
        self.assertEqual(api_health.status_code, 200)

    async def test_streamable_http_client_can_list_tools_call_tool_and_read_resource(self) -> None:
        with ExitStack() as stack:
            app = self._load_app(stack)
            queries = importlib.import_module("raelyn.mcp.queries")
            stack.enter_context(
                patch.object(
                    queries,
                    "get_video",
                    return_value={"id": "00000000-0000-0000-0000-000000000001", "title": "Demo"},
                )
            )
            stack.enter_context(
                patch.object(
                    queries,
                    "get_video_transcript",
                    return_value={
                        "ok": True,
                        "status": "ready",
                        "video_id": "00000000-0000-0000-0000-000000000001",
                        "asset_id": "00000000-0000-0000-0000-000000000002",
                        "language": "zh",
                        "source": "subtitle",
                        "variant": "polished",
                        "polish_method": None,
                        "total_chars": 5,
                        "chunk_index": 0,
                        "chunk_count": 1,
                        "has_more": False,
                        "next_chunk_index": None,
                        "next_uri": None,
                        "text": "hello",
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    queries,
                    "read_brief_body",
                    return_value="# Demo Brief",
                )
            )
            transport = ASGITransport(app=app)

            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=transport,
                    base_url="http://127.0.0.1:8000",
                    follow_redirects=True,
                    headers={"Authorization": "Bearer apitoken"},
                ) as http_client:
                    async with streamable_http_client(
                        "http://127.0.0.1:8000/mcp",
                        http_client=http_client,
                    ) as streams:
                        read_stream, write_stream, _get_session_id = streams
                        session = ClientSession(read_stream, write_stream)
                        async with session:
                            await session.initialize()
                            tools = await session.list_tools()
                            tool_names = {tool.name for tool in tools.tools}
                            get_brief_tool = next(tool for tool in tools.tools if tool.name == "get_brief")
                            tool_result = await session.call_tool(
                                "get_video",
                                {"video_id": "00000000-0000-0000-0000-000000000001"},
                            )
                            resource_result = await session.read_resource(
                                "raelyn://video/00000000-0000-0000-0000-000000000001/transcript/chunks/0"
                            )
                            brief_body_resource = await session.read_resource(
                                "raelyn://brief/00000000-0000-0000-0000-000000000003/body"
                            )

        self.assertIn("get_video", tool_names)
        self.assertIn("get_video_context", tool_names)
        self.assertIn("get_playlist_summary", tool_names)
        self.assertIn("get_playlist_latest_brief", tool_names)
        self.assertIn("list_latest_briefs", tool_names)
        self.assertNotIn("get_playlist_context", tool_names)
        self.assertEqual(set(get_brief_tool.inputSchema["properties"]), {"brief_id", "include_body"})
        self.assertEqual(
            tool_result.structuredContent,
            {"id": "00000000-0000-0000-0000-000000000001", "title": "Demo"},
        )
        self.assertEqual(len(resource_result.contents), 1)
        resource_payload = json.loads(resource_result.contents[0].text)
        self.assertEqual(resource_payload["text"], "hello")
        self.assertEqual(resource_payload["chunk_index"], 0)
        self.assertEqual(brief_body_resource.contents[0].text, "# Demo Brief")

    async def test_public_host_can_be_allowed_via_config(self) -> None:
        with ExitStack() as stack:
            app = self._load_app(
                stack,
                mcp_allowed_hosts="raelyn.example.com:234",
                mcp_allowed_origins="https://raelyn.example.com:234",
            )
            transport = ASGITransport(app=app)

            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=transport,
                    base_url="https://raelyn.example.com:234",
                    headers={"Authorization": "Bearer apitoken"},
                ) as client:
                    response = await client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Content-Type": "application/json",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "debug", "version": "1.0"},
                            },
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["serverInfo"]["name"], "raelyn")


if __name__ == "__main__":
    unittest.main()
