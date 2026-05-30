from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from raelyn.models import WorkerHeartbeat
from raelyn.timeutil import utcnow


_UNSET = object()


def touch_worker_heartbeat(
    session: Session,
    *,
    worker_id: str,
    role: str | None = None,
    active: bool = False,
    current_job_id: uuid.UUID | str | None | object = _UNSET,
) -> None:
    now = utcnow()
    hb = session.get(WorkerHeartbeat, worker_id)
    if hb:
        hb.updated_at = now
        hb.role = role
        if active:
            hb.active_at = now
        if current_job_id is not _UNSET:
            hb.current_job_id = _coerce_uuid(current_job_id)
    else:
        session.add(
            WorkerHeartbeat(
                worker_id=worker_id,
                role=role,
                updated_at=now,
                active_at=now if active else None,
                current_job_id=_coerce_uuid(current_job_id) if current_job_id is not _UNSET else None,
            )
        )


def _coerce_uuid(value: uuid.UUID | str | None | object) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None
