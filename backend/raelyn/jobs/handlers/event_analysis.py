from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
from raelyn.jobs.registry import registry
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import EventMapState, Job, Playlist, Video
from raelyn.services.event_analysis import (
    backfill_playlist_events,
    backfill_playlist_events_range,
    build_event_map_snapshot,
    embed_event,
    extract_video_events_batch,
    extract_video_events,
    _parse_event_backfill_range_date,
)
from raelyn.services.event_map_retention import prune_event_map_snapshots
from raelyn.timeutil import utcnow


_EVENT_MAP_QUIET_SECONDS = 120
_EVENT_MAP_MAX_WAIT_SECONDS = 900


def _locked_event_map_state(session: Session, playlist_id: uuid.UUID) -> EventMapState:
    state = session.execute(
        select(EventMapState).where(EventMapState.playlist_id == playlist_id).with_for_update()
    ).scalar_one_or_none()
    if state is not None:
        return state
    try:
        with session.begin_nested():
            state = EventMapState(playlist_id=playlist_id)
            session.add(state)
            session.flush([state])
        return state
    except IntegrityError:
        return session.execute(
            select(EventMapState).where(EventMapState.playlist_id == playlist_id).with_for_update()
        ).scalar_one()


def _event_map_build_scheduled_for(state: EventMapState, now: datetime) -> datetime:
    first_dirty_at = state.first_dirty_at or now
    last_dirty_at = state.last_dirty_at or now
    quiet_until = last_dirty_at + timedelta(seconds=_EVENT_MAP_QUIET_SECONDS)
    max_wait_until = first_dirty_at + timedelta(seconds=_EVENT_MAP_MAX_WAIT_SECONDS)
    return min(quiet_until, max_wait_until)


def _enqueue_dirty_event_map_build(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    state: EventMapState,
    now: datetime,
) -> uuid.UUID:
    scheduled_for = _event_map_build_scheduled_for(state, now)
    job_id = enqueue_job(
        session,
        type_="playlist.build_event_map_snapshot",
        params={
            "playlist_id": str(playlist_id),
            "trigger": "dirty",
            "requested_generation": int(state.dirty_generation or 0),
        },
        scheduled_for=scheduled_for,
    )
    build_job = session.get(Job, job_id)
    if (
        build_job is not None
        and build_job.status == "pending"
        and str((build_job.params or {}).get("trigger") or "") == "dirty"
    ):
        build_job.scheduled_for = scheduled_for
        params = dict(build_job.params or {})
        params["requested_generation"] = int(state.dirty_generation or 0)
        build_job.params = params
    active_job = session.get(Job, state.active_job_id) if state.active_job_id else None
    if active_job is None or active_job.status not in {"pending", "running"}:
        state.active_job_id = job_id
    return job_id


def _pending_event_map_build_id(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    exclude_job_id: uuid.UUID,
) -> uuid.UUID | None:
    return session.execute(
        select(Job.id)
        .where(
            Job.type == "playlist.build_event_map_snapshot",
            Job.status == "pending",
            Job.id != exclude_job_id,
            Job.params["playlist_id"].as_string() == str(playlist_id),
        )
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(1)
    ).scalar_one_or_none()


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


@registry.register("playlist.mark_event_map_dirty")
def playlist_mark_event_map_dirty(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    now = utcnow()
    state = _locked_event_map_state(session, playlist_id)
    was_clean = int(state.dirty_generation or 0) <= int(state.built_generation or 0)
    state.dirty_generation = int(state.dirty_generation or 0) + 1
    if was_clean or state.first_dirty_at is None:
        state.first_dirty_at = now
    state.last_dirty_at = now
    state.last_requested_at = now
    state.last_error = None
    build_job_id = _enqueue_dirty_event_map_build(
        session,
        playlist_id=playlist_id,
        state=state,
        now=now,
    )
    return {
        "dirty_generation": int(state.dirty_generation),
        "build_job_id": str(build_job_id),
        "scheduled_for": _event_map_build_scheduled_for(state, now).isoformat(),
    }


@registry.register("playlist.build_event_map_snapshot")
def playlist_build_event_map_snapshot(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    state = _locked_event_map_state(session, playlist_id)
    trigger = str((job.params or {}).get("trigger") or "dirty").strip().lower()
    requested_generation = int((job.params or {}).get("requested_generation") or 0)
    if trigger != "manual" and int(state.dirty_generation or 0) <= int(state.built_generation or 0):
        if state.active_job_id == job.id:
            state.active_job_id = None
        return {
            "ok": True,
            "outcome": "skipped_generation_current",
            "playlist_id": str(playlist_id),
            "requested_generation": requested_generation,
            "dirty_generation": int(state.dirty_generation or 0),
            "built_generation": int(state.built_generation or 0),
        }

    now = utcnow()
    quiet_until = _event_map_build_scheduled_for(state, now)
    if trigger != "manual" and quiet_until > now:
        pending_job_id = _pending_event_map_build_id(
            session,
            playlist_id=playlist_id,
            exclude_job_id=job.id,
        )
        if pending_job_id is not None:
            state.active_job_id = pending_job_id
            return {
                "ok": True,
                "outcome": "superseded_by_pending_build",
                "playlist_id": str(playlist_id),
                "pending_job_id": str(pending_job_id),
                "scheduled_for": quiet_until.isoformat(),
            }
        delay_seconds = max(1, int((quiet_until - now).total_seconds() + 0.999))
        raise JobReschedule(delay_seconds=delay_seconds, reason="event_map_waiting_for_quiet_period")

    state.active_job_id = job.id
    state.last_requested_at = now
    session.flush([state])

    result = build_event_map_snapshot(session, playlist_id=playlist_id, job=job)

    if result.get("ok"):
        enqueue_job(
            session,
            type_="playlist.prune_event_map_snapshots",
            params={"playlist_id": str(playlist_id)},
        )

    state = _locked_event_map_state(session, playlist_id)
    if state.active_job_id == job.id:
        state.active_job_id = None
    if int(state.dirty_generation or 0) <= int(state.built_generation or 0):
        state.first_dirty_at = None
        state.last_dirty_at = None
    else:
        _enqueue_dirty_event_map_build(
            session,
            playlist_id=playlist_id,
            state=state,
            now=utcnow(),
        )
    return result


@registry.register("playlist.prune_event_map_snapshots")
def playlist_prune_event_map_snapshots(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    result = prune_event_map_snapshots(
        session,
        playlist_id=playlist_id,
        keep_ready=max(2, int(settings.event_map_ready_snapshot_retention or 2)),
    )
    if int(result["remaining_count"] or 0) > 0:
        enqueue_job(
            session,
            type_="playlist.prune_event_map_snapshots",
            params={"playlist_id": str(playlist_id)},
            scheduled_for=utcnow() + timedelta(seconds=2),
        )
    return result
