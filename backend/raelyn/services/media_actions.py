from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Media
from raelyn.services.media_deletion import active_media_delete_job_map, ensure_media_not_deleting

_RECENT_SYNC_DOWNLOAD_PRIORITY = 8


def normalize_sync_scope(scope: str | None) -> tuple[str, int]:
    scope_key = str(scope or "").strip().lower() or "recent"
    if scope_key in {"recent", "latest"}:
        return "recent", int(settings.sync_max_entries)
    if scope_key in {"all", "full"}:
        return "all", 0
    raise ValueError(f"invalid scope: {scope!r} (expected: recent|all)")


def schedule_media_sync(session: Session, media_id: uuid.UUID, *, scope: str = "recent") -> dict[str, Any]:
    media = session.get(Media, media_id)
    if not media:
        raise LookupError("media not found")
    ensure_media_not_deleting(session, media.id)

    scope_key, max_entries = normalize_sync_scope(scope)
    video_job_params: dict[str, Any] = {"media_id": str(media.id), "force": True, "max_entries": max_entries}
    if scope_key == "recent":
        video_job_params["download_priority"] = _RECENT_SYNC_DOWNLOAD_PRIORITY
    else:
        # 全量历史同步除了发现新视频，还要给库里已发现但尚未下载的历史视频补投下载。
        video_job_params["enqueue_existing_downloads"] = True
    profile_job_id = enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media.id)}, priority=10)
    videos_job_id = enqueue_job(
        session,
        type_="media.sync_videos",
        params=video_job_params,
        priority=5,
    )
    return {
        "ok": True,
        "status": "accepted",
        "message": "media sync enqueued",
        "media_id": str(media.id),
        "scope": scope_key,
        "job_id": str(videos_job_id),
        "job_type": "media.sync_videos",
        "job_ids": [str(profile_job_id), str(videos_job_id)],
        "job_types": ["media.sync_profile", "media.sync_videos"],
    }


def schedule_all_media_sync(session: Session, *, scope: str = "recent") -> dict[str, Any]:
    scope_key, max_entries = normalize_sync_scope(scope)
    media_ids = session.execute(select(Media.id).where(Media.monitor_enabled.is_(True))).scalars().all()
    deleting = set(active_media_delete_job_map(session, list(media_ids)).keys())
    media_ids = [media_id for media_id in media_ids if media_id not in deleting]
    for media_id in media_ids:
        video_job_params: dict[str, Any] = {"media_id": str(media_id), "force": True, "max_entries": max_entries}
        if scope_key == "recent":
            video_job_params["download_priority"] = _RECENT_SYNC_DOWNLOAD_PRIORITY
        else:
            video_job_params["enqueue_existing_downloads"] = True
        enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media_id)}, priority=10)
        enqueue_job(
            session,
            type_="media.sync_videos",
            params=video_job_params,
            priority=5,
        )
    return {"ok": True, "status": "accepted", "scope": scope_key, "count": len(media_ids)}
