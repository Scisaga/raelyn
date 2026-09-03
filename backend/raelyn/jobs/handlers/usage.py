from __future__ import annotations

from sqlalchemy.orm import Session

from raelyn.jobs.registry import registry
from raelyn.models import Job
from raelyn.services.usage import backfill_legacy_external_usage, capture_resource_usage_snapshot


@registry.register("system.backfill_legacy_usage")
def system_backfill_legacy_usage(session: Session, _job: Job) -> dict[str, object]:
    return backfill_legacy_external_usage(session)


@registry.register("system.capture_usage_snapshot")
def system_capture_usage_snapshot(session: Session, _job: Job) -> dict[str, object]:
    snapshot = capture_resource_usage_snapshot(session)
    return {
        "day": snapshot.day.isoformat(),
        "captured_at": snapshot.captured_at.isoformat(),
        "video_count": int(snapshot.video_count),
        "asset_count": int(snapshot.asset_count),
        "asset_size_bytes": int(snapshot.asset_size_bytes),
        "asset_missing_size_count": int(snapshot.asset_missing_size_count),
        "database_size_bytes": (
            int(snapshot.database_size_bytes)
            if snapshot.database_size_bytes is not None
            else None
        ),
    }
