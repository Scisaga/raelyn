from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from videosync.config import settings
from videosync.db import session_scope
from videosync.services.s3 import s3_check_bucket


router = APIRouter(tags=["health"])


def _check_http_get(url: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
            resp = client.get(url)
            resp.raise_for_status()
        return {"ok": True, "url": url, "error": None}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


@router.get("/health")
def health() -> dict:
    # DB
    db: dict[str, Any]
    try:
        with session_scope() as session:
            session.execute(text("select 1"))
        db = {"ok": True, "error": None}
    except Exception as e:
        db = {"ok": False, "error": str(e)}

    # S3/MinIO
    s3 = s3_check_bucket()

    # ASR
    asr_url = settings.asr_url.strip()
    if not asr_url:
        asr = {"ok": False, "configured": False, "url": "", "error": "not configured"}
    else:
        asr = {"configured": True, **_check_http_get(asr_url.rstrip("/") + "/health")}

    # Ollama
    ollama_url = settings.ollama_url.strip()
    if not ollama_url:
        ollama = {"ok": False, "configured": False, "url": "", "error": "not configured"}
    else:
        ollama = {"configured": True, **_check_http_get(ollama_url.rstrip("/") + "/api/version")}

    ok = bool(db.get("ok"))
    deps_ok = bool(
        db.get("ok")
        and s3.get("ok")
        and (asr.get("ok") or asr.get("configured") is False)
        and (ollama.get("ok") or ollama.get("configured") is False)
    )
    return {"ok": ok, "deps_ok": deps_ok, "db": db, "s3": s3, "asr": asr, "ollama": ollama}
