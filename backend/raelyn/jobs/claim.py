from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case
from sqlalchemy import exists
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.models import Job, JobEvent, WorkerHeartbeat
from raelyn.services.provider_pause import is_provider_paused, job_provider
from raelyn.services.system_pause import is_paused
from raelyn.timeutil import utcnow


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


def requeue_orphan_running_jobs(
    session: Session,
    *,
    stale_after_seconds: int,
    priority_bump: int = 1000,
) -> int:
    """
    Requeue "running" jobs whose worker heartbeat is missing/stale.

    This is designed to make restarts safer: if the container/process restarts,
    previously "running" jobs should quickly return to pending and be retried.
    """
    now = utcnow()
    stale_before = now - timedelta(seconds=int(stale_after_seconds))

    hb = WorkerHeartbeat
    stmt = (
        select(Job)
        .where(
            Job.status == "running",
            Job.worker_id.is_not(None),
            ~exists(
                select(1).select_from(hb).where(
                    hb.worker_id == Job.worker_id,
                    hb.updated_at >= stale_before,
                )
            ),
        )
        .with_for_update(skip_locked=True)
    )
    jobs = session.execute(stmt).scalars().all()
    if not jobs:
        return 0

    max_pending_priority = session.execute(select(func.max(Job.priority)).where(Job.status == "pending")).scalar_one_or_none()
    base_priority = int((max_pending_priority or 0) + max(1, int(priority_bump or 0)))

    for idx, job in enumerate(jobs):
        job.status = "pending"
        job.worker_id = None
        job.lease_expires_at = None
        job.scheduled_for = now
        job.progress_current = None
        job.progress_total = None
        job.priority = max(int(job.priority or 0), base_priority + idx)
        session.add(JobEvent(job_id=job.id, level="warn", message="worker stale; requeued (promoted)"))

    return len(jobs)


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    type_in: list[str] | None = None,
) -> Job | None:
    if is_paused(session):
        return None
    now = utcnow()
    rank = case(*[(Job.type == t, r) for t, r in _JOB_TYPE_RANK.items()], else_=10)
    base_stmt = select(Job).where(Job.status == "pending", Job.scheduled_for <= now)
    if type_in:
        base_stmt = base_stmt.where(Job.type.in_(list(type_in)))
    ordered = base_stmt.order_by(Job.priority.desc(), rank.asc(), Job.scheduled_for.asc(), Job.created_at.asc())

    job = None
    batch_size = 50
    offset = 0
    while True:
        stmt = ordered.with_for_update(skip_locked=True).offset(offset).limit(batch_size)
        rows = session.execute(stmt).scalars().all()
        if not rows:
            return None
        for candidate in rows:
            provider = job_provider(session, candidate)
            if provider and is_provider_paused(session, provider):
                continue
            job = candidate
            break
        if job is not None:
            break
        if len(rows) < batch_size:
            return None
        offset += batch_size

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
