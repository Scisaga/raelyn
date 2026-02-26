from __future__ import annotations

import uuid

from sqlalchemy import update

from videosync.db import engine
from videosync.models import Job


def set_job_progress(*, job_id: uuid.UUID, current: int | None, total: int | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(progress_current=current, progress_total=total)
        )

