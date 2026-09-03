from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import MarketEvent, Playlist, PlaylistMedia, Video


def enabled_observation_video_ids(
    session: Session,
    video_ids: Iterable[uuid.UUID],
) -> set[uuid.UUID]:
    ids = list(dict.fromkeys(video_ids))
    if not ids:
        return set()
    return set(
        session.execute(
            select(Video.id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .join(Playlist, Playlist.id == PlaylistMedia.playlist_id)
            .where(
                Video.id.in_(ids),
                Playlist.observation_enabled.is_(True),
            )
            .distinct()
        ).scalars()
    )


def video_has_enabled_observation(session: Session, video_id: uuid.UUID) -> bool:
    return video_id in enabled_observation_video_ids(session, [video_id])


def event_has_enabled_observation(session: Session, event_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(MarketEvent.id)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .join(Playlist, Playlist.id == PlaylistMedia.playlist_id)
            .where(
                MarketEvent.id == event_id,
                Playlist.observation_enabled.is_(True),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def set_domain_observation_enabled(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    enabled: bool,
) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if playlist is None:
        raise LookupError("domain not found")

    next_enabled = bool(enabled)
    changed = bool(playlist.observation_enabled) != next_enabled
    playlist.observation_enabled = next_enabled
    session.flush([playlist])

    backfill_job_id: uuid.UUID | None = None
    dirty_job_id: uuid.UUID | None = None
    if changed and next_enabled:
        backfill_job_id = enqueue_job(
            session,
            type_="playlist.backfill_events",
            params={"playlist_id": str(playlist_id), "force": False},
            priority=1,
        )
        dirty_job_id = enqueue_job(
            session,
            type_="playlist.mark_event_map_dirty",
            params={
                "playlist_id": str(playlist_id),
                "reason": "domain_observation_resumed",
            },
            priority=1,
        )

    return {
        "ok": True,
        "playlist_id": str(playlist_id),
        "observation_enabled": next_enabled,
        "changed": changed,
        "backfill_job_id": str(backfill_job_id) if backfill_job_id else None,
        "dirty_job_id": str(dirty_job_id) if dirty_job_id else None,
    }
