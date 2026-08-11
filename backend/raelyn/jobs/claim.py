from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import case
from sqlalchemy import exists
from sqlalchemy import func
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from raelyn.models import EventMapSnapshot, Job, JobEvent, Media, WorkerHeartbeat
from raelyn.services.provider_pause import is_provider_paused, is_public_discovery_allowed_during_provider_pause, job_provider
from raelyn.services.system_pause import is_paused
from raelyn.services.worker_role_pause import is_worker_role_paused
from raelyn.services.worker_roles import job_type_worker_role, worker_role_for_job
from raelyn.services.youtube_download_circuit import (
    allow_youtube_download_circuit_claim,
    youtube_download_circuit_blocks_claims,
)
from raelyn.timeutil import utcnow


_JOB_TYPE_RANK = {
    # Prioritize post-processing so videos become "ready" quickly after download,
    # and transcripts/notes are generated without being starved by new downloads.
    "video.asr_transcribe": 0,
    "video.normalize_subtitle": 1,
    "video.extract_audio": 2,
}
_SYNC_JOB_TYPES = {
    "media.sync_profile",
    "media.sync_videos",
}
_EXECUTION_HEARTBEAT_JOB_TYPES = {
    "media.sync_profile",
    "media.sync_videos",
    "video.download",
    "video.download.youtube",
    "video.download.bilibili",
    "video.backfill_subtitles",
    "video.backfill_subtitles.youtube",
    "video.backfill_subtitles.bilibili",
}
_DIRECT_PROVIDER_JOB_TYPES = {
    "video.download.youtube": "youtube",
    "video.download.bilibili": "bilibili",
    "video.backfill_subtitles.youtube": "youtube",
    "video.backfill_subtitles.bilibili": "bilibili",
    "video.enrich_metadata.youtube": "youtube",
}
_YOUTUBE_DOWNLOAD_JOB_TYPE = "video.download.youtube"


def _finalize_requeued_event_map_execution(
    session: Session,
    *,
    job: Job,
    execution_token: uuid.UUID | None,
    now,
    reason: str,
) -> int:
    """由回收方终结旧 token 的快照；不触碰新执行仍会复用的 EventMapState。"""

    if job.type != "playlist.build_event_map_snapshot" or execution_token is None:
        return 0
    result = session.execute(
        update(EventMapSnapshot)
        .where(
            EventMapSnapshot.job_id == job.id,
            EventMapSnapshot.execution_token == execution_token,
            EventMapSnapshot.status == "running",
        )
        .values(
            status="failed",
            error_message=str(reason or "event map execution requeued")[:2000],
            finished_at=now,
            updated_at=now,
        )
    )
    return int(result.rowcount or 0)


def _all_claim_types_worker_role_paused(session: Session, type_in: list[str] | None) -> bool:
    if not type_in:
        return False
    roles: set[str] = set()
    for job_type in type_in:
        role = job_type_worker_role(job_type)
        if not role:
            return False
        roles.add(role)
    return bool(roles) and all(is_worker_role_paused(session, role) for role in roles)


def _all_claim_types_provider_paused(session: Session, type_in: list[str] | None) -> bool:
    if not type_in:
        return False
    providers: set[str] = set()
    for job_type in type_in:
        provider = _DIRECT_PROVIDER_JOB_TYPES.get(str(job_type or "").strip())
        if not provider:
            return False
        providers.add(provider)
    return bool(providers) and all(is_provider_paused(session, provider) for provider in providers)


def _is_public_discovery_sync_job(job: Job) -> bool:
    if str(getattr(job, "type", "") or "").strip() != "media.sync_videos":
        return False
    params = getattr(job, "params", None)
    return bool(params.get("public_discovery")) if isinstance(params, dict) else False


def _provider_pause_blocks_job(session: Session, *, job: Job, provider: str) -> bool:
    if not is_provider_paused(session, provider):
        return False
    if _is_public_discovery_sync_job(job):
        return not is_public_discovery_allowed_during_provider_pause(session, provider)
    return True


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

    old_execution_token = job.execution_token
    _finalize_requeued_event_map_execution(
        session,
        job=job,
        execution_token=old_execution_token,
        now=scheduled_for,
        reason=reason,
    )
    previous_scheduled_for = pending.scheduled_for
    previous_priority = int(pending.priority or 0)
    pending.scheduled_for = min(previous_scheduled_for or scheduled_for, scheduled_for)
    if desired_priority is not None:
        pending.priority = max(previous_priority, int(desired_priority or 0))
    pending.error_message = None
    pending.error_stack = None
    pending.started_at = None
    pending.finished_at = None
    pending.execution_token = None

    job.status = "failed"
    job.finished_at = utcnow()
    job.worker_id = None
    job.lease_expires_at = None
    job.execution_token = None
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


