from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from videosync.models import Job, JobEvent


def job_log(session: Session, job: Job, message: str, *, level: str = "info", data: dict[str, Any] | None = None) -> None:
    session.add(JobEvent(job_id=job.id, level=level, message=message, data=data))
    session.flush()

