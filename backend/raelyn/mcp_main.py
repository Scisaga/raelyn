from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from starlette.routing import Route

from raelyn.config import settings
from raelyn.db import init_db
from raelyn.mcp.app import create_mcp_server
from raelyn.mcp.auth import BearerTokenAuthMiddleware, normalize_mount_path, require_mcp_token
from raelyn.services.s3 import s3_ensure_bucket


def create_app() -> FastAPI:
    require_mcp_token(settings.mcp_bearer_token)
    init_db()
    s3_ensure_bucket()
    base_path = normalize_mount_path(settings.mcp_base_path)
    mcp = create_mcp_server()
    # Initialize the session manager lazily once, but let the parent app own lifespan.
    mcp.streamable_http_app()
    mcp_transport_app = StreamableHTTPASGIApp(mcp.session_manager)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="raelyn-mcp", version="0.1.0", lifespan=lifespan)
    app.router.redirect_slashes = False
    app.add_middleware(
        BearerTokenAuthMiddleware,
        token=settings.mcp_bearer_token,
        protected_prefix=base_path,
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "service": "raelyn-mcp"}

    app.router.routes.append(Route(base_path, endpoint=mcp_transport_app, methods=["GET", "POST", "DELETE"]))
    if base_path != "/":
        app.router.routes.append(Route(f"{base_path}/", endpoint=mcp_transport_app, methods=["GET", "POST", "DELETE"]))
    return app


app = create_app()
