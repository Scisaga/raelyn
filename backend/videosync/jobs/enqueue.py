from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from videosync.models import Job, JobEvent
from videosync.timeutil import utcnow


def enqueue_job(
    session: Session,
    *,
    type_: str,
    params: dict[str, Any],
    priority: int = 0,
    scheduled_for: Any | None = None,
    parent_job_id: str | None = None,
) -> uuid.UUID:
    job = Job(
        type=type_,
        status="pending",
        priority=priority,
        params=params,
        scheduled_for=scheduled_for or utcnow(),
        parent_job_id=uuid.UUID(parent_job_id) if parent_job_id else None,
    )
    session.add(job)
    session.flush()
    session.add(JobEvent(job_id=job.id, level="info", message="enqueued", data={"type": type_}))
    return job.id


def enqueue_in(session: Session, *, seconds: int, type_: str, params: dict[str, Any], priority: int = 0) -> uuid.UUID:
    return enqueue_job(
        session,
        type_=type_,
        params=params,
        priority=priority,
        scheduled_for=utcnow() + timedelta(seconds=seconds),
    )

