from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from raelyn.config import settings
from raelyn.db import init_db, session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import AppConfig, Job, Media, ResourceUsageDaily
from raelyn.services.log_timestamps import install_if_needed
from raelyn.services.provider_pause import get_provider_pause, provider_pause_allows_public_discovery
from raelyn.services.s3 import s3_ensure_bucket
from raelyn.services.system_pause import is_paused
from raelyn.services.usage import (
    LEGACY_USAGE_BACKFILL_CONFIG_KEY,
    LEGACY_USAGE_BACKFILL_VERSION,
    USAGE_TIMEZONE,
)
from raelyn.timeutil import utcnow


_USAGE_SNAPSHOT_JOB_TYPE = "system.capture_usage_snapshot"
_USAGE_LEGACY_BACKFILL_JOB_TYPE = "system.backfill_legacy_usage"
_USAGE_SNAPSHOT_INTERVAL = timedelta(hours=1)


def _has_pending_sync_job(session, media_id) -> bool:
    row = session.execute(
        text(
            "select 1 from job "
            "where type = 'media.sync_videos' "
            "and status in ('pending','running') "
            "and params->>'media_id' = :mid "
            "limit 1"
        ).bindparams(mid=str(media_id))
    ).first()
    return row is not None


def _sync_jitter_minutes(media_id: Any, last_video_sync_at: Any) -> int:
    jitter_max = max(0, int(settings.sync_interval_jitter_minutes))
    if jitter_max <= 0 or last_video_sync_at is None:
        return 0
    seed = f"{media_id}:{last_video_sync_at.isoformat() if hasattr(last_video_sync_at, 'isoformat') else last_video_sync_at}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % (jitter_max + 1)


def _is_sync_due(media: Media, now: Any) -> bool:
    last_sync = media.last_video_sync_at
    if last_sync is None:
        return True
    due_after = timedelta(minutes=int(settings.sync_interval_minutes) + _sync_jitter_minutes(media.id, last_sync))
    return last_sync + due_after <= now


def _positive_int(value: Any, *, default: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = int(default)
    return max(1, n)


def _resource_usage_snapshot_due_at(last_captured_at: datetime | None, now: datetime) -> bool:
    if last_captured_at is None:
        return True
    last = last_captured_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return last.astimezone(timezone.utc) + _USAGE_SNAPSHOT_INTERVAL <= current.astimezone(timezone.utc)


def _has_active_usage_snapshot_job(session) -> bool:
    return session.execute(
        select(Job.id)
        .where(
            Job.type == _USAGE_SNAPSHOT_JOB_TYPE,
            Job.status.in_(("pending", "running")),
        )
        .limit(1)
    ).scalar_one_or_none() is not None


def _legacy_usage_backfill_completed(session) -> bool:
    marker = session.get(AppConfig, LEGACY_USAGE_BACKFILL_CONFIG_KEY)
    value = marker.value if marker is not None and isinstance(marker.value, dict) else {}
    try:
        return int(value.get("version") or 0) >= LEGACY_USAGE_BACKFILL_VERSION
    except (TypeError, ValueError):
        return False


def tick_usage_legacy_backfill() -> int:
    """一次性入队历史 LLM 用量迁移；聚合扫描由 sync worker 执行。"""

    with session_scope() as session:
        if is_paused(session) or _legacy_usage_backfill_completed(session):
            return 0
        active_job = session.execute(
            select(Job.id)
            .where(
                Job.type == _USAGE_LEGACY_BACKFILL_JOB_TYPE,
                Job.status.in_(("pending", "running")),
            )
            .limit(1)
        ).scalar_one_or_none()
        if active_job is not None:
            return 0
        enqueue_job(
            session,
            type_=_USAGE_LEGACY_BACKFILL_JOB_TYPE,
            params={"version": LEGACY_USAGE_BACKFILL_VERSION},
            priority=0,
        )
        return 1


def tick_usage_snapshot() -> int:
    """只负责按小时入队；库存扫描与快照写入由 worker 执行。"""

    now = utcnow()
    with session_scope() as session:
        if is_paused(session) or _has_active_usage_snapshot_job(session):
            return 0
        last_captured_at = session.execute(
            select(func.max(ResourceUsageDaily.captured_at))
        ).scalar_one_or_none()
        if not _resource_usage_snapshot_due_at(last_captured_at, now):
            return 0
        local_day = now.astimezone(ZoneInfo(USAGE_TIMEZONE)).date().isoformat()
        enqueue_job(
            session,
            type_=_USAGE_SNAPSHOT_JOB_TYPE,
            params={"date": local_day},
            priority=0,
        )
        return 1


def tick() -> int:
    now = utcnow()
    threshold = now - timedelta(minutes=settings.sync_interval_minutes)
    candidate_limit = max(int(settings.sync_batch_size) * 4, int(settings.sync_batch_size))
    with session_scope() as session:
        if is_paused(session):
            return 0
        stmt = (
            select(Media)
            .where(Media.monitor_enabled.is_(True))
            .where((Media.last_video_sync_at.is_(None)) | (Media.last_video_sync_at < threshold))
            .order_by(Media.last_video_sync_at.asc().nullsfirst(), Media.updated_at.desc())
            .limit(candidate_limit)
        )
        medias = session.execute(stmt).scalars().all()
        enqueued = 0
        for m in medias:
            if enqueued >= int(settings.sync_batch_size):
                break
            if not _is_sync_due(m, now):
                continue
            if _has_pending_sync_job(session, m.id):
                continue
            provider_pause = get_provider_pause(session, m.provider)
            if provider_pause.get("paused"):
                if not bool(settings.sync_public_discovery_enabled):
                    continue
                if not provider_pause_allows_public_discovery(provider_pause):
                    continue
                enqueue_job(
                    session,
                    type_="media.sync_videos",
                    params={
                        "media_id": str(m.id),
                        "public_discovery": True,
                        "max_entries": _positive_int(settings.sync_public_discovery_max_entries, default=200),
                        "download_priority": 8,
                    },
                    priority=1,
                )
                enqueued += 1
                continue
            enqueue_job(session, type_="media.sync_videos", params={"media_id": str(m.id)}, priority=1)
            enqueued += 1
        return enqueued


def main() -> None:
    install_if_needed()
    init_db()
    s3_ensure_bucket()
    while True:
        tick()
        tick_usage_legacy_backfill()
        tick_usage_snapshot()
        time.sleep(60)


if __name__ == "__main__":
    main()
