from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from raelyn.models import Job, JobEvent
from raelyn.timeutil import utcnow


def _default_max_attempts(type_: str) -> int | None:
    # Keep retries low for provider-facing jobs to avoid hammering platforms when blocked (e.g. 352/412).
    # "1 retry" => max_attempts=2 (first try + one retry).
    if type_ in {"media.sync_profile", "media.sync_videos", "video.download"}:
        return 2
    return None


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        s = s[:10]
        try:
            return date.fromisoformat(s)
        except Exception:
            return None
    return None


def _brief_period_start(d: date, granularity: str) -> date:
    g = (granularity or "day").strip().lower()
    if g == "week":
        return d - timedelta(days=d.weekday())  # Monday
    if g == "month":
        return date(d.year, d.month, 1)
    return d


def _brief_normalize_date_and_params(type_: str, params: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    if type_ not in {"brief.generate_period", "brief.generate_daily"}:
        return None, params
    if not isinstance(params, dict):
        return None, params

    params2 = dict(params)
    playlist_raw = params2.get("playlist_id")
    try:
        playlist_id = uuid.UUID(str(playlist_raw))
    except Exception:
        return None, params2

    if type_ == "brief.generate_daily":
        g = "day"
    else:
        g = str(params2.get("granularity") or "day").strip().lower()
        if g not in {"day", "week", "month"}:
            g = "day"
        params2["granularity"] = g

    raw_date = params2.get("period_start") or params2.get("date")
    d = _parse_iso_date(raw_date)
    if not d:
        return None, params2

    if type_ == "brief.generate_period":
        d = _brief_period_start(d, g)
        params2["period_start"] = d.isoformat()

    # Always write "date" so UI and other callers can reliably display/route by day.
    params2["date"] = d.isoformat()

    dedupe_key = f"brief:{playlist_id}:{d.isoformat()}"
    return dedupe_key, params2


def enqueue_job(
    session: Session,
    *,
    type_: str,
    params: dict[str, Any],
    priority: int = 0,
    scheduled_for: Any | None = None,
    parent_job_id: str | None = None,
) -> uuid.UUID:
    max_attempts = _default_max_attempts(type_)
    dedupe_key, params2 = _brief_normalize_date_and_params(type_, params)
    job = Job(
        type=type_,
        status="pending",
        priority=priority,
        dedupe_key=dedupe_key,
        params=params2,
        scheduled_for=scheduled_for or utcnow(),
        parent_job_id=uuid.UUID(parent_job_id) if parent_job_id else None,
        **({"max_attempts": max_attempts} if isinstance(max_attempts, int) else {}),
    )
    if dedupe_key:
        try:
            with session.begin_nested():
                session.add(job)
                session.flush([job])
        except IntegrityError:
            try:
                session.expunge(job)
            except Exception:
                pass
            existing_id = session.execute(
                select(Job.id).where(Job.dedupe_key == dedupe_key, Job.status.in_(["pending", "running"])).limit(1)
            ).scalar_one_or_none()
            if existing_id:
                return existing_id
            raise
    else:
        session.add(job)
        session.flush([job])
    session.add(JobEvent(job_id=job.id, level="info", message="enqueued", data={"type": type_}))
    return job.id


def enqueue_in(session: Session, *, seconds: int, type_: str, params: dict[str, Any], priority: int = 0) -> uuid.UUID:
    return enqueue_job(
        session,
        type_=type_,
        params=params,
        priority=priority,
        scheduled_for=utcnow() + timedelta(seconds=seconds),
    )
