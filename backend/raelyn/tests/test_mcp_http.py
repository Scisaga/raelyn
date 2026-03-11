from __future__ import annotations

import importlib
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from raelyn import config


class McpHttpTests(unittest.IsolatedAsyncioTestCase):
    def _load_app(self, stack: ExitStack):
        stack.enter_context(patch.object(config.settings, "mcp_bearer_token", "testtoken"))
        stack.enter_context(patch.object(config.settings, "mcp_base_path", "/mcp"))
        stack.enter_context(patch.object(config.settings, "mcp_host", "0.0.0.0"))
        stack.enter_context(patch.object(config.settings, "mcp_port", 8001))
        stack.enter_context(patch("raelyn.db.init_db"))
        stack.enter_context(patch("raelyn.services.s3.s3_ensure_bucket"))
        sys.modules.pop("raelyn.mcp_main", None)
        module = importlib.import_module("raelyn.mcp_main")
        return module.create_app()

    async def test_health_is_public_and_mcp_requires_bearer_token(self) -> None:
        with ExitStack() as stack:
            app = self._load_app(stack)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                health = await client.get("/health")
                unauthorized = await client.post("/mcp")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True, "service": "raelyn-mcp"})
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers.get("WWW-Authenticate"), "Bearer")

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
            transport = ASGITransport(app=app)

            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                    follow_redirects=True,
                    headers={"Authorization": "Bearer testtoken"},
                ) as http_client:
                    async with streamable_http_client(
                        "http://testserver/mcp",
                        http_client=http_client,
                    ) as streams:
                        read_stream, write_stream, _get_session_id = streams
                        session = ClientSession(read_stream, write_stream)
                        async with session:
                            await session.initialize()
                            tools = await session.list_tools()
                            tool_names = {tool.name for tool in tools.tools}
                            tool_result = await session.call_tool(
                                "get_video",
                                {"video_id": "00000000-0000-0000-0000-000000000001"},
                            )
                            resource_result = await session.read_resource(
                                "raelyn://video/00000000-0000-0000-0000-000000000001/transcript/chunks/0"
                            )

        self.assertIn("get_video", tool_names)
        self.assertIn("get_video_context", tool_names)
        self.assertEqual(
            tool_result.structuredContent,
            {"id": "00000000-0000-0000-0000-000000000001", "title": "Demo"},
        )
        self.assertEqual(len(resource_result.contents), 1)
        resource_payload = json.loads(resource_result.contents[0].text)
        self.assertEqual(resource_payload["text"], "hello")
        self.assertEqual(resource_payload["chunk_index"], 0)


if __name__ == "__main__":
    unittest.main()
