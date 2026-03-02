from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import case, select

from raelyn.db import session_scope
from raelyn.models import Job, Media


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
                Job.started_at.desc().nullslast(),
                Job.scheduled_for.asc(),
                Job.created_at.asc(),
            )
        else:
            stmt = stmt.order_by(Job.created_at.desc())

        items = session.execute(stmt.limit(limit)).scalars().all()

        media_ids: list[uuid.UUID] = []
        for j in items:
            try:
                mid = (j.params or {}).get("media_id")
                if mid:
                    media_ids.append(uuid.UUID(str(mid)))
            except Exception:
                continue
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
            try:
                mid = (j.params or {}).get("media_id")
                if mid:
                    media_name = media_name_by_id.get(str(mid))
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
