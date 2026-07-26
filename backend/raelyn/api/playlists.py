from __future__ import annotations

import tempfile
import struct
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Iterator

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Date, and_, case, cast, func, literal, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased

from raelyn.api.asset_refs import AssetRef, build_asset_ref
from raelyn.api.orm import OrmModel
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import (
    Asset,
    EventMapCanonical,
    EventMapCanonicalMember,
    EventMapEntityIndex,
    EventMapRecordRevision,
    EventMapSnapshot,
    EventMapState,
    EventMapStory,
    EventMapStoryEdge,
    EventMapTopic,
    EventMapTopicMember,
    Job,
    Media,
    MarketEvent,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
    Playlist,
    PlaylistMedia,
    Video,
)
from raelyn.services.brief_schedule import schedule_brief_refresh_for_media_change
from raelyn.services.assets import replace_standalone_asset
from raelyn.services.periods import day_bounds_utc, local_date, normalize_granularity, period_bounds_utc, period_start
from raelyn.services.event_analysis import (
    event_filter_clause,
    playlist_event_coverage,
    playlist_event_map_coverage,
    request_event_map_rebuild,
    request_playlist_event_backfill,
    schedule_playlist_event_map_dirty,
    update_event_status,
)
from raelyn.services.event_map_domain import (
    EVENT_MAP_SEMANTIC_FAMILIES,
    enrich_event_map_type_categories,
    normalize_event_map_entity,
)
from raelyn.services.event_map_projection import (
    EVENT_MAP_PROJECTION_METHOD,
    EVENT_MAP_PROJECTION_VERSION,
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


def _configure_event_map_query(
    session: Any,
    *,
    force_hash_join: bool = False,
) -> None:
    """给事件地图在线聚合设置事务级边界，不改变连接池中后续请求。"""
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    timeout_seconds = max(1, min(120, int(settings.event_map_entity_query_timeout_seconds or 10)))
    session.execute(
        text("select set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{timeout_seconds}s"},
    )
    if force_hash_join:
        # 单个 ready 快照包含十万至百万实体行；随机 canonical Nested Loop
        # 会在保留多版快照的大索引上退化为数小时查询。该排行必须线性扫描并 Hash Join。
        session.execute(text("select set_config('enable_nestloop', 'off', true)"))


def _event_map_query_timeout(exc: DBAPIError) -> bool:
    return str(getattr(exc.orig, "sqlstate", "") or "") == "57014"


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
            _schedule_playlist_event_map_dirty(session, playlist.id, reason="playlist_created")

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


_EVENT_MAP_SCENE_RECORD = struct.Struct("<I16sfffiiBBBBIII")
_EVENT_MAP_SCENE_PROTOCOL_VERSION = 2
_EVENT_MAP_SCENE_ROWS_PER_CHUNK = 2048
_EVENT_MAP_INDEX_RECORD = struct.Struct("<I")
_EVENT_MAP_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_EVENT_MAP_NO_INDEX = 0xFFFFFFFF


def _event_map_day(value: date) -> int:
    return int(value.toordinal() - _EVENT_MAP_EPOCH_ORDINAL)


def _event_map_date(value: int) -> date:
    return date.fromordinal(_EVENT_MAP_EPOCH_ORDINAL + int(value))


def _event_map_state(session: Any, playlist_id: uuid.UUID) -> EventMapState:
    state = session.get(EventMapState, playlist_id)
    if state is not None:
        return state
    state = EventMapState(playlist_id=playlist_id)
    session.add(state)
    session.flush([state])
    return state


def _schedule_playlist_event_map_dirty(
    session: Any,
    playlist_id: uuid.UUID,
    *,
    reason: str,
    source_video_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return schedule_playlist_event_map_dirty(
        session,
        playlist_id=playlist_id,
        reason=reason,
        source_video_id=source_video_id,
        priority=0,
    )


def _active_event_map_build_job(session: Any, playlist_id: uuid.UUID) -> Job | None:
    return (
        session.execute(
            select(Job)
            .where(
                Job.type == "playlist.build_event_map_snapshot",
                Job.status.in_(["pending", "running", "cancel_requested"]),
                Job.params["playlist_id"].as_string() == str(playlist_id),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _event_map_job_payload(job: Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "job_id": str(job.id),
        "status": job.status,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "cancel_requested_at": job.cancel_requested_at,
    }


def _event_map_snapshot_is_current(snapshot: EventMapSnapshot | None) -> bool:
    return bool(
        snapshot is not None
        and snapshot.status == "ready"
        and getattr(snapshot, "layout_algorithm_version", None) == EVENT_MAP_PROJECTION_VERSION
        and getattr(snapshot, "projection_method", None) == EVENT_MAP_PROJECTION_METHOD
    )


def _resolve_event_map_snapshot(
    session: Any,
    *,
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
) -> EventMapSnapshot:
    if snapshot_id is None:
        state = session.get(EventMapState, playlist_id)
        snapshot_id = state.current_snapshot_id if state is not None else None
    snapshot = session.get(EventMapSnapshot, snapshot_id) if snapshot_id is not None else None
    if (
        snapshot is None
        or snapshot.playlist_id != playlist_id
        or not _event_map_snapshot_is_current(snapshot)
    ):
        raise HTTPException(status_code=409, detail="event map snapshot is not ready")
    return snapshot


def _event_map_topics(session: Any, snapshot_id: uuid.UUID) -> list[EventMapTopic]:
    return (
        session.execute(
            select(EventMapTopic)
            .where(EventMapTopic.snapshot_id == snapshot_id)
            .order_by(EventMapTopic.level.asc(), EventMapTopic.label.asc(), EventMapTopic.topic_id.asc())
        )
        .scalars()
        .all()
    )

@router.get("/playlists/{playlist_id}/events/map/manifest")
def get_playlist_event_map_manifest(playlist_id: uuid.UUID, compact: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        state = session.get(EventMapState, playlist_id)
        snapshot = None
        if state is not None and state.current_snapshot_id is not None:
            candidate = session.get(EventMapSnapshot, state.current_snapshot_id)
            if _event_map_snapshot_is_current(candidate):
                snapshot = candidate
        build_job = _active_event_map_build_job(session, playlist_id)
        backfill_job = _active_playlist_event_backfill_job(session, playlist_id)
        coverage = {} if compact else playlist_event_map_coverage(session, playlist_id)
        dirty_generation = int(state.dirty_generation or 0) if state is not None else 0
        built_generation = int(state.built_generation or 0) if state is not None else 0
        dirty = dirty_generation > built_generation
        if snapshot is not None:
            status = "ready"
        elif build_job is not None:
            status = "running" if build_job.status == "running" else "pending"
        elif state is not None and state.last_error:
            status = "failed"
        else:
            status = "missing"
        if build_job is not None:
            build_status = "running" if build_job.status == "running" else "pending"
        elif state is not None and state.last_error and dirty:
            build_status = "failed"
        else:
            build_status = "idle"

        payload: dict[str, Any] = {
            "playlist_id": str(playlist_id),
            "snapshot_id": str(snapshot.id) if snapshot is not None else None,
            "status": status,
            "build_status": build_status,
            "build_error": state.last_error if state is not None else None,
            "building": build_job is not None,
            "dirty": dirty,
            "dirty_generation": dirty_generation,
            "built_generation": built_generation,
            "last_requested_at": state.last_requested_at if state is not None else None,
            "last_built_at": state.last_built_at if state is not None else None,
            "last_error": state.last_error if state is not None else None,
            "build_job": _event_map_job_payload(build_job),
            "backfill_job": (
                _playlist_event_backfill_job_out(session, backfill_job).model_dump()
                if backfill_job is not None
                else None
            ),
            **coverage,
        }
        if compact:
            payload.update(
                {
                    "canonical_count": int(snapshot.canonical_count or 0) if snapshot is not None else 0,
                    "record_count": int(snapshot.member_count or snapshot.input_record_count or 0) if snapshot is not None else 0,
                    "event_skipped": (
                        sum((snapshot.skipped_reason_counts or {}).values())
                        if snapshot is not None
                        else 0
                    ),
                }
            )
            return payload
        if snapshot is None:
            payload.update(
                {
                    "canonical_count": 0,
                    "record_count": 0,
                    "story_count": 0,
                    "topic_count": 0,
                    "entity_count": 0,
                    "type_categories": [],
                    "semantic_families": list(EVENT_MAP_SEMANTIC_FAMILIES),
                    "topics": [],
                    "monthly_distribution": [],
                    "time_bounds": {},
                    "dimension": 3,
                    "scene_protocol_version": _EVENT_MAP_SCENE_PROTOCOL_VERSION,
                    "scene_record_size": _EVENT_MAP_SCENE_RECORD.size,
                }
            )
            return payload

        time_start, time_end = session.execute(
            select(
                func.min(EventMapCanonical.event_time_start),
                func.max(EventMapCanonical.event_time_end),
            ).where(EventMapCanonical.snapshot_id == snapshot.id)
        ).one()
        topics = _event_map_topics(session, snapshot.id)
        topic_order = {topic.topic_id: index for index, topic in enumerate(topics)}
        anchor_rows = session.execute(
            select(
                EventMapCanonical.canonical_id,
                EventMapCanonical.point_index,
                EventMapCanonical.title,
            ).where(
                EventMapCanonical.snapshot_id == snapshot.id,
                EventMapCanonical.canonical_id.in_(
                    [topic.anchor_canonical_id for topic in topics if topic.anchor_canonical_id is not None]
                ),
            )
        ).all()
        topic_anchors = {
            canonical_id: {
                "point_index": int(point_index),
                "title": str(title or "").strip(),
            }
            for canonical_id, point_index, title in anchor_rows
        }
        payload.update(
            {
                "snapshot_id": str(snapshot.id),
                "parent_snapshot_id": str(snapshot.parent_snapshot_id) if snapshot.parent_snapshot_id else None,
                "input_generation": int(snapshot.input_generation or 0),
                "build_key": snapshot.build_key,
                "layout_continuity": snapshot.layout_continuity,
                "projection_method": snapshot.projection_method,
                "projection_seed": snapshot.projection_seed,
                "dimension": 3,
                "scene_protocol_version": _EVENT_MAP_SCENE_PROTOCOL_VERSION,
                "scene_record_size": _EVENT_MAP_SCENE_RECORD.size,
                "bounds": snapshot.bounds or {},
                "canonical_count": int(snapshot.canonical_count or 0),
                "record_count": int(snapshot.member_count or snapshot.input_record_count or 0),
                "story_count": int(snapshot.story_count or 0),
                "topic_count": len(topics),
                "topic_count_total": int(snapshot.topic_count or 0),
                "entity_count": int(snapshot.entity_count or 0),
                "event_skipped": sum((snapshot.skipped_reason_counts or {}).values()),
                "skipped_reason_counts": snapshot.skipped_reason_counts or {},
                "peak_rss_bytes": snapshot.peak_rss_bytes,
                "type_categories": enrich_event_map_type_categories(snapshot.type_categories),
                "semantic_families": list(EVENT_MAP_SEMANTIC_FAMILIES),
                "time_bounds": {
                    "start": time_start.date().isoformat() if time_start is not None else None,
                    "end": time_end.date().isoformat() if time_end is not None else None,
                },
                "monthly_distribution": snapshot.monthly_distribution or [],
                "topics": [
                    {
                        "topic_index": topic_order[topic.topic_id],
                        "topic_id": str(topic.topic_id),
                        "level": int(topic.level),
                        "parent_topic_index": (
                            topic_order.get(topic.parent_topic_id)
                            if topic.parent_topic_id is not None
                            else None
                        ),
                        "label": topic.label,
                        "top_terms": topic.top_terms or [],
                        "center_x": topic.center_x,
                        "center_y": topic.center_y,
                        "center_z": topic.center_z,
                        "radius": topic.radius,
                        "canonical_count": int(topic.canonical_count or 0),
                        "record_count": int(topic.member_count or 0),
                        "anchor_canonical_id": (
                            str(topic.anchor_canonical_id)
                            if topic.anchor_canonical_id is not None
                            else None
                        ),
                        "anchor_point_index": (
                            topic_anchors.get(topic.anchor_canonical_id, {}).get("point_index")
                            if topic.anchor_canonical_id is not None
                            else None
                        ),
                        "anchor_title": (
                            topic_anchors.get(topic.anchor_canonical_id, {}).get("title") or None
                            if topic.anchor_canonical_id is not None
                            else None
                        ),
                    }
                    for topic in topics
                ],
                "capabilities": {
                    "topic_regions": bool(topics),
                    "stories": int(snapshot.story_count or 0) > 0,
                    "records": True,
                    "entities": True,
                },
            }
        )
        return payload


def _event_map_scene_topic_order(session: Any, snapshot_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """返回场景流所需的小型索引，不把 canonical membership 全量载入内存。"""
    topics = _event_map_topics(session, snapshot_id)
    topic_order = {topic.topic_id: index for index, topic in enumerate(topics)}
    return topic_order


def _event_map_scene_statement(
    *,
    snapshot_id: uuid.UUID,
) -> Any:
    """只读取二进制场景记录所需列，避免为全量点创建 ORM 实体。"""
    macro_topic_member = aliased(EventMapTopicMember)
    local_topic_member = aliased(EventMapTopicMember)
    statement = (
        select(
            EventMapCanonical.point_index,
            EventMapCanonical.canonical_id,
            EventMapCanonical.x,
            EventMapCanonical.y,
            EventMapCanonical.z,
            EventMapCanonical.event_start_day,
            EventMapCanonical.event_end_day,
            EventMapCanonical.event_type_code,
            EventMapCanonical.time_precision_code,
            EventMapCanonical.uncertainty_flags,
            EventMapCanonical.time_disagreement_count,
            EventMapCanonical.member_count,
            macro_topic_member.topic_id.label("macro_topic_id"),
            local_topic_member.topic_id.label("local_topic_id"),
        )
        .select_from(EventMapCanonical)
        .outerjoin(
            macro_topic_member,
            and_(
                macro_topic_member.snapshot_id == EventMapCanonical.snapshot_id,
                macro_topic_member.canonical_id == EventMapCanonical.canonical_id,
                macro_topic_member.level == 0,
            ),
        )
        .outerjoin(
            local_topic_member,
            and_(
                local_topic_member.snapshot_id == EventMapCanonical.snapshot_id,
                local_topic_member.canonical_id == EventMapCanonical.canonical_id,
                local_topic_member.level == 1,
            ),
        )
    )
    return statement.where(EventMapCanonical.snapshot_id == snapshot_id).order_by(
        EventMapCanonical.point_index.asc()
    )


def _event_map_scene_chunks(
    rows: Any,
    *,
    canonical_count: int,
    topic_order: dict[uuid.UUID, int],
) -> Iterator[bytes]:
    chunk = bytearray()
    expected_index = 0
    for (
        point_index,
        canonical_id,
        x,
        y,
        z,
        event_start_day,
        event_end_day,
        event_type_code,
        time_precision_code,
        uncertainty_flags,
        time_disagreement_count,
        member_count,
        macro_topic_id,
        local_topic_id,
    ) in rows:
        if int(point_index) != expected_index:
            raise RuntimeError("event map point_index is not contiguous")
        flags = (1 if uncertainty_flags else 0) | (2 if int(time_disagreement_count or 0) else 0)
        chunk.extend(
            _EVENT_MAP_SCENE_RECORD.pack(
                expected_index,
                canonical_id.bytes,
                float(x),
                float(y),
                float(z),
                int(event_start_day),
                int(event_end_day),
                int(event_type_code) & 0xFF,
                int(time_precision_code) & 0xFF,
                flags,
                0,
                int(member_count or 0),
                int(topic_order.get(macro_topic_id, _EVENT_MAP_NO_INDEX)),
                int(topic_order.get(local_topic_id, _EVENT_MAP_NO_INDEX)),
            )
        )
        expected_index += 1
        if expected_index % _EVENT_MAP_SCENE_ROWS_PER_CHUNK == 0:
            yield bytes(chunk)
            chunk.clear()
    if expected_index != canonical_count:
        raise RuntimeError("event map canonical count changed inside immutable snapshot")
    if chunk:
        yield bytes(chunk)


@router.get("/playlists/{playlist_id}/events/map/scene")
def get_playlist_event_map_scene(
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
) -> StreamingResponse:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        snapshot = _resolve_event_map_snapshot(
            session,
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
        )
        canonical_count = int(snapshot.canonical_count or 0)

    def stream_scene() -> Iterator[bytes]:
        with session_scope() as stream_session:
            pinned = _resolve_event_map_snapshot(
                stream_session,
                playlist_id=playlist_id,
                snapshot_id=snapshot_id,
            )
            topic_order = _event_map_scene_topic_order(stream_session, pinned.id)
            statement = _event_map_scene_statement(
                snapshot_id=pinned.id,
            )
            rows = stream_session.execute(
                statement.execution_options(yield_per=_EVENT_MAP_SCENE_ROWS_PER_CHUNK)
            )
            yield from _event_map_scene_chunks(
                rows,
                canonical_count=canonical_count,
                topic_order=topic_order,
            )

    return StreamingResponse(
        stream_scene(),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"event-map-scene-{snapshot_id}"',
            "X-Event-Map-Snapshot-Id": str(snapshot_id),
            "X-Event-Map-Record-Size": str(_EVENT_MAP_SCENE_RECORD.size),
            "X-Event-Map-Protocol-Version": str(_EVENT_MAP_SCENE_PROTOCOL_VERSION),
            "X-Event-Map-Canonical-Count": str(canonical_count),
        },
    )


@router.get("/playlists/{playlist_id}/events/map/entities")
def get_playlist_event_map_entities(
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    start_date: date | None = None,
    end_date: date | None = None,
    q: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=400, detail="invalid date range")
    query = str(q or "").strip()
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        snapshot = _resolve_event_map_snapshot(session, playlist_id=playlist_id, snapshot_id=snapshot_id)
        _configure_event_map_query(session, force_hash_join=True)
        clauses: list[Any] = [EventMapEntityIndex.snapshot_id == snapshot.id]
        if start_date is not None:
            clauses.append(EventMapCanonical.event_end_day >= _event_map_day(start_date))
        if end_date is not None:
            clauses.append(EventMapCanonical.event_start_day <= _event_map_day(end_date))
        if query:
            pattern = f"%{query}%"
            clauses.append(
                or_(
                    EventMapEntityIndex.name.ilike(pattern),
                    EventMapEntityIndex.normalized_key.ilike(pattern),
                    EventMapEntityIndex.entity_type.ilike(pattern),
                )
            )
        canonical_count = func.count(EventMapEntityIndex.canonical_id)
        try:
            rows = session.execute(
                select(
                    EventMapEntityIndex.entity_type,
                    EventMapEntityIndex.normalized_key,
                    func.min(EventMapEntityIndex.name),
                    canonical_count.label("canonical_count"),
                )
                .join(
                    EventMapCanonical,
                    and_(
                        EventMapCanonical.snapshot_id == EventMapEntityIndex.snapshot_id,
                        EventMapCanonical.canonical_id == EventMapEntityIndex.canonical_id,
                    ),
                )
                .where(*clauses)
                .group_by(EventMapEntityIndex.entity_type, EventMapEntityIndex.normalized_key)
                .order_by(
                    canonical_count.desc(),
                    EventMapEntityIndex.entity_type.asc(),
                    EventMapEntityIndex.normalized_key.asc(),
                )
                .limit(max(1, min(100, int(limit or 40))))
            ).all()
        except DBAPIError as exc:
            if _event_map_query_timeout(exc):
                raise HTTPException(status_code=503, detail="event map entity ranking timed out") from exc
            raise
        return [
            {
                "entity_type": str(entity_type),
                "normalized_key": str(normalized_key),
                "name": str(name or normalized_key),
                "canonical_count": int(count or 0),
            }
            for entity_type, normalized_key, name, count in rows
        ]


@router.get("/playlists/{playlist_id}/events/map/entity-indices")
def get_playlist_event_map_entity_indices(
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    normalized_key: str,
    entity_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Response:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=400, detail="invalid date range")
    key = str(normalized_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="normalized_key is required")
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        snapshot = _resolve_event_map_snapshot(session, playlist_id=playlist_id, snapshot_id=snapshot_id)
        _configure_event_map_query(session)
        clauses: list[Any] = [
            EventMapEntityIndex.snapshot_id == snapshot.id,
            EventMapEntityIndex.normalized_key == key,
        ]
        if entity_type:
            clauses.append(EventMapEntityIndex.entity_type == entity_type)
        if start_date is not None:
            clauses.append(EventMapCanonical.event_end_day >= _event_map_day(start_date))
        if end_date is not None:
            clauses.append(EventMapCanonical.event_start_day <= _event_map_day(end_date))
        try:
            indices = session.execute(
                select(EventMapEntityIndex.point_index)
                .join(
                    EventMapCanonical,
                    and_(
                        EventMapCanonical.snapshot_id == EventMapEntityIndex.snapshot_id,
                        EventMapCanonical.canonical_id == EventMapEntityIndex.canonical_id,
                    ),
                )
                .where(*clauses)
                .order_by(EventMapEntityIndex.point_index.asc())
            ).scalars().all()
        except DBAPIError as exc:
            if _event_map_query_timeout(exc):
                raise HTTPException(status_code=503, detail="event map entity index query timed out") from exc
            raise
        body = b"".join(_EVENT_MAP_INDEX_RECORD.pack(int(index)) for index in indices)
        return Response(
            content=body,
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Event-Map-Snapshot-Id": str(snapshot.id),
                "X-Event-Map-Record-Size": str(_EVENT_MAP_INDEX_RECORD.size),
                "X-Event-Map-Index-Count": str(len(indices)),
            },
        )


def _event_map_record_payload(revision: EventMapRecordRevision, member: EventMapCanonicalMember) -> dict[str, Any]:
    source = dict(revision.source_json or {})
    relations = list(source.pop("relations", []) or [])
    return {
        "id": str(revision.id),
        "record_id": str(revision.event_id) if revision.event_id else str(revision.id),
        "event_id": str(revision.event_id) if revision.event_id else None,
        "title": revision.title,
        "summary": revision.summary,
        "event_time_start": revision.event_time_start,
        "event_time_end": revision.event_time_end,
        "time_precision": revision.time_precision,
        "event_type": revision.event_type,
        "source": source,
        "entities": list(revision.entities_json or []),
        "relations": relations,
        "source_label": source.get("media_name") or source.get("title") or source.get("provider"),
        "is_representative": bool(member.is_representative),
        "assignment_kind": member.assignment_kind,
        "decision_score": member.decision_score,
        "reason_codes": member.reason_codes or [],
    }


def _event_map_local_entity_relations(
    member_rows: list[tuple[EventMapCanonicalMember, EventMapRecordRevision]],
    entity_values: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """从冻结 revision 中恢复局部实体角色和实体间关系，不回查可变业务表。"""
    entity_id_to_key: dict[str, str] = {}
    for _, revision in member_rows:
        for raw_entity in list(revision.entities_json or []):
            entity_type = str(raw_entity.get("entity_type") or "other")
            normalized = normalize_event_map_entity(
                entity_type,
                str(raw_entity.get("name") or ""),
                str(raw_entity.get("normalized_key") or ""),
            )
            if not normalized or ":" not in normalized:
                continue
            normalized_type, normalized_key = normalized.split(":", 1)
            value = entity_values.get((normalized_type, normalized_key))
            if value is None:
                continue
            raw_id = str(raw_entity.get("id") or "").strip()
            if raw_id:
                entity_id_to_key[raw_id] = normalized
            role = str(raw_entity.get("role") or "other").strip() or "other"
            role_counts = value.setdefault("_role_counts", {})
            role_counts[role] = int(role_counts.get(role, 0)) + 1

    entity_relations: list[dict[str, Any]] = []
    seen_entity_relations: set[tuple[str, str, str, str]] = set()
    for _, revision in member_rows:
        source = dict(revision.source_json or {})
        for relation in list(source.get("relations") or []):
            source_key = entity_id_to_key.get(str(relation.get("source_entity_id") or ""))
            target_key = entity_id_to_key.get(str(relation.get("target_entity_id") or ""))
            if not source_key or not target_key or source_key == target_key:
                continue
            relation_type = str(relation.get("relation_type") or "related_to")
            direction = str(relation.get("direction") or "directed")
            identity = (source_key, target_key, relation_type, direction)
            if identity in seen_entity_relations:
                continue
            seen_entity_relations.add(identity)
            entity_relations.append(
                {
                    "id": str(relation.get("id") or f"{revision.id}:{len(entity_relations)}"),
                    "source_entity_id": source_key,
                    "target_entity_id": target_key,
                    "relation_type": relation_type,
                    "direction": direction,
                    "confidence": relation.get("confidence"),
                    "evidence_text": relation.get("evidence_text"),
                }
            )
            if len(entity_relations) >= 24:
                break
        if len(entity_relations) >= 24:
            break

    for value in entity_values.values():
        role_counts = value.pop("_role_counts", {})
        roles = [
            {"role": role, "record_count": count}
            for role, count in sorted(role_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        value["roles"] = roles
        value["role_label"] = roles[0]["role"] if roles else ""
    return entity_relations


@router.get("/playlists/{playlist_id}/events/map/canonical/{canonical_id}")
def get_playlist_event_map_canonical(
    playlist_id: uuid.UUID,
    canonical_id: uuid.UUID,
    snapshot_id: uuid.UUID,
) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        snapshot = _resolve_event_map_snapshot(session, playlist_id=playlist_id, snapshot_id=snapshot_id)
        canonical = session.get(
            EventMapCanonical,
            {"snapshot_id": snapshot.id, "canonical_id": canonical_id},
        )
        if canonical is None:
            raise HTTPException(status_code=404, detail="canonical event not found")
        topic = (
            session.execute(
                select(EventMapTopic)
                .join(
                    EventMapTopicMember,
                    and_(
                        EventMapTopicMember.snapshot_id == EventMapTopic.snapshot_id,
                        EventMapTopicMember.topic_id == EventMapTopic.topic_id,
                    ),
                )
                .where(
                    EventMapTopicMember.snapshot_id == snapshot.id,
                    EventMapTopicMember.canonical_id == canonical_id,
                    EventMapTopicMember.level == 0,
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        member_rows = session.execute(
            select(EventMapCanonicalMember, EventMapRecordRevision)
            .join(
                EventMapRecordRevision,
                EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id,
            )
            .where(
                EventMapCanonicalMember.snapshot_id == snapshot.id,
                EventMapCanonicalMember.canonical_id == canonical_id,
            )
            .order_by(
                EventMapCanonicalMember.is_representative.desc(),
                EventMapRecordRevision.event_time_start.asc(),
                EventMapRecordRevision.id.asc(),
            )
            .limit(100)
        ).all()
        members = [_event_map_record_payload(revision, member) for member, revision in member_rows]

        entity_values = {
            (row.entity_type, row.normalized_key): {
                "id": f"{row.entity_type}:{row.normalized_key}",
                "entity_type": row.entity_type,
                "normalized_key": row.normalized_key,
                "name": row.name,
                "record_count": int(row.record_count or 0),
            }
            for row in session.execute(
                select(EventMapEntityIndex)
                .where(
                    EventMapEntityIndex.snapshot_id == snapshot.id,
                    EventMapEntityIndex.canonical_id == canonical_id,
                )
                .order_by(
                    EventMapEntityIndex.record_count.desc(),
                    EventMapEntityIndex.entity_type.asc(),
                    EventMapEntityIndex.normalized_key.asc(),
                )
            ).scalars()
        }
        entity_relations = _event_map_local_entity_relations(member_rows, entity_values)
        evidence: list[dict[str, Any]] = []
        for _, revision in member_rows:
            for item in list(revision.evidence_json or []):
                if len(evidence) >= 24:
                    break
                payload = dict(item)
                payload.setdefault("id", f"{revision.id}:{len(evidence)}")
                payload.setdefault("text", payload.get("evidence_text"))
                evidence.append(payload)

        edge_rows = session.execute(
            select(EventMapStoryEdge).where(
                EventMapStoryEdge.snapshot_id == snapshot.id,
                or_(
                    EventMapStoryEdge.source_canonical_id == canonical_id,
                    EventMapStoryEdge.target_canonical_id == canonical_id,
                ),
            )
            .order_by(EventMapStoryEdge.score.desc().nullslast(), EventMapStoryEdge.edge_id.asc())
            .limit(24)
        ).scalars().all()
        other_ids = {
            edge.target_canonical_id if edge.source_canonical_id == canonical_id else edge.source_canonical_id
            for edge in edge_rows
        }
        other_canonicals = {
            row.canonical_id: row
            for row in session.execute(
                select(EventMapCanonical).where(
                    EventMapCanonical.snapshot_id == snapshot.id,
                    EventMapCanonical.canonical_id.in_(other_ids),
                )
            ).scalars()
        } if other_ids else {}
        story_ids = {edge.story_id for edge in edge_rows}
        stories = {
            row.story_id: row
            for row in session.execute(
                select(EventMapStory).where(
                    EventMapStory.snapshot_id == snapshot.id,
                    EventMapStory.story_id.in_(story_ids),
                )
            ).scalars()
        } if story_ids else {}
        story_edges: list[dict[str, Any]] = []
        for edge in edge_rows:
            outgoing = edge.source_canonical_id == canonical_id
            other_id = edge.target_canonical_id if outgoing else edge.source_canonical_id
            other = other_canonicals.get(other_id)
            story = stories.get(edge.story_id)
            story_edges.append(
                {
                    "id": str(edge.edge_id),
                    "story_id": str(edge.story_id),
                    "type": edge.relation_type,
                    "status": edge.status,
                    "score": edge.score,
                    "direction": "outgoing" if outgoing else "incoming",
                    "other_canonical_id": str(other_id),
                    "other_point_index": int(other.point_index) if other is not None else None,
                    "other_title": other.title if other is not None else None,
                    "story_title": story.title if story is not None else None,
                }
            )
        return {
            "snapshot_id": str(snapshot.id),
            "canonical_id": str(canonical.canonical_id),
            "point_index": int(canonical.point_index),
            "title": canonical.title,
            "summary": canonical.summary,
            "event_type": canonical.event_type,
            "event_time_start": canonical.event_time_start,
            "event_time_end": canonical.event_time_end,
            "time_precision": canonical.time_precision,
            "occurrence_label": (
                f"{canonical.event_time_start.date().isoformat()} ～ {canonical.event_time_end.date().isoformat()}"
            ),
            "member_count": int(canonical.member_count or 0),
            "identity_state": canonical.identity_state,
            "decision_score": canonical.decision_score,
            "reason_codes": canonical.reason_codes or [],
            "uncertainty_flags": canonical.uncertainty_flags or [],
            "topic": (
                {
                    "topic_id": str(topic.topic_id),
                    "label": topic.label,
                    "top_terms": topic.top_terms or [],
                }
                if topic is not None
                else None
            ),
            "members": members,
            "story_edges": story_edges,
            "entities": list(entity_values.values()),
            "entity_relations": entity_relations,
            "evidence": evidence,
        }


@router.get("/playlists/{playlist_id}/events/map/topic/{topic_id}")
def get_playlist_event_map_topic(
    playlist_id: uuid.UUID,
    topic_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    start_date: date | None = None,
    end_date: date | None = None,
    event_type_code: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=400, detail="invalid date range")
    with session_scope() as session:
        snapshot = _resolve_event_map_snapshot(session, playlist_id=playlist_id, snapshot_id=snapshot_id)
        topic = session.get(EventMapTopic, {"snapshot_id": snapshot.id, "topic_id": topic_id})
        if topic is None:
            raise HTTPException(status_code=404, detail="topic not found")
        _configure_event_map_query(session)
        clauses: list[Any] = [
            EventMapTopicMember.snapshot_id == snapshot.id,
            EventMapTopicMember.topic_id == topic_id,
        ]
        if start_date is not None:
            clauses.append(EventMapCanonical.event_end_day >= _event_map_day(start_date))
        if end_date is not None:
            clauses.append(EventMapCanonical.event_start_day <= _event_map_day(end_date))
        if event_type_code is not None:
            clauses.append(EventMapCanonical.event_type_code == int(event_type_code))
        representatives = session.execute(
            select(EventMapCanonical)
            .join(
                EventMapTopicMember,
                and_(
                    EventMapTopicMember.snapshot_id == EventMapCanonical.snapshot_id,
                    EventMapTopicMember.canonical_id == EventMapCanonical.canonical_id,
                ),
            )
            .where(*clauses)
            .order_by(EventMapTopicMember.score.desc().nullslast(), EventMapCanonical.member_count.desc())
            .limit(max(1, min(24, int(limit or 20))))
        ).scalars().all()
        return {
            "snapshot_id": str(snapshot.id),
            "topic_id": str(topic.topic_id),
            "level": int(topic.level),
            "parent_topic_id": str(topic.parent_topic_id) if topic.parent_topic_id is not None else None,
            "label": topic.label,
            "top_terms": topic.top_terms or [],
            "canonical_count": int(topic.canonical_count or 0),
            "record_count": int(topic.member_count or 0),
            "representatives": [
                {
                    "canonical_id": str(item.canonical_id),
                    "point_index": int(item.point_index),
                    "title": item.title,
                    "event_time_start": item.event_time_start.date().isoformat(),
                    "event_time_end": item.event_time_end.date().isoformat(),
                    "event_type": item.event_type,
                    "member_count": int(item.member_count or 0),
                }
                for item in representatives
            ],
        }


@router.get("/playlists/{playlist_id}/events/map/story/{story_id}")
def get_playlist_event_map_story(
    playlist_id: uuid.UUID,
    story_id: uuid.UUID,
    snapshot_id: uuid.UUID,
) -> dict[str, Any]:
    with session_scope() as session:
        snapshot = _resolve_event_map_snapshot(session, playlist_id=playlist_id, snapshot_id=snapshot_id)
        story = session.get(EventMapStory, {"snapshot_id": snapshot.id, "story_id": story_id})
        if story is None:
            raise HTTPException(status_code=404, detail="story not found")
        edges = session.execute(
            select(EventMapStoryEdge)
            .where(
                EventMapStoryEdge.snapshot_id == snapshot.id,
                EventMapStoryEdge.story_id == story_id,
            )
            .order_by(EventMapStoryEdge.created_at.asc(), EventMapStoryEdge.edge_id.asc())
        ).scalars().all()
        return {
            "snapshot_id": str(snapshot.id),
            "story_id": str(story.story_id),
            "title": story.title,
            "summary": story.summary,
            "story_type": story.story_type,
            "event_time_start": story.event_time_start,
            "event_time_end": story.event_time_end,
            "edges": [
                {
                    "edge_id": str(edge.edge_id),
                    "source_canonical_id": str(edge.source_canonical_id),
                    "target_canonical_id": str(edge.target_canonical_id),
                    "relation_type": edge.relation_type,
                    "status": edge.status,
                    "score": edge.score,
                }
                for edge in edges
            ],
        }


@router.get("/playlists/{playlist_id}/events/map/search")
def search_playlist_event_map(
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    q: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    query = str(q or "").strip()
    if len(query) < 2:
        return []
    capped = max(1, min(60, int(limit or 30)))
    pattern = f"%{query}%"
    with session_scope() as session:
        snapshot = _resolve_event_map_snapshot(session, playlist_id=playlist_id, snapshot_id=snapshot_id)
        results: list[dict[str, Any]] = []
        canonicals = session.execute(
            select(EventMapCanonical)
            .where(
                EventMapCanonical.snapshot_id == snapshot.id,
                or_(
                    EventMapCanonical.title.ilike(pattern),
                    EventMapCanonical.summary.ilike(pattern),
                ),
            )
            .order_by(EventMapCanonical.member_count.desc(), EventMapCanonical.point_index.asc())
            .limit(min(20, capped))
        ).scalars().all()
        results.extend(
            {
                "kind": "canonical",
                "id": str(item.canonical_id),
                "point_index": int(item.point_index),
                "title": item.title,
                "label": item.title,
            }
            for item in canonicals
        )
        topic_rows = session.execute(
            select(EventMapTopic, EventMapCanonical)
            .join(
                EventMapCanonical,
                and_(
                    EventMapCanonical.snapshot_id == EventMapTopic.snapshot_id,
                    EventMapCanonical.canonical_id == EventMapTopic.anchor_canonical_id,
                ),
            )
            .where(
                EventMapTopic.snapshot_id == snapshot.id,
                EventMapTopic.level == 0,
                EventMapTopic.label.ilike(pattern),
            )
            .order_by(EventMapTopic.canonical_count.desc(), EventMapTopic.topic_id.asc())
            .limit(min(10, max(0, capped - len(results))))
        ).all()
        results.extend(
            {
                "kind": "topic",
                "id": str(topic.topic_id),
                "canonical_id": str(anchor.canonical_id),
                "point_index": int(anchor.point_index),
                "title": topic.label,
                "label": topic.label,
            }
            for topic, anchor in topic_rows
        )
        entity_count = func.count(EventMapEntityIndex.canonical_id)
        entity_rows = session.execute(
            select(
                EventMapEntityIndex.entity_type,
                EventMapEntityIndex.normalized_key,
                func.min(EventMapEntityIndex.name),
                func.min(EventMapEntityIndex.point_index),
            )
            .where(
                EventMapEntityIndex.snapshot_id == snapshot.id,
                or_(
                    EventMapEntityIndex.name.ilike(pattern),
                    EventMapEntityIndex.normalized_key.ilike(pattern),
                ),
            )
            .group_by(EventMapEntityIndex.entity_type, EventMapEntityIndex.normalized_key)
            .order_by(
                entity_count.desc(),
                EventMapEntityIndex.entity_type.asc(),
                EventMapEntityIndex.normalized_key.asc(),
            )
            .limit(min(10, max(0, capped - len(results))))
        ).all()
        results.extend(
            {
                "kind": "entity",
                "id": f"{entity_type}:{normalized_key}",
                "entity_type": entity_type,
                "normalized_key": normalized_key,
                "name": name,
                "label": name,
                "point_index": int(point_index) if point_index is not None else None,
            }
            for entity_type, normalized_key, name, point_index in entity_rows
        )
        return results[:capped]


@router.post("/playlists/{playlist_id}/events/map/rebuild")
def rebuild_playlist_event_map(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        existing = _active_event_map_build_job(session, playlist_id)
        job = request_event_map_rebuild(session, playlist_id, priority=1)
        state = _event_map_state(session, playlist_id)
        return {
            "ok": True,
            "created": existing is None or existing.id != job.id,
            "playlist_id": str(playlist_id),
            "job_id": str(job.id),
            "requested_generation": int(state.dirty_generation),
        }


@router.get("/playlists/{playlist_id}/events/export")
def export_playlist_events(
    playlist_id: uuid.UUID,
    level: str = "canonical",
    snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    normalized_level = str(level or "").strip().lower()
    if normalized_level not in {"canonical", "record"}:
        raise HTTPException(status_code=400, detail="level must be canonical or record")
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        snapshot = _resolve_event_map_snapshot(
            session,
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
        )
        if normalized_level == "canonical":
            rows = session.execute(
                select(EventMapCanonical)
                .where(EventMapCanonical.snapshot_id == snapshot.id)
                .order_by(EventMapCanonical.event_time_start.asc(), EventMapCanonical.point_index.asc())
            ).scalars().all()
            events = [
                {
                    "canonical_id": str(row.canonical_id),
                    "event_time_start": row.event_time_start,
                    "event_time_end": row.event_time_end,
                    "time_precision": row.time_precision,
                    "event_type": row.event_type,
                    "title": row.title,
                    "summary": row.summary,
                    "member_count": int(row.member_count or 0),
                }
                for row in rows
            ]
        else:
            rows = session.execute(
                select(EventMapCanonicalMember, EventMapRecordRevision)
                .join(
                    EventMapRecordRevision,
                    EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id,
                )
                .where(EventMapCanonicalMember.snapshot_id == snapshot.id)
                .order_by(
                    EventMapRecordRevision.event_time_start.asc(),
                    EventMapRecordRevision.id.asc(),
                )
            ).all()
            events = [
                {
                    **_event_map_record_payload(revision, member),
                    "canonical_id": str(member.canonical_id),
                }
                for member, revision in rows
            ]
        return {
            "snapshot_id": str(snapshot.id),
            "time_basis": "event_time",
            "level": normalized_level,
            "events": events,
        }


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
            _schedule_playlist_event_map_dirty(session, playlist_id, reason="playlist_media_added")
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
            _schedule_playlist_event_map_dirty(session, playlist_id, reason="playlist_media_removed")
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
            _schedule_playlist_event_map_dirty(session, playlist_id, reason="playlist_media_replaced")
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
