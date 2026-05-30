from __future__ import annotations

from contextlib import contextmanager
import threading
import time
import uuid
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy import update

from raelyn.db import engine
from raelyn.models import Job, WorkerHeartbeat
from raelyn.timeutil import utcnow


_state = threading.local()
_DEFAULT_MIN_INTERVAL_SECONDS = 2.0


def configure_worker_activity(*, worker_id: str, role: str | None = None) -> None:
    _state.worker_id = str(worker_id or "").strip()
    _state.role = str(role or "").strip() or "all"
    _state.current_job_id = None
    _state.last_touch_monotonic = 0.0


@contextmanager
def worker_job_activity(*, worker_id: str, role: str | None, job_id: uuid.UUID | str | None) -> Iterator[None]:
    old_worker_id = getattr(_state, "worker_id", None)
    old_role = getattr(_state, "role", None)
    old_job_id = getattr(_state, "current_job_id", None)
    old_last_touch = getattr(_state, "last_touch_monotonic", 0.0)

    configure_worker_activity(worker_id=worker_id, role=role)
    _state.current_job_id = _coerce_uuid(job_id)
    touch_current_worker_activity(force=True)
    try:
        yield
    finally:
        touch_current_worker_activity(force=True, current_job_id=None)
        _state.worker_id = old_worker_id
        _state.role = old_role
        _state.current_job_id = old_job_id
        _state.last_touch_monotonic = old_last_touch


def touch_current_worker_activity(
    *,
    force: bool = False,
    current_job_id: uuid.UUID | str | None | object = ...,
    min_interval_seconds: float = _DEFAULT_MIN_INTERVAL_SECONDS,
) -> bool:
    worker_id = str(getattr(_state, "worker_id", "") or "").strip()
    if not worker_id:
        return False

    now_monotonic = time.monotonic()
    last_touch = float(getattr(_state, "last_touch_monotonic", 0.0) or 0.0)
    if not force and now_monotonic - last_touch < max(0.0, float(min_interval_seconds or 0.0)):
        return True

    role = str(getattr(_state, "role", "") or "").strip() or "all"
    if current_job_id is ...:
        job_id = getattr(_state, "current_job_id", None)
    else:
        job_id = _coerce_uuid(current_job_id)
        _state.current_job_id = job_id

    with engine.begin() as conn:
        conn.execute(
            update(WorkerHeartbeat)
            .where(WorkerHeartbeat.worker_id == worker_id)
            .values(role=role, active_at=utcnow(), current_job_id=job_id)
        )
    _state.last_touch_monotonic = now_monotonic
    return True


def touch_worker_activity_for_job(*, job_id: uuid.UUID | str | None) -> None:
    coerced_job_id = _coerce_uuid(job_id)
    if coerced_job_id is None:
        return

    worker_id = (
        select(Job.worker_id)
        .where(
            Job.id == coerced_job_id,
            Job.worker_id.is_not(None),
        )
        .scalar_subquery()
    )
    with engine.begin() as conn:
        conn.execute(
            update(WorkerHeartbeat)
            .where(WorkerHeartbeat.worker_id == worker_id)
            .values(active_at=utcnow(), current_job_id=coerced_job_id)
        )


def _coerce_uuid(value: uuid.UUID | str | None | object) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None
