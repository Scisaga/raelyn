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
from raelyn.services.worker_role_pause import is_worker_role_paused
from raelyn.services.worker_roles import worker_role_for_job
from raelyn.timeutil import utcnow


_JOB_TYPE_RANK = {
    # Prioritize post-processing so videos become "ready" quickly after download,
    # and transcripts/notes are generated without being starved by new downloads.
    "video.asr_transcribe": 0,
    "video.normalize_subtitle": 1,
    "video.extract_audio": 2,
}


def _merge_requeue_into_existing_pending_job(
    session: Session,
    *,
    job: Job,
    scheduled_for,
    desired_priority: int | None = None,
    reason: str,
) -> bool:
    dedupe_key = str(getattr(job, "dedupe_key", "") or "").strip()
    if not dedupe_key:
        return False

    pending = session.execute(
        select(Job)
        .where(Job.dedupe_key == dedupe_key, Job.status == "pending", Job.id != job.id)
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if not pending:
        return False

    previous_scheduled_for = pending.scheduled_for
    previous_priority = int(pending.priority or 0)
    pending.scheduled_for = min(previous_scheduled_for or scheduled_for, scheduled_for)
    if desired_priority is not None:
        pending.priority = max(previous_priority, int(desired_priority or 0))
    pending.error_message = None
    pending.error_stack = None
    pending.started_at = None
    pending.finished_at = None

    job.status = "failed"
    job.finished_at = utcnow()
    job.worker_id = None
    job.lease_expires_at = None
    job.progress_current = None
    job.progress_total = None
    job.error_message = reason

    session.add(
        JobEvent(
            job_id=pending.id,
            level="warn",
            message=reason,
            data={
                "source_job_id": str(job.id),
                "scheduled_for": pending.scheduled_for.isoformat() if pending.scheduled_for else None,
                "previous_scheduled_for": previous_scheduled_for.isoformat() if previous_scheduled_for else None,
            },
        )
    )
    session.add(
        JobEvent(
            job_id=job.id,
            level="warn",
            message=reason,
            data={
                "pending_job_id": str(pending.id),
                "scheduled_for": pending.scheduled_for.isoformat() if pending.scheduled_for else None,
            },
        )
    )
    return True


def requeue_expired_running_jobs(session: Session) -> int:
    now = utcnow()
    stmt = select(Job).where(Job.status == "running", Job.lease_expires_at.is_not(None), Job.lease_expires_at < now)
    jobs = session.execute(stmt).scalars().all()
    for job in jobs:
        if _merge_requeue_into_existing_pending_job(
            session,
            job=job,
            scheduled_for=now,
            desired_priority=int(job.priority or 0),
            reason="lease expired; merged into existing pending job",
        ):
            continue
        job.status = "pending"
        job.worker_id = None
        job.lease_expires_at = None
        session.add(JobEvent(job_id=job.id, level="warn", message="lease expired; requeued"))
        job.started_at = None
        job.finished_at = None
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
        desired_priority = max(int(job.priority or 0), base_priority + idx)
        if _merge_requeue_into_existing_pending_job(
            session,
            job=job,
            scheduled_for=now,
            desired_priority=desired_priority,
            reason="worker stale; merged into existing pending job",
        ):
            continue
        job.status = "pending"
        job.worker_id = None
        job.lease_expires_at = None
        job.scheduled_for = now
        job.progress_current = None
        job.progress_total = None
        job.priority = desired_priority
        job.started_at = None
        job.finished_at = None
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
    # 同优先级、同类型等级、同计划时间下，优先领取新创建的任务，
    # 这样新添加的视频采集任务不会长期被旧任务压在后面。
    ordered = base_stmt.order_by(
        Job.priority.desc(),
        rank.asc(),
        Job.scheduled_for.asc(),
        Job.created_at.desc(),
        Job.id.desc(),
    )

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
            role = worker_role_for_job(session, candidate)
            if role and is_worker_role_paused(session, role):
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
