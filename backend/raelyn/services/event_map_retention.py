from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from raelyn.models import EventMapSnapshot, EventMapState


EVENT_MAP_PRUNE_BATCH_SIZE = 1
_PRUNABLE_STATUSES = ("ready", "failed", "canceled")


def _protected_snapshot_ids(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    keep_ready: int,
) -> set[uuid.UUID]:
    protected: set[uuid.UUID] = set()
    current_snapshot_id = session.execute(
        select(EventMapState.current_snapshot_id).where(EventMapState.playlist_id == playlist_id)
    ).scalar_one_or_none()
    if current_snapshot_id is not None:
        protected.add(current_snapshot_id)
    ready_ids = session.execute(
        select(EventMapSnapshot.id)
        .where(
            EventMapSnapshot.playlist_id == playlist_id,
            EventMapSnapshot.status == "ready",
        )
        .order_by(
            EventMapSnapshot.finished_at.desc().nullslast(),
            EventMapSnapshot.created_at.desc(),
            EventMapSnapshot.id.desc(),
        )
        .limit(max(1, int(keep_ready)))
    ).scalars()
    protected.update(ready_ids)
    return protected


def _prunable_snapshot_clause(
    *,
    playlist_id: uuid.UUID,
    protected: set[uuid.UUID],
) -> list[Any]:
    clauses: list[Any] = [
        EventMapSnapshot.playlist_id == playlist_id,
        EventMapSnapshot.status.in_(_PRUNABLE_STATUSES),
    ]
    if protected:
        clauses.append(EventMapSnapshot.id.not_in(protected))
    return clauses


def prune_event_map_snapshots(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    keep_ready: int = 2,
    batch_size: int = EVENT_MAP_PRUNE_BATCH_SIZE,
) -> dict[str, Any]:
    """逐批删除不可见旧快照；current、上一版 ready 和所有运行中快照始终受保护。"""
    protected = _protected_snapshot_ids(
        session,
        playlist_id=playlist_id,
        keep_ready=max(2, int(keep_ready)),
    )
    clauses = _prunable_snapshot_clause(playlist_id=playlist_id, protected=protected)
    candidates = session.execute(
        select(EventMapSnapshot.id, EventMapSnapshot.status)
        .where(*clauses)
        .order_by(
            case((EventMapSnapshot.status == "ready", 1), else_=0).asc(),
            EventMapSnapshot.finished_at.asc().nullsfirst(),
            EventMapSnapshot.created_at.asc(),
            EventMapSnapshot.id.asc(),
        )
        .limit(max(1, int(batch_size)))
    ).all()
    deleted_ids = [snapshot_id for snapshot_id, _status in candidates]
    if deleted_ids:
        session.execute(delete(EventMapSnapshot).where(EventMapSnapshot.id.in_(deleted_ids)))
        session.flush()
    remaining = int(
        session.execute(
            select(func.count()).select_from(EventMapSnapshot).where(*clauses)
        ).scalar_one()
        or 0
    )
    return {
        "playlist_id": str(playlist_id),
        "deleted_snapshot_ids": [str(value) for value in deleted_ids],
        "deleted_count": len(deleted_ids),
        "remaining_count": remaining,
        "protected_snapshot_ids": [str(value) for value in sorted(protected, key=str)],
        "keep_ready": max(2, int(keep_ready)),
    }
