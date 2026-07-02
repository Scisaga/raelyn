from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from raelyn.models import AppConfig, Job, Media, Video
from raelyn.timeutil import utcnow


PROVIDER_PAUSE_CONFIG_KEY = "provider_pause"
BILIBILI_PROVIDER_PAUSE_REASON = "bilibili_risk_control"
_PUBLIC_DISCOVERY_PAUSE_REASONS = {"youtube_bot_check", "youtube_auth_check"}


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
        "B站任务已暂停：请求被风控、出口网络受限，或当前 yt-dlp 尚未包含 B站 412 提取器修复；"
        "请先更新 YTDLP_COOKIES_BILIBILI，若仍 412，请降低同步/下载频率、稍后重试或等待 yt-dlp 官方版本更新。"
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


def provider_pause_allows_public_discovery(pause: dict[str, Any]) -> bool:
    if not bool((pause or {}).get("paused")):
        return False
    reason = str((pause or {}).get("reason") or "").strip()
    return reason.startswith("ytdlp_cookies_") or reason in _PUBLIC_DISCOVERY_PAUSE_REASONS


def is_public_discovery_allowed_during_provider_pause(session: Session, provider: str) -> bool:
    return provider_pause_allows_public_discovery(get_provider_pause(session, provider))


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
    if job_type in {"video.download.youtube", "video.backfill_subtitles.youtube", "video.enrich_metadata.youtube"}:
        return "youtube"
    if job_type in {"video.download.bilibili", "video.backfill_subtitles.bilibili"}:
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

    if job_type in {"video.download", "video.backfill_subtitles"}:
        raw_video_id = params.get("video_id")
        try:
            video_id = uuid.UUID(str(raw_video_id))
        except Exception:
            return None
        video = session.get(Video, video_id)
        return _normalize_provider(getattr(video, "provider", None)) or None

    return None
