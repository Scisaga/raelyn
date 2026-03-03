from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.db import session_scope
from raelyn.models import AppConfig
from raelyn.services.system_pause import clear_pause, get_pause
from raelyn.timeutil import utcnow


router = APIRouter(tags=["config"])


class ConfigUpsert(BaseModel):
    value: dict


def _looks_like_netscape_cookie_file(text: str) -> bool:
    # Netscape cookies.txt lines are tab-separated with 7 fields:
    # domain \t flag \t path \t secure \t expiration \t name \t value
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("\t") >= 6:
            return True
    return False


@router.get("/config")
def get_config() -> dict:
    with session_scope() as session:
        items = session.execute(select(AppConfig)).scalars().all()
        return {"data": {i.key: i.value for i in items}}


@router.put("/config/{key}")
def put_config(key: str, payload: ConfigUpsert) -> dict:
    with session_scope() as session:
        if key == "ytdlp_cookies":
            try:
                text = payload.value.get("text") if isinstance(payload.value, dict) else ""
            except Exception:
                text = ""
            if isinstance(text, str) and text.strip():
                if not _looks_like_netscape_cookie_file(text):
                    raise HTTPException(
                        status_code=400,
                        detail="ytdlp_cookies must be Netscape cookies.txt format (tab-separated).",
                    )

        existing = session.get(AppConfig, key)
        if existing:
            existing.value = payload.value
            existing.updated_at = utcnow()
        else:
            session.add(AppConfig(key=key, value=payload.value))

        # If the system was paused due to invalid/expired cookies, resume automatically after cookies update.
        if key == "ytdlp_cookies":
            p = get_pause(session)
            reason = str(p.get("reason") or "")
            if p.get("paused") and reason.startswith("ytdlp_cookies_"):
                clear_pause(session)
    return {"ok": True}
