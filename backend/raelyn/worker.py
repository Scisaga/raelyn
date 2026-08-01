from __future__ import annotations

import os
import socket
import threading
import time
import traceback
import uuid
from datetime import timedelta

from raelyn.worker_runtime_env import configure_worker_runtime_env

# 数值库在线程池首次初始化时读取环境变量，必须早于 SQLAlchemy、handlers 等项目导入。
configure_worker_runtime_env()

from sqlalchemy import select

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.db import init_db
from raelyn.jobs.claim import claim_next_job, requeue_expired_running_jobs, requeue_orphan_running_jobs
from raelyn.jobs.heartbeat import touch_worker_heartbeat
import raelyn.jobs.handlers  # noqa: F401  注册 handlers
from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.jobs.reschedule import JobReschedule, JobTerminalFailure
from raelyn.jobs.worker_activity import configure_worker_activity, touch_current_worker_activity, worker_job_activity
from raelyn.models import Asset, Job, Media, Video, WorkerHeartbeat
from raelyn.services.job_cancellation import JobCancelRequested, finalize_canceled_job
from raelyn.services.log_timestamps import install_if_needed
from raelyn.services.asr import inspect_asr_backend_defer
from raelyn.services.provider_cookies import cookie_config_name, cookie_provider_label, normalize_cookie_provider
from raelyn.services.provider_pause import (
    ProviderPauseRequestError,
    job_provider,
    set_provider_paused,
    should_defer_provider_pause_for_retry,
)
from raelyn.services.s3 import s3_ensure_bucket
from raelyn.services.worker_roles import is_known_worker_role, normalize_worker_role, worker_role_types
from raelyn.services.ytdlp import YTDLP_RETRY_WITHOUT_COOKIES_PARAM, YtdlpCookiesInvalidError
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
    if job_type in {
        "media.sync_profile",
        "media.sync_videos",
        "media.delete",
        "video.enrich_metadata.youtube",
        "video.download",
        "video.download.youtube",
        "video.download.bilibili",
    }:
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


_SYNC_JOB_TYPES = {"media.sync_profile", "media.sync_videos"}
_DOWNLOAD_JOB_TYPES = {"video.download", "video.download.youtube", "video.download.bilibili"}
_EXECUTION_WATCHDOG_JOB_TYPES = _SYNC_JOB_TYPES | _DOWNLOAD_JOB_TYPES
_ASR_JOB_TYPE = "video.asr_transcribe"
_ASR_CLAIM_DEFER_SLEEP_SECONDS = 5.0


def _mark_media_sync_terminal_failure_cooldown(session, *, job: Job, now) -> None:
    if str(getattr(job, "type", "") or "").strip() != "media.sync_videos":
        return

    try:
        raw_media_id = (job.params or {}).get("media_id")
        media_id = uuid.UUID(str(raw_media_id))
    except (AttributeError, TypeError, ValueError):
        return

    media = session.execute(
        select(Media)
        .where(Media.id == media_id)
        .with_for_update()
    ).scalar_one_or_none()
    if media:
        media.last_video_sync_at = now


