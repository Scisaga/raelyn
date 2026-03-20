from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from raelyn.models import Video


def parse_published_at(info: dict[str, Any] | None) -> datetime | None:
    if not info:
        return None

    for key in ("timestamp", "release_timestamp"):
        v = info.get(key)
        if v is None:
            continue
        try:
            return datetime.fromtimestamp(int(v), tz=timezone.utc)
        except Exception:
            continue

    for key in ("upload_date", "release_date"):
        v = info.get(key)
        if not isinstance(v, str):
            continue
        s = v.strip()
        if len(s) != 8 or not s.isdigit():
            continue
        try:
            return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None


def backfill_video_published_at(session: Session, *, limit: int = 5000) -> int:
    stmt = (
        select(Video)
        .where(
            Video.published_at.is_(None),
            Video.raw_info.is_not(None),
            or_(
                Video.raw_info["timestamp"].astext.is_not(None),
                Video.raw_info["release_timestamp"].astext.is_not(None),
                Video.raw_info["upload_date"].astext.is_not(None),
                Video.raw_info["release_date"].astext.is_not(None),
            ),
        )
        .order_by(Video.created_at.desc())
        .limit(limit)
    )
    videos = session.execute(stmt).scalars().all()
    updated = 0
    for video in videos:
        published_at = parse_published_at(video.raw_info)
        if not published_at:
            continue
        video.published_at = published_at
        updated += 1
    if updated:
        session.flush()
    return updated
