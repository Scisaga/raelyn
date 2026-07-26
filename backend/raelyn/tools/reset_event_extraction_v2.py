from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.db import session_scope
from raelyn.models import (
    EventMapCanonical,
    EventMapCanonicalIdentity,
    EventMapCanonicalLineage,
    EventMapCanonicalMember,
    EventMapProjectionAnchor,
    EventMapRecordRevision,
    EventMapSnapshot,
    EventMapState,
    EventMapStory,
    EventMapStoryEdge,
    EventMapStoryMember,
    EventMapTopic,
    EventMapTopicMember,
    Job,
    JobEvent,
    MarketEvent,
    MarketEventEmbedding,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
    VideoEventExtractionRun,
)
from raelyn.timeutil import utcnow


EVENT_RESET_JOB_TYPES = (
    "playlist.backfill_events",
    "playlist.backfill_events_range",
    "video.extract_events",
    "video.extract_events_batch",
    "event.embed",
    "playlist.mark_event_map_dirty",
    "playlist.build_event_map_snapshot",
    "playlist.prune_event_map_snapshots",
)


def _count(session: Session, model: Any) -> int:
    return int(session.execute(select(func.count()).select_from(model)).scalar_one() or 0)


def collect_event_extraction_reset_counts(session: Session) -> dict[str, int]:
    job_count = int(
        session.execute(
            select(func.count()).select_from(Job).where(Job.type.in_(EVENT_RESET_JOB_TYPES))
        ).scalar_one()
        or 0
    )
    return {
        "jobs": job_count,
        "market_event_relation": _count(session, MarketEventRelation),
        "market_event_entity": _count(session, MarketEventEntity),
        "market_event_evidence": _count(session, MarketEventEvidence),
        "market_event_embedding": _count(session, MarketEventEmbedding),
        "market_event": _count(session, MarketEvent),
        "event_map_story_edge": _count(session, EventMapStoryEdge),
        "event_map_story_member": _count(session, EventMapStoryMember),
        "event_map_story": _count(session, EventMapStory),
        "event_map_topic_member": _count(session, EventMapTopicMember),
        "event_map_projection_anchor": _count(session, EventMapProjectionAnchor),
        "event_map_topic": _count(session, EventMapTopic),
        "event_map_canonical_lineage": _count(session, EventMapCanonicalLineage),
        "event_map_canonical_member": _count(session, EventMapCanonicalMember),
        "event_map_canonical": _count(session, EventMapCanonical),
        "event_map_canonical_identity": _count(session, EventMapCanonicalIdentity),
        "event_map_record_revision": _count(session, EventMapRecordRevision),
        "event_map_snapshot": _count(session, EventMapSnapshot),
        "event_map_state": _count(session, EventMapState),
        "video_event_extraction_run": _count(session, VideoEventExtractionRun),
    }


def reset_event_extraction_v2(session: Session, *, execute: bool) -> dict[str, Any]:
    counts = collect_event_extraction_reset_counts(session)
    result: dict[str, Any] = {"dry_run": not execute, "counts": counts}
    if not execute:
        return result

    now = utcnow()
    session.execute(
        update(EventMapState).values(
            current_snapshot_id=None,
            active_job_id=None,
            dirty_generation=case(
                (
                    EventMapState.dirty_generation <= EventMapState.built_generation,
                    EventMapState.built_generation + 1,
                ),
                else_=EventMapState.dirty_generation + 1,
            ),
            first_dirty_at=func.coalesce(EventMapState.first_dirty_at, now),
            last_dirty_at=now,
            last_requested_at=now,
            last_built_at=None,
            last_error=None,
            updated_at=now,
        )
    )

    job_ids = select(Job.id).where(Job.type.in_(EVENT_RESET_JOB_TYPES))
    session.execute(delete(JobEvent).where(JobEvent.job_id.in_(job_ids)))
    session.execute(delete(Job).where(Job.type.in_(EVENT_RESET_JOB_TYPES)))

    # Delete immutable snapshot children explicitly so the reset has the same
    # semantics even when a local test database does not enable FK cascades.
    for model in (
        EventMapStoryEdge,
        EventMapStoryMember,
        EventMapStory,
        EventMapTopicMember,
        EventMapProjectionAnchor,
        EventMapTopic,
        EventMapCanonicalLineage,
        EventMapCanonicalMember,
        EventMapCanonical,
        EventMapSnapshot,
        EventMapCanonicalIdentity,
        EventMapRecordRevision,
        MarketEventRelation,
        MarketEventEntity,
        MarketEventEvidence,
        MarketEventEmbedding,
        MarketEvent,
        VideoEventExtractionRun,
    ):
        session.execute(delete(model))
    session.flush()
    deleted_counts = dict(counts)
    updated_state_count = deleted_counts.pop("event_map_state", 0)
    result["deleted"] = deleted_counts
    result["updated"] = {"event_map_state": updated_state_count}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset event extraction v2 data and event-pipeline jobs. Default is dry-run.",
    )
    parser.add_argument("--yes", action="store_true", help="Actually delete data; omitted means dry-run.")
    args = parser.parse_args(argv)

    with session_scope() as session:
        result = reset_event_extraction_v2(session, execute=bool(args.yes))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.yes:
        print("Dry-run only. Re-run with --yes to delete the counted rows.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
