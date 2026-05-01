from __future__ import annotations

import hashlib
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text

from raelyn.config import settings
from raelyn.db import init_db, session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Media
from raelyn.services.log_timestamps import install_if_needed
from raelyn.services.provider_pause import is_provider_paused
from raelyn.services.s3 import s3_ensure_bucket
from raelyn.services.system_pause import is_paused
from raelyn.timeutil import utcnow


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
            if is_provider_paused(session, m.provider):
                continue
            if _has_pending_sync_job(session, m.id):
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
        time.sleep(60)


if __name__ == "__main__":
    main()
