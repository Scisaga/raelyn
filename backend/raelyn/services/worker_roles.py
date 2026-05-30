from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from raelyn.models import Job, Media, Video


WORKER_ROLE_ALL = "all"
CONTROLLABLE_WORKER_ROLES: tuple[str, ...] = (
    "download_youtube",
    "download_bilibili",
    "audio",
    "process",
    "asr",
    "sync",
    "embedding",
    "analysis",
    "ai",
)
KNOWN_WORKER_ROLES: tuple[str, ...] = CONTROLLABLE_WORKER_ROLES + (WORKER_ROLE_ALL,)

WORKER_ROLE_TYPES: dict[str, list[str]] = {
    "download_youtube": ["video.download.youtube"],
    "download_bilibili": ["video.download.bilibili"],
    "audio": ["video.extract_audio"],
    "process": ["video.normalize_subtitle"],
    "asr": ["video.asr_transcribe"],
    "sync": ["media.sync_profile", "media.sync_videos", "media.delete"],
    "embedding": ["event.embed"],
    "analysis": ["playlist.build_event_regime_snapshot"],
    "ai": [
        "video.extract_events",
        "playlist.backfill_events",
        "video.polish_transcript",
        "brief.generate_daily",
        "brief.generate_period",
    ],
}

_TYPE_TO_ROLE: dict[str, str] = {}
for _role, _types in WORKER_ROLE_TYPES.items():
    for _job_type in _types:
        _TYPE_TO_ROLE[_job_type] = _role


def normalize_worker_role(role: str | None) -> str:
    value = str(role or "").strip().lower()
    if value in {"", "default"}:
        return WORKER_ROLE_ALL
    return value


def is_known_worker_role(role: str | None) -> bool:
    return normalize_worker_role(role) in KNOWN_WORKER_ROLES


def is_controllable_worker_role(role: str | None) -> bool:
    return normalize_worker_role(role) in CONTROLLABLE_WORKER_ROLES


def worker_role_types(role: str | None) -> list[str] | None:
    normalized = normalize_worker_role(role)
    if normalized == WORKER_ROLE_ALL:
        return None
    types = WORKER_ROLE_TYPES.get(normalized)
    if not types:
        return None
    return list(types)


def known_worker_roles() -> list[str]:
    return list(KNOWN_WORKER_ROLES)


def job_type_worker_role(job_type: str | None) -> str | None:
    normalized_type = str(job_type or "").strip()
    if not normalized_type:
        return None
    return _TYPE_TO_ROLE.get(normalized_type)


def worker_role_for_job(session: Session, job: Job | Any) -> str | None:
    job_type = str(getattr(job, "type", "") or "").strip()
    if not job_type:
        return None

    direct = job_type_worker_role(job_type)
    if direct:
        return direct

    if job_type != "video.download":
        return None

    params = getattr(job, "params", None) or {}
    if not isinstance(params, dict):
        return None

    raw_video_id = params.get("video_id")
    try:
        video_id = uuid.UUID(str(raw_video_id))
    except Exception:
        return None

    video = session.get(Video, video_id)
    provider = str(getattr(video, "provider", "") or "").strip().lower()
    if provider == "youtube":
        return "download_youtube"
    if provider == "bilibili":
        return "download_bilibili"
    return None


def provider_for_job(session: Session, job: Job | Any) -> str | None:
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
        provider = str(getattr(media, "provider", "") or "").strip().lower()
        return provider or None

    if job_type == "video.download":
        raw_video_id = params.get("video_id")
        try:
            video_id = uuid.UUID(str(raw_video_id))
        except Exception:
            return None
        video = session.get(Video, video_id)
        provider = str(getattr(video, "provider", "") or "").strip().lower()
        return provider or None

    return None
