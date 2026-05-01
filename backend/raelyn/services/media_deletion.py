from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import uuid

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from raelyn.models import Asset, Brief, Job, Media, Playlist, PlaylistMedia, Video
from raelyn.services.brief_prompt import brief_period_bounds_utc
from raelyn.services.brief_schedule import _period_starts_for_timestamps, schedule_brief_refresh


MEDIA_DELETE_JOB_TYPE = "media.delete"
MEDIA_DELETE_PRIORITY = 20
_ACTIVE_JOB_STATUSES = ("pending", "running")
_RELATED_MEDIA_JOB_TYPES = {"media.sync_profile", "media.sync_videos", MEDIA_DELETE_JOB_TYPE}
_RELATED_VIDEO_JOB_TYPES = {
    "video.download",
    "video.download.youtube",
    "video.download.bilibili",
    "video.extract_audio",
    "video.normalize_subtitle",
    "video.asr_transcribe",
    "video.polish_transcript",
}
_RELATED_BRIEF_JOB_TYPES = {"brief.generate_period", "brief.generate_daily"}


@dataclass(frozen=True)
class AffectedBriefPeriod:
    playlist_id: uuid.UUID
    granularity: str
    period_start: date


@dataclass
class MediaDeleteSnapshot:
    media_id: uuid.UUID
    provider: str
    video_ids: list[uuid.UUID]
    affected_periods: list[AffectedBriefPeriod]
    cleanup_buckets: set[str]
    prefixes: list[str]


def _job_media_id_expr():
    return Job.params["media_id"].astext


def _job_video_id_expr():
    return Job.params["video_id"].astext


def _job_playlist_id_expr():
    return Job.params["playlist_id"].astext


