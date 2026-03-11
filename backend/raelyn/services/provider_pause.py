from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from raelyn.models import AppConfig, Job, Media, Video
from raelyn.timeutil import utcnow


PROVIDER_PAUSE_CONFIG_KEY = "provider_pause"
BILIBILI_PROVIDER_PAUSE_REASON = "bilibili_risk_control"


class ProviderPauseRequestError(RuntimeError):
    def __init__(self, *, provider: str, reason: str, message: str) -> None:
        self.provider = _normalize_provider(provider)
        self.reason = str(reason or "").strip() or "provider_pause_requested"
        super().__init__(str(message or "").strip() or "provider pause requested")


def provider_display_name(provider: str) -> str:
    p = _normalize_provider(provider)
    if p == "bilibili":
        return "B站"
    if p == "youtube":
        return "YouTube"
    return p or "provider"


def bilibili_provider_pause_message() -> str:
    return (
        "B站任务已暂停：请求被风控或登录态无效。请在 UI -> 设置 更新 YTDLP_COOKIES；"
        "必要时降低同步频率、稍后重试或更换网络。"
    )


def _normalize_provider(provider: str | None) -> str:
    return str(provider or "").strip().lower()


def _sanitize_pause(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"paused": False, "reason": None, "message": None, "set_at": None}
    paused = bool(value.get("paused")) if isinstance(value.get("paused"), bool) else False
    reason = value.get("reason")
    message = value.get("message")
    set_at = value.get("set_at")
    return {
        "paused": paused,
        "reason": str(reason) if isinstance(reason, str) and reason else None,
        "message": str(message) if isinstance(message, str) and message else None,
        "set_at": str(set_at) if isinstance(set_at, str) and set_at else None,
    }


def _load_provider_pause_map(session: Session) -> tuple[AppConfig | None, dict[str, Any]]:
    item = session.get(AppConfig, PROVIDER_PAUSE_CONFIG_KEY)
    value = item.value if item else None
    return item, value if isinstance(value, dict) else {}


def get_provider_pauses(session: Session) -> dict[str, dict[str, Any]]:
    _item, value = _load_provider_pause_map(session)
    out: dict[str, dict[str, Any]] = {}
    for raw_provider, raw_pause in value.items():
        provider = _normalize_provider(raw_provider)
        if not provider:
            continue
        pause = _sanitize_pause(raw_pause)
        if pause["paused"]:
            out[provider] = pause
    return out


def get_provider_pause(session: Session, provider: str) -> dict[str, Any]:
    p = _normalize_provider(provider)
    if not p:
        return {"paused": False, "reason": None, "message": None, "set_at": None}
    return get_provider_pauses(session).get(p, {"paused": False, "reason": None, "message": None, "set_at": None})


def is_provider_paused(session: Session, provider: str) -> bool:
    return bool(get_provider_pause(session, provider).get("paused"))


def set_provider_paused(session: Session, *, provider: str, reason: str, message: str) -> dict[str, Any]:
    p = _normalize_provider(provider)
    if not p:
        return {"paused": False, "reason": None, "message": None, "set_at": None}

    pause = {
        "paused": True,
        "reason": str(reason or "").strip() or "provider_pause_requested",
        "message": str(message or "").strip() or f"{provider_display_name(p)}任务已暂停",
        "set_at": utcnow().isoformat(),
    }
    item, value = _load_provider_pause_map(session)
    current = _sanitize_pause(value.get(p))
    if current["paused"] and current["reason"] == pause["reason"] and current["message"] == pause["message"]:
        return current

    next_value = dict(value)
    next_value[p] = pause
    if item:
        item.value = next_value
        item.updated_at = utcnow()
    else:
        session.add(AppConfig(key=PROVIDER_PAUSE_CONFIG_KEY, value=next_value))
    session.flush()
    return get_provider_pause(session, p)


def clear_provider_pause(session: Session, *, provider: str) -> dict[str, Any]:
    p = _normalize_provider(provider)
    if not p:
        return {"paused": False, "reason": None, "message": None, "set_at": None}
    clear_provider_pauses(session, providers=[p])
    return get_provider_pause(session, p)


def clear_provider_pauses(session: Session, *, providers: list[str] | None = None) -> dict[str, dict[str, Any]]:
    item, value = _load_provider_pause_map(session)
    if not item:
        return {}

    if providers:
        remove = {_normalize_provider(provider) for provider in providers if _normalize_provider(provider)}
        next_value = {k: v for k, v in value.items() if _normalize_provider(k) not in remove}
    else:
        next_value = {}

    if next_value == value:
        return get_provider_pauses(session)

    item.value = next_value
    item.updated_at = utcnow()
    session.flush()
    return get_provider_pauses(session)


def job_provider(session: Session, job: Job) -> str | None:
    job_type = str(getattr(job, "type", "") or "").strip()
    if job_type == "video.download.youtube":
        return "youtube"
    if job_type == "video.download.bilibili":
        return "bilibili"

    params = getattr(job, "params", None) or {}
    if not isinstance(params, dict):
        return None

    if job_type in {"media.sync_profile", "media.sync_videos"}:
        raw_media_id = params.get("media_id")
        try:
            media_id = uuid.UUID(str(raw_media_id))
        except Exception:
            return None
        media = session.get(Media, media_id)
        return _normalize_provider(getattr(media, "provider", None)) or None

    if job_type in {"video.download"}:
        raw_video_id = params.get("video_id")
        try:
            video_id = uuid.UUID(str(raw_video_id))
        except Exception:
            return None
        video = session.get(Video, video_id)
        return _normalize_provider(getattr(video, "provider", None)) or None

    return None
