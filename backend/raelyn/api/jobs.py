from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import case
from sqlalchemy import delete
from sqlalchemy import func, select

from raelyn.api.orm import OrmModel
from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Job, JobEvent, Media
from raelyn.timeutil import utcnow


router = APIRouter(tags=["jobs"])


class JobListOut(OrmModel):
    id: uuid.UUID
    type: str
    status: str
    priority: int
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    error_message: str | None = None
    attempt: int
    max_attempts: int
    scheduled_for: Any
    created_at: Any
    started_at: Any | None = None
    finished_at: Any | None = None
    worker_id: str | None = None
    parent_job_id: uuid.UUID | None = None
    media_name: str | None = None


class JobOut(JobListOut):
    error_stack: str | None = None


class JobEventOut(OrmModel):
    id: int
    ts: Any
    level: str
    message: str
    data: dict[str, Any] | None = None


class JobSeriesPoint(BaseModel):
    ts: Any
    counts: dict[str, int]
    total: int


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in str(value).split(",") if s.strip()]


def _media_name_map(session, jobs: list[Job]) -> dict[str, str]:
    media_ids: list[uuid.UUID] = []
    for j in jobs:
        try:
            mid = (j.params or {}).get("media_id")
            if mid:
                media_ids.append(uuid.UUID(str(mid)))
        except Exception:
            continue
    media_ids = list(dict.fromkeys(media_ids))
    if not media_ids:
        return {}

    rows = session.execute(select(Media.id, Media.name, Media.provider_media_id).where(Media.id.in_(media_ids))).all()
    out: dict[str, str] = {}
    for mid, name, provider_media_id in rows:
        label = (name or provider_media_id or str(mid)) if mid else ""
        out[str(mid)] = label
    return out


@router.get("/jobs", response_model=list[JobListOut])
def list_jobs(
    status: str | None = None,
    status_in: str | None = None,
    type: str | None = None,
    created_since: datetime | None = None,
    created_until: datetime | None = None,
    finished_since: datetime | None = None,
    finished_until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobListOut]:
    with session_scope() as session:
        stmt = select(Job)
        statuses: list[str] = []
        if status_in:
            statuses.extend([s.strip() for s in status_in.split(",") if s.strip()])
        if status:
            statuses.append(status)
        if statuses:
            stmt = stmt.where(Job.status.in_(list(dict.fromkeys(statuses))))
        if type:
            stmt = stmt.where(Job.type == type)
        if created_since:
            stmt = stmt.where(Job.created_at >= created_since)
        if created_until:
            stmt = stmt.where(Job.created_at < created_until)
        if finished_since:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at >= finished_since)
        if finished_until:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at < finished_until)

        status_set = set(statuses) if statuses else set()
        active = {"pending", "running"}
        done = {"succeeded", "failed", "canceled"}
        if statuses and status_set.issubset(active):
            stmt = stmt.order_by(
                case((Job.status == "running", 0), else_=1),
                Job.started_at.desc().nullslast(),
                Job.scheduled_for.asc(),
                Job.created_at.asc(),
            )
        elif statuses and status_set.issubset(done):
            stmt = stmt.order_by(Job.finished_at.desc().nullslast(), Job.created_at.desc())
        else:
            stmt = stmt.order_by(Job.created_at.desc())

        stmt = stmt.limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        media_name_by_id = _media_name_map(session, items)
        out: list[JobListOut] = []
        for j in items:
            payload = JobListOut.model_validate(j).model_dump()
            try:
                mid = (j.params or {}).get("media_id")
                if mid:
                    payload["media_name"] = media_name_by_id.get(str(mid))
            except Exception:
                pass
            out.append(JobListOut(**payload))
        return out


