from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import select, text

from videosync.config import settings
from videosync.db import init_db, session_scope
from videosync.jobs.enqueue import enqueue_job
from videosync.models import Media
from videosync.services.s3 import s3_ensure_bucket
from videosync.timeutil import utcnow


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


def tick() -> int:
    now = utcnow()
    threshold = now - timedelta(minutes=settings.sync_interval_minutes)
    with session_scope() as session:
        stmt = (
            select(Media)
            .where(Media.monitor_enabled.is_(True))
            .where((Media.last_video_sync_at.is_(None)) | (Media.last_video_sync_at < threshold))
            .order_by(Media.last_video_sync_at.asc().nullsfirst(), Media.updated_at.desc())
            .limit(settings.sync_batch_size)
        )
        medias = session.execute(stmt).scalars().all()
        enqueued = 0
        for m in medias:
            if _has_pending_sync_job(session, m.id):
                continue
            enqueue_job(session, type_="media.sync_videos", params={"media_id": str(m.id)}, priority=1)
            enqueued += 1
        return enqueued


def main() -> None:
    init_db()
    s3_ensure_bucket()
    while True:
        tick()
        time.sleep(60)


if __name__ == "__main__":
    main()
