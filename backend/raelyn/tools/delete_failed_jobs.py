from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import delete, func, select

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.db import session_scope
from raelyn.models import Job


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in str(value).split(",") if s.strip()]


def count_jobs_by_status(status_in: list[str]) -> int:
    statuses = list(dict.fromkeys([s for s in status_in if s]))
    if not statuses:
        return 0
    with session_scope() as session:
        return int(session.execute(select(func.count()).select_from(Job).where(Job.status.in_(statuses))).scalar_one() or 0)


def delete_jobs_by_status(status_in: list[str]) -> int:
    statuses = list(dict.fromkeys([s for s in status_in if s]))
    if not statuses:
        return 0

    with session_scope() as session:
        expected = int(
            session.execute(select(func.count()).select_from(Job).where(Job.status.in_(statuses))).scalar_one() or 0
        )
        if expected <= 0:
            return 0

        res = session.execute(delete(Job).where(Job.status.in_(statuses)))
        # SQLAlchemy may return None/-1 for rowcount depending on dialect/driver.
        rowcount = res.rowcount
        if rowcount is None or int(rowcount) < 0:
            return expected
        return int(rowcount)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Delete jobs by status (and cascaded events).")
    ap.add_argument(
        "--status-in",
        default="failed",
        help="Comma-separated Job.status values to delete (default: failed)",
    )
    ap.add_argument("--count", action="store_true", help="Print matching count and exit (no delete)")
    ap.add_argument("--yes", action="store_true", help="Actually perform the delete (required)")
    args = ap.parse_args(argv)

    statuses = _parse_csv(args.status_in)
    if not statuses:
        print("No statuses provided via --status-in.", file=sys.stderr)
        return 2

    if args.count:
        n = count_jobs_by_status(statuses)
        print(f"[jobs] status_in={','.join(statuses)} count={n}")
        return 0

    if not args.yes:
        print("Refusing to run without --yes (or use --count).", file=sys.stderr)
        return 2

    n = delete_jobs_by_status(statuses)
    if statuses == ["failed"]:
        print(f"[jobs] deleted_failed={n}")
    else:
        print(f"[jobs] deleted_status_in={n} status_in={','.join(statuses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
