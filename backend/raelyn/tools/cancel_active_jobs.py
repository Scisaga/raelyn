from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.db import session_scope
from raelyn.models import Job, JobEvent
from raelyn.timeutil import utcnow


def cancel_active_jobs(*, include_running: bool = True, include_pending: bool = True) -> dict[str, int]:
    statuses: list[str] = []
    if include_pending:
        statuses.append("pending")
    if include_running:
        statuses.append("running")
    statuses = list(dict.fromkeys(statuses))
    if not statuses:
        return {"canceled": 0}

    now = utcnow()
    with session_scope() as session:
        jobs = session.execute(select(Job).where(Job.status.in_(statuses))).scalars().all()
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
        return {"canceled": n}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cancel all pending/running jobs (mark status=canceled).")
    ap.add_argument("--yes", action="store_true", help="Actually perform the cancel (required)")
    ap.add_argument("--pending", action="store_true", help="Cancel pending jobs (default: true)")
    ap.add_argument("--running", action="store_true", help="Cancel running jobs (default: true)")
    args = ap.parse_args(argv)

    include_pending = True
    include_running = True
    if args.pending or args.running:
        include_pending = bool(args.pending)
        include_running = bool(args.running)

    if not args.yes:
        print("Refusing to run without --yes.", file=sys.stderr)
        return 2

    r = cancel_active_jobs(include_pending=include_pending, include_running=include_running)
    print(f"[jobs] canceled={r.get('canceled', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
