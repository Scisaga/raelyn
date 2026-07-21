from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import Date, case, cast, func, literal, or_, select

from raelyn.api.asset_refs import AssetRef, build_asset_ref
from raelyn.api.orm import OrmModel
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import (
    Asset,
    EventRegimeCandidate,
    EventRegimeRun,
    EventRegimeSignal,
    EventRegimeState,
    Media,
    MarketEvent,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
    Playlist,
    PlaylistMedia,
    Video,
    Job,
)
from raelyn.services.brief_schedule import schedule_brief_refresh_for_media_change
from raelyn.services.assets import replace_standalone_asset
from raelyn.services.periods import day_bounds_utc, local_date, normalize_granularity, period_bounds_utc, period_start
from raelyn.services.event_analysis import (
    active_event_regime_run,
    ensure_event_regime_state,
    event_filter_clause,
    mark_playlist_event_regime_dirty,
    pending_event_regime_job,
    playlist_event_coverage,
    playlist_event_regime_coverage,
    request_event_regime_rebuild,
    request_playlist_event_backfill,
    update_event_status,
)
from raelyn.services.video_admission import (
    ensure_video_published_at_backfilled,
    video_has_playback_asset_expr,
)
from raelyn.services.video_time import (
    normalize_time_basis,
    selected_content_time_subquery,
)
from raelyn.timeutil import utcnow


router = APIRouter(tags=["playlists"])

_PLAYLIST_IMG_MAX_BYTES = 2 * 1024 * 1024
class PlaylistCreate(BaseModel):
    name: str
    description: str | None = None
    media_ids: list[uuid.UUID] = []


class PlaylistUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    brief_granularity: str | None = None  # day | week | month
    brief_prompt: str | None = None  # null/empty => use default


class PlaylistMediaOut(BaseModel):
    id: uuid.UUID
    provider: str
    url: str
    name: str | None = None
    avatar_asset: AssetRef | None = None


