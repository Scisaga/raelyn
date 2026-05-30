from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import WorkerHeartbeat
from raelyn.services.worker_role_pause import (
    clear_worker_role_pause,
    get_worker_role_pauses,
    set_worker_role_paused,
)
from raelyn.services.worker_roles import (
    is_controllable_worker_role,
    known_worker_roles,
    normalize_worker_role,
)
from raelyn.timeutil import utcnow


router = APIRouter(tags=["workers"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_worker_id(worker_id: str) -> dict[str, Any]:
    s = str(worker_id or "").strip()
    host = None
    pid = None
    nonce = None
    parts = s.split(":")
    if len(parts) >= 1:
        host = parts[0] or None
    if len(parts) >= 2:
        try:
            pid = int(parts[1])
        except Exception:
            pid = None
    if len(parts) >= 3:
        nonce = parts[2] or None
    return {"host": host, "pid": pid, "nonce": nonce}


class WorkerRolePauseRequest(BaseModel):
    reason: str | None = None
    message: str | None = None


@router.get("/workers")
def list_workers() -> dict[str, Any]:
    now = utcnow()
    stale_after_seconds = int(settings.worker_stale_after_seconds or 0) or 20
    stale_before = now - timedelta(seconds=stale_after_seconds)
    execution_stale_after_seconds = int(settings.worker_execution_stale_after_seconds or 0) or stale_after_seconds
    execution_stale_before = now - timedelta(seconds=execution_stale_after_seconds)
    # Keep a small window so recent restarts don't inflate totals for too long.
    # total = number of worker processes seen within this window (online + recently-offline).
    window_seconds = max(60, min(300, stale_after_seconds * 6))
    window_before = now - timedelta(seconds=window_seconds)

    with session_scope() as session:
        pauses = get_worker_role_pauses(session)
        rows = (
            session.execute(
                select(WorkerHeartbeat)
                .where(WorkerHeartbeat.updated_at >= window_before)
                .order_by(WorkerHeartbeat.updated_at.desc())
            )
            .scalars()
            .all()
        )

        workers: list[dict[str, Any]] = []
        roles: dict[str, dict[str, Any]] = {}
        for role in known_worker_roles():
            pause = pauses.get(role) or {}
            roles[role] = {
                "role": role,
                "online": 0,
                "total": 0,
                "last_seen_at": None,
                "paused": bool(pause.get("paused")),
                "pause_reason": pause.get("reason"),
                "pause_message": pause.get("message"),
                "pause_set_at": pause.get("set_at"),
                "controllable": is_controllable_worker_role(role),
            }
        for hb in rows:
            wid = str(getattr(hb, "worker_id", "") or "")
            role = normalize_worker_role(getattr(hb, "role", ""))
            updated_at = getattr(hb, "updated_at", None)
            active_at = getattr(hb, "active_at", None)
            online = bool(updated_at and updated_at >= stale_before)
            execution_online = bool(active_at and active_at >= execution_stale_before)
            parsed = _parse_worker_id(wid)
            payload = {
                "worker_id": wid,
                "role": role,
                "updated_at": _iso(updated_at),
                "active_at": _iso(active_at),
                "current_job_id": str(getattr(hb, "current_job_id", "") or "") or None,
                "online": online,
                "execution_online": execution_online,
                **parsed,
            }
            workers.append(payload)

            r = roles.get(role)
            if not r:
                pause = pauses.get(role) or {}
                r = {
                    "role": role,
                    "online": 0,
                    "total": 0,
                    "last_seen_at": None,
                    "paused": bool(pause.get("paused")),
                    "pause_reason": pause.get("reason"),
                    "pause_message": pause.get("message"),
                    "pause_set_at": pause.get("set_at"),
                    "controllable": is_controllable_worker_role(role),
                }
                roles[role] = r
            r["total"] = int(r["total"]) + 1
            if online:
                r["online"] = int(r["online"]) + 1
            if updated_at:
                cur = r.get("last_seen_at")
                if not cur or (isinstance(cur, str) and updated_at.isoformat() > cur):
                    r["last_seen_at"] = _iso(updated_at)

        return {
            "now": _iso(now),
            "stale_after_seconds": stale_after_seconds,
            "execution_stale_after_seconds": execution_stale_after_seconds,
            "window_seconds": window_seconds,
            "workers": workers,
            "roles": sorted(list(roles.values()), key=lambda x: str(x.get("role") or "")),
        }


@router.post("/workers/roles/{role}/pause")
def pause_worker_role(role: str, payload: WorkerRolePauseRequest) -> dict[str, Any]:
    normalized = normalize_worker_role(role)
    if not is_controllable_worker_role(normalized):
        raise HTTPException(status_code=400, detail=f"invalid controllable worker role: {role}")

    reason = str(payload.reason or "").strip() or "manual_worker_role_pause"
    message = (
        str(payload.message or "").strip()
        or f"已暂停 {normalized} worker 领取新任务；运行中任务不受影响。"
    )
    with session_scope() as session:
        pause = set_worker_role_paused(session, role=normalized, reason=reason, message=message)
        return {"ok": True, "role": normalized, "pause": pause}


@router.post("/workers/roles/{role}/resume")
def resume_worker_role(role: str) -> dict[str, Any]:
    normalized = normalize_worker_role(role)
    if not is_controllable_worker_role(normalized):
        raise HTTPException(status_code=400, detail=f"invalid controllable worker role: {role}")

    with session_scope() as session:
        pause = clear_worker_role_pause(session, role=normalized)
        return {"ok": True, "role": normalized, "pause": pause}
