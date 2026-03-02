from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from raelyn.api.assets import router as assets_router
from raelyn.api.briefs import router as briefs_router
from raelyn.api.config_api import router as config_router
from raelyn.api.health import router as health_router
from raelyn.api.jobs import router as jobs_router
from raelyn.api.media import router as media_router
from raelyn.api.playlists import router as playlists_router
from raelyn.api.stats import router as stats_router
from raelyn.api.videos import router as videos_router
from raelyn.api.video_assets import router as video_assets_router
from raelyn.api.ws import router as ws_router
from raelyn.db import init_db
from raelyn.services.s3 import s3_ensure_bucket


init_db()
s3_ensure_bucket()

app = FastAPI(title="raelyn", version="0.1.0")

static_dir = Path(__file__).resolve().parents[2] / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index() -> FileResponse:
    index_path = static_dir / "index.html"
    return FileResponse(str(index_path))


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
app.include_router(ws_router, prefix="/api")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    # Allow API/static to 404 properly; everything else returns the SPA shell so History API routes work.
    if full_path.startswith(("api", "static", ".well-known")):
        raise HTTPException(status_code=404)
    index_path = static_dir / "index.html"
    return FileResponse(str(index_path))
