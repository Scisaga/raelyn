from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from raelyn.api.auth import OptionalBearerTokenAuthMiddleware
from raelyn.api.assets import router as assets_router
from raelyn.api.briefs import router as briefs_router
from raelyn.api.config_api import router as config_router
from raelyn.api.health import router as health_router
from raelyn.api.jobs import router as jobs_router
from raelyn.api.media import router as media_router
from raelyn.api.playlists import router as playlists_router
from raelyn.api.stats import router as stats_router
from raelyn.api.system import router as system_router
from raelyn.api.workers import router as workers_router
from raelyn.api.videos import router as videos_router
from raelyn.api.video_assets import router as video_assets_router
from raelyn.api.ws import router as ws_router
from raelyn.config import settings
from raelyn.db import init_db
from raelyn.mcp.app import create_mcp_http_mount
from raelyn.mcp.auth import BearerTokenAuthMiddleware, normalize_mount_path
from raelyn.services.s3 import s3_ensure_bucket
from raelyn.services.system_pause import SystemPausedError
from starlette.routing import Route

static_dir = Path(__file__).resolve().parents[2] / "static"


def _static_root_file(filename: str, *, media_type: str) -> FileResponse:
    path = static_dir / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type=media_type, headers={"Cache-Control": "no-cache"})


def create_app() -> FastAPI:
    mcp_token = str(settings.mcp_bearer_token or "").strip()
    mcp_base_path = normalize_mount_path(settings.mcp_base_path)
    mcp_mount = create_mcp_http_mount(token=mcp_token) if mcp_token else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        s3_ensure_bucket()
        try:
            from raelyn.recover_orphan_jobs import recover

            recover()
        except Exception:
            # Best-effort; worker(s) will also run recovery. Avoid blocking API startup.
            pass

        if mcp_mount is None:
            yield
            return

        async with mcp_mount.session_manager.run():
            yield

    app = FastAPI(title="raelyn", version="0.1.0", lifespan=lifespan)
    app.add_middleware(OptionalBearerTokenAuthMiddleware, token=settings.api_bearer_token, protected_prefix="/api")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.mount("/pwa", StaticFiles(directory=str(static_dir / "pwa")), name="pwa")
    if mcp_mount is not None:
        app.add_middleware(
            BearerTokenAuthMiddleware,
            token=mcp_token,
            protected_prefix=mcp_base_path,
            public_paths={f"{mcp_base_path}/health"},
        )

    @app.exception_handler(SystemPausedError)
    def _system_paused_handler(_request, exc: SystemPausedError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"ok": False, "detail": str(exc), "pause": exc.pause})

    @app.api_route("/", methods=["GET", "HEAD"])
    def index() -> FileResponse:
        index_path = static_dir / "index.html"
        return FileResponse(str(index_path))

    @app.api_route("/manifest.webmanifest", methods=["GET", "HEAD"])
    def web_manifest() -> FileResponse:
        return _static_root_file("manifest.webmanifest", media_type="application/manifest+json")

    @app.api_route("/sw.js", methods=["GET", "HEAD"])
    def service_worker() -> FileResponse:
        return _static_root_file("sw.js", media_type="application/javascript")

    if mcp_mount is not None:
        @app.get(f"{mcp_base_path}/health")
        def mcp_health() -> dict[str, object]:
            return {"ok": True, "service": "raelyn-mcp"}

        app.router.routes.append(Route(mcp_base_path, endpoint=mcp_mount.transport_app, methods=["GET", "POST", "DELETE"]))
        if mcp_base_path != "/":
            app.router.routes.append(Route(f"{mcp_base_path}/", endpoint=mcp_mount.transport_app, methods=["GET", "POST", "DELETE"]))
    else:
        def _mcp_not_found() -> None:
            raise HTTPException(status_code=404)

        app.add_api_route(f"{mcp_base_path}/health", _mcp_not_found, methods=["GET"])
        app.add_api_route(mcp_base_path, _mcp_not_found, methods=["GET", "POST", "DELETE"])
        if mcp_base_path != "/":
            app.add_api_route(f"{mcp_base_path}/", _mcp_not_found, methods=["GET", "POST", "DELETE"])

    app.include_router(health_router, prefix="/api")
    app.include_router(media_router, prefix="/api")
    app.include_router(videos_router, prefix="/api")
    app.include_router(video_assets_router, prefix="/api")
    app.include_router(assets_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(playlists_router, prefix="/api")
    app.include_router(briefs_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(stats_router, prefix="/api")
    app.include_router(system_router, prefix="/api")
    app.include_router(workers_router, prefix="/api")
    app.include_router(ws_router, prefix="/api")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    def spa_fallback(full_path: str) -> FileResponse:
        # Allow API/static/MCP to 404 properly; everything else returns the SPA shell so History API routes work.
        if full_path.startswith(("api", "static", "pwa", ".well-known")):
            raise HTTPException(status_code=404)
        if mcp_base_path != "/":
            mcp_prefix = mcp_base_path.lstrip("/")
            if full_path == mcp_prefix or full_path.startswith(f"{mcp_prefix}/"):
                raise HTTPException(status_code=404)
        index_path = static_dir / "index.html"
        return FileResponse(str(index_path))

    return app


app = create_app()
