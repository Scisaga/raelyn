from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.db import session_scope
from raelyn.models import AppConfig
from raelyn.services.brief_schedule import (
    brief_generation_policy_defaults,
    normalize_brief_generation_policy,
)
from raelyn.services.provider_pause import clear_provider_pauses
from raelyn.services.transcript_polish_prompt import (
    TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY,
    transcript_polish_prompt_defaults,
)
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


def _validate_llm_transcript_polish_prompt_value(value: dict) -> None:
    text = value.get("text") if isinstance(value, dict) else None
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="llm_transcript_polish_prompt.text must be a string.")
    if len(text.encode("utf-8")) > 64 * 1024:
        raise HTTPException(status_code=400, detail="llm_transcript_polish_prompt.text is too large (max 64KB).")


def _validate_brief_generation_policy_value(value: dict) -> None:
    try:
        normalize_brief_generation_policy(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/config")
def get_config() -> dict:
    with session_scope() as session:
        items = session.execute(select(AppConfig)).scalars().all()
        return {"data": {i.key: i.value for i in items}}


@router.get("/config/defaults")
def get_config_defaults() -> dict:
    defaults = transcript_polish_prompt_defaults()
    defaults.update(brief_generation_policy_defaults())
    return defaults


@router.put("/config/{key}")
def put_config(key: str, payload: ConfigUpsert) -> dict:
    with session_scope() as session:
        value = payload.value
        if key == "ytdlp_cookies":
            try:
                text = value.get("text") if isinstance(value, dict) else ""
            except Exception:
                text = ""
            if isinstance(text, str) and text.strip():
                if not _looks_like_netscape_cookie_file(text):
                    raise HTTPException(
                        status_code=400,
                        detail="ytdlp_cookies must be Netscape cookies.txt format (tab-separated).",
                    )
        if key == TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY:
            _validate_llm_transcript_polish_prompt_value(value)
        if key == "brief_generation_policy":
            _validate_brief_generation_policy_value(value)
            value = normalize_brief_generation_policy(value)

        existing = session.get(AppConfig, key)
        if existing:
            existing.value = value
            existing.updated_at = utcnow()
        else:
            session.add(AppConfig(key=key, value=value))

        # If the system was paused due to invalid/expired cookies, resume automatically after cookies update.
        if key == "ytdlp_cookies":
            p = get_pause(session)
            reason = str(p.get("reason") or "")
            if p.get("paused") and reason.startswith("ytdlp_cookies_"):
                clear_pause(session)
            clear_provider_pauses(session, providers=["bilibili", "youtube"])
    return {"ok": True}
