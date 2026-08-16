from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from raelyn.models import (
    Asset,
    Brief,
    DailyBrief,
    DomainObservationCursor,
    EventMapCanonicalHistoryRevision,
    EventMapCanonicalIdentity,
    EventMapChange,
    EventMapSnapshot,
    EventMapStoryHistoryRevision,
    EventMapStoryIdentity,
    Job,
    Media,
    Playlist,
    PlaylistMedia,
    Video,
)
from raelyn.services.brief_schedule import schedule_brief_refresh_for_media_change
from raelyn.services.event_analysis import schedule_playlist_event_map_dirty


ACTIVE_DOMAIN_JOB_STATUSES = ("pending", "running", "cancel_requested")


@dataclass(frozen=True)
class DomainSourceAttachment:
    media: Media
    attached: bool


def attach_domain_source(
    session: Session,
    *,
    domain_id: uuid.UUID,
    media_id: uuid.UUID,
) -> DomainSourceAttachment:
    playlist = session.get(Playlist, domain_id)
    if playlist is None:
        raise LookupError("domain not found")
    media = session.get(Media, media_id)
    if media is None:
        raise LookupError("media not found")

    relation = session.get(PlaylistMedia, {"playlist_id": domain_id, "media_id": media_id})
    attached = relation is None
    if attached:
        session.add(PlaylistMedia(playlist_id=domain_id, media_id=media_id))
        schedule_playlist_event_map_dirty(
            session,
            playlist_id=domain_id,
            reason="playlist_media_added",
            priority=0,
        )
        schedule_brief_refresh_for_media_change(
            session,
            playlist_id=domain_id,
            changed_media_ids=[media_id],
            change_type="media_added",
        )
    return DomainSourceAttachment(media=media, attached=attached)


def detach_domain_source(
    session: Session,
    *,
    domain_id: uuid.UUID,
    media_id: uuid.UUID,
) -> bool:
    if session.get(Playlist, domain_id) is None:
        raise LookupError("domain not found")
    relation = session.get(PlaylistMedia, {"playlist_id": domain_id, "media_id": media_id})
    if relation is None:
        return False

    session.delete(relation)
    schedule_playlist_event_map_dirty(
        session,
        playlist_id=domain_id,
        reason="playlist_media_removed",
        priority=0,
    )
    schedule_brief_refresh_for_media_change(
        session,
        playlist_id=domain_id,
        changed_media_ids=[media_id],
        change_type="media_removed",
    )
    return True


def _count(session: Session, model: Any, condition: Any) -> int:
    return int(session.execute(select(func.count()).select_from(model).where(condition)).scalar_one())


def active_domain_jobs(session: Session, domain_id: uuid.UUID) -> list[Job]:
    """返回直接属于观测域的活跃任务，以及它们仍活跃的子任务。"""

    jobs = list(
        session.execute(
            select(Job)
            .where(Job.status.in_(ACTIVE_DOMAIN_JOB_STATUSES))
            .order_by(Job.created_at.asc(), Job.id.asc())
        ).scalars()
    )
    selected_ids = {
        job.id
        for job in jobs
        if str(dict(job.params or {}).get("playlist_id") or "").strip() == str(domain_id)
    }
    changed = True
    while changed:
        changed = False
        for job in jobs:
            if job.id in selected_ids or job.parent_job_id not in selected_ids:
                continue
            selected_ids.add(job.id)
            changed = True
    return [job for job in jobs if job.id in selected_ids]


def domain_deletion_impact(session: Session, domain_id: uuid.UUID) -> dict[str, Any]:
    playlist = session.get(Playlist, domain_id)
    if playlist is None:
        raise LookupError("domain not found")

    media_ids = list(
        session.execute(
            select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == domain_id)
        ).scalars()
    )
    shared_video_count = 0
    shared_asset_count = 0
    if media_ids:
        shared_video_count = _count(session, Video, Video.media_id.in_(media_ids))
        shared_asset_count = int(
            session.execute(
                select(func.count())
                .select_from(Asset)
                .join(Video, Video.id == Asset.video_id)
                .where(Video.media_id.in_(media_ids))
            ).scalar_one()
        )

    jobs = active_domain_jobs(session, domain_id)
    counts = {
        "source_links": len(media_ids),
        "briefs": _count(session, Brief, Brief.playlist_id == domain_id),
        "legacy_daily_briefs": _count(session, DailyBrief, DailyBrief.playlist_id == domain_id),
        "snapshots": _count(session, EventMapSnapshot, EventMapSnapshot.playlist_id == domain_id),
        "changes": _count(session, EventMapChange, EventMapChange.playlist_id == domain_id),
        "canonical_identities": _count(
            session,
            EventMapCanonicalIdentity,
            EventMapCanonicalIdentity.playlist_id == domain_id,
        ),
        "canonical_history_revisions": _count(
            session,
            EventMapCanonicalHistoryRevision,
            EventMapCanonicalHistoryRevision.playlist_id == domain_id,
        ),
        "story_identities": _count(
            session,
            EventMapStoryIdentity,
            EventMapStoryIdentity.playlist_id == domain_id,
        ),
        "story_history_revisions": _count(
            session,
            EventMapStoryHistoryRevision,
            EventMapStoryHistoryRevision.playlist_id == domain_id,
        ),
        "observation_cursor": _count(
            session,
            DomainObservationCursor,
            DomainObservationCursor.playlist_id == domain_id,
        ),
        "active_jobs": len(jobs),
    }
    return {
        "domain_id": str(domain_id),
        "name": playlist.name,
        "counts": counts,
        "active_jobs": [
            {"id": str(job.id), "type": job.type, "status": job.status}
            for job in jobs
        ],
        "retained_shared_data": {
            "sources": len(media_ids),
            "videos": shared_video_count,
            "assets": shared_asset_count,
            "message": "共享信源、来源记录和播放资产不会随观测域删除。",
        },
    }


def delete_domain(
    session: Session,
    *,
    domain_id: uuid.UUID,
    confirm_name: str,
) -> dict[str, Any]:
    playlist = session.get(Playlist, domain_id)
    if playlist is None:
        raise LookupError("domain not found")
    if str(confirm_name or "") != str(playlist.name or ""):
        raise ValueError("confirm_name does not match the complete domain name")

    return delete_domain_record(session, domain_id=domain_id)


def delete_domain_record(session: Session, *, domain_id: uuid.UUID) -> dict[str, Any]:
    """供新域 API 与旧 Playlist 兼容 API 共用的删除实现。"""

    playlist = session.get(Playlist, domain_id)
    if playlist is None:
        raise LookupError("domain not found")

    jobs = active_domain_jobs(session, domain_id)
    if jobs:
        raise RuntimeError("active domain jobs exist")

    impact = domain_deletion_impact(session, domain_id)
    session.delete(playlist)
    return impact