class PlaylistOut(OrmModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    avatar_asset: AssetRef | None = None
    background_asset: AssetRef | None = None
    brief_granularity: str = "day"
    media_count: int | None = None
    media_preview: list[PlaylistMediaOut] = Field(default_factory=list)
    video_count: int | None = None
    latest_video_at: Any | None = None
    earliest_date: date | None = None
    latest_date: date | None = None
    created_at: Any
    updated_at: Any


class PlaylistDetailOut(PlaylistOut):
    brief_prompt: str | None = None
    media: list[PlaylistMediaOut] = []


class PlaylistMediaAdd(BaseModel):
    media_id: uuid.UUID


class PlaylistMediaReplace(BaseModel):
    media_ids: list[uuid.UUID]


class PlaylistEventBackfillJobOut(BaseModel):
    job_id: uuid.UUID
    status: str
    progress_current: int | None = None
    progress_total: int | None = None
    created_at: Any
    started_at: Any | None = None
    cancel_requested_at: Any | None = None
    scanned: int = 0
    enqueued: int = 0
    skipped: int = 0
    force: bool = False
    range_finished: int = 0
    range_pending: int = 0
    range_running: int = 0
    range_failed: int = 0
    range_total: int = 0
    video_extracted: int = 0
    video_pending: int = 0
    video_running: int = 0
    video_failed: int = 0
    video_total: int = 0
    elapsed_seconds: int | None = None
    estimated_total_seconds: int | None = None


class PlaylistEventsSummaryOut(BaseModel):
    playlist_id: uuid.UUID
    video_total: int = 0
    video_with_events: int = 0
    event_total: int = 0
    accepted: int = 0
    draft: int = 0
    rejected: int = 0
    failed: int = 0
    coverage_ratio: float = 0.0
    backfill_job: PlaylistEventBackfillJobOut | None = None


class PlaylistEventsExtractRequest(BaseModel):
    force: bool = False


class MarketEventEntityOut(BaseModel):
    id: uuid.UUID
    entity_type: str
    name: str
    normalized_key: str
    role: str | None = None
    confidence: float | None = None


class PlaylistEventEntitySuggestionOut(BaseModel):
    entity_type: str
    name: str
    normalized_key: str
    count: int = 0


class MarketEventEvidenceOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    evidence_text: str | None = None
    evidence_json: dict[str, Any] | None = None
    confidence: float | None = None
    source_id: str | None = None
    source_kind: str | None = None
    source_label: str | None = None
    verified: bool = False


class MarketEventRelationOut(BaseModel):
    id: uuid.UUID
    source_entity_id: uuid.UUID | None = None
    target_entity_id: uuid.UUID | None = None
    relation_type: str
    direction: str | None = None
    magnitude: dict[str, Any] | None = None
    confidence: float | None = None
    evidence_text: str | None = None


class MarketEventOut(BaseModel):
    id: uuid.UUID
    event_time_start: Any | None = None
    event_time_end: Any | None = None
    time_precision: str
    available_at: Any | None = None
    event_type: str
    title: str | None = None
    summary: str | None = None
    direction: str | None = None
    magnitude: dict[str, Any] | None = None
    surprise_or_delta: dict[str, Any] | None = None
    confidence: float | None = None
    status: str
    source_video_id: uuid.UUID
    transcript_asset_id: uuid.UUID | None = None
    extraction_model: str | None = None
    prompt_version: str | None = None
    source_hash: str
    created_at: Any
    updated_at: Any
    evidence_count: int = 0
    provenance_status: str = "missing"
    source_video_title: str | None = None
    source_media_name: str | None = None
    entities: list[MarketEventEntityOut] = Field(default_factory=list)


class MarketEventDetailOut(MarketEventOut):
    evidence: list[MarketEventEvidenceOut] = Field(default_factory=list)
    relations: list[MarketEventRelationOut] = Field(default_factory=list)
    raw_payload: dict[str, Any] | None = None


class MarketEventPatch(BaseModel):
    status: str | None = None


class EventRegimeSummaryOut(BaseModel):
    playlist_id: uuid.UUID
    analysis_dirty: bool
    running: bool
    backfill_job: PlaylistEventBackfillJobOut | None = None
    active_run_id: uuid.UUID | None = None
    last_ready_run_id: uuid.UUID | None = None
    last_requested_at: Any | None = None
    last_built_at: Any | None = None
    last_error: str | None = None
    event_total: int = 0
    event_embedded: int = 0
    event_eligible: int = 0
    event_scale_excluded: int = 0
    event_skipped: int = 0
    event_failed: int = 0
    candidate_count: int = 0
    signal_start_date: date | None = None
    signal_end_date: date | None = None


class EventRegimeSignalOut(BaseModel):
    id: uuid.UUID
    granularity: str
    period_date: date
    rolling_window: int
    event_count: int
    ready_embedding_count: int
    drift_score: float | None = None
    drift_rolling_mean: float | None = None
    drift_rolling_std: float | None = None
    drift_rolling_z: float | None = None
    dispersion_mean: float | None = None
    dispersion_std: float | None = None
    dispersion_p25: float | None = None
    dispersion_p75: float | None = None
    projection_id: str | None = None
    projection_method: str | None = None
    projection_x: float | None = None
    projection_y: float | None = None
    projection_z: float | None = None
    projection_explained_variance_ratio: list[float] | None = None
    linked_candidate_id: uuid.UUID | None = None


class EventRegimeCandidateOut(BaseModel):
    id: uuid.UUID
    candidate_date: date
    effective_trade_date: date
    peak_date: date | None = None
    event_start: date | None = None
    event_end: date | None = None
    event_type: str = "burst"
    status: str
    score: float
    confidence: float | None = None
    uncertainty: float | None = None
    drift_score: float | None = None
    dispersion_score: float | None = None
    drift_rolling_z: float | None = None
    breakpoint_date: date | None = None
    detection_method: str = ""
    detection_granularity: str = ""
    boundary_score: float | None = None
    boundary_z: float | None = None
    before_start: date | None = None
    before_end: date | None = None
    after_start: date | None = None
    after_end: date | None = None
    supporting_granularities: list[str] = Field(default_factory=list)
    summary: str | None = None
    top_terms: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    evidence_video_ids: list[str] = Field(default_factory=list)
    evidence_preview: str = ""
    available_at: Any | None = None


class EventRegimeCandidateDetailOut(EventRegimeCandidateOut):
    evidence: dict[str, Any] = Field(default_factory=dict)


class EventRegimeCandidatePatch(BaseModel):
    status: str | None = None
    candidate_date: date | None = None
    effective_trade_date: date | None = None
    event_type: str | None = None


def _media_avatar_asset(session, m: Any) -> AssetRef | None:
    asset_id = getattr(m, "avatar_asset_id", None)
    return build_asset_ref(session.get(Asset, asset_id)) if asset_id else None


def _media_avatar_asset_map(session, media_items: list[Any]) -> dict[uuid.UUID, AssetRef | None]:
    asset_ids = {getattr(m, "avatar_asset_id", None) for m in media_items if getattr(m, "avatar_asset_id", None)}
    assets_by_id: dict[uuid.UUID, Asset] = {}
    if asset_ids:
        assets_by_id = {
            asset.id: asset
            for asset in session.execute(select(Asset).where(Asset.id.in_(list(asset_ids)))).scalars().all()
        }
    return {
        m.id: build_asset_ref(assets_by_id.get(getattr(m, "avatar_asset_id", None)))
        if getattr(m, "avatar_asset_id", None)
        else None
        for m in media_items
    }


@dataclass(frozen=True)
class _PlaylistTimelineColumns:
    selected_content_time: Any | None
    timeline_at: Any
    content_published_at: Any
    time_source: Any
    time_status: Any
    time_confidence: Any


def _playlist_timeline_columns(time_basis: str | None = "content", *, name: str = "playlist_selected_content_time") -> _PlaylistTimelineColumns:
    basis = normalize_time_basis(time_basis)
    if basis == "platform":
        return _PlaylistTimelineColumns(
            selected_content_time=None,
            timeline_at=Video.published_at,
            content_published_at=literal(None),
            time_source=case((Video.published_at.is_(None), None), else_=literal("video.published_at")),
            time_status=case((Video.published_at.is_(None), None), else_=literal("platform")),
            time_confidence=literal(None),
        )

    selected = selected_content_time_subquery(name)
    timeline_at = func.coalesce(selected.c.content_published_at, Video.published_at)
    return _PlaylistTimelineColumns(
        selected_content_time=selected,
        timeline_at=timeline_at,
        content_published_at=selected.c.content_published_at,
        time_source=case(
            (timeline_at.is_(None), None),
            else_=func.coalesce(selected.c.time_source, literal("video.published_at")),
        ),
        time_status=case(
            (timeline_at.is_(None), None),
            else_=func.coalesce(selected.c.time_status, literal("platform_fallback")),
        ),
        time_confidence=selected.c.time_confidence,
    )


def _join_playlist_timeline(stmt: Any, columns: _PlaylistTimelineColumns):
    if columns.selected_content_time is None:
        return stmt
    return stmt.outerjoin(columns.selected_content_time, columns.selected_content_time.c.video_id == Video.id)


def _playlist_playback_clauses(columns: _PlaylistTimelineColumns) -> tuple[Any, Any]:
    return (columns.timeline_at.is_not(None), video_has_playback_asset_expr())


def _playlist_video_stats(session, playlist_id: uuid.UUID) -> tuple[int, Any | None, Any | None]:
    columns = _playlist_timeline_columns("content", name="playlist_stats_content_time")
    stmt = (
        select(func.count(Video.id), func.min(columns.timeline_at), func.max(columns.timeline_at))
        .select_from(Video)
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
    )
    stmt = _join_playlist_timeline(stmt, columns).where(
        PlaylistMedia.playlist_id == playlist_id,
        *_playlist_playback_clauses(columns),
    )
    count, min_ts, max_ts = session.execute(stmt).one()
    return int(count or 0), min_ts, max_ts


def _playlist_out(session, p: Playlist, *, preview: list[PlaylistMediaOut] | None = None) -> PlaylistOut:
    _ensure_playlist_published_at_backfilled(session)
    out = PlaylistOut.model_validate(p)
    out.brief_granularity = (getattr(p, "brief_granularity", None) or "day").strip() or "day"
    out.avatar_asset = build_asset_ref(session.get(Asset, getattr(p, "avatar_asset_id", None))) if getattr(p, "avatar_asset_id", None) else None
    out.background_asset = (
        build_asset_ref(session.get(Asset, getattr(p, "background_asset_id", None))) if getattr(p, "background_asset_id", None) else None
    )

    out.media_count = int(
        session.execute(
            select(func.count(PlaylistMedia.media_id)).where(PlaylistMedia.playlist_id == p.id)
        ).scalar_one()
        or 0
    )
    if preview is not None:
        out.media_preview = preview
    else:
        # Default: best-effort preview for single playlist.
        rows = (
            session.execute(
                select(Media)
                .join(PlaylistMedia, PlaylistMedia.media_id == Media.id)
                .where(PlaylistMedia.playlist_id == p.id)
                .order_by(PlaylistMedia.added_at.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )
        out.media_preview = [
            PlaylistMediaOut(
                id=m.id,
                provider=m.provider,
                url=m.url,
                name=m.name,
                avatar_asset=_media_avatar_asset(session, m),
            )
            for m in rows
        ]
    if out.media_count:
        vcnt, min_ts, max_ts = _playlist_video_stats(session, p.id)
        out.video_count = int(vcnt or 0)
        out.latest_video_at = max_ts
        out.earliest_date = local_date(min_ts)
        out.latest_date = local_date(max_ts)
    else:
        out.video_count = 0
        out.latest_video_at = None
        out.earliest_date = None
        out.latest_date = None
    return out


def _active_playlist_event_backfill_job(session, playlist_id: uuid.UUID) -> Job | None:
    active_statuses = ["pending", "running"]
    jobs = (
        session.execute(
            select(Job)
            .where(Job.type.in_(["playlist.backfill_events", "playlist.backfill_events_range"]), Job.status.in_(active_statuses))
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    for job in jobs:
        params = job.params if isinstance(job.params, dict) else {}
        if str(params.get("playlist_id") or "") == str(playlist_id):
            return job

    video_jobs = (
        session.execute(
            select(Job)
            .where(
                Job.type.in_(["video.extract_events", "video.extract_events_batch"]),
                Job.status.in_(active_statuses),
                Job.parent_job_id.is_not(None),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    for job in video_jobs:
        parent_job = session.get(Job, job.parent_job_id)
        if not parent_job or parent_job.type != "playlist.backfill_events_range":
            continue
        params = parent_job.params if isinstance(parent_job.params, dict) else {}
        if str(params.get("playlist_id") or "") == str(playlist_id):
            return job
    return None


def _playlist_event_period_bounds(period: date | None, granularity: str | None) -> tuple[Any | None, Any | None]:
    if period is None:
        return None, None
    try:
        normalized = normalize_granularity(granularity or "day")
        pstart = period_start(period, normalized)
        return period_bounds_utc(pstart, normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _playlist_events_summary(
    session,
    playlist_id: uuid.UUID,
    *,
    available_start: Any | None = None,
    available_end: Any | None = None,
) -> PlaylistEventsSummaryOut:
    coverage = playlist_event_coverage(session, playlist_id, available_start=available_start, available_end=available_end)
    backfill_job = _active_playlist_event_backfill_job(session, playlist_id)
    return PlaylistEventsSummaryOut(
        playlist_id=playlist_id,
        video_total=int(coverage.get("video_total") or 0),
        video_with_events=int(coverage.get("video_with_events") or 0),
        event_total=int(coverage.get("event_total") or 0),
        accepted=int(coverage.get("accepted") or 0),
        draft=int(coverage.get("draft") or 0),
        rejected=int(coverage.get("rejected") or 0),
        failed=0,
        coverage_ratio=float(coverage.get("coverage_ratio") or 0.0),
        backfill_job=_playlist_event_backfill_job_out(session, backfill_job) if backfill_job else None,
    )


def _playlist_event_backfill_progress(session, job: Job) -> dict[str, int | None]:
    range_job_ids: list[uuid.UUID] = []
    elapsed_job = job
    parent_job: Job | None = job if job.type == "playlist.backfill_events" else None
    if job.type == "playlist.backfill_events":
        range_job_ids = (
            session.execute(
                select(Job.id).where(
                    Job.parent_job_id == job.id,
                    Job.type == "playlist.backfill_events_range",
                )
            )
            .scalars()
            .all()
        )
    elif job.type == "playlist.backfill_events_range":
        parent_job = session.get(Job, job.parent_job_id) if job.parent_job_id else None
        if parent_job:
            elapsed_job = parent_job
            range_job_ids = (
                session.execute(
                    select(Job.id).where(
                        Job.parent_job_id == parent_job.id,
                        Job.type == "playlist.backfill_events_range",
                    )
                )
                .scalars()
                .all()
            )
        if not range_job_ids:
            range_job_ids = [job.id]
    elif job.type in {"video.extract_events", "video.extract_events_batch"}:
        range_job = session.get(Job, job.parent_job_id) if job.parent_job_id else None
        if range_job and range_job.type == "playlist.backfill_events_range":
            parent_job = session.get(Job, range_job.parent_job_id) if range_job.parent_job_id else None
            if parent_job:
                elapsed_job = parent_job
                range_job_ids = (
                    session.execute(
                        select(Job.id).where(
                            Job.parent_job_id == parent_job.id,
                            Job.type == "playlist.backfill_events_range",
                        )
                    )
                    .scalars()
                    .all()
                )
            if not range_job_ids:
                range_job_ids = [range_job.id]

    result = job.result if isinstance(job.result, dict) else {}
    progress: dict[str, int | None] = {
        "scanned": int(result.get("scanned") or 0),
        "enqueued": int(result.get("enqueued") or 0),
        "skipped": int(result.get("skipped") or 0),
        "range_finished": 0,
        "range_pending": 0,
        "range_running": 0,
        "range_failed": 0,
        "range_total": 0,
        "video_extracted": 0,
        "video_pending": 0,
        "video_running": 0,
        "video_failed": 0,
        "video_total": 0,
        "elapsed_seconds": None,
        "estimated_total_seconds": None,
    }

    if not range_job_ids:
        planned_total = int(getattr(parent_job, "progress_total", None) or 0) if parent_job else 0
        if planned_total > 0:
            progress["range_pending"] = planned_total
            progress["range_total"] = planned_total
        return progress

    range_rows = (
        session.execute(
            select(Job.status, Job.result)
            .where(Job.type == "playlist.backfill_events_range", Job.id.in_(range_job_ids))
        )
        .all()
    )
    range_counts: dict[str, int] = {}
    scanned = enqueued = skipped = 0
    for status, range_result in range_rows:
        status_key = str(status or "").lower()
        range_counts[status_key] = range_counts.get(status_key, 0) + 1
        if isinstance(range_result, dict):
            scanned += int(range_result.get("scanned") or 0)
            enqueued += int(range_result.get("enqueued") or 0)
            skipped += int(range_result.get("skipped") or 0)

    known_range_total = sum(range_counts.values())
    planned_range_total = known_range_total
    if parent_job and parent_job.progress_total is not None:
        planned_range_total = max(planned_range_total, int(parent_job.progress_total or 0))
    missing_range_count = max(0, planned_range_total - known_range_total)
    progress.update(
        {
            "scanned": scanned,
            "enqueued": enqueued,
            "skipped": skipped,
            "range_finished": range_counts.get("succeeded", 0),
            "range_pending": range_counts.get("pending", 0) + missing_range_count,
            "range_running": range_counts.get("running", 0),
            "range_failed": range_counts.get("failed", 0),
            "range_total": planned_range_total,
        }
    )

    child_jobs = (
        session.execute(
            select(Job)
            .where(
                Job.type.in_(["video.extract_events", "video.extract_events_batch"]),
                Job.parent_job_id.in_(range_job_ids),
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for child in child_jobs:
        params = child.params if isinstance(child.params, dict) else {}
        video_count = 1
        if child.type == "video.extract_events_batch":
            raw_video_ids = params.get("video_ids")
            video_count = len(raw_video_ids) if isinstance(raw_video_ids, list) else 0
            result = child.result if isinstance(child.result, dict) else {}
            if child.status == "succeeded":
                video_count = int(result.get("videos") or result.get("cached_videos") or video_count or 0)
        if video_count <= 0:
            video_count = 1
        status_key = str(child.status or "").lower()
        counts[status_key] = counts.get(status_key, 0) + video_count
    extracted = counts.get("succeeded", 0)
    pending = counts.get("pending", 0)
    running = counts.get("running", 0)
    failed = counts.get("failed", 0)
    canceled = counts.get("canceled", 0)
    total = sum(counts.values())

    started_at = elapsed_job.started_at or elapsed_job.created_at
    elapsed_seconds = None
    estimated_total_seconds = None
    if started_at:
        elapsed_seconds = max(0, int((utcnow() - started_at).total_seconds()))
        finished = extracted + failed + canceled
        estimated_total_seconds = _playlist_event_backfill_estimated_total_seconds(
            elapsed_seconds=elapsed_seconds,
            range_finished=int(progress.get("range_finished") or 0),
            range_failed=int(progress.get("range_failed") or 0),
            range_total=int(progress.get("range_total") or 0),
            video_finished=finished,
            video_total=total,
        )

    progress.update(
        {
            "video_extracted": extracted,
            "video_pending": pending,
            "video_running": running,
            "video_failed": failed,
            "video_total": total,
            "elapsed_seconds": elapsed_seconds,
            "estimated_total_seconds": estimated_total_seconds,
        }
    )
    return progress


def _playlist_event_backfill_estimated_total_seconds(
    *,
    elapsed_seconds: int | None,
    range_finished: int,
    range_failed: int,
    range_total: int,
    video_finished: int,
    video_total: int,
) -> int | None:
    if elapsed_seconds is None:
        return None

    elapsed = max(0, int(elapsed_seconds))
    known_video_total = max(0, int(video_total))
    completed_videos = max(0, int(video_finished))
    planned_ranges = max(0, int(range_total))
    succeeded_ranges = max(0, int(range_finished))
    terminal_ranges = max(0, int(range_finished)) + max(0, int(range_failed))
    if planned_ranges > 0:
        terminal_ranges = min(planned_ranges, terminal_ranges)
    remaining_ranges = max(0, planned_ranges - terminal_ranges)

    if remaining_ranges <= 0:
        if known_video_total > 0 and completed_videos > 0:
            return max(elapsed, int(round(elapsed * known_video_total / completed_videos)))
        if planned_ranges > 0 and terminal_ranges > 0:
            return elapsed
        return None

    if known_video_total > 0:
        if completed_videos <= 0 or succeeded_ranges <= 0:
            return None
        avg_videos_per_succeeded_range = known_video_total / succeeded_ranges
    elif terminal_ranges > 0:
        avg_videos_per_succeeded_range = 0.0
    else:
        return None

    estimated_video_total = known_video_total + remaining_ranges * avg_videos_per_succeeded_range
    estimated_total_work = planned_ranges + estimated_video_total
    completed_work = terminal_ranges + completed_videos
    if completed_work <= 0:
        return None
    return max(elapsed, int(round(elapsed * estimated_total_work / completed_work)))


def _playlist_event_backfill_job_out(session, job: Job) -> PlaylistEventBackfillJobOut:
    result = job.result if isinstance(job.result, dict) else {}
    progress = _playlist_event_backfill_progress(session, job)
    return PlaylistEventBackfillJobOut(
        job_id=job.id,
        status=str(job.status or ""),
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        created_at=job.created_at,
        started_at=job.started_at,
        cancel_requested_at=job.cancel_requested_at,
        scanned=int(progress.get("scanned") or result.get("scanned") or 0),
        enqueued=int(progress.get("enqueued") or result.get("enqueued") or 0),
        skipped=int(progress.get("skipped") or result.get("skipped") or 0),
        force=bool(result.get("force") or (job.params or {}).get("force")),
        range_finished=int(progress.get("range_finished") or 0),
        range_pending=int(progress.get("range_pending") or 0),
        range_running=int(progress.get("range_running") or 0),
        range_failed=int(progress.get("range_failed") or 0),
        range_total=int(progress.get("range_total") or 0),
        video_extracted=int(progress.get("video_extracted") or 0),
        video_pending=int(progress.get("video_pending") or 0),
        video_running=int(progress.get("video_running") or 0),
        video_failed=int(progress.get("video_failed") or 0),
        video_total=int(progress.get("video_total") or 0),
        elapsed_seconds=progress.get("elapsed_seconds"),
        estimated_total_seconds=progress.get("estimated_total_seconds"),
    )


def _playlist_last_ready_regime_run_id(session, playlist_id: uuid.UUID) -> uuid.UUID | None:
    state = ensure_event_regime_state(session, playlist_id)
    return state.last_ready_run_id


def _event_entities(session, event_id: uuid.UUID) -> list[MarketEventEntityOut]:
    rows = (
        session.execute(
            select(MarketEventEntity)
            .where(MarketEventEntity.event_id == event_id)
            .order_by(MarketEventEntity.entity_type.asc(), MarketEventEntity.name.asc(), MarketEventEntity.id.asc())
        )
        .scalars()
        .all()
    )
    return [
        MarketEventEntityOut(
            id=row.id,
            entity_type=row.entity_type,
            name=row.name,
            normalized_key=row.normalized_key,
            role=row.role,
            confidence=row.confidence,
        )
        for row in rows
    ]


def _event_evidence_rows(session, event_id: uuid.UUID) -> list[MarketEventEvidence]:
    return (
        session.execute(
            select(MarketEventEvidence)
            .where(MarketEventEvidence.event_id == event_id)
            .order_by(MarketEventEvidence.created_at.asc(), MarketEventEvidence.id.asc())
        )
        .scalars()
        .all()
    )


def _event_provenance_status(evidence_rows: list[MarketEventEvidence]) -> str:
    if not evidence_rows:
        return "missing"
    for row in evidence_rows:
        payload = row.evidence_json if isinstance(row.evidence_json, dict) else {}
        if payload.get("schema_version") == "event_provenance_v1" and payload.get("verified") is True:
            return "verified"
    return "unverified"


def _event_source_labels(session, event: MarketEvent) -> tuple[str | None, str | None]:
    video = session.get(Video, event.source_video_id)
    media = session.get(Media, video.media_id) if video and video.media_id else None
    return (video.title if video else None, media.name if media else None)


def _event_evidence_out(row: MarketEventEvidence) -> MarketEventEvidenceOut:
    payload = row.evidence_json if isinstance(row.evidence_json, dict) else {}
    return MarketEventEvidenceOut(
        id=row.id,
        video_id=row.video_id,
        evidence_text=row.evidence_text,
        evidence_json=row.evidence_json,
        confidence=row.confidence,
        source_id=str(payload.get("source_id") or "") or None,
        source_kind=str(payload.get("source_kind") or "") or None,
        source_label=str(payload.get("source_label") or "") or None,
        verified=bool(payload.get("verified") is True),
    )


def _event_out(session, event: MarketEvent, *, include_entities: bool = True) -> MarketEventOut:
    evidence_rows = _event_evidence_rows(session, event.id)
    source_video_title, source_media_name = _event_source_labels(session, event)
    return MarketEventOut(
        id=event.id,
        event_time_start=event.event_time_start,
        event_time_end=event.event_time_end,
        time_precision=event.time_precision,
        available_at=event.available_at,
        event_type=event.event_type,
        title=event.title,
        summary=event.summary,
        direction=event.direction,
        magnitude=event.magnitude,
        surprise_or_delta=event.surprise_or_delta,
        confidence=event.confidence,
        status=event.status,
        source_video_id=event.source_video_id,
        transcript_asset_id=event.transcript_asset_id,
        extraction_model=event.extraction_model,
        prompt_version=event.prompt_version,
        source_hash=event.source_hash,
        created_at=event.created_at,
        updated_at=event.updated_at,
        evidence_count=len(evidence_rows),
        provenance_status=_event_provenance_status(evidence_rows),
        source_video_title=source_video_title,
        source_media_name=source_media_name,
        entities=_event_entities(session, event.id) if include_entities else [],
    )


def _event_detail_out(session, event: MarketEvent) -> MarketEventDetailOut:
    base = _event_out(session, event).model_dump()
    evidence_rows = _event_evidence_rows(session, event.id)
    relation_rows = (
        session.execute(
            select(MarketEventRelation)
            .where(MarketEventRelation.event_id == event.id)
            .order_by(MarketEventRelation.created_at.asc(), MarketEventRelation.id.asc())
        )
        .scalars()
        .all()
    )
    return MarketEventDetailOut(
        **base,
        evidence=[_event_evidence_out(row) for row in evidence_rows],
        relations=[
            MarketEventRelationOut(
                id=row.id,
                source_entity_id=row.source_entity_id,
                target_entity_id=row.target_entity_id,
                relation_type=row.relation_type,
                direction=row.direction,
                magnitude=row.magnitude,
                confidence=row.confidence,
                evidence_text=row.evidence_text,
            )
            for row in relation_rows
        ],
        raw_payload=event.raw_payload,
    )


def _event_belongs_to_playlist(session, *, playlist_id: uuid.UUID, event_id: uuid.UUID) -> MarketEvent | None:
    return (
        session.execute(
            select(MarketEvent)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(PlaylistMedia.playlist_id == playlist_id, MarketEvent.id == event_id)
            .limit(1)
        )
        .scalars()
        .first()
    )


def _event_regime_summary(session, playlist_id: uuid.UUID) -> EventRegimeSummaryOut:
    state = ensure_event_regime_state(session, playlist_id)
    active_run = active_event_regime_run(session, playlist_id)
    pending_job = pending_event_regime_job(session, playlist_id)
    backfill_job = _active_playlist_event_backfill_job(session, playlist_id)
    last_ready_run = session.get(EventRegimeRun, state.last_ready_run_id) if state.last_ready_run_id else None
    coverage = playlist_event_regime_coverage(session, playlist_id)
    candidate_count = 0
    signal_start_date = None
    signal_end_date = None
    if last_ready_run:
        candidate_count = int(
            session.execute(
                select(func.count()).select_from(EventRegimeCandidate).where(EventRegimeCandidate.regime_run_id == last_ready_run.id)
            ).scalar_one()
            or 0
        )
        signal_start_date, signal_end_date = session.execute(
            select(func.min(EventRegimeSignal.period_date), func.max(EventRegimeSignal.period_date)).where(
                EventRegimeSignal.regime_run_id == last_ready_run.id,
            )
        ).one()
    return EventRegimeSummaryOut(
        playlist_id=playlist_id,
        analysis_dirty=bool(state.analysis_dirty),
        running=active_run is not None or pending_job is not None,
        backfill_job=_playlist_event_backfill_job_out(session, backfill_job) if backfill_job else None,
        active_run_id=getattr(active_run, "id", None),
        last_ready_run_id=state.last_ready_run_id,
        last_requested_at=state.last_requested_at,
        last_built_at=state.last_built_at,
        last_error=state.last_error,
        event_total=coverage["event_total"],
        event_embedded=coverage["event_embedded"],
        event_eligible=coverage["event_eligible"],
        event_scale_excluded=coverage["event_scale_excluded"],
        event_skipped=coverage["event_skipped"],
        event_failed=coverage["event_failed"],
        candidate_count=candidate_count,
        signal_start_date=signal_start_date,
        signal_end_date=signal_end_date,
    )


def _candidate_out(candidate: EventRegimeCandidate) -> EventRegimeCandidateOut:
    evidence = candidate.evidence_json if isinstance(candidate.evidence_json, dict) else {}
    top_terms = getattr(candidate, "top_terms", None) if isinstance(getattr(candidate, "top_terms", None), list) else []
    evidence_event_ids = getattr(candidate, "evidence_event_ids", None) if isinstance(getattr(candidate, "evidence_event_ids", None), list) else []
    evidence_video_ids = getattr(candidate, "evidence_video_ids", None) if isinstance(getattr(candidate, "evidence_video_ids", None), list) else []
    return EventRegimeCandidateOut(
        id=candidate.id,
        candidate_date=candidate.candidate_date,
        effective_trade_date=candidate.effective_trade_date,
        peak_date=getattr(candidate, "peak_date", None),
        event_start=getattr(candidate, "event_start", None),
        event_end=getattr(candidate, "event_end", None),
        event_type=str(getattr(candidate, "event_type", None) or "burst"),
        status=candidate.status,
        score=float(candidate.score or 0.0),
        confidence=getattr(candidate, "confidence", None),
        uncertainty=getattr(candidate, "uncertainty", None),
        drift_score=candidate.drift_score,
        dispersion_score=candidate.dispersion_score,
        drift_rolling_z=candidate.drift_rolling_z,
        breakpoint_date=candidate.peak_date,
        detection_method="event_embedding_regime_v1",
        detection_granularity=str(evidence.get("granularity") or "").strip(),
        boundary_score=candidate.score,
        boundary_z=candidate.drift_rolling_z,
        before_start=getattr(candidate, "train_start", None),
        before_end=getattr(candidate, "train_end", None),
        after_start=getattr(candidate, "valid_start", None),
        after_end=getattr(candidate, "valid_end", None),
        supporting_granularities=[
            str(item)
            for item in (
                evidence.get("supporting_granularities")
                if isinstance(evidence.get("supporting_granularities"), list)
                else ([evidence.get("granularity")] if evidence.get("granularity") else [])
            )
            if str(item).strip()
        ],
        summary=getattr(candidate, "summary", None),
        top_terms=[str(item) for item in top_terms],
        evidence_event_ids=[str(item) for item in evidence_event_ids],
        evidence_video_ids=[str(item) for item in evidence_video_ids],
        evidence_preview=str(evidence.get("preview") or "").strip(),
        available_at=getattr(candidate, "available_at", None),
    )


def _candidate_evidence_videos(session: Any, candidate: EventRegimeCandidate, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    raw_ids = getattr(candidate, "evidence_video_ids", None)
    if not isinstance(raw_ids, list) or not raw_ids:
        videos = evidence.get("videos")
        return [item for item in videos if isinstance(item, dict)] if isinstance(videos, list) else []

    video_ids: list[uuid.UUID] = []
    for raw_id in raw_ids:
        try:
            video_ids.append(uuid.UUID(str(raw_id)))
        except (TypeError, ValueError):
            continue
    if not video_ids:
        return []

    existing_videos = evidence.get("videos")
    existing_by_id = (
        {
            str(item.get("video_id")): item
            for item in existing_videos
            if isinstance(item, dict) and item.get("video_id")
        }
        if isinstance(existing_videos, list)
        else {}
    )

    rows = (
        session.execute(
            select(Video, Media)
            .join(Media, Media.id == Video.media_id)
            .where(Video.id.in_(video_ids))
        )
        .all()
    )
    by_id = {str(video.id): (video, media) for video, media in rows}
    out: list[dict[str, Any]] = []
    for raw_id in raw_ids:
        item = by_id.get(str(raw_id))
        if not item:
            continue
        video, media = item
        existing = existing_by_id.get(str(video.id), {})
        out.append(
            {
                "video_id": str(video.id),
                "title": video.title,
                "url": video.url,
                "published_at": video.published_at,
                "media_id": str(media.id),
                "media_name": media.name,
                "shift_score": existing.get("shift_score") if isinstance(existing, dict) else None,
            }
        )
    return out


def _candidate_detail_out(candidate: EventRegimeCandidate, session: Any | None = None) -> EventRegimeCandidateDetailOut:
    base = _candidate_out(candidate)
    evidence = dict(candidate.evidence_json) if isinstance(candidate.evidence_json, dict) else {}
    if session is not None:
        videos = _candidate_evidence_videos(session, candidate, evidence)
        if videos:
            evidence["videos"] = videos
    return EventRegimeCandidateDetailOut(
        **base.model_dump(),
        evidence=evidence,
    )


@router.post("/playlists", response_model=PlaylistOut)
def create_playlist(payload: PlaylistCreate) -> PlaylistOut:
    with session_scope() as session:
        playlist = Playlist(name=payload.name, description=payload.description)
        session.add(playlist)
        session.flush()
        media_ids = list(dict.fromkeys(payload.media_ids or []))
        if media_ids:
            items = session.execute(select(Media.id).where(Media.id.in_(list(media_ids)))).scalars().all()
            found = {uuid.UUID(str(x)) for x in items}
            missing = [str(x) for x in media_ids if x not in found]
            if missing:
                raise HTTPException(status_code=404, detail=f"media not found: {', '.join(missing)}")
            for mid in media_ids:
                session.add(PlaylistMedia(playlist_id=playlist.id, media_id=mid))
            mark_playlist_event_regime_dirty(session, playlist.id)

        schedule_brief_refresh_for_media_change(
            session,
            playlist_id=playlist.id,
            changed_media_ids=media_ids,
            change_type="media_added",
        )
        session.refresh(playlist)
        return _playlist_out(session, playlist)


@router.get("/playlists", response_model=list[PlaylistOut])
def list_playlists(limit: int = 100, offset: int = 0) -> list[PlaylistOut]:
    with session_scope() as session:
        _ensure_playlist_published_at_backfilled(session)
        stmt = select(Playlist).order_by(Playlist.updated_at.desc()).limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        playlist_ids = [p.id for p in items]
        preview_map: dict[uuid.UUID, list[PlaylistMediaOut]] = {pid: [] for pid in playlist_ids}
        media_count_map: dict[uuid.UUID, int] = {}
        video_stat_map: dict[uuid.UUID, dict[str, Any]] = {}
        asset_ids: set[uuid.UUID] = set()
        preview_rows: list[Any] = []

        if playlist_ids:
            for p in items:
                for asset_id in (getattr(p, "avatar_asset_id", None), getattr(p, "background_asset_id", None)):
                    if asset_id:
                        asset_ids.add(asset_id)

            # media counts
            for pid, cnt in session.execute(
                select(PlaylistMedia.playlist_id, func.count(PlaylistMedia.media_id))
                .where(PlaylistMedia.playlist_id.in_(playlist_ids))
                .group_by(PlaylistMedia.playlist_id)
            ).all():
                media_count_map[pid] = int(cnt or 0)

            # video counts + time range: playback list uses content timeline, with platform time fallback.
            timeline_columns = _playlist_timeline_columns("content", name="playlist_list_content_time")
            stat_stmt = (
                select(
                    PlaylistMedia.playlist_id,
                    func.count(Video.id),
                    func.min(timeline_columns.timeline_at),
                    func.max(timeline_columns.timeline_at),
                )
                .select_from(PlaylistMedia)
                .join(Video, Video.media_id == PlaylistMedia.media_id)
            )
            stat_stmt = _join_playlist_timeline(stat_stmt, timeline_columns).where(
                PlaylistMedia.playlist_id.in_(playlist_ids),
                *_playlist_playback_clauses(timeline_columns),
            )
            for pid, vcnt, min_ts, max_ts in session.execute(
                stat_stmt.group_by(PlaylistMedia.playlist_id)
            ).all():
                video_stat_map[pid] = {"video_count": int(vcnt or 0), "min_ts": min_ts, "max_ts": max_ts}

            # media preview (first 5 by added_at) - db-agnostic, no window funcs
            preview_rows = (
                session.execute(
                    select(
                        PlaylistMedia.playlist_id.label("playlist_id"),
                        Media.id.label("media_id"),
                        Media.provider.label("provider"),
                        Media.url.label("url"),
                        Media.name.label("name"),
                        Media.avatar_asset_id.label("avatar_asset_id"),
                    )
                    .join(Media, Media.id == PlaylistMedia.media_id)
                    .where(PlaylistMedia.playlist_id.in_(playlist_ids))
                    .order_by(PlaylistMedia.playlist_id.asc(), PlaylistMedia.added_at.desc())
                )
                .mappings()
                .all()
            )
            for r in preview_rows:
                pid = r["playlist_id"]
                cur = preview_map.get(pid)
                if cur is None:
                    cur = []
                    preview_map[pid] = cur
                if len(cur) >= 5:
                    continue
                avatar_asset_id = r["avatar_asset_id"]
                if avatar_asset_id:
                    asset_ids.add(avatar_asset_id)
                cur.append(
                    PlaylistMediaOut(
                        id=r["media_id"],
                        provider=r["provider"],
                        url=r["url"],
                        name=r["name"],
                        avatar_asset=None,
                    )
                )

        asset_by_id = {}
        if asset_ids:
            asset_by_id = {asset.id: asset for asset in session.execute(select(Asset).where(Asset.id.in_(list(asset_ids)))).scalars().all()}

        if asset_by_id:
            for r in preview_rows:
                avatar_asset_id = r["avatar_asset_id"]
                if not avatar_asset_id:
                    continue
                cur = preview_map.get(r["playlist_id"]) or []
                for item in cur:
                    if item.id == r["media_id"]:
                        item.avatar_asset = build_asset_ref(asset_by_id.get(avatar_asset_id))
                        break

        out_items: list[PlaylistOut] = []
        for p in items:
            out = PlaylistOut.model_validate(p)
            out.brief_granularity = (getattr(p, "brief_granularity", None) or "day").strip() or "day"
            avatar_asset_id = getattr(p, "avatar_asset_id", None)
            background_asset_id = getattr(p, "background_asset_id", None)
            out.avatar_asset = build_asset_ref(asset_by_id.get(avatar_asset_id)) if avatar_asset_id else None
            out.background_asset = build_asset_ref(asset_by_id.get(background_asset_id)) if background_asset_id else None
            out.media_preview = preview_map.get(p.id) or []
            out.media_count = int(media_count_map.get(p.id) or 0)

            stats = video_stat_map.get(p.id) or {}
            max_ts = stats.get("max_ts")
            min_ts = stats.get("min_ts")
            out.video_count = int(stats.get("video_count") or 0)
            out.latest_video_at = max_ts
            out.earliest_date = local_date(min_ts)
            out.latest_date = local_date(max_ts)
            out_items.append(out)
        return out_items


@router.get("/playlists/{playlist_id}", response_model=PlaylistOut)
def get_playlist(playlist_id: uuid.UUID) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        return _playlist_out(session, playlist)


@router.get("/playlists/{playlist_id}/detail", response_model=PlaylistDetailOut)
def get_playlist_detail(playlist_id: uuid.UUID) -> PlaylistDetailOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")

        out = PlaylistDetailOut.model_validate(_playlist_out(session, playlist).model_dump())
        out.brief_prompt = (getattr(playlist, "brief_prompt", None) or "").strip() or None
        rows = (
            session.execute(
                select(Media)
                .join(PlaylistMedia, PlaylistMedia.media_id == Media.id)
                .where(PlaylistMedia.playlist_id == playlist_id)
                .order_by(PlaylistMedia.added_at.desc())
            )
            .scalars()
            .all()
        )
        avatar_by_media_id = _media_avatar_asset_map(session, rows)
        media_out: list[PlaylistMediaOut] = []
        for m in rows:
            media_out.append(
                PlaylistMediaOut(
                    id=m.id,
                    provider=m.provider,
                    url=m.url,
                    name=m.name,
                    avatar_asset=avatar_by_media_id.get(m.id),
                )
            )
        out.media = media_out
        return out


@router.post("/playlists/{playlist_id}/events/extract")
def extract_playlist_events(
    playlist_id: uuid.UUID,
    payload: PlaylistEventsExtractRequest | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        data = payload or PlaylistEventsExtractRequest()
        job = request_playlist_event_backfill(session, playlist_id=playlist_id, force=bool(data.force), priority=1)
        return {
            "ok": True,
            "playlist_id": str(playlist_id),
            "job_id": str(job.id),
            "backfill_job": _playlist_event_backfill_job_out(session, job).model_dump(),
        }


@router.get("/playlists/{playlist_id}/events/summary", response_model=PlaylistEventsSummaryOut)
def get_playlist_events_summary(
    playlist_id: uuid.UUID,
    period: Annotated[date | None, Query(alias="period_start")] = None,
    granularity: str | None = "day",
) -> PlaylistEventsSummaryOut:
    available_start, available_end = _playlist_event_period_bounds(period, granularity)
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        return _playlist_events_summary(session, playlist_id, available_start=available_start, available_end=available_end)


@router.get("/playlists/{playlist_id}/events", response_model=list[MarketEventOut])
def list_playlist_events(
    playlist_id: uuid.UUID,
    status: str | None = None,
    event_type: str | None = None,
    entity: str | None = None,
    min_confidence: float | None = None,
    period: Annotated[date | None, Query(alias="period_start")] = None,
    granularity: str | None = "day",
    limit: int = 200,
    offset: int = 0,
) -> list[MarketEventOut]:
    available_start, available_end = _playlist_event_period_bounds(period, granularity)
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        clauses = event_filter_clause(
            playlist_id=playlist_id,
            status=status,
            event_type=event_type,
            entity=entity,
            min_confidence=min_confidence,
            available_start=available_start,
            available_end=available_end,
        )
        rows = (
            session.execute(
                select(MarketEvent)
                .join(Video, Video.id == MarketEvent.source_video_id)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(*clauses)
                .order_by(
                    MarketEvent.available_at.desc().nullslast(),
                    MarketEvent.event_time_start.desc().nullslast(),
                    MarketEvent.created_at.desc(),
                    MarketEvent.id.desc(),
                )
                .limit(max(1, min(1000, int(limit or 200))))
                .offset(max(0, int(offset or 0)))
            )
            .scalars()
            .all()
        )
        return [_event_out(session, row) for row in rows]


@router.get("/playlists/{playlist_id}/events/entities", response_model=list[PlaylistEventEntitySuggestionOut])
def list_playlist_event_entity_suggestions(
    playlist_id: uuid.UUID,
    q: str | None = None,
    status: str | None = None,
    period: Annotated[date | None, Query(alias="period_start")] = None,
    granularity: str | None = "day",
    limit: int = 20,
) -> list[PlaylistEventEntitySuggestionOut]:
    available_start, available_end = _playlist_event_period_bounds(period, granularity)
    query = str(q or "").strip()
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        clauses = event_filter_clause(
            playlist_id=playlist_id,
            status=status,
            available_start=available_start,
            available_end=available_end,
        )
        if query:
            normalized = query.lower().replace(" ", "_")
            clauses.append(
                or_(
                    MarketEventEntity.name.ilike(f"%{query}%"),
                    MarketEventEntity.normalized_key.ilike(f"%{normalized}%"),
                    MarketEventEntity.entity_type.ilike(f"%{query}%"),
                )
            )
        event_count = func.count(func.distinct(MarketEventEntity.event_id))
        rows = session.execute(
            select(
                MarketEventEntity.entity_type,
                MarketEventEntity.name,
                MarketEventEntity.normalized_key,
                event_count.label("count"),
            )
            .join(MarketEvent, MarketEvent.id == MarketEventEntity.event_id)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(*clauses)
            .group_by(MarketEventEntity.entity_type, MarketEventEntity.name, MarketEventEntity.normalized_key)
            .order_by(event_count.desc(), MarketEventEntity.entity_type.asc(), MarketEventEntity.name.asc())
            .limit(max(1, min(50, int(limit or 20))))
        ).all()
        return [
            PlaylistEventEntitySuggestionOut(
                entity_type=str(entity_type or "other"),
                name=str(name or ""),
                normalized_key=str(normalized_key or ""),
                count=int(count or 0),
            )
            for entity_type, name, normalized_key, count in rows
            if str(name or "").strip()
        ]


@router.get("/playlists/{playlist_id}/events/graph")
def get_playlist_events_graph(
    playlist_id: uuid.UUID,
    status: str | None = "accepted",
    limit: int = 300,
) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        clauses = event_filter_clause(playlist_id=playlist_id, status=status)
        events = (
            session.execute(
                select(MarketEvent)
                .join(Video, Video.id == MarketEvent.source_video_id)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(*clauses)
                .order_by(MarketEvent.event_time_start.desc().nullslast(), MarketEvent.id.desc())
                .limit(max(1, min(1000, int(limit or 300))))
            )
            .scalars()
            .all()
        )
        event_ids = [event.id for event in events]
        entities = (
            session.execute(select(MarketEventEntity).where(MarketEventEntity.event_id.in_(event_ids))).scalars().all()
            if event_ids
            else []
        )
        relations = (
            session.execute(select(MarketEventRelation).where(MarketEventRelation.event_id.in_(event_ids))).scalars().all()
            if event_ids
            else []
        )
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for event in events:
            nodes.append({"id": str(event.id), "type": "event", "label": event.title or event.event_type, "status": event.status})
        for entity in entities:
            nodes.append({"id": str(entity.id), "type": entity.entity_type, "label": entity.name, "role": entity.role})
            edges.append({"source": str(entity.event_id), "target": str(entity.id), "type": entity.role or "mentions", "confidence": entity.confidence})
        for relation in relations:
            if relation.source_entity_id and relation.target_entity_id:
                edges.append(
                    {
                        "source": str(relation.source_entity_id),
                        "target": str(relation.target_entity_id),
                        "type": relation.relation_type,
                        "direction": relation.direction,
                        "confidence": relation.confidence,
                    }
                )
        return {"nodes": nodes, "edges": edges}


@router.get("/playlists/{playlist_id}/events/{event_id}", response_model=MarketEventDetailOut)
def get_playlist_event(playlist_id: uuid.UUID, event_id: uuid.UUID) -> MarketEventDetailOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        event = _event_belongs_to_playlist(session, playlist_id=playlist_id, event_id=event_id)
        if not event:
            raise HTTPException(status_code=404, detail="event not found")
        return _event_detail_out(session, event)


@router.patch("/playlists/{playlist_id}/events/{event_id}", response_model=MarketEventDetailOut)
def patch_playlist_event(
    playlist_id: uuid.UUID,
    event_id: uuid.UUID,
    payload: MarketEventPatch,
) -> MarketEventDetailOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        event = _event_belongs_to_playlist(session, playlist_id=playlist_id, event_id=event_id)
        if not event:
            raise HTTPException(status_code=404, detail="event not found")
        if payload.status is not None:
            try:
                event = update_event_status(session, event_id=event_id, status=payload.status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.flush([event])
        return _event_detail_out(session, event)


@router.post("/playlists/{playlist_id}/regime/rebuild")
def rebuild_playlist_event_regime(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        run = request_event_regime_rebuild(session, playlist_id=playlist_id, priority=1)
        job = pending_event_regime_job(session, playlist_id)
        return {
            "ok": True,
            "playlist_id": str(playlist_id),
            "run_id": str(run.id),
            "job_id": str(job.id) if job else None,
        }


@router.get("/playlists/{playlist_id}/regime/summary", response_model=EventRegimeSummaryOut)
def get_playlist_event_regime_summary(playlist_id: uuid.UUID) -> EventRegimeSummaryOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        return _event_regime_summary(session, playlist_id)


@router.get("/playlists/{playlist_id}/regime/signals", response_model=list[EventRegimeSignalOut])
def get_playlist_event_regime_signals(
    playlist_id: uuid.UUID,
    granularity: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[EventRegimeSignalOut]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_regime_run_id(session, playlist_id)
        if not last_ready_run_id:
            return []
        stmt = select(EventRegimeSignal).where(EventRegimeSignal.regime_run_id == last_ready_run_id)
        if granularity:
            try:
                normalized = normalize_granularity(granularity)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            stmt = stmt.where(EventRegimeSignal.granularity == normalized)
        if since is not None:
            stmt = stmt.where(EventRegimeSignal.period_date >= since)
        if until is not None:
            stmt = stmt.where(EventRegimeSignal.period_date <= until)
        if since is not None and until is not None and since > until:
            raise HTTPException(status_code=400, detail="invalid date range")
        rows = (
            session.execute(
                stmt.order_by(
                    EventRegimeSignal.granularity.asc(),
                    EventRegimeSignal.period_date.asc(),
                    EventRegimeSignal.id.asc(),
                )
            )
            .scalars()
            .all()
        )
        return [
            EventRegimeSignalOut(
                id=row.id,
                granularity=row.granularity,
                period_date=row.period_date,
                rolling_window=row.rolling_window,
                event_count=row.event_count,
                ready_embedding_count=row.ready_embedding_count,
                drift_score=row.drift_score,
                drift_rolling_mean=row.drift_rolling_mean,
                drift_rolling_std=row.drift_rolling_std,
                drift_rolling_z=row.drift_rolling_z,
                dispersion_mean=row.dispersion_mean,
                dispersion_std=row.dispersion_std,
                dispersion_p25=row.dispersion_p25,
                dispersion_p75=row.dispersion_p75,
                projection_id=row.projection_id,
                projection_method=row.projection_method,
                projection_x=row.projection_x,
                projection_y=row.projection_y,
                projection_z=row.projection_z,
                projection_explained_variance_ratio=row.projection_explained_variance_ratio,
                linked_candidate_id=row.linked_candidate_id,
            )
            for row in rows
        ]


@router.get("/playlists/{playlist_id}/regime/candidates", response_model=list[EventRegimeCandidateOut])
def get_playlist_event_regime_candidates(playlist_id: uuid.UUID) -> list[EventRegimeCandidateOut]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_regime_run_id(session, playlist_id)
        if not last_ready_run_id:
            return []
        rows = (
            session.execute(
                select(EventRegimeCandidate)
                .where(EventRegimeCandidate.regime_run_id == last_ready_run_id)
                .order_by(EventRegimeCandidate.candidate_date.desc(), EventRegimeCandidate.id.asc())
            )
            .scalars()
            .all()
        )
        return [_candidate_out(row) for row in rows]


@router.get("/playlists/{playlist_id}/regime/candidates/{candidate_id}", response_model=EventRegimeCandidateDetailOut)
def get_playlist_event_regime_candidate(playlist_id: uuid.UUID, candidate_id: uuid.UUID) -> EventRegimeCandidateDetailOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_regime_run_id(session, playlist_id)
        if not last_ready_run_id:
            raise HTTPException(status_code=404, detail="regime snapshot not found")
        candidate = session.get(EventRegimeCandidate, candidate_id)
        if not candidate or candidate.regime_run_id != last_ready_run_id:
            raise HTTPException(status_code=404, detail="candidate not found")
        return _candidate_detail_out(candidate, session=session)


@router.patch("/playlists/{playlist_id}/regime/candidates/{candidate_id}", response_model=EventRegimeCandidateDetailOut)
def patch_playlist_event_regime_candidate(
    playlist_id: uuid.UUID,
    candidate_id: uuid.UUID,
    payload: EventRegimeCandidatePatch,
) -> EventRegimeCandidateDetailOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_regime_run_id(session, playlist_id)
        if not last_ready_run_id:
            raise HTTPException(status_code=404, detail="regime snapshot not found")
        candidate = session.get(EventRegimeCandidate, candidate_id)
        if not candidate or candidate.regime_run_id != last_ready_run_id:
            raise HTTPException(status_code=404, detail="candidate not found")
        if payload.status is not None:
            status = str(payload.status or "").strip().lower()
            if status not in {"draft", "accepted", "rejected"}:
                raise HTTPException(status_code=400, detail="invalid status")
            candidate.status = status
        if payload.event_type is not None:
            event_type = str(payload.event_type or "").strip().lower()
            if event_type not in {"event_regime_shift", "transition", "regime", "burst"}:
                raise HTTPException(status_code=400, detail="invalid event_type")
            candidate.event_type = event_type
        for field in ["candidate_date", "effective_trade_date"]:
            if field in payload.model_fields_set:
                setattr(candidate, field, getattr(payload, field))
        session.flush([candidate])
        return _candidate_detail_out(candidate, session=session)


@router.get("/playlists/{playlist_id}/regime/export/events")
def export_playlist_regime_events(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        rows = (
            session.execute(
                select(MarketEvent)
                .join(Video, Video.id == MarketEvent.source_video_id)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(
                    PlaylistMedia.playlist_id == playlist_id,
                    MarketEvent.status == "accepted",
                    MarketEvent.event_time_start.is_not(None),
                )
                .order_by(MarketEvent.event_time_start.asc(), MarketEvent.id.asc())
            )
            .scalars()
            .all()
        )
        return {
            "window_mode": "event_regime",
            "events": [
                {
                    "event_id": str(row.id),
                    "event_time_start": row.event_time_start.isoformat() if row.event_time_start else None,
                    "event_time_end": row.event_time_end.isoformat() if row.event_time_end else None,
                    "available_at": row.available_at.isoformat() if row.available_at else None,
                    "event_type": row.event_type,
                    "title": row.title,
                    "confidence": row.confidence,
                    "source_video_id": str(row.source_video_id),
                }
                for row in rows
            ],
        }


@router.patch("/playlists/{playlist_id}", response_model=PlaylistOut)
def update_playlist(playlist_id: uuid.UUID, payload: PlaylistUpdate) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        if payload.name is not None:
            playlist.name = payload.name
        if payload.description is not None:
            playlist.description = payload.description
        if payload.brief_granularity is not None:
            g = payload.brief_granularity.strip().lower()
            if g not in {"day", "week", "month"}:
                raise HTTPException(status_code=400, detail="invalid brief_granularity (day/week/month)")
            playlist.brief_granularity = g
        if "brief_prompt" in payload.model_fields_set:
            p = (payload.brief_prompt or "").strip()
            playlist.brief_prompt = p or None
        session.flush()
        return _playlist_out(session, playlist)


@router.delete("/playlists/{playlist_id}/background", response_model=PlaylistOut)
def clear_playlist_background(playlist_id: uuid.UUID) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        playlist.background_asset_id = None
        playlist.background_s3_key = None
        session.flush()
        return _playlist_out(session, playlist)


@router.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: uuid.UUID) -> dict:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        session.delete(playlist)
    return {"ok": True}


@router.post("/playlists/{playlist_id}/media")
def add_playlist_media(playlist_id: uuid.UUID, payload: PlaylistMediaAdd) -> dict:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        media = session.get(Media, payload.media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")

        existing = session.get(PlaylistMedia, {"playlist_id": playlist_id, "media_id": payload.media_id})
        if not existing:
            session.add(PlaylistMedia(playlist_id=playlist_id, media_id=payload.media_id))
            mark_playlist_event_regime_dirty(session, playlist_id)
            schedule_brief_refresh_for_media_change(
                session,
                playlist_id=playlist_id,
                changed_media_ids=[payload.media_id],
                change_type="media_added",
            )
    return {"ok": True}


@router.delete("/playlists/{playlist_id}/media/{media_id}")
def remove_playlist_media(playlist_id: uuid.UUID, media_id: uuid.UUID) -> dict:
    with session_scope() as session:
        existing = session.get(PlaylistMedia, {"playlist_id": playlist_id, "media_id": media_id})
        if existing:
            session.delete(existing)
            mark_playlist_event_regime_dirty(session, playlist_id)
            schedule_brief_refresh_for_media_change(
                session,
                playlist_id=playlist_id,
                changed_media_ids=[media_id],
                change_type="media_removed",
            )
    return {"ok": True}


@router.put("/playlists/{playlist_id}/media")
def replace_playlist_media(playlist_id: uuid.UUID, payload: PlaylistMediaReplace) -> dict:
    ids = list(dict.fromkeys(payload.media_ids or []))
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        if ids:
            found = set(session.execute(select(Media.id).where(Media.id.in_(list(ids)))).scalars().all())
            missing = [str(x) for x in ids if x not in found]
            if missing:
                raise HTTPException(status_code=404, detail=f"media not found: {', '.join(missing)}")

        existing = session.execute(select(PlaylistMedia).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        existing_set = {pm.media_id for pm in existing}
        desired_set = set(ids)
        changed_ids = list(existing_set.symmetric_difference(desired_set))

        for pm in existing:
            if pm.media_id not in desired_set:
                session.delete(pm)
        for mid in ids:
            if mid not in existing_set:
                session.add(PlaylistMedia(playlist_id=playlist_id, media_id=mid))

        if changed_ids:
            mark_playlist_event_regime_dirty(session, playlist_id)
            schedule_brief_refresh_for_media_change(
                session,
                playlist_id=playlist_id,
                changed_media_ids=changed_ids,
                change_type="media_replaced",
            )
    return {"ok": True}


def _save_playlist_image(*, file: UploadFile, key_base: str) -> tuple[Path, str, str, str]:
    ct = (file.content_type or "").lower().strip()
    if ct not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="unsupported image type (jpg/png/webp only)")

    fmt = "jpg" if ct == "image/jpeg" else "png" if ct == "image/png" else "webp"
    suffix = f".{fmt}"
    key = f"{key_base}{suffix}"
    with tempfile.NamedTemporaryFile(prefix="raelyn-playlist-", suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        size = 0
        while True:
            chunk = file.file.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _PLAYLIST_IMG_MAX_BYTES:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(status_code=400, detail="image too large (max 2MB)")
            tmp.write(chunk)

    return tmp_path, ct, key, fmt


@router.post("/playlists/{playlist_id}/avatar", response_model=PlaylistOut)
def upload_playlist_avatar(playlist_id: uuid.UUID, file: UploadFile = File(...)) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        tmp_path, ct, key, fmt = _save_playlist_image(file=file, key_base=f"playlist/{playlist_id}/avatar")
        try:
            asset = replace_standalone_asset(
                session,
                asset_id=playlist.avatar_asset_id,
                type_="image",
                format_=fmt,
                source="playlist",
                variant="avatar",
                local_path=tmp_path,
                s3_key=key,
                metadata={"playlist_id": str(playlist_id), "kind": "avatar"},
                content_type=ct,
            )
            playlist.avatar_asset_id = asset.id
            playlist.avatar_s3_key = key
            session.flush()
            return _playlist_out(session, playlist)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


@router.post("/playlists/{playlist_id}/background", response_model=PlaylistOut)
def upload_playlist_background(playlist_id: uuid.UUID, file: UploadFile = File(...)) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        tmp_path, ct, key, fmt = _save_playlist_image(file=file, key_base=f"playlist/{playlist_id}/background")
        try:
            asset = replace_standalone_asset(
                session,
                asset_id=playlist.background_asset_id,
                type_="image",
                format_=fmt,
                source="playlist",
                variant="background",
                local_path=tmp_path,
                s3_key=key,
                metadata={"playlist_id": str(playlist_id), "kind": "background"},
                content_type=ct,
            )
            playlist.background_asset_id = asset.id
            playlist.background_s3_key = key
            session.flush()
            return _playlist_out(session, playlist)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


class PlaylistVideoOut(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    url: str
    title: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    published_at: Any | None = None
    content_published_at: Any | None = None
    timeline_at: Any | None = None
    time_source: str | None = None
    time_status: str | None = None
    time_confidence: float | None = None
    duration_sec: int | None = None
    status: str
    error_message: str | None = None
    media_name: str | None = None
    media_avatar_asset: AssetRef | None = None


class PlaylistPeriodCountOut(BaseModel):
    period_start: date
    count: int


_MISSING_AVATAR = object()


def _ensure_playlist_published_at_backfilled(session) -> None:
    ensure_video_published_at_backfilled(session)


def _playlist_video_description(video: Video) -> str | None:
    desc = video.description
    if (not desc) and video.raw_info and isinstance(video.raw_info, dict) and video.raw_info.get("description"):
        try:
            desc = str(video.raw_info.get("description") or "")
        except Exception:
            desc = None
    if desc:
        desc = desc.strip().replace("\n", " ")
        if len(desc) > 140:
            desc = desc[:140].rstrip() + "…"
    return desc


def _playlist_video_out(
    session,
    video: Video,
    media: Media,
    *,
    timeline_at: Any | None = None,
    content_published_at: Any | None = None,
    time_source: str | None = None,
    time_status: str | None = None,
    time_confidence: float | None = None,
    media_avatar_asset: AssetRef | None | object = _MISSING_AVATAR,
) -> PlaylistVideoOut:
    avatar_asset = _media_avatar_asset(session, media) if media_avatar_asset is _MISSING_AVATAR else media_avatar_asset
    return PlaylistVideoOut(
        id=video.id,
        media_id=video.media_id,
        url=video.url,
        title=video.title,
        description=_playlist_video_description(video),
        thumbnail_url=video.thumbnail_url,
        published_at=video.published_at,
        content_published_at=content_published_at,
        timeline_at=timeline_at if timeline_at is not None else video.published_at,
        time_source=time_source,
        time_status=time_status,
        time_confidence=time_confidence,
        duration_sec=video.duration_sec,
        status=video.status,
        error_message=video.error_message,
        media_name=media.name,
        media_avatar_asset=avatar_asset,
    )


def _playlist_period_counts_from_timestamps(
    timestamps: list[Any],
    *,
    granularity: str,
    timezone_name: str | None = None,
) -> list[PlaylistPeriodCountOut]:
    counts: dict[date, int] = {}
    for ts in timestamps:
        local_day = local_date(ts, timezone_name=timezone_name)
        if not local_day:
            continue
        ps = period_start(local_day, granularity)
        counts[ps] = int(counts.get(ps, 0) or 0) + 1
    return [PlaylistPeriodCountOut(period_start=ps, count=counts[ps]) for ps in sorted(counts)]


@router.get("/playlists/{playlist_id}/videos_by_date", response_model=list[PlaylistVideoOut])
def list_playlist_videos_by_date(playlist_id: uuid.UUID, date: date, time_basis: str = "content") -> list[PlaylistVideoOut]:
    try:
        resolved_time_basis = normalize_time_basis(time_basis)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    with session_scope() as session:
        _ensure_playlist_published_at_backfilled(session)
        start, end = day_bounds_utc(date)
        timeline_columns = _playlist_timeline_columns(resolved_time_basis, name="playlist_date_content_time")
        stmt = (
            select(
                Video,
                Media,
                timeline_columns.timeline_at.label("timeline_at"),
                timeline_columns.content_published_at.label("content_published_at"),
                timeline_columns.time_source.label("time_source"),
                timeline_columns.time_status.label("time_status"),
                timeline_columns.time_confidence.label("time_confidence"),
            )
            .select_from(Video)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .join(Media, Media.id == Video.media_id)
        )
        stmt = _join_playlist_timeline(stmt, timeline_columns).where(
            PlaylistMedia.playlist_id == playlist_id,
            *_playlist_playback_clauses(timeline_columns),
            timeline_columns.timeline_at >= start,
            timeline_columns.timeline_at < end,
        )
        rows = (
            session.execute(
                stmt.order_by(timeline_columns.timeline_at.asc(), Video.created_at.asc(), Video.id.asc())
            )
            .all()
        )

        avatar_by_media_id = _media_avatar_asset_map(session, list({m.id: m for _, m, *_ in rows}.values()))
        out: list[PlaylistVideoOut] = []
        for v, m, timeline_at, content_published_at, time_source_value, time_status_value, time_confidence_value in rows:
            out.append(
                _playlist_video_out(
                    session,
                    v,
                    m,
                    timeline_at=timeline_at,
                    content_published_at=content_published_at,
                    time_source=time_source_value,
                    time_status=time_status_value,
                    time_confidence=time_confidence_value,
                    media_avatar_asset=avatar_by_media_id.get(m.id),
                )
            )
        return out


@router.get("/playlists/{playlist_id}/video_counts_by_period", response_model=list[PlaylistPeriodCountOut])
def list_playlist_video_counts_by_period(
    playlist_id: uuid.UUID,
    granularity: str = "day",
    start: date = ...,
    end: date = ...,
    time_basis: str = "content",
) -> list[PlaylistPeriodCountOut]:
    try:
        g = normalize_granularity(granularity)
        resolved_time_basis = normalize_time_basis(time_basis)
        pstart = period_start(start, g)
        pend = period_start(end, g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if pend < pstart:
        return []

    if g == "day":
        periods = (pend - pstart).days + 1
    elif g == "week":
        periods = ((pend - pstart).days // 7) + 1
    else:
        periods = (pend.year - pstart.year) * 12 + (pend.month - pstart.month) + 1
    if periods > 400:
        raise HTTPException(status_code=400, detail="range too large (max 400 periods)")

    start_utc, _ = period_bounds_utc(pstart, g)
    _, end_utc = period_bounds_utc(pend, g)

    tzname = (getattr(settings, "timezone", None) or "UTC").strip() or "UTC"
    unit = "day" if g == "day" else ("week" if g == "week" else "month")

    with session_scope() as session:
        _ensure_playlist_published_at_backfilled(session)
        timeline_columns = _playlist_timeline_columns(resolved_time_basis, name="playlist_counts_content_time")
        dialect_name = ""
        try:
            dialect_name = str(session.bind.dialect.name or "").strip().lower()
        except Exception:
            dialect_name = ""

        if dialect_name == "postgresql":
            local_ts = func.timezone(tzname, timeline_columns.timeline_at)
            bucket = func.date_trunc(unit, local_ts)
            period_start_expr = cast(bucket, Date)
            stmt = (
                select(
                    period_start_expr.label("period_start"),
                    func.count(Video.id).label("count"),
                )
                .select_from(Video)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            )
            stmt = _join_playlist_timeline(stmt, timeline_columns).where(
                PlaylistMedia.playlist_id == playlist_id,
                *_playlist_playback_clauses(timeline_columns),
                timeline_columns.timeline_at >= start_utc,
                timeline_columns.timeline_at < end_utc,
            )
            rows = (
                session.execute(
                    stmt.group_by(period_start_expr).order_by(period_start_expr.asc())
                )
                .all()
            )

            out: list[PlaylistPeriodCountOut] = []
            for ps, cnt in rows:
                try:
                    out.append(PlaylistPeriodCountOut(period_start=ps, count=int(cnt or 0)))
                except Exception:
                    continue
            return out

        rows = (
            session.execute(
                _join_playlist_timeline(
                    select(timeline_columns.timeline_at.label("timeline_at"))
                    .select_from(Video)
                    .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id),
                    timeline_columns,
                )
                .where(
                    PlaylistMedia.playlist_id == playlist_id,
                    *_playlist_playback_clauses(timeline_columns),
                    timeline_columns.timeline_at >= start_utc,
                    timeline_columns.timeline_at < end_utc,
                )
                .order_by(timeline_columns.timeline_at.asc(), Video.id.asc())
            )
            .all()
        )
        timestamps = [timeline_at for (timeline_at,) in rows if timeline_at]
        return _playlist_period_counts_from_timestamps(timestamps, granularity=g, timezone_name=tzname)


@router.get("/playlists/{playlist_id}/videos_by_period", response_model=list[PlaylistVideoOut])
def list_playlist_videos_by_period(
    playlist_id: uuid.UUID,
    granularity: str = "day",
    date: date = ...,
    limit: int = 200,
    time_basis: str = "content",
) -> list[PlaylistVideoOut]:
    try:
        g = normalize_granularity(granularity)
        resolved_time_basis = normalize_time_basis(time_basis)
        pstart = period_start(date, g)
        start, end = period_bounds_utc(pstart, g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    n = max(1, min(int(limit or 200), 500))
    with session_scope() as session:
        _ensure_playlist_published_at_backfilled(session)
        timeline_columns = _playlist_timeline_columns(resolved_time_basis, name="playlist_period_content_time")
        stmt = (
            select(
                Video,
                Media,
                timeline_columns.timeline_at.label("timeline_at"),
                timeline_columns.content_published_at.label("content_published_at"),
                timeline_columns.time_source.label("time_source"),
                timeline_columns.time_status.label("time_status"),
                timeline_columns.time_confidence.label("time_confidence"),
            )
            .select_from(Video)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .join(Media, Media.id == Video.media_id)
        )
        stmt = _join_playlist_timeline(stmt, timeline_columns).where(
            PlaylistMedia.playlist_id == playlist_id,
            *_playlist_playback_clauses(timeline_columns),
            timeline_columns.timeline_at >= start,
            timeline_columns.timeline_at < end,
        )
        rows = (
            session.execute(
                stmt.order_by(timeline_columns.timeline_at.asc(), Video.created_at.asc(), Video.id.asc()).limit(n)
            )
            .all()
        )

        avatar_by_media_id = _media_avatar_asset_map(session, list({m.id: m for _, m, *_ in rows}.values()))
        out: list[PlaylistVideoOut] = []
        for v, m, timeline_at, content_published_at, time_source_value, time_status_value, time_confidence_value in rows:
            out.append(
                _playlist_video_out(
                    session,
                    v,
                    m,
                    timeline_at=timeline_at,
                    content_published_at=content_published_at,
                    time_source=time_source_value,
                    time_status=time_status_value,
                    time_confidence=time_confidence_value,
                    media_avatar_asset=avatar_by_media_id.get(m.id),
                )
            )
        return out