def _max_attempts(job: Job) -> int:
    return max(1, int(job.max_attempts or 1))


def _retry_backoff_seconds(attempt: int) -> int:
    return min(600, 10 * (2 ** max(0, int(attempt or 1) - 1)))


def _is_sync_execution_heartbeat_stale(
    session: Session,
    *,
    job: Job,
    process_stale_before,
    execution_stale_before,
) -> bool:
    if str(getattr(job, "type", "") or "").strip() not in _SYNC_JOB_TYPES:
        return False
    worker_id = str(getattr(job, "worker_id", "") or "").strip()
    if not worker_id:
        return False

    hb = session.get(WorkerHeartbeat, worker_id)
    if not hb or not hb.updated_at or hb.updated_at < process_stale_before:
        return False
    return (not hb.active_at) or hb.active_at < execution_stale_before


def _touch_media_sync_cooldown_after_terminal_failure(session: Session, *, job: Job, now) -> None:
    if str(getattr(job, "type", "") or "").strip() != "media.sync_videos":
        return
    raw_media_id = (job.params or {}).get("media_id") if isinstance(job.params, dict) else None
    try:
        media_id = uuid.UUID(str(raw_media_id))
    except Exception:
        return
    media = session.get(Media, media_id)
    if media:
        media.last_video_sync_at = now


def _fail_sync_job_for_execution_heartbeat_stale(
    session: Session,
    *,
    job: Job,
    now,
    execution_stale_after_seconds: int,
) -> None:
    worker_id = job.worker_id
    job.attempt = int(job.attempt or 0) + 1
    job.max_attempts = _max_attempts(job)
    reason = (
        "sync execution heartbeat stale; "
        f"worker_id={worker_id} "
        f"job_type={job.type} "
        f"stale_after_seconds={int(execution_stale_after_seconds)}"
    )
    job.error_message = reason
    job.error_stack = None
    job.worker_id = None
    job.lease_expires_at = None
    job.execution_token = None
    job.progress_current = None
    job.progress_total = None

    if job.attempt < job.max_attempts:
        backoff = _retry_backoff_seconds(job.attempt)
        job.status = "pending"
        job.scheduled_for = now + timedelta(seconds=backoff)
        job.started_at = None
        job.finished_at = None
        session.add(
            JobEvent(
                job_id=job.id,
                level="warn",
                message="sync execution heartbeat stale; retry scheduled",
                data={"attempt": job.attempt, "backoff_seconds": backoff, "worker_id": str(worker_id or "")},
            )
        )
        return

    job.status = "failed"
    job.finished_at = now
    _touch_media_sync_cooldown_after_terminal_failure(session, job=job, now=now)
    session.add(
        JobEvent(
            job_id=job.id,
            level="error",
            message="sync execution heartbeat stale; no retry",
            data={"attempt": job.attempt, "worker_id": str(worker_id or "")},
        )
    )


def requeue_expired_running_jobs(session: Session) -> int:
    now = utcnow()
    stmt = (
        select(Job)
        .where(Job.status == "running", Job.lease_expires_at.is_not(None), Job.lease_expires_at < now)
        .with_for_update(skip_locked=True)
    )
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
        _finalize_requeued_event_map_execution(
            session,
            job=job,
            execution_token=job.execution_token,
            now=now,
            reason="event map execution lease expired; requeued",
        )
        job.status = "pending"
        job.worker_id = None
        job.lease_expires_at = None
        job.execution_token = None
        session.add(JobEvent(job_id=job.id, level="warn", message="lease expired; requeued"))
        job.started_at = None
        job.finished_at = None
    return len(jobs)


