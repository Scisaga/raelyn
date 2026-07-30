from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.db import session_scope
from raelyn.models import Asset, Job, Video
from raelyn.services.video_actions import schedule_video_download


_REPAIR_PIPELINE_JOB_TYPES = (
    "video.download",
    "video.download.youtube",
    "video.download.bilibili",
    "video.extract_audio",
    "video.asr_transcribe",
)
_ACTIVE_JOB_STATUSES = ("pending", "running")
_EMPTY_AUDIO_SIZE_BYTES = 257


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _active_repair_video_ids(session: Session) -> set[uuid.UUID]:
    jobs = (
        session.execute(
            select(Job).where(
                Job.type.in_(_REPAIR_PIPELINE_JOB_TYPES),
                Job.status.in_(_ACTIVE_JOB_STATUSES),
            )
        )
        .scalars()
        .all()
    )
    return {
        video_id
        for job in jobs
        if (video_id := _uuid((job.params or {}).get("video_id"))) is not None
    }


def _candidate_video_ids(session: Session, *, limit: int | None) -> list[uuid.UUID]:
    statement = (
        select(Video.id)
        .join(Asset, Asset.video_id == Video.id)
        .where(
            Video.provider == "youtube",
            Asset.type == "audio",
            Asset.format == "m4a",
            Asset.source == "ffmpeg",
            Asset.variant == "raw",
            Asset.size_bytes == _EMPTY_AUDIO_SIZE_BYTES,
        )
        .distinct()
        .order_by(Video.id.asc())
    )
    if isinstance(limit, int) and limit > 0:
        statement = statement.limit(limit)
    candidate_ids = list(session.execute(statement).scalars())
    active_video_ids = _active_repair_video_ids(session)
    return [video_id for video_id in candidate_ids if video_id not in active_video_ids]


def repair_empty_audio_assets(
    session: Session,
    *,
    execute: bool,
    limit: int | None = None,
    priority: int = 20,
) -> dict[str, Any]:
    video_ids = _candidate_video_ids(session, limit=limit)
    result: dict[str, Any] = {
        "dry_run": not execute,
        "empty_audio_size_bytes": _EMPTY_AUDIO_SIZE_BYTES,
        "candidates": len(video_ids),
        "download_jobs_enqueued": 0,
        "priority": priority,
    }
    if not execute:
        return result

    enqueued = 0
    for video_id in video_ids:
        scheduled = schedule_video_download(
            session,
            video_id,
            priority=priority,
            force=True,
        )
        if not scheduled.get("reused"):
            enqueued += 1
    session.flush()
    result["download_jobs_enqueued"] = enqueued
    return result


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Re-download YouTube videos whose ffmpeg raw M4A asset is the "
            "257-byte empty container observed in the ASR incident. Default is read-only dry-run."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Apply the repair; omitted means dry-run.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum candidates to inspect/enqueue.")
    parser.add_argument("--priority", type=int, default=20, help="Priority for forced download jobs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    if isinstance(args.limit, int) and args.limit <= 0:
        raise SystemExit("--limit must be greater than 0")
    if args.priority < 0:
        raise SystemExit("--priority must be zero or greater")

    with session_scope() as session:
        result = repair_empty_audio_assets(
            session,
            execute=bool(args.yes),
            limit=args.limit,
            priority=args.priority,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.yes:
        print("Dry-run only. Re-run with --yes after deploying the code fix.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