@router.get("/jobs/series", response_model=list[JobSeriesPoint])
def jobs_series(
    *,
    since: datetime,
    until: datetime,
    status_in: str | None = None,
    type: str | None = None,
    type_in: str | None = None,
    ts_field: str = "created_at",
    bucket: str = "hour",
) -> list[JobSeriesPoint]:
    allowed_buckets = {"minute", "hour", "day"}
    if bucket not in allowed_buckets:
        raise HTTPException(status_code=400, detail=f"invalid bucket: {bucket}")

    allowed_ts = {"created_at", "started_at", "finished_at", "scheduled_for"}
    if ts_field not in allowed_ts:
        raise HTTPException(status_code=400, detail=f"invalid ts_field: {ts_field}")

    statuses = _parse_csv(status_in)
    types = _parse_csv(type_in)
    if type and str(type).strip():
        types.append(str(type).strip())
    types = list(dict.fromkeys([t for t in types if t]))

    ts_col = getattr(Job, ts_field)
    bucket_ts = func.date_trunc(bucket, ts_col).label("bucket_ts")

    with session_scope() as session:
        stmt = select(bucket_ts, Job.status, func.count(Job.id).label("n")).where(ts_col >= since, ts_col < until)
        if ts_field == "finished_at":
            stmt = stmt.where(Job.finished_at.is_not(None))
        if statuses:
            stmt = stmt.where(Job.status.in_(statuses))
        if types:
            stmt = stmt.where(Job.type.in_(types))
        stmt = stmt.group_by(bucket_ts, Job.status).order_by(bucket_ts.asc())

        rows = session.execute(stmt).all()
        counts_by_bucket: dict[Any, dict[str, int]] = {}
        for bts, st, n in rows:
            bucket_counts = counts_by_bucket.setdefault(bts, {})
            bucket_counts[str(st)] = int(n or 0)

        out: list[JobSeriesPoint] = []
        for bts in sorted(counts_by_bucket.keys()):
            counts = counts_by_bucket[bts]
            total = sum(int(v) for v in counts.values())
            out.append(JobSeriesPoint(ts=bts, counts=counts, total=total))
        return out


@router.get("/jobs/{job_id:uuid}", response_model=JobOut)
def get_job(job_id: uuid.UUID) -> JobOut:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return JobOut.model_validate(job)


@router.get("/jobs/{job_id:uuid}/events", response_model=list[JobEventOut])
def list_job_events(job_id: uuid.UUID, limit: int = 200) -> list[JobEventOut]:
    with session_scope() as session:
        stmt = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.ts.desc()).limit(limit)
        items = session.execute(stmt).scalars().all()
        return [JobEventOut.model_validate(e) for e in items]


@router.post("/jobs/{job_id:uuid}/cancel")
def cancel_job(job_id: uuid.UUID) -> dict:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status in {"succeeded", "failed"}:
            return {"ok": True}
        job.status = "canceled"
        job.finished_at = utcnow()
        session.add(JobEvent(job_id=job.id, level="info", message="canceled"))
    return {"ok": True}


@router.post("/jobs/{job_id:uuid}/retry")
def retry_job(job_id: uuid.UUID) -> dict:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        enqueue_job(
            session,
            type_=job.type,
            params=job.params,
            priority=job.priority,
            parent_job_id=str(job.parent_job_id) if job.parent_job_id else None,
        )
        session.add(JobEvent(job_id=job.id, level="info", message="retry enqueued"))
    return {"ok": True}


@router.post("/jobs/cancel_active")
def cancel_active_jobs() -> dict:
    with session_scope() as session:
        now = utcnow()
        jobs = session.execute(select(Job).where(Job.status.in_(["pending", "running"]))).scalars().all()
        n = 0
        for j in jobs:
            if j.status not in {"pending", "running"}:
                continue
            j.status = "canceled"
            j.finished_at = now
            j.lease_expires_at = None
            j.worker_id = None
            session.add(JobEvent(job_id=j.id, level="info", message="canceled (bulk)"))
            n += 1
        session.flush()
        return {"ok": True, "canceled": n}


@router.post("/jobs/delete_failed")
def delete_failed_jobs(
    *,
    type: str | None = None,
    type_in: str | None = None,
    error_contains: str | None = None,
    created_since: datetime | None = None,
    created_until: datetime | None = None,
    finished_since: datetime | None = None,
    finished_until: datetime | None = None,
) -> dict:
    with session_scope() as session:
        stmt = delete(Job).where(Job.status == "failed")
        types = _parse_csv(type_in)
        if type and str(type).strip():
            types.append(str(type).strip())
        types = list(dict.fromkeys([t for t in types if t]))
        if types:
            stmt = stmt.where(Job.type.in_(types))
        if error_contains and str(error_contains).strip():
            stmt = stmt.where(Job.error_message.is_not(None), Job.error_message.ilike(f"%{str(error_contains).strip()}%"))
        if created_since:
            stmt = stmt.where(Job.created_at >= created_since)
        if created_until:
            stmt = stmt.where(Job.created_at < created_until)
        if finished_since:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at >= finished_since)
        if finished_until:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at < finished_until)

        res = session.execute(stmt)
        n = int(res.rowcount or 0)
        return {"ok": True, "deleted": n, "status": "failed", "type_in": types or None, "error_contains": error_contains or None}
