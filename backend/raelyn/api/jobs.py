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
from raelyn.models import Job, JobEvent, Media, Video
from raelyn.services.job_cancellation import request_job_cancel
from raelyn.services.ytdlp import YTDLP_RETRY_WITHOUT_COOKIES_PARAM
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
    video_title: str | None = None
    video_published_at: Any | None = None
    video_provider_video_id: str | None = None
    video_url: str | None = None


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


class JobCountsOut(BaseModel):
    counts: dict[str, int]
    total: int


class JobTypeCountItem(BaseModel):
    type: str
    count: int
    counts: dict[str, int]
    percentage: float


class JobTypeCountsOut(BaseModel):
    counts: dict[str, int]
    items: list[JobTypeCountItem]
    total: int


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in str(value).split(",") if s.strip()]


def _job_filter_values(
    *,
    status: str | None = None,
    status_in: str | None = None,
    type: str | None = None,
    type_in: str | None = None,
) -> tuple[list[str], list[str]]:
    statuses = _parse_csv(status_in)
    if status and str(status).strip():
        statuses.append(str(status).strip())
    statuses = list(dict.fromkeys([s for s in statuses if s]))

    types = _parse_csv(type_in)
    if type and str(type).strip():
        types.append(str(type).strip())
    types = list(dict.fromkeys([t for t in types if t]))
    return statuses, types


def _job_context_maps(
    session,
    jobs: list[Job],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    media_ids: list[uuid.UUID] = []
    video_ids: list[uuid.UUID] = []
    for j in jobs:
        try:
            mid = (j.params or {}).get("media_id")
            if mid:
                media_ids.append(uuid.UUID(str(mid)))
        except Exception:
            pass
        try:
            video_id = (j.params or {}).get("video_id")
            if video_id:
                video_ids.append(uuid.UUID(str(video_id)))
        except Exception:
            pass

    video_ids = list(dict.fromkeys(video_ids))
    video_context_by_id: dict[str, dict[str, Any]] = {}
    if video_ids:
        rows = session.execute(
            select(Video.id, Video.media_id, Video.title, Video.published_at, Video.provider_video_id, Video.url).where(
                Video.id.in_(video_ids)
            )
        ).all()
        for video_id, media_id, title, published_at, provider_video_id, url in rows:
            video_context_by_id[str(video_id)] = {
                "media_id": str(media_id) if media_id else None,
                "title": str(title).strip() if isinstance(title, str) and title.strip() else None,
                "published_at": published_at,
                "provider_video_id": (
                    str(provider_video_id).strip()
                    if isinstance(provider_video_id, str) and str(provider_video_id).strip()
                    else None
                ),
                "url": str(url).strip() if isinstance(url, str) and str(url).strip() else None,
            }
            if media_id:
                media_ids.append(media_id)

    media_ids = list(dict.fromkeys(media_ids))
    if not media_ids:
        return {}, video_context_by_id

    rows = session.execute(select(Media.id, Media.name, Media.provider_media_id).where(Media.id.in_(media_ids))).all()
    out: dict[str, str] = {}
    for mid, name, provider_media_id in rows:
        label = (name or provider_media_id or str(mid)) if mid else ""
        out[str(mid)] = label
    return out, video_context_by_id


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
                Job.started_at.asc().nullslast(),
                Job.scheduled_for.asc(),
                Job.created_at.asc(),
            )
        elif statuses and status_set.issubset(done):
            stmt = stmt.order_by(Job.finished_at.desc().nullslast(), Job.created_at.desc())
        else:
            stmt = stmt.order_by(Job.created_at.desc())

        stmt = stmt.limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        media_name_by_id, video_context_by_id = _job_context_maps(session, items)
        out: list[JobListOut] = []
        for j in items:
            payload = JobListOut.model_validate(j).model_dump()
            try:
                mid = (j.params or {}).get("media_id")
                video_id = (j.params or {}).get("video_id")
                video_ctx = video_context_by_id.get(str(video_id)) if video_id else None
                effective_media_id = str(mid) if mid else (video_ctx.get("media_id") if isinstance(video_ctx, dict) else None)
                if effective_media_id:
                    payload["media_name"] = media_name_by_id.get(str(effective_media_id))
                if isinstance(video_ctx, dict):
                    payload["video_title"] = video_ctx.get("title")
                    payload["video_published_at"] = video_ctx.get("published_at")
                    payload["video_provider_video_id"] = video_ctx.get("provider_video_id")
                    payload["video_url"] = video_ctx.get("url")
            except Exception:
                pass
            out.append(JobListOut(**payload))
        return out


