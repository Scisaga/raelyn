from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import WorkerHeartbeat
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


@router.get("/workers")
def list_workers() -> dict[str, Any]:
    now = utcnow()
    stale_after_seconds = int(settings.worker_stale_after_seconds or 0) or 20
    stale_before = now - timedelta(seconds=stale_after_seconds)
    # Keep a small window so recent restarts don't inflate totals for too long.
    # total = number of worker processes seen within this window (online + recently-offline).
    window_seconds = max(60, min(300, stale_after_seconds * 6))
    window_before = now - timedelta(seconds=window_seconds)

    with session_scope() as session:
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
        for hb in rows:
            wid = str(getattr(hb, "worker_id", "") or "")
            role = str(getattr(hb, "role", "") or "").strip() or "all"
            updated_at = getattr(hb, "updated_at", None)
            online = bool(updated_at and updated_at >= stale_before)
            parsed = _parse_worker_id(wid)
            payload = {
                "worker_id": wid,
                "role": role,
                "updated_at": _iso(updated_at),
                "online": online,
                **parsed,
            }
            workers.append(payload)

            r = roles.get(role)
            if not r:
                r = {"role": role, "online": 0, "total": 0, "last_seen_at": None}
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
            "window_seconds": window_seconds,
            "workers": workers,
            "roles": sorted(list(roles.values()), key=lambda x: str(x.get("role") or "")),
        }
