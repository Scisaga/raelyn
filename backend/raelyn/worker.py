from __future__ import annotations

import os
import socket
import threading
import time
import traceback
import uuid
from datetime import timedelta

from sqlalchemy import select

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.db import init_db
from raelyn.jobs.claim import claim_next_job, requeue_expired_running_jobs, requeue_orphan_running_jobs
from raelyn.jobs.heartbeat import touch_worker_heartbeat
import raelyn.jobs.handlers  # noqa: F401  注册 handlers
from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import Job
from raelyn.services.job_cancellation import JobCancelRequested, finalize_canceled_job, job_cancel_requested
from raelyn.services.log_timestamps import install_if_needed
from raelyn.services.s3 import s3_ensure_bucket
from raelyn.services.worker_roles import is_known_worker_role, normalize_worker_role, worker_role_types
from raelyn.timeutil import utcnow


def _worker_id() -> str:
    host = socket.gethostname()
    pid = os.getpid()
    # Add a short per-process nonce to avoid collisions across restarts where PIDs may repeat.
    nonce = uuid.uuid4().hex[:8]
    return f"{host}:{pid}:{nonce}"


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def _effective_max_attempts(job_type: str, current: int) -> int:
    # Reduce retries for provider-facing jobs to avoid repeated blocks.
    if job_type in {"media.sync_profile", "media.sync_videos", "media.delete", "video.download", "video.download.youtube", "video.download.bilibili"}:
        return min(int(current or 0) or 5, 2)
    return int(current or 0) or 5


def _resolve_worker_types() -> list[str] | None:
    types_env = os.getenv("WORKER_TYPES", "").strip()
    if types_env:
        types = _parse_csv(types_env)
        return types or None

    role = normalize_worker_role(os.getenv("WORKER_ROLE", ""))
    types = worker_role_types(role)
    if types is not None:
        return types
    if is_known_worker_role(role):
        return None

    print(f"[worker] unknown WORKER_ROLE={role!r}; running in all-types mode")
    return None


