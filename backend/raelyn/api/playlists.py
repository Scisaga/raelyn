from __future__ import annotations

import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import Date, cast, func, select

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
    MarketEventEmbedding,
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
    request_event_regime_rebuild,
    request_playlist_event_backfill,
    update_event_status,
)
from raelyn.services.video_admission import (
    ensure_video_published_at_backfilled,
    playback_admitted_video_expr,
)
from raelyn.services.video_time import (
    content_published_at_expr,
    normalize_time_basis,
    timeline_confidence_expr,
    timeline_source_expr,
    timeline_status_expr,
    timeline_time_expr,
)


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


class MarketEventEvidenceOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    evidence_text: str | None = None
    evidence_json: dict[str, Any] | None = None
    confidence: float | None = None


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
    active_run_id: uuid.UUID | None = None
    last_ready_run_id: uuid.UUID | None = None
    last_requested_at: Any | None = None
    last_built_at: Any | None = None
    last_error: str | None = None
    event_total: int = 0
    event_embedded: int = 0
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


def _playlist_out(session, p: Playlist, *, preview: list[PlaylistMediaOut] | None = None) -> PlaylistOut:
    _ensure_playlist_published_at_backfilled(session)
    out = PlaylistOut.model_validate(p)
    out.brief_granularity = (getattr(p, "brief_granularity", None) or "day").strip() or "day"
    out.avatar_asset = build_asset_ref(session.get(Asset, getattr(p, "avatar_asset_id", None))) if getattr(p, "avatar_asset_id", None) else None
    out.background_asset = (
        build_asset_ref(session.get(Asset, getattr(p, "background_asset_id", None))) if getattr(p, "background_asset_id", None) else None
    )

    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == p.id)).scalars().all()
    out.media_count = len(media_ids)
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
    if media_ids:
        admitted_expr = playback_admitted_video_expr()
        co_ts = timeline_time_expr()
        vcnt, min_ts, max_ts = session.execute(
            select(func.count(), func.min(co_ts), func.max(co_ts))
            .select_from(Video)
            .where(Video.media_id.in_(list(media_ids)), admitted_expr)
        ).one()
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
    jobs = (
        session.execute(
            select(Job)
            .where(Job.type == "playlist.backfill_events", Job.status.in_(["pending", "running"]))
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
    return None


def _playlist_events_summary(session, playlist_id: uuid.UUID) -> PlaylistEventsSummaryOut:
    coverage = playlist_event_coverage(session, playlist_id)
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
        backfill_job=_playlist_event_backfill_job_out(backfill_job) if backfill_job else None,
    )


def _playlist_event_backfill_job_out(job: Job) -> PlaylistEventBackfillJobOut:
    result = job.result if isinstance(job.result, dict) else {}
    return PlaylistEventBackfillJobOut(
        job_id=job.id,
        status=str(job.status or ""),
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        created_at=job.created_at,
        started_at=job.started_at,
        cancel_requested_at=job.cancel_requested_at,
        scanned=int(result.get("scanned") or 0),
        enqueued=int(result.get("enqueued") or 0),
        skipped=int(result.get("skipped") or 0),
        force=bool(result.get("force") or (job.params or {}).get("force")),
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


def _event_out(session, event: MarketEvent, *, include_entities: bool = True) -> MarketEventOut:
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
        entities=_event_entities(session, event.id) if include_entities else [],
    )


def _event_detail_out(session, event: MarketEvent) -> MarketEventDetailOut:
    base = _event_out(session, event).model_dump()
    evidence_rows = (
        session.execute(
            select(MarketEventEvidence)
            .where(MarketEventEvidence.event_id == event.id)
            .order_by(MarketEventEvidence.created_at.asc(), MarketEventEvidence.id.asc())
        )
        .scalars()
        .all()
    )
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
        evidence=[
            MarketEventEvidenceOut(
                id=row.id,
                video_id=row.video_id,
                evidence_text=row.evidence_text,
                evidence_json=row.evidence_json,
                confidence=row.confidence,
            )
            for row in evidence_rows
        ],
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
    last_ready_run = session.get(EventRegimeRun, state.last_ready_run_id) if state.last_ready_run_id else None
    event_total = event_embedded = event_skipped = event_failed = 0
    candidate_count = 0
    signal_start_date = None
    signal_end_date = None
    if last_ready_run:
        event_total = int(last_ready_run.event_total or 0)
        event_embedded = int(last_ready_run.event_embedded or 0)
        event_skipped = int(last_ready_run.event_skipped or 0)
        event_failed = int(last_ready_run.event_failed or 0)
        candidate_count = int(
            session.execute(
                select(func.count()).select_from(EventRegimeCandidate).where(EventRegimeCandidate.regime_run_id == last_ready_run.id)
            ).scalar_one()
            or 0
        )
        signal_start_date, signal_end_date = session.execute(
            select(func.min(EventRegimeSignal.period_date), func.max(EventRegimeSignal.period_date)).where(
                EventRegimeSignal.regime_run_id == last_ready_run.id,
                EventRegimeSignal.granularity == "day",
            )
        ).one()
    else:
        coverage = playlist_event_coverage(session, playlist_id)
        event_total = int(coverage.get("accepted") or 0)
        event_embedded = int(
            session.execute(
                select(func.count())
                .select_from(MarketEventEmbedding)
                .join(MarketEvent, MarketEvent.id == MarketEventEmbedding.event_id)
                .join(Video, Video.id == MarketEvent.source_video_id)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(
                    PlaylistMedia.playlist_id == playlist_id,
                    MarketEvent.status == "accepted",
                    MarketEventEmbedding.status == "ready",
                )
            ).scalar_one()
            or 0
        )
        event_skipped = max(0, event_total - event_embedded)
    return EventRegimeSummaryOut(
        playlist_id=playlist_id,
        analysis_dirty=bool(state.analysis_dirty),
        running=active_run is not None or pending_job is not None,
        active_run_id=getattr(active_run, "id", None),
        last_ready_run_id=state.last_ready_run_id,
        last_requested_at=state.last_requested_at,
        last_built_at=state.last_built_at,
        last_error=state.last_error,
        event_total=event_total,
        event_embedded=event_embedded,
        event_skipped=event_skipped,
        event_failed=event_failed,
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


def _candidate_detail_out(candidate: EventRegimeCandidate) -> EventRegimeCandidateDetailOut:
    base = _candidate_out(candidate)
    return EventRegimeCandidateDetailOut(
        **base.model_dump(),
        evidence=candidate.evidence_json if isinstance(candidate.evidence_json, dict) else {},
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

            admitted_expr = playback_admitted_video_expr()
            # video counts + time range: playback list uses content timeline, with platform time fallback.
            co_ts = timeline_time_expr()
            for pid, vcnt, min_ts, max_ts in session.execute(
                select(
                    PlaylistMedia.playlist_id,
                    func.count(Video.id),
                    func.min(co_ts),
                    func.max(co_ts),
                )
                .join(Video, Video.media_id == PlaylistMedia.media_id)
                .where(PlaylistMedia.playlist_id.in_(playlist_ids), admitted_expr)
                .group_by(PlaylistMedia.playlist_id)
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
        preview_avatar_by_media_id = {item.id: item.avatar_asset for item in out.media_preview if item.avatar_asset}
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
        media_out: list[PlaylistMediaOut] = []
        for m in rows:
            media_out.append(
                PlaylistMediaOut(
                    id=m.id,
                    provider=m.provider,
                    url=m.url,
                    name=m.name,
                    avatar_asset=preview_avatar_by_media_id.get(m.id),
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
            "backfill_job": _playlist_event_backfill_job_out(job).model_dump(),
        }


@router.get("/playlists/{playlist_id}/events/summary", response_model=PlaylistEventsSummaryOut)
def get_playlist_events_summary(playlist_id: uuid.UUID) -> PlaylistEventsSummaryOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        return _playlist_events_summary(session, playlist_id)


@router.get("/playlists/{playlist_id}/events", response_model=list[MarketEventOut])
def list_playlist_events(
    playlist_id: uuid.UUID,
    status: str | None = None,
    event_type: str | None = None,
    entity: str | None = None,
    min_confidence: float | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[MarketEventOut]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        clauses = event_filter_clause(
            playlist_id=playlist_id,
            status=status,
            event_type=event_type,
            entity=entity,
            min_confidence=min_confidence,
        )
        rows = (
            session.execute(
                select(MarketEvent)
                .join(Video, Video.id == MarketEvent.source_video_id)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(*clauses)
                .order_by(
                    MarketEvent.event_time_start.desc().nullslast(),
                    MarketEvent.available_at.desc().nullslast(),
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
        return _candidate_detail_out(candidate)


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
        return _candidate_detail_out(candidate)


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


def _playlist_video_ts_expr(time_basis: str | None = "content"):
    return timeline_time_expr(time_basis=time_basis)


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
) -> PlaylistVideoOut:
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
        media_avatar_asset=_media_avatar_asset(session, media),
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
        media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        if not media_ids:
            return []
        start, end = day_bounds_utc(date)
        ts_expr = _playlist_video_ts_expr(resolved_time_basis)
        content_ts_expr = content_published_at_expr().label("content_published_at")
        time_source = timeline_source_expr(time_basis=resolved_time_basis).label("time_source")
        time_status = timeline_status_expr(time_basis=resolved_time_basis).label("time_status")
        time_confidence = timeline_confidence_expr(time_basis=resolved_time_basis).label("time_confidence")
        admitted_expr = playback_admitted_video_expr()
        rows = (
            session.execute(
                select(Video, Media, ts_expr.label("timeline_at"), content_ts_expr, time_source, time_status, time_confidence)
                .join(Media, Media.id == Video.media_id)
                .where(Video.media_id.in_(list(media_ids)), admitted_expr, ts_expr >= start, ts_expr < end)
                .order_by(ts_expr.asc(), Video.created_at.asc(), Video.id.asc())
            )
            .all()
        )

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
        ts_expr = _playlist_video_ts_expr(resolved_time_basis)
        admitted_expr = playback_admitted_video_expr()
        dialect_name = ""
        try:
            dialect_name = str(session.bind.dialect.name or "").strip().lower()
        except Exception:
            dialect_name = ""

        if dialect_name == "postgresql":
            local_ts = func.timezone(tzname, ts_expr)
            bucket = func.date_trunc(unit, local_ts)
            period_start_expr = cast(bucket, Date)
            rows = (
                session.execute(
                    select(
                        period_start_expr.label("period_start"),
                        func.count(Video.id).label("count"),
                    )
                    .select_from(Video)
                    .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                    .where(
                        PlaylistMedia.playlist_id == playlist_id,
                        admitted_expr,
                        ts_expr >= start_utc,
                        ts_expr < end_utc,
                    )
                    .group_by(period_start_expr)
                    .order_by(period_start_expr.asc())
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
                select(ts_expr.label("timeline_at"))
                .select_from(Video)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(
                    PlaylistMedia.playlist_id == playlist_id,
                    admitted_expr,
                    ts_expr >= start_utc,
                    ts_expr < end_utc,
                )
                .order_by(ts_expr.asc(), Video.id.asc())
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
        media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        if not media_ids:
            return []
        ts_expr = _playlist_video_ts_expr(resolved_time_basis)
        content_ts_expr = content_published_at_expr().label("content_published_at")
        time_source = timeline_source_expr(time_basis=resolved_time_basis).label("time_source")
        time_status = timeline_status_expr(time_basis=resolved_time_basis).label("time_status")
        time_confidence = timeline_confidence_expr(time_basis=resolved_time_basis).label("time_confidence")
        admitted_expr = playback_admitted_video_expr()
        rows = (
            session.execute(
                select(Video, Media, ts_expr.label("timeline_at"), content_ts_expr, time_source, time_status, time_confidence)
                .join(Media, Media.id == Video.media_id)
                .where(
                    Video.media_id.in_(list(media_ids)),
                    admitted_expr,
                    ts_expr >= start,
                    ts_expr < end,
                )
                .order_by(ts_expr.asc(), Video.created_at.asc(), Video.id.asc())
                .limit(n)
            )
            .all()
        )

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
                )
            )
        return out
