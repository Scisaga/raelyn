#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and Path(sys.executable).absolute() != VENV_PYTHON.absolute():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Asset, Playlist, PlaylistMedia, Video
from raelyn.services.llm import llm_enabled
from raelyn.services.video_time import normalize_time_basis, timeline_time_expr


def _parse_date(value: str | None, *, name: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be YYYY-MM-DD, got: {raw}") from exc


def _date_start_utc(value: date, timezone_name: str) -> datetime:
    tzinfo = ZoneInfo(timezone_name)
    return datetime.combine(value, time.min, tzinfo=tzinfo).astimezone(ZoneInfo("UTC"))


def _log(message: str) -> None:
    print(message, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enqueue video.extract_events jobs for videos in a playlist date range.",
    )
    parser.add_argument("playlist_id", help="Playlist UUID.")
    parser.add_argument("--since", help="Start local date, inclusive, YYYY-MM-DD. Default: today - --days.")
    parser.add_argument("--until", help="End local date, inclusive, YYYY-MM-DD. Default: today.")
    parser.add_argument("--days", type=int, default=365, help="Lookback days when --since is omitted. Default: 365.")
    parser.add_argument(
        "--time-basis",
        default="content",
        choices=["content", "platform"],
        help="Date basis for selecting videos. Default: content.",
    )
    parser.add_argument("--force", action="store_true", help="Force event re-extraction in video.extract_events.")
    parser.add_argument("--priority", type=int, default=1, help="Job priority. Default: 1.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max videos to enqueue. Default: no limit.")
    parser.add_argument("--progress-every", type=int, default=100, help="Print enqueue progress every N jobs. Default: 100.")
    parser.add_argument(
        "--commit-every",
        type=int,
        default=0,
        help="Commit enqueued jobs every N jobs. Default: same as --progress-every.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print matching count without enqueueing jobs.")
    parser.add_argument(
        "--skip-llm-check",
        action="store_true",
        help="Do not fail when LLM is not configured; jobs may skip at execution time.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        playlist_id = uuid.UUID(str(args.playlist_id))
    except ValueError as exc:
        raise SystemExit(f"playlist_id must be a UUID, got: {args.playlist_id}") from exc

    time_basis = normalize_time_basis(args.time_basis)
    timezone_name = str(settings.timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
    today = datetime.now(ZoneInfo(timezone_name)).date()
    until_date = _parse_date(args.until, name="--until") or today
    if args.since:
        since_date = _parse_date(args.since, name="--since")
    else:
        days = max(1, int(args.days or 365))
        since_date = until_date - timedelta(days=days)
    if since_date is None:
        raise SystemExit("failed to resolve --since")
    if since_date > until_date:
        raise SystemExit("--since must be <= --until")

    start_utc = _date_start_utc(since_date, timezone_name)
    end_utc = _date_start_utc(until_date + timedelta(days=1), timezone_name)

    _log("[event-enqueue] start")
    _log(f"[event-enqueue] playlist_id={playlist_id}")
    _log(f"[event-enqueue] time_basis={time_basis} force={bool(args.force)} dry_run={bool(args.dry_run)}")
    _log(f"[event-enqueue] local_range={since_date.isoformat()} ~ {until_date.isoformat()} timezone={timezone_name}")
    _log(f"[event-enqueue] utc_range={start_utc.isoformat()} ~ {end_utc.isoformat()}")

    if not args.skip_llm_check and not llm_enabled():
        raise SystemExit("LLM is not configured; set LLM_URL/LLM_MODEL or use --skip-llm-check.")
    _log("[event-enqueue] llm_check=ok")

    with session_scope() as session:
        _log("[event-enqueue] opening database session")
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise SystemExit(f"playlist not found: {playlist_id}")
        _log(f"[event-enqueue] playlist_found name={playlist.name!r}")

        timeline = timeline_time_expr(Video, time_basis=time_basis)
        has_plain_transcript = (
            select(Asset.id)
            .where(
                Asset.video_id == Video.id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.variant == "plain",
            )
            .limit(1)
            .exists()
        )
        stmt = (
            select(Video.id, timeline.label("timeline_at"))
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(
                PlaylistMedia.playlist_id == playlist_id,
                timeline >= start_utc,
                timeline < end_utc,
                has_plain_transcript,
            )
            .order_by(timeline.asc(), Video.id.asc())
        )
        if int(args.limit or 0) > 0:
            stmt = stmt.limit(int(args.limit))
            _log(f"[event-enqueue] limit={int(args.limit)}")
        _log("[event-enqueue] querying matching videos with plain transcript ...")
        rows = session.execute(stmt).all()
        _log(f"[event-enqueue] matching_videos_with_plain_transcript={len(rows)}")
        if rows:
            first_video_id, first_timeline_at = rows[0]
            last_video_id, last_timeline_at = rows[-1]
            _log(f"[event-enqueue] first_match video_id={first_video_id} timeline_at={first_timeline_at}")
            _log(f"[event-enqueue] last_match video_id={last_video_id} timeline_at={last_timeline_at}")

        job_ids: list[uuid.UUID] = []
        if not args.dry_run:
            progress_every = max(1, int(args.progress_every or 100))
            commit_every = max(1, int(args.commit_every or progress_every))
            _log("[event-enqueue] enqueueing video.extract_events jobs ...")
            for index, (video_id, _timeline_at) in enumerate(rows, start=1):
                job_ids.append(
                    enqueue_job(
                        session,
                        type_="video.extract_events",
                        params={"video_id": str(video_id), "force": bool(args.force)},
                        priority=int(args.priority),
                    )
                )
                if index == 1 or index % progress_every == 0 or index == len(rows):
                    _log(f"[event-enqueue] enqueued {index}/{len(rows)}")
                if index % commit_every == 0:
                    session.commit()
                    _log(f"[event-enqueue] committed {index}/{len(rows)}")
            if job_ids and len(job_ids) % commit_every != 0:
                session.commit()
                _log(f"[event-enqueue] committed {len(job_ids)}/{len(rows)}")

        if args.dry_run:
            _log("[event-enqueue] dry_run=true")
        else:
            _log(f"[event-enqueue] submitted_video_extract_events={len(job_ids)}")
            _log(f"[event-enqueue] unique_job_ids_returned={len(set(job_ids))}")
        _log("[event-enqueue] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
