from __future__ import annotations

from raelyn.config import settings
from raelyn.db import init_db, session_scope
from raelyn.jobs.claim import requeue_expired_running_jobs, requeue_orphan_running_jobs


def recover() -> dict[str, int]:
    """
    Best-effort recovery for interrupted workers:
    - requeue expired leases
    - requeue orphaned running jobs (stale/missing worker heartbeat), promoted to the front
    """
    init_db()
    with session_scope() as session:
        expired = requeue_expired_running_jobs(session)
        orphaned = requeue_orphan_running_jobs(
            session,
            stale_after_seconds=settings.worker_stale_after_seconds,
            execution_stale_after_seconds=settings.worker_execution_stale_after_seconds,
            priority_bump=settings.orphan_requeue_priority_bump,
        )
        session.flush()
        return {"expired": int(expired or 0), "orphaned": int(orphaned or 0)}


def main() -> None:
    r = recover()
    print(f"[recovery] requeued expired={r['expired']} orphaned={r['orphaned']}", flush=True)


if __name__ == "__main__":
    main()
