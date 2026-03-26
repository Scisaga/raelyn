from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from datetime import timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import case, func, select

from raelyn.api.auth import api_ws_authorized, close_api_ws_unauthorized
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import Job, Media, Video
from raelyn.timeutil import utcnow


router = APIRouter(tags=["ws"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _query_jobs(
    *,
    status_in: str,
    type_in: str | None = None,
    type: str | None = None,
    limit: int,
    finished_since: datetime | None = None,
    finished_until: datetime | None = None,
) -> list[dict[str, Any]]:
    statuses = [s.strip() for s in (status_in or "").split(",") if s.strip()]
    if not statuses:
        statuses = ["pending", "running"]

    types: list[str] = []
    if type_in:
        types.extend([s.strip() for s in str(type_in).split(",") if s.strip()])
    if type:
        types.append(str(type).strip())
    types = list(dict.fromkeys([t for t in types if t]))

    with session_scope() as session:
        stmt = select(Job).where(Job.status.in_(statuses))
        if types:
            stmt = stmt.where(Job.type.in_(types))
        if finished_since:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at >= finished_since)
        if finished_until:
            stmt = stmt.where(Job.finished_at.is_not(None), Job.finished_at < finished_until)

        status_set = set(statuses)
        active = {"pending", "running"}
        if status_set.issubset(active):
            stmt = stmt.order_by(
                case((Job.status == "running", 0), else_=1),
                Job.started_at.asc().nullslast(),
                Job.scheduled_for.asc(),
                Job.created_at.asc(),
            )
        else:
            stmt = stmt.order_by(Job.created_at.desc())

        items = session.execute(stmt.limit(limit)).scalars().all()

        media_ids: list[uuid.UUID] = []
        video_ids: list[uuid.UUID] = []
        for j in items:
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
        media_name_by_id: dict[str, str] = {}
        if media_ids:
            rows = session.execute(select(Media.id, Media.name, Media.provider_media_id).where(Media.id.in_(media_ids))).all()
            for mid, name, provider_media_id in rows:
                label = (name or provider_media_id or str(mid)) if mid else ""
                media_name_by_id[str(mid)] = label

        out: list[dict[str, Any]] = []
        for j in items:
            media_name = None
            video_title = None
            video_published_at = None
            video_provider_video_id = None
            video_url = None
            try:
                mid = (j.params or {}).get("media_id")
                video_id = (j.params or {}).get("video_id")
                video_ctx = video_context_by_id.get(str(video_id)) if video_id else None
                effective_media_id = str(mid) if mid else (video_ctx.get("media_id") if isinstance(video_ctx, dict) else None)
                if effective_media_id:
                    media_name = media_name_by_id.get(str(effective_media_id))
                if isinstance(video_ctx, dict):
                    video_title = video_ctx.get("title")
                    video_published_at = _iso(video_ctx.get("published_at"))
                    video_provider_video_id = video_ctx.get("provider_video_id")
                    video_url = video_ctx.get("url")
            except Exception:
                media_name = None
            out.append(
                {
                    "id": str(j.id),
                    "type": j.type,
                    "status": j.status,
                    "priority": j.priority,
                    "params": j.params,
                    "result": j.result,
                    "progress_current": j.progress_current,
                    "progress_total": j.progress_total,
                    "error_message": j.error_message,
                    "attempt": j.attempt,
                    "max_attempts": j.max_attempts,
                    "scheduled_for": _iso(j.scheduled_for),
                    "created_at": _iso(j.created_at),
                    "started_at": _iso(j.started_at),
                    "finished_at": _iso(j.finished_at),
                    "worker_id": j.worker_id,
                    "parent_job_id": str(j.parent_job_id) if j.parent_job_id else None,
                    "media_name": media_name,
                    "video_title": video_title,
                    "video_published_at": video_published_at,
                    "video_provider_video_id": video_provider_video_id,
                    "video_url": video_url,
                }
            )
        return out


@router.websocket("/ws/jobs")
async def ws_jobs(
    ws: WebSocket,
    status_in: str = "pending,running",
    type_in: str | None = None,
    type: str | None = None,
    limit: int = 100,
    interval_seconds: float = 1.0,
    finished_since: datetime | None = None,
    finished_until: datetime | None = None,
) -> None:
    if not api_ws_authorized(ws, token=settings.api_bearer_token):
        await close_api_ws_unauthorized(ws)
        return
    await ws.accept()
    try:
        while True:
            jobs = await asyncio.to_thread(
                _query_jobs,
                status_in=status_in,
                type_in=type_in,
                type=type,
                limit=max(1, min(500, int(limit))),
                finished_since=finished_since,
                finished_until=finished_until,
            )
            await ws.send_json(jsonable_encoder({"type": "jobs", "jobs": jobs}))
            await asyncio.sleep(max(0.2, float(interval_seconds)))
    except WebSocketDisconnect:
        return


def _query_job_stats(*, window_hours: int = 24) -> dict[str, Any]:
    hours = int(window_hours or 24)
    hours = max(1, min(168, hours))
    now = utcnow()
    since = now - timedelta(hours=hours)

    with session_scope() as session:
        rows = session.execute(
            select(Job.status, func.count(Job.id)).where(Job.status.in_(["pending", "running"])).group_by(Job.status)
        ).all()
        active_map = {str(st): int(n or 0) for st, n in rows}

        done_rows = session.execute(
            select(Job.status, func.count(Job.id))
            .where(Job.finished_at.is_not(None), Job.finished_at >= since, Job.finished_at < now)
            .where(Job.status.in_(["succeeded", "failed"]))
            .group_by(Job.status)
        ).all()
        done_map = {str(st): int(n or 0) for st, n in done_rows}

        return {
            "pending": int(active_map.get("pending", 0)),
            "running": int(active_map.get("running", 0)),
            "succeeded_24h": int(done_map.get("succeeded", 0)),
            "failed": int(done_map.get("failed", 0)),
            "window_hours": hours,
        }


@router.websocket("/ws/job_stats")
async def ws_job_stats(
    ws: WebSocket,
    interval_seconds: float = 1.0,
    window_hours: int = 24,
) -> None:
    if not api_ws_authorized(ws, token=settings.api_bearer_token):
        await close_api_ws_unauthorized(ws)
        return
    await ws.accept()
    try:
        while True:
            payload = await asyncio.to_thread(_query_job_stats, window_hours=window_hours)
            await ws.send_json(jsonable_encoder({"type": "job_stats", **payload}))
            await asyncio.sleep(max(0.3, float(interval_seconds)))
    except WebSocketDisconnect:
        return
