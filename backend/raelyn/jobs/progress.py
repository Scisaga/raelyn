from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import update

from raelyn.db import engine
from raelyn.models import Job


def set_job_progress(
    *,
    job_id: uuid.UUID,
    current: int | None,
    total: int | None,
    lease_expires_at: datetime | None = None,
) -> None:
    values = {"progress_current": current, "progress_total": total}
    if lease_expires_at is not None:
        values["lease_expires_at"] = lease_expires_at
    with engine.begin() as conn:
        conn.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(**values)
        )