@router.get("/jobs/counts", response_model=JobCountsOut)
def job_counts(
    status: str | None = None,
    status_in: str | None = None,
    type: str | None = None,
    type_in: str | None = None,
    created_since: datetime | None = None,
    created_until: datetime | None = None,
    finished_since: datetime | None = None,
    finished_until: datetime | None = None,
) -> JobCountsOut:
    statuses, types = _job_filter_values(status=status, status_in=status_in, type=type, type_in=type_in)

    with session_scope() as session:
        stmt = select(Job.status, func.count(Job.id).label("n"))
        if statuses:
            stmt = stmt.where(Job.status.in_(statuses))
        if types:
            stmt = stmt.where(Job.type.in_(types))
        if created_since:
            stmt = stmt.where(Job.created_at >= created_since)
        if created_until:
            stmt = stmt.where(Job.created_at < created_until)
        if finished_since:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at >= finished_since)
        if finished_until:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at < finished_until)
        stmt = stmt.group_by(Job.status)

        rows = session.execute(stmt).all()
        counts = {str(st): int(n or 0) for st, n in rows}
        total = sum(counts.values())
        return JobCountsOut(counts=counts, total=total)


@router.get("/jobs/type_counts", response_model=JobTypeCountsOut)
def job_type_counts(
    status: str | None = None,
    status_in: str | None = None,
    type: str | None = None,
    type_in: str | None = None,
    created_since: datetime | None = None,
    created_until: datetime | None = None,
    finished_since: datetime | None = None,
    finished_until: datetime | None = None,
) -> JobTypeCountsOut:
    statuses, types = _job_filter_values(status=status, status_in=status_in, type=type, type_in=type_in)

    with session_scope() as session:
        stmt = select(Job.type, Job.status, func.count(Job.id).label("n"))
        if statuses:
            stmt = stmt.where(Job.status.in_(statuses))
        if types:
            stmt = stmt.where(Job.type.in_(types))
        if created_since:
            stmt = stmt.where(Job.created_at >= created_since)
        if created_until:
            stmt = stmt.where(Job.created_at < created_until)
        if finished_since:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at >= finished_since)
        if finished_until:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at < finished_until)
        stmt = stmt.group_by(Job.type, Job.status)

        rows = session.execute(stmt).all()
        counts: dict[str, int] = {}
        by_type: dict[str, dict[str, int]] = {}
        for type_value, status_value, n in rows:
            job_type = str(type_value or "")
            job_status = str(status_value or "")
            count = int(n or 0)
            if not job_type or not job_status:
                continue
            counts[job_status] = int(counts.get(job_status, 0)) + count
            type_counts = by_type.setdefault(job_type, {})
            type_counts[job_status] = int(type_counts.get(job_status, 0)) + count

        status_keys = statuses or sorted(counts.keys())
        for status_key in status_keys:
            counts.setdefault(status_key, 0)

        total = sum(counts.values())
        items: list[JobTypeCountItem] = []
        for job_type, type_counts in sorted(
            by_type.items(),
            key=lambda item: (-sum(int(v) for v in item[1].values()), item[0]),
        ):
            count = sum(int(v) for v in type_counts.values())
            percentage = round((count * 100.0) / total, 2) if total > 0 else 0.0
            items.append(
                JobTypeCountItem(
                    type=job_type,
                    count=count,
                    counts={status_key: int(type_counts.get(status_key, 0)) for status_key in status_keys},
                    percentage=percentage,
                )
            )
        return JobTypeCountsOut(counts=counts, items=items, total=total)


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
        payload = JobOut.model_validate(job).model_dump()
        media_name_by_id, video_context_by_id = _job_context_maps(session, [job])
        try:
            mid = (job.params or {}).get("media_id")
            video_id = (job.params or {}).get("video_id")
            video_ctx = video_context_by_id.get(str(video_id)) if video_id else None
            effective_media_id = str(mid) if mid else (video_ctx.get("media_id") if isinstance(video_ctx, dict) else None)
            if effective_media_id:
                payload["media_name"] = media_name_by_id.get(str(effective_media_id))
            if isinstance(video_ctx, dict):
                payload["video_title"] = video_ctx.get("title")
                payload["video_published_at"] = video_ctx.get("published_at")
                payload["video_provider_video_id"] = video_ctx.get("provider_video_id")
                payload["video_url"] = video_ctx.get("url")
        except Exception:
            pass
        return JobOut(**payload)


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
        action = request_job_cancel(session, job, reason="manual")
        if action == "noop":
            return {"ok": True}
        return {"ok": True, "job_id": str(job_id), "status": "canceled" if action == "canceled" else "cancel_requested"}


