from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from raelyn.config import settings
from raelyn.mcp.auth import require_mcp_token
from raelyn.mcp.resources import register_resources
from raelyn.mcp.tools import register_tools


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        name="raelyn",
        instructions="Read media, video, transcript, playlist, brief, and job data from the local raelyn instance.",
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(settings.mcp_dns_rebinding_protection_enabled),
            allowed_hosts=settings.mcp_allowed_host_values(),
            allowed_origins=settings.mcp_allowed_origin_values(),
        ),
    )
    register_tools(mcp)
    register_resources(mcp)
    return mcp


@dataclass(slots=True)
class McpHttpMount:
    session_manager: Any
    transport_app: Any


def create_mcp_http_mount(*, token: str) -> McpHttpMount:
    require_mcp_token(token)
    mcp = create_mcp_server()
    mcp.streamable_http_app()
    transport_app = StreamableHTTPASGIApp(mcp.session_manager)
    return McpHttpMount(session_manager=mcp.session_manager, transport_app=transport_app)
