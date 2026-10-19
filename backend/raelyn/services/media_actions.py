from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Job, JobEvent, Media
from raelyn.services.media_deletion import active_media_delete_job_map, ensure_media_not_deleting
from raelyn.timeutil import utcnow

_RECENT_SYNC_DOWNLOAD_PRIORITY = 8
_FULL_SYNC_DOWNLOAD_PRIORITY = 5


def normalize_sync_scope(scope: str | None) -> tuple[str, int]:
    scope_key = str(scope or "").strip().lower() or "recent"
    if scope_key in {"recent", "latest"}:
        return "recent", int(settings.sync_max_entries)
    if scope_key in {"all", "full"}:
        return "all", 0
    raise ValueError(f"invalid scope: {scope!r} (expected: recent|all)")


def _schedule_video_sync(session: Session, *, params: dict[str, Any]) -> uuid.UUID:
    job_id = enqueue_job(session, type_="media.sync_videos", params=params, priority=5)
    if params.get("enqueue_existing_downloads"):
        # 普通入队去重保留旧参数；显式全量请求必须将 pending 近期扫描升级。
        # enqueue 的插入/冲突更新已锁定该行，不能修改另一条正在运行的同步。
        now = utcnow()
        session.execute(
            update(Job).where(Job.id == job_id, Job.status == "pending").values(
                params=params,
                priority=case((Job.priority < 5, 5), else_=Job.priority),
                scheduled_for=case((Job.scheduled_for > now, now), else_=Job.scheduled_for),
            )
        )
        session.add(JobEvent(
            job_id=job_id, level="info", message="full media sync requested",
            data={"scope": "all", "max_entries": 0, "enqueue_existing_downloads": True},
        ))
    return job_id


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
        video_job_params["download_priority"] = _FULL_SYNC_DOWNLOAD_PRIORITY
        # 全量历史同步除了发现新视频，还要给库里已发现但尚未下载的历史视频补投下载。
        video_job_params["enqueue_existing_downloads"] = True
    profile_job_id = enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media.id)}, priority=10)
    videos_job_id = _schedule_video_sync(session, params=video_job_params)
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
            video_job_params["download_priority"] = _FULL_SYNC_DOWNLOAD_PRIORITY
            video_job_params["enqueue_existing_downloads"] = True
        enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media_id)}, priority=10)
        _schedule_video_sync(session, params=video_job_params)
    return {"ok": True, "status": "accepted", "scope": scope_key, "count": len(media_ids)}
