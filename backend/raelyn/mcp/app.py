from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from raelyn.config import settings
from raelyn.mcp.resources import register_resources
from raelyn.mcp.tools import register_tools


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        name="raelyn",
        instructions="Read media, video, transcript, playlist, brief, and job data from the local raelyn instance.",
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
    )
    register_tools(mcp)
    register_resources(mcp)
    return mcp