def active_media_delete_job(session: Session, media_id: uuid.UUID) -> Job | None:
    return (
        session.execute(
            select(Job)
            .where(
                Job.type == MEDIA_DELETE_JOB_TYPE,
                Job.status.in_(_ACTIVE_JOB_STATUSES),
                _job_media_id_expr() == cast(media_id, String),
            )
            .order_by(Job.created_at.asc(), Job.id.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def active_media_delete_job_map(session: Session, media_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    ids = list(dict.fromkeys(media_ids))
    if not ids:
        return {}
    rows = (
        session.execute(
            select(Job.id, _job_media_id_expr())
            .where(
                Job.type == MEDIA_DELETE_JOB_TYPE,
                Job.status.in_(_ACTIVE_JOB_STATUSES),
                _job_media_id_expr().in_([str(media_id) for media_id in ids]),
            )
            .order_by(Job.created_at.asc(), Job.id.asc())
        )
        .all()
    )
    out: dict[uuid.UUID, uuid.UUID] = {}
    for job_id, raw_media_id in rows:
        try:
            media_id = uuid.UUID(str(raw_media_id))
        except Exception:
            continue
        if media_id not in out:
            out[media_id] = job_id
    return out


def ensure_media_not_deleting(session: Session, media_id: uuid.UUID) -> None:
    if active_media_delete_job(session, media_id):
        raise RuntimeError("媒体删除中，当前操作不可用")


def collect_media_delete_snapshot(session: Session, media: Media) -> MediaDeleteSnapshot:
    video_ids = session.execute(select(Video.id).where(Video.media_id == media.id)).scalars().all()
    playlist_rows = session.execute(
        select(Playlist.id, Playlist.brief_granularity)
        .join(PlaylistMedia, PlaylistMedia.playlist_id == Playlist.id)
        .where(PlaylistMedia.media_id == media.id)
        .distinct()
    ).all()

    co_ts = func.coalesce(Video.published_at, Video.created_at)
    timestamps = session.execute(select(co_ts).where(Video.media_id == media.id)).scalars().all()

    affected_periods: set[AffectedBriefPeriod] = set()
    for playlist_id, granularity in playlist_rows:
        for period_start in _period_starts_for_timestamps(
            timestamps=list(timestamps),
            granularity=str(granularity or "day"),
        ):
            affected_periods.add(
                AffectedBriefPeriod(
                    playlist_id=playlist_id,
                    granularity=str(granularity or "day").strip().lower() or "day",
                    period_start=period_start,
                )
            )

    asset_buckets = (
        session.execute(select(Asset.s3_bucket).join(Video, Asset.video_id == Video.id).where(Video.media_id == media.id))
        .scalars()
        .all()
    )
    cleanup_buckets = {str(bucket or "").strip() for bucket in asset_buckets if str(bucket or "").strip()}
    if media.avatar_asset_id:
        avatar_asset = session.get(Asset, media.avatar_asset_id)
        if avatar_asset:
            avatar_bucket = str(avatar_asset.s3_bucket or "").strip()
            if avatar_bucket:
                cleanup_buckets.add(avatar_bucket)

    return MediaDeleteSnapshot(
        media_id=media.id,
        provider=str(media.provider or "").strip(),
        video_ids=list(video_ids),
        affected_periods=sorted(affected_periods, key=lambda item: (str(item.playlist_id), item.granularity, item.period_start)),
        cleanup_buckets=cleanup_buckets,
        prefixes=[f"{media.provider}/{media.id}/", f"media/{media.id}/"],
    )


def list_related_jobs(session: Session, snapshot: MediaDeleteSnapshot, *, exclude_job_id: uuid.UUID | None = None) -> list[Job]:
    jobs_by_id: dict[uuid.UUID, Job] = {}

    media_jobs = (
        session.execute(
            select(Job).where(
                Job.status.in_(_ACTIVE_JOB_STATUSES),
                Job.type.in_(_RELATED_MEDIA_JOB_TYPES),
                _job_media_id_expr() == cast(snapshot.media_id, String),
            )
        )
        .scalars()
        .all()
    )
    for job in media_jobs:
        if exclude_job_id and job.id == exclude_job_id:
            continue
        jobs_by_id[job.id] = job

    if snapshot.video_ids:
        video_jobs = (
            session.execute(
                select(Job).where(
                    Job.status.in_(_ACTIVE_JOB_STATUSES),
                    Job.type.in_(_RELATED_VIDEO_JOB_TYPES),
                    _job_video_id_expr().in_([str(video_id) for video_id in snapshot.video_ids]),
                )
            )
            .scalars()
            .all()
        )
        for job in video_jobs:
            if exclude_job_id and job.id == exclude_job_id:
                continue
            jobs_by_id[job.id] = job

    playlist_ids = list({str(item.playlist_id) for item in snapshot.affected_periods})
    if playlist_ids:
        brief_jobs = (
            session.execute(
                select(Job).where(
                    Job.status.in_(_ACTIVE_JOB_STATUSES),
                    Job.type.in_(_RELATED_BRIEF_JOB_TYPES),
                    _job_playlist_id_expr().in_(playlist_ids),
                )
            )
            .scalars()
            .all()
        )
        affected = {(str(item.playlist_id), item.granularity, item.period_start.isoformat()) for item in snapshot.affected_periods}
        for job in brief_jobs:
            if exclude_job_id and job.id == exclude_job_id:
                continue
            params = dict(job.params or {})
            granularity = "day" if job.type == "brief.generate_daily" else str(params.get("granularity") or "day").strip().lower()
            period_start = str(params.get("period_start") or params.get("date") or "").strip()
            key = (str(params.get("playlist_id") or "").strip(), granularity, period_start)
            if key in affected:
                jobs_by_id[job.id] = job

    return sorted(jobs_by_id.values(), key=lambda item: (str(item.created_at or ""), str(item.id)))


def mark_brief_empty(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    period_start: date,
    message: str = "本周期无视频",
) -> Brief:
    brief = session.execute(
        select(Brief).where(
            Brief.playlist_id == playlist_id,
            Brief.granularity == granularity,
            Brief.period_start == period_start,
        )
    ).scalar_one_or_none()
    if not brief:
        brief = Brief(playlist_id=playlist_id, granularity=granularity, period_start=period_start, status="empty")
        session.add(brief)
        session.flush()
    brief.status = "empty"
    brief.markdown_asset_id = None
    brief.error_message = message
    return brief


def refresh_affected_briefs_after_media_delete(session: Session, affected_periods: list[AffectedBriefPeriod]) -> dict[str, int]:
    refreshed = 0
    emptied = 0
    co_ts = func.coalesce(Video.published_at, Video.created_at)

    for item in affected_periods:
        playlist = session.get(Playlist, item.playlist_id)
        if not playlist:
            continue

        media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == item.playlist_id)).scalars().all()
        if not media_ids:
            mark_brief_empty(
                session,
                playlist_id=item.playlist_id,
                granularity=item.granularity,
                period_start=item.period_start,
            )
            emptied += 1
            continue

        start_utc, end_utc = brief_period_bounds_utc(item.period_start, item.granularity)
        has_video = session.execute(
            select(Video.id)
            .where(Video.media_id.in_(list(media_ids)), co_ts >= start_utc, co_ts < end_utc)
            .limit(1)
        ).scalar_one_or_none()
        if not has_video:
            mark_brief_empty(
                session,
                playlist_id=item.playlist_id,
                granularity=item.granularity,
                period_start=item.period_start,
            )
            emptied += 1
            continue

        if schedule_brief_refresh(
            session,
            playlist_id=item.playlist_id,
            granularity=item.granularity,
            period_start=item.period_start,
            trigger_mode="auto",
            reason="media_removed",
        ):
            refreshed += 1

    return {"refreshed": refreshed, "emptied": emptied}