def requeue_orphan_running_jobs(
    session: Session,
    *,
    stale_after_seconds: int,
    execution_stale_after_seconds: int | None = None,
    priority_bump: int = 1000,
) -> int:
    """
    回收 worker 心跳缺失或过期的 running 任务。

    进程重启后，之前处于 running 的任务应尽快回到 pending 重试。
    下载任务还要求主执行线程活动心跳新鲜，避免“心跳线程仍活着”
    掩盖下载执行循环已经卡死。
    """
    now = utcnow()
    process_stale_before = now - timedelta(seconds=int(stale_after_seconds))
    execution_stale_after = int(execution_stale_after_seconds or stale_after_seconds)
    execution_stale_before = now - timedelta(seconds=execution_stale_after)

    hb = WorkerHeartbeat
    process_heartbeat_fresh = exists(
        select(1).select_from(hb).where(
            hb.worker_id == Job.worker_id,
            hb.updated_at >= process_stale_before,
        )
    )
    execution_heartbeat_fresh = exists(
        select(1).select_from(hb).where(
            hb.worker_id == Job.worker_id,
            hb.active_at.is_not(None),
            hb.active_at >= execution_stale_before,
        )
    )
    stmt = (
        select(Job)
        .where(
            Job.status == "running",
            Job.worker_id.is_not(None),
            (
                (~process_heartbeat_fresh)
                | (Job.type.in_(_EXECUTION_HEARTBEAT_JOB_TYPES) & ~execution_heartbeat_fresh)
            ),
        )
        .with_for_update(skip_locked=True)
    )
    jobs = session.execute(stmt).scalars().all()
    if not jobs:
        return 0

    requeue_jobs: list[Job] = []
    for job in jobs:
        if _is_sync_execution_heartbeat_stale(
            session,
            job=job,
            process_stale_before=process_stale_before,
            execution_stale_before=execution_stale_before,
        ):
            _fail_sync_job_for_execution_heartbeat_stale(
                session,
                job=job,
                now=now,
                execution_stale_after_seconds=execution_stale_after,
            )
            continue
        requeue_jobs.append(job)

    if not requeue_jobs:
        return len(jobs)

    max_pending_priority = session.execute(select(func.max(Job.priority)).where(Job.status == "pending")).scalar_one_or_none()
    base_priority = int((max_pending_priority or 0) + max(1, int(priority_bump or 0)))

    for idx, job in enumerate(requeue_jobs):
        desired_priority = max(int(job.priority or 0), base_priority + idx)
        if _merge_requeue_into_existing_pending_job(
            session,
            job=job,
            scheduled_for=now,
            desired_priority=desired_priority,
            reason="worker stale; merged into existing pending job",
        ):
            continue
        _finalize_requeued_event_map_execution(
            session,
            job=job,
            execution_token=job.execution_token,
            now=now,
            reason="event map worker stale; execution requeued",
        )
        job.status = "pending"
        job.worker_id = None
        job.lease_expires_at = None
        job.execution_token = None
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
    skip_type_in: set[str] | None = None,
) -> Job | None:
    now = utcnow()
    if is_paused(session):
        return None
    if _all_claim_types_worker_role_paused(session, type_in):
        return None
    if _all_claim_types_provider_paused(session, type_in):
        return None
    youtube_download_circuit_blocked: bool | None = None
    rank = case(*[(Job.type == t, r) for t, r in _JOB_TYPE_RANK.items()], else_=10)
    base_stmt = select(Job).where(Job.status == "pending", Job.scheduled_for <= now)
    if type_in:
        base_stmt = base_stmt.where(Job.type.in_(list(type_in)))
    skip_types = set(skip_type_in or set())
    if skip_types:
        base_stmt = base_stmt.where(Job.type.not_in(list(skip_types)))
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
            if str(getattr(candidate, "type", "") or "") in skip_types:
                continue
            provider = job_provider(session, candidate)
            if provider and _provider_pause_blocks_job(session, job=candidate, provider=provider):
                continue
            role = worker_role_for_job(session, candidate)
            if role and is_worker_role_paused(session, role):
                continue
            if candidate.type == _YOUTUBE_DOWNLOAD_JOB_TYPE:
                if youtube_download_circuit_blocked is None:
                    youtube_download_circuit_blocked = youtube_download_circuit_blocks_claims(session, now=now)
                if youtube_download_circuit_blocked:
                    continue
                if not allow_youtube_download_circuit_claim(session, job_id=candidate.id, now=now):
                    youtube_download_circuit_blocked = True
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
    job.execution_token = uuid.uuid4()
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
