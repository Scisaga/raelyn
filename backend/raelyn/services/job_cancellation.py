from __future__ import annotations

from sqlalchemy.orm import Session

from raelyn.models import Job, JobEvent
from raelyn.timeutil import utcnow


class JobCancelRequested(Exception):
    pass


def job_cancel_requested(job: Job | None) -> bool:
    return bool(job and getattr(job, "cancel_requested_at", None))


def request_job_cancel(session: Session, job: Job, *, reason: str = "manual") -> str:
    now = utcnow()
    status = str(getattr(job, "status", "") or "").strip().lower()
    if status in {"succeeded", "failed", "canceled"}:
        return "noop"

    if status == "pending":
        job.cancel_requested_at = now
        job.status = "canceled"
        job.finished_at = now
        job.lease_expires_at = None
        job.worker_id = None
        session.add(
            JobEvent(
                job_id=job.id,
                level="info",
                message="canceled",
                data={"reason": reason, "mode": "immediate"},
            )
        )
        return "canceled"

    if not job.cancel_requested_at:
        job.cancel_requested_at = now
        session.add(
            JobEvent(
                job_id=job.id,
                level="info",
                message="cancel requested",
                data={"reason": reason, "mode": "cooperative"},
            )
        )
        return "requested"
    return "already_requested"


def finalize_canceled_job(session: Session, job: Job, *, message: str, reason: str) -> None:
    now = utcnow()
    job.status = "canceled"
    job.finished_at = now
    job.lease_expires_at = None
    job.worker_id = None
    session.add(
        JobEvent(
            job_id=job.id,
            level="info",
            message=message,
            data={"reason": reason},
        )
    )


def raise_if_job_cancel_requested(session: Session, job: Job) -> None:
    try:
        session.refresh(job)
    except Exception:
        pass
    if job_cancel_requested(job):
        raise JobCancelRequested("cancel requested")
