from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Asset, Job, Video, VideoEventExtractionRun


DOWNLOAD_JOB_TYPES = (
    "video.download",
    "video.download.youtube",
    "video.download.bilibili",
)
EVENT_EXTRACTION_JOB_TYPES = (
    "video.extract_events",
    "video.extract_events_batch",
)
ACTIVE_JOB_STATUSES = ("pending", "running")


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _job_video_ids(job: Job) -> set[uuid.UUID]:
    params = dict(job.params or {})
    values: list[Any]
    if job.type == "video.extract_events_batch":
        raw = params.get("video_ids")
        values = raw if isinstance(raw, list) else []
    else:
        values = [params.get("video_id")]
    return {video_id for value in values if (video_id := _uuid(value)) is not None}


def _latest_failed_downloads(session: Session) -> dict[uuid.UUID, str | None]:
    video_id_text = Job.params["video_id"].as_string()
    ranked = (
        select(
            video_id_text.label("video_id"),
            Job.status.label("status"),
            Job.error_message.label("error_message"),
            func.row_number()
            .over(
                partition_by=video_id_text,
                order_by=(func.coalesce(Job.finished_at, Job.created_at).desc(), Job.id.desc()),
            )
            .label("position"),
        )
        .where(Job.type.in_(DOWNLOAD_JOB_TYPES), video_id_text.is_not(None))
        .subquery()
    )
    rows = session.execute(
        select(ranked.c.video_id, ranked.c.error_message).where(
            ranked.c.position == 1,
            ranked.c.status == "failed",
        )
    ).all()
    result: dict[uuid.UUID, str | None] = {}
    for raw_video_id, error_message in rows:
        video_id = _uuid(raw_video_id)
        if video_id is not None:
            result[video_id] = str(error_message) if error_message else None
    return result


def _active_video_ids(session: Session, job_types: tuple[str, ...]) -> set[uuid.UUID]:
    jobs = (
        session.execute(
            select(Job).where(
                Job.type.in_(job_types),
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        .scalars()
        .all()
    )
    return {video_id for job in jobs for video_id in _job_video_ids(job)}


def _download_status_candidates(
    session: Session,
    *,
    lock: bool,
) -> list[tuple[Video, str | None]]:
    latest_failed = _latest_failed_downloads(session)
    if not latest_failed:
        return []
    active_video_ids = _active_video_ids(session, DOWNLOAD_JOB_TYPES)
    candidate_ids = sorted(set(latest_failed) - active_video_ids, key=str)
    if not candidate_ids:
        return []
    statement = (
        select(Video)
        .where(
            Video.id.in_(candidate_ids),
            Video.status.in_(["discovered", "downloading"]),
        )
        .order_by(Video.id.asc())
    )
    if lock:
        statement = statement.with_for_update()
    videos = session.execute(statement).scalars().all()
    if not videos:
        return []

    # 执行模式可能等待视频行锁；拿到锁后重新读取活跃任务和视频资产，
    # 避免把等待期间已开始下载或已落盘的视频误改成 failed。
    if lock:
        active_video_ids = _active_video_ids(session, DOWNLOAD_JOB_TYPES)
    locked_video_ids = [video.id for video in videos]
    video_asset_ids = set(
        session.execute(
            select(Asset.video_id).where(
                Asset.video_id.in_(locked_video_ids),
                Asset.type == "video",
            )
        ).scalars()
    )
    return [
        (video, latest_failed.get(video.id))
        for video in videos
        if video.id not in active_video_ids and video.id not in video_asset_ids
    ]


def _failed_extraction_video_ids(session: Session) -> list[uuid.UUID]:
    ranked = (
        select(
            VideoEventExtractionRun.video_id.label("video_id"),
            VideoEventExtractionRun.status.label("status"),
            func.row_number()
            .over(
                partition_by=VideoEventExtractionRun.video_id,
                order_by=(
                    VideoEventExtractionRun.updated_at.desc(),
                    VideoEventExtractionRun.id.desc(),
                ),
            )
            .label("position"),
        )
        .subquery()
    )
    failed_ids = set(
        session.execute(
            select(ranked.c.video_id).where(
                ranked.c.position == 1,
                ranked.c.status == "failed",
            )
        ).scalars()
    )
    if not failed_ids:
        return []
    active_video_ids = _active_video_ids(session, EVENT_EXTRACTION_JOB_TYPES)
    existing_video_ids = set(
        session.execute(select(Video.id).where(Video.id.in_(failed_ids))).scalars()
    )
    return sorted(existing_video_ids - active_video_ids, key=str)


def _event_extraction_repair_params(video_id: uuid.UUID) -> dict[str, Any]:
    return {"video_id": str(video_id), "force": False}


def repair_video_event_pipeline(session: Session, *, execute: bool) -> dict[str, Any]:
    download_candidates = _download_status_candidates(session, lock=execute)
    extraction_video_ids = _failed_extraction_video_ids(session)
    result: dict[str, Any] = {
        "dry_run": not execute,
        "download_status_candidates": len(download_candidates),
        "event_extraction_candidates": len(extraction_video_ids),
        "download_jobs_enqueued": 0,
    }
    if not execute:
        return result

    for video, error_message in download_candidates:
        video.status = "failed"
        if error_message:
            video.error_message = error_message

    enqueued = 0
    for video_id in extraction_video_ids:
        enqueue_job(
            session,
            type_="video.extract_events",
            params=_event_extraction_repair_params(video_id),
        )
        enqueued += 1
    session.flush()
    result.update(
        {
            "download_status_updated": len(download_candidates),
            "event_extraction_enqueued_or_reused": enqueued,
        }
    )
    return result


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Repair terminal download video states and requeue failed event extractions. "
            "Default is read-only dry-run."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Apply the repair; omitted means dry-run.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)

    with session_scope() as session:
        result = repair_video_event_pipeline(session, execute=bool(args.yes))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.yes:
        print("Dry-run only. Re-run with --yes after deploying the code fix.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
