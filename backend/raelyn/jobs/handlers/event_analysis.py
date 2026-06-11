from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from raelyn.jobs.registry import registry
from raelyn.models import Job, Playlist, Video
from raelyn.services.event_analysis import (
    backfill_playlist_events,
    backfill_playlist_events_range,
    build_event_regime_snapshot,
    embed_event,
    ensure_event_regime_state,
    extract_video_events_batch,
    extract_video_events,
    mark_playlist_event_regime_dirty_if_needed,
    _parse_event_backfill_range_date,
)


@registry.register("video.extract_events")
def video_extract_events(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(str(job.params["video_id"]))
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}
    return extract_video_events(session, video_id=video_id, force=bool(job.params.get("force", False)), job=job)


@registry.register("video.extract_events_batch")
def video_extract_events_batch(session: Session, job: Job) -> dict | None:
    raw_video_ids = job.params.get("video_ids")
    if not isinstance(raw_video_ids, list):
        return {"skipped": "video_ids must be list"}
    video_ids: list[uuid.UUID] = []
    for raw in raw_video_ids:
        try:
            video_ids.append(uuid.UUID(str(raw)))
        except Exception:
            continue
    if not video_ids:
        return {"skipped": "no valid video ids"}
    return extract_video_events_batch(
        session,
        video_ids=video_ids,
        force=bool(job.params.get("force", False)),
        job=job,
    )


@registry.register("playlist.backfill_events")
def playlist_backfill_events(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    return backfill_playlist_events(
        session,
        playlist_id=playlist_id,
        force=bool(job.params.get("force", False)),
        job=job,
    )


@registry.register("playlist.backfill_events_range")
def playlist_backfill_events_range(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    return backfill_playlist_events_range(
        session,
        playlist_id=playlist_id,
        range_start=_parse_event_backfill_range_date(job.params.get("range_start"), name="range_start"),
        range_end=_parse_event_backfill_range_date(job.params.get("range_end"), name="range_end"),
        force=bool(job.params.get("force", False)),
        job=job,
    )


@registry.register("event.embed")
def event_embed(session: Session, job: Job) -> dict | None:
    event_id = uuid.UUID(str(job.params["event_id"]))
    return embed_event(session, event_id=event_id)


@registry.register("playlist.mark_event_regime_dirty")
def playlist_mark_event_regime_dirty(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    return mark_playlist_event_regime_dirty_if_needed(session, playlist_id)


@registry.register("playlist.build_event_regime_snapshot")
def playlist_build_event_regime_snapshot(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    ensure_event_regime_state(session, playlist_id)
    return build_event_regime_snapshot(session, playlist_id=playlist_id, job=job)