def _mark_download_video_terminal_failure(session, *, job: Job) -> None:
    if str(getattr(job, "type", "") or "").strip() not in _DOWNLOAD_JOB_TYPES:
        return

    try:
        raw_video_id = (job.params or {}).get("video_id")
        video_id = uuid.UUID(str(raw_video_id))
    except Exception:
        return

    video = session.execute(
        select(Video)
        .where(Video.id == video_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not video:
        return

    has_video_asset = session.execute(
        select(Asset.id)
        .where(
            Asset.video_id == video.id,
            Asset.type == "video",
        )
        .limit(1)
    ).scalar_one_or_none() is not None
    video_status = str(getattr(video, "status", "") or "").strip()
    if video_status in {"discovered", "downloading"} and not has_video_asset:
        video.status = "failed"
    if getattr(job, "error_message", None):
        video.error_message = job.error_message


def _claim_skip_types_for_external_capacity(type_in: list[str] | None) -> tuple[set[str], float]:
    if type_in is not None and _ASR_JOB_TYPE not in type_in:
        return set(), 1.0

    defer = inspect_asr_backend_defer()
    if defer is None:
        return set(), 1.0

    sleep_seconds = _ASR_CLAIM_DEFER_SLEEP_SECONDS if type_in == [_ASR_JOB_TYPE] else 1.0
    return {_ASR_JOB_TYPE}, sleep_seconds


def _update_download_retry_params(job: Job) -> None:
    params = dict(getattr(job, "params", None) or {})
    params.pop(YTDLP_RETRY_WITHOUT_COOKIES_PARAM, None)
    job.params = params


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
    pending.execution_token = None

    job.status = "failed"
    job.finished_at = utcnow()
    job.lease_expires_at = None
    job.worker_id = None
    job.execution_token = None
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


def _finalize_terminal_failure(session, *, job: Job, reason: str, stack: str | None = None) -> None:
    job.attempt += 1
    job.status = "failed"
    job.error_message = str(reason or "").strip() or "terminal job failure"
    job.error_stack = stack
    job.lease_expires_at = None
    job.worker_id = None
    job.execution_token = None
    job.finished_at = utcnow()
    _mark_download_video_terminal_failure(session, job=job)
    job_log(session, job, "failed; no retry", level="error", data={"attempt": job.attempt, "terminal": True})


def _lock_owned_running_job(
    session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID | None,
    lock: bool = True,
) -> tuple[Job | None, bool]:
    if execution_token is None:
        return None, False
    statement = select(Job, Job.cancel_requested_at).where(
        Job.id == job_id,
        Job.status == "running",
        Job.worker_id == worker_id,
        Job.execution_token == execution_token,
    )
    if lock:
        statement = statement.with_for_update()
    with session.no_autoflush:
        row = session.execute(statement).one_or_none()
    if row is None:
        return None, False
    return row[0], row[1] is not None


def _persist_provider_pause_after_rollback(session, *, job: Job, err: Exception) -> None:
    if isinstance(err, YtdlpCookiesInvalidError):
        provider = normalize_cookie_provider(getattr(err, "provider", None)) or normalize_cookie_provider(job_provider(session, job))
        if not provider:
            return
        reason = getattr(err, "reason", "") or "ytdlp_cookies_invalid"
        msg = str(err) or "cookies invalid"
        pause_msg = (
            f"{cookie_provider_label(provider)}任务已暂停：{msg}"
            f"（请在 UI -> 设置 更新 {cookie_config_name(provider)}）"
        )
        pause = set_provider_paused(session, provider=provider, reason=str(reason), message=pause_msg)
        job_log(session, job, pause.get("message") or pause_msg, level="error")
        return

    if isinstance(err, ProviderPauseRequestError):
        provider = getattr(err, "provider", "") or ""
        if not provider:
            return
        reason = getattr(err, "reason", "") or "provider_pause_requested"
        msg = str(err) or f"{provider} paused"
        pause = set_provider_paused(session, provider=provider, reason=str(reason), message=msg)
        job_log(session, job, pause.get("message") or msg, level="error")


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


def _execution_stale_reason(session, *, worker_id: str, stale_after_seconds: int) -> str | None:
    hb = session.get(WorkerHeartbeat, worker_id)
    if not hb or not hb.current_job_id or not hb.active_at:
        return None

    job = session.get(Job, hb.current_job_id)
    if not job or str(getattr(job, "type", "") or "").strip() not in _EXECUTION_WATCHDOG_JOB_TYPES:
        return None

    age_seconds = int((utcnow() - hb.active_at).total_seconds())
    threshold = max(1, int(stale_after_seconds or 0))
    if age_seconds <= threshold:
        return None

    return (
        "worker execution heartbeat stale; "
        f"worker_id={worker_id} job_id={hb.current_job_id} "
        f"job_type={getattr(job, 'type', None)} "
        f"job_status={getattr(job, 'status', None)} "
        f"age_seconds={age_seconds} stale_after_seconds={threshold}"
    )


class _ExecutionWatchdogThread(threading.Thread):
    def __init__(self, *, worker_id: str, interval_seconds: int, stale_after_seconds: int) -> None:
        super().__init__(daemon=True)
        self._worker_id = worker_id
        self._interval = max(1, int(interval_seconds or 0))
        self._stale_after_seconds = max(1, int(stale_after_seconds or 0))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                with session_scope() as session:
                    reason = _execution_stale_reason(
                        session,
                        worker_id=self._worker_id,
                        stale_after_seconds=self._stale_after_seconds,
                    )
            except Exception:
                continue

            if reason:
                print(f"[worker] {reason}; exiting for supervisor restart", flush=True)
                os._exit(70)


def _worker_needs_execution_watchdog(type_in: list[str] | None) -> bool:
    if type_in is None:
        return True
    return bool(set(type_in) & _EXECUTION_WATCHDOG_JOB_TYPES)


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
        touch_worker_heartbeat(session, worker_id=worker_id, role=role, active=True, current_job_id=None)
    configure_worker_activity(worker_id=worker_id, role=role)

    hb = _HeartbeatThread(worker_id=worker_id, interval_seconds=settings.worker_heartbeat_interval_seconds, role=role)
    hb.start()
    if _worker_needs_execution_watchdog(type_in):
        _ExecutionWatchdogThread(
            worker_id=worker_id,
            interval_seconds=settings.worker_heartbeat_interval_seconds,
            stale_after_seconds=settings.worker_execution_stale_after_seconds,
        ).start()

    # On restart, promptly recover orphaned "running" jobs from dead workers.
    with session_scope() as session:
        requeue_expired_running_jobs(session)
        requeue_orphan_running_jobs(
            session,
            stale_after_seconds=settings.worker_stale_after_seconds,
            execution_stale_after_seconds=settings.worker_execution_stale_after_seconds,
            priority_bump=settings.orphan_requeue_priority_bump,
        )

    while True:
        touch_current_worker_activity()
        now = time.time()
        if now - last_reap > 15:
            with session_scope() as session:
                touch_worker_heartbeat(session, worker_id=worker_id, role=role, active=True, current_job_id=None)
                requeue_expired_running_jobs(session)
                requeue_orphan_running_jobs(
                    session,
                    stale_after_seconds=settings.worker_stale_after_seconds,
                    execution_stale_after_seconds=settings.worker_execution_stale_after_seconds,
                    priority_bump=settings.orphan_requeue_priority_bump,
                )
            last_reap = now

        skip_type_in, sleep_seconds = _claim_skip_types_for_external_capacity(type_in)

        with session_scope() as session:
            job = claim_next_job(
                session,
                worker_id=worker_id,
                lease_seconds=3600,
                type_in=type_in,
                skip_type_in=skip_type_in,
            )
            if not job:
                pass
            else:
                claimed_execution_token = job.execution_token
                job_id = job.id
                job_type = job.type
                touch_worker_heartbeat(session, worker_id=worker_id, role=role, active=True, current_job_id=job.id)
                # Commit the claim immediately so the API can observe "running" jobs
                # while long-running handlers (download/transcode) are executing.
                session.flush()
                session.commit()

                job, cancel_requested = _lock_owned_running_job(
                    session,
                    job_id=job_id,
                    worker_id=worker_id,
                    execution_token=claimed_execution_token,
                    lock=False,
                )
                if job is None:
                    session.rollback()
                    print(f"[worker] discarded stale execution id={job_id} type={job_type}", flush=True)
                    continue
                handler = registry.get(job.type)
                if not handler:
                    job, _cancel_requested = _lock_owned_running_job(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        execution_token=claimed_execution_token,
                    )
                    if job is None:
                        session.rollback()
                        print(f"[worker] discarded stale unknown handler id={job_id} type={job_type}", flush=True)
                        continue
                    job.status = "failed"
                    job.error_message = f"unknown job type: {job.type}"
                    job.finished_at = utcnow()
                    job.lease_expires_at = None
                    job.worker_id = None
                    job.execution_token = None
                    job_log(session, job, job.error_message or "failed", level="error")
                    continue

                if cancel_requested:
                    job, cancel_requested = _lock_owned_running_job(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        execution_token=claimed_execution_token,
                    )
                    if job is None:
                        session.rollback()
                        print(f"[worker] discarded stale cancellation id={job_id} type={job_type}", flush=True)
                        continue
                    if cancel_requested:
                        finalize_canceled_job(session, job, message="canceled before run", reason="cancel_requested")
                        continue

                try:
                    with worker_job_activity(worker_id=worker_id, role=role, job_id=job.id):
                        job_log(session, job, "running")
                        result = handler(session, job)
                    job, cancel_requested = _lock_owned_running_job(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        execution_token=claimed_execution_token,
                    )
                    if job is None:
                        session.rollback()
                        print(f"[worker] discarded stale result id={job_id} type={job_type}", flush=True)
                        continue
                    if cancel_requested:
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
                    job.execution_token = None
                    job_log(session, job, "succeeded")
                except JobCancelRequested:
                    job, _cancel_requested = _lock_owned_running_job(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        execution_token=claimed_execution_token,
                    )
                    if job is None:
                        session.rollback()
                        print(f"[worker] discarded stale cancellation id={job_id} type={job_type}", flush=True)
                        continue
                    finalize_canceled_job(session, job, message="canceled while running", reason="cancel_requested")
                except JobTerminalFailure as e:
                    job, cancel_requested = _lock_owned_running_job(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        execution_token=claimed_execution_token,
                    )
                    if job is None:
                        session.rollback()
                        print(f"[worker] discarded stale terminal failure id={job_id} type={job_type}", flush=True)
                        continue
                    if cancel_requested:
                        finalize_canceled_job(session, job, message="canceled after terminal failure", reason="cancel_requested")
                        continue

                    _finalize_terminal_failure(session, job=job, reason=e.reason)
                except JobReschedule as e:
                    job, cancel_requested = _lock_owned_running_job(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        execution_token=claimed_execution_token,
                    )
                    if job is None:
                        session.rollback()
                        print(f"[worker] discarded stale reschedule id={job_id} type={job_type}", flush=True)
                        continue
                    if cancel_requested:
                        finalize_canceled_job(session, job, message="canceled while rescheduling", reason="cancel_requested")
                        continue

                    job.status = "pending"
                    job.scheduled_for = utcnow() + timedelta(seconds=int(e.delay_seconds or 0))
                    job.error_message = None
                    job.error_stack = None
                    job.lease_expires_at = None
                    job.worker_id = None
                    job.execution_token = None
                    job.progress_current = None
                    job.progress_total = None
                    job.started_at = None
                    job.finished_at = None
                    job_log(session, job, f"rescheduled: {e.reason}", level="warn", data={"delay_seconds": e.delay_seconds})
                except Exception as e:
                    error_stack = traceback.format_exc()
                    session.rollback()
                    job, cancel_requested = _lock_owned_running_job(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        execution_token=claimed_execution_token,
                    )
                    if job is None:
                        session.rollback()
                        print(f"[worker] discarded stale exception id={job_id} type={job_type}", flush=True)
                        continue
                    if cancel_requested:
                        finalize_canceled_job(session, job, message="canceled after handler error", reason="cancel_requested")
                        continue

                    next_attempt = int(job.attempt or 0) + 1
                    effective_max_attempts = _effective_max_attempts(job.type, job.max_attempts)
                    provider_pause_deferred = should_defer_provider_pause_for_retry(
                        e,
                        next_attempt=next_attempt,
                        max_attempts=effective_max_attempts,
                    )
                    if provider_pause_deferred:
                        job_log(
                            session,
                            job,
                            "YouTube channel auth check failed; defer provider pause until retry is exhausted",
                            level="warn",
                            data={
                                "provider": e.provider,
                                "reason": e.reason,
                                "attempt": next_attempt,
                                "max_attempts": effective_max_attempts,
                            },
                        )
                    else:
                        _persist_provider_pause_after_rollback(session, job=job, err=e)

                    job.attempt = next_attempt
                    job.max_attempts = effective_max_attempts
                    if provider_pause_deferred:
                        job.error_message = (
                            "YouTube 频道/播放列表鉴权检查失败；已安排任务级重试，尚未暂停 provider"
                        )
                    else:
                        job.error_message = str(e)
                    job.error_stack = error_stack
                    job.lease_expires_at = None
                    job.worker_id = None
                    job.execution_token = None
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
                        _update_download_retry_params(job)
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
                        _mark_media_sync_terminal_failure_cooldown(
                            session,
                            job=job,
                            now=job.finished_at,
                        )
                        _mark_download_video_terminal_failure(session, job=job)
                        job_log(session, job, "failed; no more retries", level="error", data={"attempt": job.attempt})

        time.sleep(sleep_seconds)


def main() -> None:
    install_if_needed()
    run_loop()


if __name__ == "__main__":
    main()
