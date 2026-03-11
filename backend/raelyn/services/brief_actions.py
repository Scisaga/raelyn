from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from raelyn.services.brief_schedule import schedule_brief_refresh
from raelyn.services.periods import iter_period_starts, normalize_granularity, period_start


def schedule_brief_generation(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    date_in_period: date,
) -> dict[str, Any]:
    g = normalize_granularity(granularity)
    period_start_value = period_start(date_in_period, g)
    job_id = schedule_brief_refresh(
        session,
        playlist_id=playlist_id,
        granularity=g,
        period_start=period_start_value,
        trigger_mode="manual",
        reason="manual_generate",
    )
    if not job_id:
        raise LookupError("playlist not found")
    return {
        "ok": True,
        "status": "accepted",
        "message": "brief generation enqueued",
        "playlist_id": str(playlist_id),
        "granularity": g,
        "period_start": period_start_value.isoformat(),
        "job_id": str(job_id),
        "job_type": "brief.generate_period",
    }


def schedule_brief_generation_range(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    if to_date < from_date:
        raise ValueError("to_date must be >= from_date")

    g = normalize_granularity(granularity)
    enqueued = 0
    job_ids: list[str] = []
    for current in iter_period_starts(from_date, to_date, g):
        job_id = schedule_brief_refresh(
            session,
            playlist_id=playlist_id,
            granularity=g,
            period_start=current,
            trigger_mode="manual",
            reason="manual_generate_range",
        )
        if not job_id:
            raise LookupError("playlist not found")
        enqueued += 1
        job_ids.append(str(job_id))

    return {
        "ok": True,
        "status": "accepted",
        "message": "brief generation range enqueued",
        "playlist_id": str(playlist_id),
        "granularity": g,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "enqueued": enqueued,
        "job_ids": job_ids,
        "job_types": ["brief.generate_period"] * len(job_ids),
    }
