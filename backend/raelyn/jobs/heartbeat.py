from __future__ import annotations

from sqlalchemy.orm import Session

from raelyn.models import WorkerHeartbeat
from raelyn.timeutil import utcnow


def touch_worker_heartbeat(session: Session, *, worker_id: str, role: str | None = None) -> None:
    now = utcnow()
    hb = session.get(WorkerHeartbeat, worker_id)
    if hb:
        hb.updated_at = now
        hb.role = role
    else:
        session.add(WorkerHeartbeat(worker_id=worker_id, role=role, updated_at=now))
