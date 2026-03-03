from __future__ import annotations

import os
import socket
import time
import traceback
from datetime import timedelta

from raelyn.db import session_scope
from raelyn.db import init_db
from raelyn.jobs.claim import claim_next_job, requeue_expired_running_jobs
from raelyn.jobs.handlers import *  # noqa: F403  注册 handlers
from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.services.log_timestamps import install_if_needed
from raelyn.services.s3 import s3_ensure_bucket
from raelyn.timeutil import utcnow


def _worker_id() -> str:
    host = socket.gethostname()
    pid = os.getpid()
    return f"{host}:{pid}"


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


_ROLE_TYPES: dict[str, list[str]] = {
    "download": ["video.download"],
    "process": ["video.extract_audio", "video.normalize_subtitle", "video.asr_transcribe"],
    "sync": ["media.sync_profile", "media.sync_videos"],
    "ai": ["video.generate_note", "brief.generate_daily"],
}


def _effective_max_attempts(job_type: str, current: int) -> int:
    # Reduce retries for provider-facing jobs to avoid repeated blocks.
    if job_type in {"media.sync_profile", "media.sync_videos", "video.download"}:
        return min(int(current or 0) or 5, 2)
    return int(current or 0) or 5


def _resolve_worker_types() -> list[str] | None:
    types_env = os.getenv("WORKER_TYPES", "").strip()
    if types_env:
        types = _parse_csv(types_env)
        return types or None

    role = os.getenv("WORKER_ROLE", "").strip().lower()
    if not role or role in {"all", "default"}:
        return None

    if role in _ROLE_TYPES:
        return list(_ROLE_TYPES[role])

    print(f"[worker] unknown WORKER_ROLE={role!r}; running in all-types mode")
    return None


def run_loop() -> None:
    init_db()
    s3_ensure_bucket()
    worker_id = _worker_id()
    type_in = _resolve_worker_types()
    role = os.getenv("WORKER_ROLE", "").strip() or "all"
    if type_in:
        print(f"[worker] started id={worker_id} role={role} types={','.join(type_in)}")
    else:
        print(f"[worker] started id={worker_id} role={role} types=ALL")
    last_reap = 0.0

    while True:
        now = time.time()
        if now - last_reap > 15:
            with session_scope() as session:
                requeue_expired_running_jobs(session)
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

                    job.result = result
                    job.status = "succeeded"
                    job.error_message = None
                    job.error_stack = None
                    job.finished_at = utcnow()
                    job.lease_expires_at = None
                    job_log(session, job, "succeeded")
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
                        job.status = "pending"
                        job.scheduled_for = utcnow() + timedelta(seconds=backoff)
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