def _pending_duplicate_for_retry(session, job: Job) -> Job | None:
    dedupe_key = str(getattr(job, "dedupe_key", "") or "").strip()
    if not dedupe_key:
        return None
    return (
        session.execute(
            select(Job)
            .where(Job.dedupe_key == dedupe_key, Job.status == "pending", Job.id != job.id)
            .order_by(Job.created_at.asc(), Job.id.asc())
            .limit(1)
        )
        .scalar_one_or_none()
    )


@router.post("/jobs/{job_id:uuid}/retry")
def retry_job(job_id: uuid.UUID) -> dict:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status in {"pending", "running"}:
            return {"ok": True, "job_id": str(job.id), "status": job.status}

        previous_status = str(job.status or "")
        previous_attempt = int(job.attempt or 0)
        retry_at = utcnow()
        pending_duplicate = _pending_duplicate_for_retry(session, job)
        if pending_duplicate:
            pending_duplicate.status = "canceled"
            pending_duplicate.finished_at = retry_at
            pending_duplicate.worker_id = None
            pending_duplicate.lease_expires_at = None
            pending_duplicate.cancel_requested_at = retry_at
            pending_duplicate.error_message = "superseded by manual retry"
            session.add(
                JobEvent(
                    job_id=pending_duplicate.id,
                    level="warn",
                    message="canceled; superseded by manual retry",
                    data={"retry_job_id": str(job.id)},
                )
            )
            session.flush([pending_duplicate])

        job.status = "pending"
        job.attempt = 0
        params = dict(job.params or {})
        params.pop(YTDLP_RETRY_WITHOUT_COOKIES_PARAM, None)
        job.params = params
        job.result = None
        job.progress_current = None
        job.progress_total = None
        job.error_message = None
        job.error_stack = None
        job.cancel_requested_at = None
        job.started_at = None
        job.finished_at = None
        job.lease_expires_at = None
        job.worker_id = None
        job.scheduled_for = retry_at
        event_data = {"previous_status": previous_status, "previous_attempt": previous_attempt}
        if pending_duplicate:
            event_data["superseded_pending_job_id"] = str(pending_duplicate.id)
        session.add(
            JobEvent(
                job_id=job.id,
                level="info",
                message="manual retry requested",
                data=event_data,
            )
        )
    return {"ok": True, "job_id": str(job_id), "status": "pending"}


@router.post("/jobs/cancel_active")
def cancel_active_jobs() -> dict:
    with session_scope() as session:
        jobs = session.execute(select(Job).where(Job.status.in_(["pending", "running"]))).scalars().all()
        n = 0
        for j in jobs:
            if j.status not in {"pending", "running"}:
                continue
            action = request_job_cancel(session, j, reason="bulk")
            if action != "noop":
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
