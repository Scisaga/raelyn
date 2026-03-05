from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from raelyn.config import settings
from raelyn.db import session_scope
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


def _check_http_get(url: str, *, timeout_seconds: float = 2.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds), headers=headers) as client:
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
    db["url"] = _sanitize_dsn(settings.database_url)

    # S3/MinIO
    s3 = s3_check_bucket()

    # ASR
    asr_url = settings.asr_url.strip()
    if not asr_url:
        asr = {"ok": False, "configured": False, "url": "", "error": "not configured"}
    else:
        asr = {"configured": True, **_check_http_get(asr_url.rstrip("/") + "/health")}

    # LLM
    llm_url = settings.llm_url.strip()
    if not llm_url:
        llm = {"ok": False, "configured": False, "url": "", "error": "not configured"}
    else:
        llm_headers: dict[str, str] = {}
        api_key = settings.llm_api_key.strip()
        if api_key:
            llm_headers["Authorization"] = f"Bearer {api_key}"
        raw_headers = settings.llm_headers_json.strip()
        if raw_headers:
            try:
                import json

                extra = json.loads(raw_headers)
                if isinstance(extra, dict):
                    for k, v in extra.items():
                        if v is None:
                            continue
                        llm_headers[str(k)] = str(v)
            except Exception:
                llm_headers = llm_headers

        u = llm_url.lower()
        if "/api/generate" in u:
            base = llm_url.split("/api/generate", 1)[0].rstrip("/")
            check_url = base + "/api/version"
        elif "/chat/completions" in u:
            base = llm_url.split("/chat/completions", 1)[0].rstrip("/")
            check_url = base + "/models"
        elif "/completions" in u:
            base = llm_url.split("/completions", 1)[0].rstrip("/")
            check_url = base + "/models"
        else:
            check_url = llm_url

        llm = {"configured": True, **_check_http_get(check_url, headers=llm_headers)}

    ok = bool(db.get("ok"))
    deps_ok = bool(
        db.get("ok")
        and s3.get("ok")
        and (asr.get("ok") or asr.get("configured") is False)
        and (llm.get("ok") or llm.get("configured") is False)
    )
    return {"ok": ok, "deps_ok": deps_ok, "db": db, "s3": s3, "asr": asr, "llm": llm}
