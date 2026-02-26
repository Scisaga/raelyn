from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case
from sqlalchemy import select
from sqlalchemy.orm import Session

from videosync.models import Job, JobEvent
from videosync.timeutil import utcnow


_JOB_TYPE_RANK = {
    # Prioritize post-processing so videos become "ready" quickly after download,
    # and transcripts/notes are generated without being starved by new downloads.
    "video.asr_transcribe": 0,
    "video.normalize_subtitle": 1,
    "video.extract_audio": 2,
}


def requeue_expired_running_jobs(session: Session) -> int:
    now = utcnow()
    stmt = select(Job).where(Job.status == "running", Job.lease_expires_at.is_not(None), Job.lease_expires_at < now)
    jobs = session.execute(stmt).scalars().all()
    for job in jobs:
        job.status = "pending"
        job.worker_id = None
        job.lease_expires_at = None
        session.add(JobEvent(job_id=job.id, level="warn", message="lease expired; requeued"))
    return len(jobs)


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    type_in: list[str] | None = None,
) -> Job | None:
    now = utcnow()
    rank = case(*[(Job.type == t, r) for t, r in _JOB_TYPE_RANK.items()], else_=10)
    stmt = select(Job).where(Job.status == "pending", Job.scheduled_for <= now)
    if type_in:
        stmt = stmt.where(Job.type.in_(list(type_in)))
    stmt = (
        stmt.order_by(Job.priority.desc(), rank.asc(), Job.scheduled_for.asc(), Job.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if not job:
        return None

    job.status = "running"
    job.worker_id = worker_id
    # Clear stale errors from previous attempts so the UI doesn't show an old error
    # while the job is currently running.
    job.error_message = None
    job.error_stack = None
    job.progress_current = None
    job.progress_total = None
    if not job.started_at:
        job.started_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    session.add(JobEvent(job_id=job.id, level="info", message="claimed", data={"worker_id": worker_id}))
    return job