def _merge_retry_into_existing_pending_job(
    session,
    *,
    job: Job,
    retry_at,
    attempt: int,
    backoff_seconds: int,
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
    pending.scheduled_for = min(previous_scheduled_for or retry_at, retry_at)
    pending.priority = max(previous_priority, int(job.priority or 0))
    pending.error_message = None
    pending.error_stack = None
    pending.started_at = None
    pending.finished_at = None

    job.status = "failed"
    job.finished_at = utcnow()
    job.lease_expires_at = None
    job.worker_id = None
    job.progress_current = None
    job.progress_total = None

    job_log(
        session,
        pending,
        "retry merged from duplicate running job",
        level="warn",
        data={
            "source_job_id": str(job.id),
            "attempt": attempt,
            "scheduled_for": pending.scheduled_for.isoformat() if pending.scheduled_for else None,
            "previous_scheduled_for": previous_scheduled_for.isoformat() if previous_scheduled_for else None,
            "backoff_seconds": backoff_seconds,
        },
    )
    job_log(
        session,
        job,
        "failed; retry merged into existing pending job",
        level="warn",
        data={
            "attempt": attempt,
            "pending_job_id": str(pending.id),
            "scheduled_for": pending.scheduled_for.isoformat() if pending.scheduled_for else None,
            "backoff_seconds": backoff_seconds,
        },
    )
    return True


class _HeartbeatThread(threading.Thread):
    def __init__(self, *, worker_id: str, interval_seconds: int, role: str) -> None:
        super().__init__(daemon=True)
        self._worker_id = worker_id
        self._interval = max(1, int(interval_seconds or 0))
        self._role = str(role or "").strip() or "all"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with session_scope() as session:
                    touch_worker_heartbeat(session, worker_id=self._worker_id, role=self._role)
            except Exception:
                # Best-effort heartbeat; ignore transient DB errors.
                pass


def run_loop() -> None:
    init_db()
    s3_ensure_bucket()
    worker_id = _worker_id()
    type_in = _resolve_worker_types()
    role = normalize_worker_role(os.getenv("WORKER_ROLE", ""))
    if type_in:
        print(f"[worker] started id={worker_id} role={role} types={','.join(type_in)}")
    else:
        print(f"[worker] started id={worker_id} role={role} types=ALL")
    last_reap = 0.0

    # Ensure the heartbeat row exists before we claim any jobs, so peers won't
    # mis-classify us as stale during startup.
    with session_scope() as session:
        touch_worker_heartbeat(session, worker_id=worker_id, role=role)

    hb = _HeartbeatThread(worker_id=worker_id, interval_seconds=settings.worker_heartbeat_interval_seconds, role=role)
    hb.start()

    # On restart, promptly recover orphaned "running" jobs from dead workers.
    with session_scope() as session:
        requeue_expired_running_jobs(session)
        requeue_orphan_running_jobs(
            session,
            stale_after_seconds=settings.worker_stale_after_seconds,
            priority_bump=settings.orphan_requeue_priority_bump,
        )

    while True:
        now = time.time()
        if now - last_reap > 15:
            with session_scope() as session:
                requeue_expired_running_jobs(session)
                requeue_orphan_running_jobs(
                    session,
                    stale_after_seconds=settings.worker_stale_after_seconds,
                    priority_bump=settings.orphan_requeue_priority_bump,
                )
            last_reap = now

        with session_scope() as session:
            job = claim_next_job(session, worker_id=worker_id, lease_seconds=3600, type_in=type_in)
            if not job:
                pass
            else:
                # Commit the claim immediately so the API can observe "running" jobs
                # while long-running handlers (download/transcode) are executing.
                session.flush()
                session.commit()

                handler = registry.get(job.type)
                if not handler:
                    job.status = "failed"
                    job.error_message = f"unknown job type: {job.type}"
                    job.finished_at = utcnow()
                    job_log(session, job, job.error_message or "failed", level="error")
                    continue

                if job.status == "canceled":
                    job.finished_at = utcnow()
                    job_log(session, job, "job canceled; skip")
                    continue
                if job_cancel_requested(job):
                    finalize_canceled_job(session, job, message="canceled before run", reason="cancel_requested")
                    continue

                try:
                    job_log(session, job, "running")
                    result = handler(session, job)
                    # If an operator canceled the job while it was running, do not overwrite status.
                    try:
                        session.refresh(job)
                    except Exception:
                        pass
                    if job.status == "canceled":
                        job.finished_at = job.finished_at or utcnow()
                        job.lease_expires_at = None
                        job_log(session, job, "canceled; result ignored", level="warn")
                        continue
                    if job_cancel_requested(job):
                        finalize_canceled_job(session, job, message="canceled after handler completed", reason="cancel_requested")
                        continue

                    job.result = result
                    if (
                        isinstance(job.progress_current, int)
                        and isinstance(job.progress_total, int)
                        and job.progress_total > 0
                        and job.progress_current < job.progress_total
                    ):
                        job.progress_current = job.progress_total
                    job.status = "succeeded"
                    job.error_message = None
                    job.error_stack = None
                    job.finished_at = utcnow()
                    job.lease_expires_at = None
                    job_log(session, job, "succeeded")
                except JobCancelRequested:
                    finalize_canceled_job(session, job, message="canceled while running", reason="cancel_requested")
                except JobReschedule as e:
                    # Best-effort: if an operator canceled the job while it was running, keep status=canceled.
                    try:
                        session.refresh(job)
                    except Exception:
                        pass
                    if job.status == "canceled":
                        job.finished_at = job.finished_at or utcnow()
                        job.lease_expires_at = None
                        job_log(session, job, "canceled", level="warn")
                        continue
                    if job_cancel_requested(job):
                        finalize_canceled_job(session, job, message="canceled while rescheduling", reason="cancel_requested")
                        continue

                    job.status = "pending"
                    job.scheduled_for = utcnow() + timedelta(seconds=int(e.delay_seconds or 0))
                    job.error_message = None
                    job.error_stack = None
                    job.lease_expires_at = None
                    job.worker_id = None
                    job.progress_current = None
                    job.progress_total = None
                    job.started_at = None
                    job.finished_at = None
                    job_log(session, job, f"rescheduled: {e.reason}", level="warn", data={"delay_seconds": e.delay_seconds})
                except Exception as e:
                    # If canceled while running, keep status=canceled and avoid retries.
                    try:
                        session.refresh(job)
                    except Exception:
                        pass
                    if job.status == "canceled":
                        job.finished_at = job.finished_at or utcnow()
                        job.lease_expires_at = None
                        job.worker_id = None
                        job_log(session, job, "canceled", level="warn")
                        continue
                    if job_cancel_requested(job):
                        finalize_canceled_job(session, job, message="canceled after handler error", reason="cancel_requested")
                        continue

                    job.attempt += 1
                    job.max_attempts = _effective_max_attempts(job.type, job.max_attempts)
                    job.error_message = str(e)
                    job.error_stack = traceback.format_exc()
                    job.lease_expires_at = None
                    job.worker_id = None
                    try:
                        print(
                            f"[worker] job failed id={job.id} type={job.type} "
                            f"attempt={job.attempt}/{job.max_attempts} err={job.error_message}",
                            flush=True,
                        )
                    except Exception:
                        pass

                    if job.attempt < job.max_attempts:
                        backoff = min(600, 10 * (2 ** (job.attempt - 1)))
                        retry_at = utcnow() + timedelta(seconds=backoff)
                        if _merge_retry_into_existing_pending_job(
                            session,
                            job=job,
                            retry_at=retry_at,
                            attempt=job.attempt,
                            backoff_seconds=backoff,
                        ):
                            continue
                        job.status = "pending"
                        job.scheduled_for = retry_at
                        job.started_at = None
                        job.finished_at = None
                        job_log(session, job, f"failed; retry in {backoff}s", level="warn", data={"attempt": job.attempt})
                    else:
                        job.status = "failed"
                        job.finished_at = utcnow()
                        job_log(session, job, "failed; no more retries", level="error", data={"attempt": job.attempt})

        time.sleep(1)


def main() -> None:
    install_if_needed()
    run_loop()


if __name__ == "__main__":
    main()
