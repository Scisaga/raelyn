from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.db import session_scope
from raelyn.models import (
    EventRegimeCandidate,
    EventRegimeRun,
    EventRegimeSignal,
    EventRegimeState,
    Job,
    JobEvent,
    MarketEvent,
    MarketEventEmbedding,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
    VideoEventExtractionRun,
)


EVENT_RESET_JOB_TYPES = (
    "playlist.backfill_events",
    "playlist.backfill_events_range",
    "video.extract_events",
    "video.extract_events_batch",
    "event.embed",
    "playlist.mark_event_regime_dirty",
    "playlist.build_event_regime_snapshot",
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
        "event_regime_signal": _count(session, EventRegimeSignal),
        "event_regime_candidate": _count(session, EventRegimeCandidate),
        "event_regime_run": _count(session, EventRegimeRun),
        "event_regime_state": _count(session, EventRegimeState),
        "video_event_extraction_run": _count(session, VideoEventExtractionRun),
    }


def reset_event_extraction_v2(session: Session, *, execute: bool) -> dict[str, Any]:
    counts = collect_event_extraction_reset_counts(session)
    result: dict[str, Any] = {"dry_run": not execute, "counts": counts}
    if not execute:
        return result

    job_ids = select(Job.id).where(Job.type.in_(EVENT_RESET_JOB_TYPES))
    session.execute(delete(JobEvent).where(JobEvent.job_id.in_(job_ids)))
    session.execute(delete(Job).where(Job.type.in_(EVENT_RESET_JOB_TYPES)))

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        session.execute(
            text(
                "truncate table "
                "market_event_relation, market_event_entity, market_event_evidence, market_event_embedding, market_event"
            )
        )
        session.execute(text("truncate table event_regime_signal, event_regime_candidate"))
        session.execute(text("truncate table video_event_extraction_run"))
    else:
        for model in (MarketEventRelation, MarketEventEntity, MarketEventEvidence, MarketEventEmbedding):
            session.execute(delete(model))
        session.execute(delete(MarketEvent))
        session.execute(delete(EventRegimeSignal))
        session.execute(delete(EventRegimeCandidate))
        session.execute(delete(VideoEventExtractionRun))

    session.execute(
        update(EventRegimeState).values(last_ready_run_id=None, analysis_dirty=True)
    )
    session.execute(delete(EventRegimeRun))
    session.flush()
    result["deleted"] = counts
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
