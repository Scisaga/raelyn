from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit
from fastapi import APIRouter
from sqlalchemy import text

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.services.embeddings import check_embedding_health
from raelyn.services.inference import build_inference_status
from raelyn.services.inference import check_asr_health
from raelyn.services.inference import check_llm_health
from raelyn.services.s3 import s3_check_bucket


router = APIRouter(tags=["health"])


def _sanitize_dsn(dsn: str) -> str:
    s = (dsn or "").strip()
    if not s:
        return ""
    try:
        parts = urlsplit(s)
        if parts.scheme.startswith("sqlite"):
            return s
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        if parts.username:
            netloc = f"{parts.username}@{netloc}"
        return urlunsplit((parts.scheme, netloc, parts.path or "", "", ""))
    except Exception:
        return s


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

    db["url"] = _sanitize_dsn(settings.database_url)

    # S3/MinIO
    s3 = s3_check_bucket()

    asr = check_asr_health()
    embedding = check_embedding_health()
    llm = check_llm_health()
    inference = build_inference_status()

    ok = bool(db.get("ok"))
    deps_ok = bool(
        db.get("ok")
        and s3.get("ok")
        and (asr.get("ok") or asr.get("configured") is False)
        and (embedding.get("ok") or embedding.get("configured") is False)
        and (llm.get("ok") or llm.get("configured") is False)
    )
    return {
        "ok": ok,
        "deps_ok": deps_ok,
        "db": db,
        "s3": s3,
        "asr": asr,
        "embedding": embedding,
        "llm": llm,
        "inference": inference,
    }
