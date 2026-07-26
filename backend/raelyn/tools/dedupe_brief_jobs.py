from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.db import session_scope
from raelyn.models import Job, JobEvent
from raelyn.timeutil import utcnow


def _parse_iso_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        s = s[:10]
        try:
            return date.fromisoformat(s)
        except Exception:
            return None
    return None


def _period_start(d: date, granularity: str) -> date:
    g = (granularity or "day").strip().lower()
    if g == "week":
        return d - timedelta(days=d.weekday())  # Monday
    if g == "month":
        return date(d.year, d.month, 1)
    return d


def _brief_key(j: Job) -> tuple[str, str] | None:
    params = j.params if isinstance(j.params, dict) else {}
    pid_raw = params.get("playlist_id")
    try:
        pid = str(uuid.UUID(str(pid_raw)))
    except Exception:
        return None

    raw_date = params.get("period_start") or params.get("date")
    d = _parse_iso_date(raw_date)
    if not d:
        return None

    if j.type == "brief.generate_daily":
        g = "day"
    else:
        g = str(params.get("granularity") or "day").strip().lower()
        if g not in {"day", "week", "month"}:
            g = "day"
        d = _period_start(d, g)

    return pid, d.isoformat()


def dedupe_pending_brief_jobs(*, yes: bool = False) -> dict[str, int]:
    with session_scope() as session:
        jobs = (
            session.execute(
                select(Job).where(Job.status == "pending", Job.type.in_(["brief.generate_period", "brief.generate_daily"]))
            )
            .scalars()
            .all()
        )

        groups: dict[tuple[str, str], list[Job]] = {}
        for j in jobs:
            k = _brief_key(j)
            if not k:
                continue
            groups.setdefault(k, []).append(j)

        total_groups = len(groups)
        total_considered = len(jobs)
        to_cancel: list[Job] = []
        for _, items in groups.items():
            if len(items) <= 1:
                continue
            items.sort(key=lambda x: (x.scheduled_for or utcnow(), x.created_at or utcnow()))
            to_cancel.extend(items[1:])

        if not yes:
            return {
                "considered": total_considered,
                "groups": total_groups,
                "duplicates": len(to_cancel),
                "canceled": 0,
            }

        now = utcnow()
        n = 0
        for j in to_cancel:
            if j.status != "pending":
                continue
            j.status = "canceled"
            j.finished_at = now
            j.lease_expires_at = None
            j.worker_id = None
            j.execution_token = None
            session.add(JobEvent(job_id=j.id, level="info", message="canceled (dedupe brief pending)"))
            n += 1
        session.flush()
        return {
            "considered": total_considered,
            "groups": total_groups,
            "duplicates": len(to_cancel),
            "canceled": n,
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Cancel duplicate pending brief jobs, grouped by (playlist_id, normalized period_start/date)."
    )
    ap.add_argument("--yes", action="store_true", help="Actually perform the cancels (required)")
    args = ap.parse_args(argv)

    r = dedupe_pending_brief_jobs(yes=bool(args.yes))
    if args.yes:
        print(
            f"[brief dedupe] considered={r.get('considered', 0)} groups={r.get('groups', 0)} "
            f"duplicates={r.get('duplicates', 0)} canceled={r.get('canceled', 0)}"
        )
    else:
        print(
            f"[brief dedupe] DRY RUN (add --yes to apply): considered={r.get('considered', 0)} "
            f"groups={r.get('groups', 0)} duplicates={r.get('duplicates', 0)}"
        )
        return 2 if int(r.get("duplicates", 0) or 0) > 0 else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
