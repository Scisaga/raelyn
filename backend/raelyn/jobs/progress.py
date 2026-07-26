from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import case, update
from sqlalchemy.engine import Connection

from raelyn.db import engine
from raelyn.models import Job
from raelyn.jobs.worker_activity import touch_current_worker_activity
from raelyn.jobs.worker_activity import touch_worker_activity_for_job


def _set_job_progress(
    conn: Connection,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    current: int | None,
    total: int | None,
    lease_expires_at: datetime | None = None,
) -> bool:
    if not str(worker_id or "").strip() or execution_token is None:
        return False
    values: dict[str, Any] = {"progress_current": current, "progress_total": total}
    if lease_expires_at is not None:
        values["lease_expires_at"] = case(
            (Job.lease_expires_at.is_(None), lease_expires_at),
            (Job.lease_expires_at < lease_expires_at, lease_expires_at),
            else_=Job.lease_expires_at,
        )
    result = conn.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.worker_id == worker_id,
            Job.execution_token == execution_token,
        )
        .values(**values)
    )
    return result.rowcount == 1


def set_job_progress(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    current: int | None,
    total: int | None,
    lease_expires_at: datetime | None = None,
) -> bool:
    with engine.begin() as conn:
        updated = _set_job_progress(
            conn,
            job_id=job_id,
            worker_id=worker_id,
            execution_token=execution_token,
            current=current,
            total=total,
            lease_expires_at=lease_expires_at,
        )
    if updated and not touch_current_worker_activity():
        touch_worker_activity_for_job(job_id=job_id)
    return updated


def _set_job_lease_deadline(
    conn: Connection,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    lease_expires_at: datetime,
) -> bool:
    if not str(worker_id or "").strip() or execution_token is None:
        return False
    next_lease = case(
        (Job.lease_expires_at.is_(None), lease_expires_at),
        (Job.lease_expires_at < lease_expires_at, lease_expires_at),
        else_=Job.lease_expires_at,
    )
    result = conn.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.worker_id == worker_id,
            Job.execution_token == execution_token,
        )
        .values(lease_expires_at=next_lease)
    )
    return result.rowcount == 1


def set_job_lease_deadline(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    lease_expires_at: datetime,
) -> bool:
    with engine.begin() as conn:
        return _set_job_lease_deadline(
            conn,
            job_id=job_id,
            worker_id=worker_id,
            execution_token=execution_token,
            lease_expires_at=lease_expires_at,
        )
