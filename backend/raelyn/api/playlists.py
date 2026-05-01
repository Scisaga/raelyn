from __future__ import annotations

import tempfile
import uuid
import math
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
    Media,
    Playlist,
    PlaylistAnalysisCandidate,
    PlaylistAnalysisPeriod,
    PlaylistAnalysisRun,
    PlaylistAnalysisSignal,
    PlaylistAnalysisState,
    PlaylistMedia,
    Video,
    Job,
)
from raelyn.services.brief_schedule import schedule_brief_refresh_for_media_change
from raelyn.services.assets import replace_standalone_asset
from raelyn.services.periods import day_bounds_utc, local_date, normalize_granularity, period_bounds_utc, period_start
from raelyn.services.playlist_analysis import (
    active_playlist_analysis_run,
    active_playlist_embedding_backfill_job,
    ensure_playlist_analysis_state,
    mark_playlist_analysis_dirty,
    pending_playlist_analysis_job,
    playlist_coverage_stats,
    request_playlist_embedding_backfill,
    request_playlist_analysis_rebuild,
)
from raelyn.services.video_admission import (
    ensure_video_published_at_backfilled,
    playback_admitted_video_expr,
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


class PlaylistAnalysisBackfillJobOut(BaseModel):
    job_id: uuid.UUID
    status: str
    progress_current: int | None = None
    progress_total: int | None = None
    created_at: Any
    started_at: Any | None = None
    cancel_requested_at: Any | None = None
    scanned: int = 0
    selected: int = 0
    embedded: int = 0
    skipped_empty: int = 0
    skipped_over_budget: int = 0
    embedding_batches: int = 0
    remote_errors: int = 0
    force: bool = False


class PlaylistAnalysisSummaryOut(BaseModel):
    playlist_id: uuid.UUID
    analysis_dirty: bool
    running: bool
    active_run_id: uuid.UUID | None = None
    last_ready_run_id: uuid.UUID | None = None
    last_requested_at: Any | None = None
    last_built_at: Any | None = None
    last_error: str | None = None
    video_total: int = 0
    video_embedded: int = 0
    video_skipped: int = 0
    video_failed: int = 0
    period_count: int = 0
    candidate_count: int = 0
    signal_start_date: date | None = None
    signal_end_date: date | None = None
    backfill_job: PlaylistAnalysisBackfillJobOut | None = None


class PlaylistEmbeddingBackfillRequest(BaseModel):
    force: bool = False
    batch_size: int | None = None


class PlaylistAnalysisPeriodOut(BaseModel):
    id: uuid.UUID
    period_date: date
    video_count: int
    drift_score: float | None = None
    dispersion_score: float | None = None
    drift_rolling_mean: float | None = None
    drift_rolling_std: float | None = None
    drift_rolling_z: float | None = None
    dispersion_std: float | None = None
    dispersion_p25: float | None = None
    dispersion_p75: float | None = None
    projection_x: float | None = None
    projection_y: float | None = None
    projection_z: float | None = None


class PlaylistAnalysisSignalOut(BaseModel):
    id: uuid.UUID
    granularity: str
    period_date: date
    rolling_window: int
    video_count: int
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
    linked_event_id: uuid.UUID | None = None


class PlaylistAnalysisCandidateOut(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    event_date: date
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
    detection_method: str | None = None
    detection_granularity: str | None = None
    boundary_score: float | None = None
    boundary_z: float | None = None
    before_start: date | None = None
    before_end: date | None = None
    after_start: date | None = None
    after_end: date | None = None
    supporting_granularities: list[str] = Field(default_factory=list)
    summary: str | None = None
    top_terms: list[str] = Field(default_factory=list)
    evidence_video_ids: list[str] = Field(default_factory=list)
    evidence_preview: str = ""
    available_at: Any | None = None


class PlaylistAnalysisCandidateDetailOut(PlaylistAnalysisCandidateOut):
    evidence: dict[str, Any] = Field(default_factory=dict)


class PlaylistAnalysisCandidatePatch(BaseModel):
    status: str | None = None
    candidate_date: date | None = None
    effective_trade_date: date | None = None
    event_type: str | None = None


class PlaylistAnalysisEvidenceOut(BaseModel):
    event_id: uuid.UUID
    video_id: str
    media_id: str | None = None
    title: str | None = None
    media_name: str | None = None
    published_at: str | None = None
    distance_to_centroid: float | None = None
    shift_score: float | None = None


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
        vcnt, min_ts, max_ts = session.execute(
            select(func.count(), func.min(Video.published_at), func.max(Video.published_at))
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


def _playlist_analysis_summary(session, playlist_id: uuid.UUID) -> PlaylistAnalysisSummaryOut:
    state = ensure_playlist_analysis_state(session, playlist_id)
    active_run = active_playlist_analysis_run(session, playlist_id)
    pending_job = pending_playlist_analysis_job(session, playlist_id)
    backfill_job = active_playlist_embedding_backfill_job(session, playlist_id)
    last_ready_run = session.get(PlaylistAnalysisRun, state.last_ready_run_id) if state.last_ready_run_id else None
    if last_ready_run:
        video_total = int(last_ready_run.video_total or 0)
        video_embedded = int(last_ready_run.video_embedded or 0)
        video_skipped = int(last_ready_run.video_skipped or 0)
        video_failed = int(last_ready_run.video_failed or 0)
    else:
        coverage = playlist_coverage_stats(session, playlist_id)
        video_total = coverage.video_total
        video_embedded = coverage.video_embedded
        video_skipped = coverage.video_skipped
        video_failed = coverage.video_failed
    period_count = 0
    candidate_count = 0
    signal_start_date = None
    signal_end_date = None
    if last_ready_run:
        period_count = int(
            session.execute(select(func.count()).select_from(PlaylistAnalysisPeriod).where(PlaylistAnalysisPeriod.analysis_run_id == last_ready_run.id)).scalar_one()
            or 0
        )
        candidate_count = int(
            session.execute(
                select(func.count()).select_from(PlaylistAnalysisCandidate).where(PlaylistAnalysisCandidate.analysis_run_id == last_ready_run.id)
            ).scalar_one()
            or 0
        )
        signal_start_date, signal_end_date = session.execute(
            select(func.min(PlaylistAnalysisSignal.period_date), func.max(PlaylistAnalysisSignal.period_date)).where(
                PlaylistAnalysisSignal.analysis_run_id == last_ready_run.id,
                PlaylistAnalysisSignal.granularity == "day",
            )
        ).one()
    return PlaylistAnalysisSummaryOut(
        playlist_id=playlist_id,
        analysis_dirty=bool(state.analysis_dirty),
        running=active_run is not None or pending_job is not None,
        active_run_id=getattr(active_run, "id", None),
        last_ready_run_id=state.last_ready_run_id,
        last_requested_at=state.last_requested_at,
        last_built_at=state.last_built_at,
        last_error=state.last_error,
        video_total=video_total,
        video_embedded=video_embedded,
        video_skipped=video_skipped,
        video_failed=video_failed,
        period_count=period_count,
        candidate_count=candidate_count,
        signal_start_date=signal_start_date,
        signal_end_date=signal_end_date,
        backfill_job=_playlist_analysis_backfill_job_out(backfill_job) if backfill_job else None,
    )


def _playlist_analysis_backfill_job_out(job: Job) -> PlaylistAnalysisBackfillJobOut:
    result = job.result if isinstance(job.result, dict) else {}
    return PlaylistAnalysisBackfillJobOut(
        job_id=job.id,
        status=str(job.status or ""),
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        created_at=job.created_at,
        started_at=job.started_at,
        cancel_requested_at=job.cancel_requested_at,
        scanned=int(result.get("scanned") or 0),
        selected=int(result.get("selected") or 0),
        embedded=int(result.get("embedded") or 0),
        skipped_empty=int(result.get("skipped_empty") or 0),
        skipped_over_budget=int(result.get("skipped_over_budget") or 0),
        embedding_batches=int(result.get("embedding_batches") or 0),
        remote_errors=int(result.get("remote_errors") or 0),
        force=bool(result.get("force") or (job.params or {}).get("force")),
    )


def _playlist_last_ready_run_id(session, playlist_id: uuid.UUID) -> uuid.UUID | None:
    state = ensure_playlist_analysis_state(session, playlist_id)
    return state.last_ready_run_id


def _candidate_detection_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _candidate_detection_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _candidate_out(candidate: PlaylistAnalysisCandidate) -> PlaylistAnalysisCandidateOut:
    evidence = candidate.evidence_json if isinstance(candidate.evidence_json, dict) else {}
    detection = evidence.get("detection") if isinstance(evidence.get("detection"), dict) else {}
    top_terms = getattr(candidate, "top_terms", None) if isinstance(getattr(candidate, "top_terms", None), list) else []
    evidence_video_ids = (
        getattr(candidate, "evidence_video_ids", None)
        if isinstance(getattr(candidate, "evidence_video_ids", None), list)
        else []
    )
    return PlaylistAnalysisCandidateOut(
        id=candidate.id,
        event_id=candidate.id,
        event_date=candidate.candidate_date,
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
        breakpoint_date=_candidate_detection_date(detection.get("breakpoint_date")),
        detection_method=str(detection.get("method") or "").strip() or None,
        detection_granularity=str(detection.get("granularity") or "").strip() or None,
        boundary_score=_candidate_detection_float(detection.get("boundary_score")),
        boundary_z=_candidate_detection_float(detection.get("boundary_z")),
        before_start=_candidate_detection_date(detection.get("before_start")),
        before_end=_candidate_detection_date(detection.get("before_end")),
        after_start=_candidate_detection_date(detection.get("after_start")),
        after_end=_candidate_detection_date(detection.get("after_end")),
        supporting_granularities=[
            str(item)
            for item in (
                detection.get("supporting_granularities")
                if isinstance(detection.get("supporting_granularities"), list)
                else []
            )
        ],
        summary=getattr(candidate, "summary", None),
        top_terms=[str(item) for item in top_terms],
        evidence_video_ids=[str(item) for item in evidence_video_ids],
        evidence_preview=str(evidence.get("preview") or "").strip(),
        available_at=getattr(candidate, "available_at", None),
    )


def _candidate_detail_out(candidate: PlaylistAnalysisCandidate) -> PlaylistAnalysisCandidateDetailOut:
    base = _candidate_out(candidate)
    return PlaylistAnalysisCandidateDetailOut(
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
            mark_playlist_analysis_dirty(session, playlist.id)

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
            # video counts + time range: playback list only includes published videos with video assets.
            co_ts = Video.published_at
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


@router.get("/playlists/{playlist_id}/analysis/summary", response_model=PlaylistAnalysisSummaryOut)
def get_playlist_analysis_summary(playlist_id: uuid.UUID) -> PlaylistAnalysisSummaryOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        return _playlist_analysis_summary(session, playlist_id)


@router.post("/playlists/{playlist_id}/analysis/rebuild")
def rebuild_playlist_analysis(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        job_id, created = request_playlist_analysis_rebuild(session, playlist_id=playlist_id, priority=1)
        return {
            "ok": True,
            "playlist_id": str(playlist_id),
            "job_id": str(job_id) if job_id else None,
            "created": bool(created),
        }


@router.post("/playlists/{playlist_id}/analysis/backfill_embeddings")
def backfill_playlist_analysis_embeddings(
    playlist_id: uuid.UUID,
    payload: PlaylistEmbeddingBackfillRequest | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        data = payload or PlaylistEmbeddingBackfillRequest()
        job_id, created = request_playlist_embedding_backfill(
            session,
            playlist_id=playlist_id,
            force=bool(data.force),
            batch_size=data.batch_size,
            priority=1,
        )
        job = session.get(Job, job_id)
        return {
            "ok": True,
            "playlist_id": str(playlist_id),
            "job_id": str(job_id),
            "created": bool(created),
            "backfill_job": _playlist_analysis_backfill_job_out(job).model_dump() if job else None,
        }


@router.get("/playlists/{playlist_id}/analysis/periods", response_model=list[PlaylistAnalysisPeriodOut])
def get_playlist_analysis_periods(playlist_id: uuid.UUID) -> list[PlaylistAnalysisPeriodOut]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_run_id(session, playlist_id)
        if not last_ready_run_id:
            return []
        rows = (
            session.execute(
                select(
                    PlaylistAnalysisPeriod.id,
                    PlaylistAnalysisPeriod.period_date,
                    PlaylistAnalysisPeriod.video_count,
                    PlaylistAnalysisPeriod.drift_score,
                    PlaylistAnalysisPeriod.dispersion_score,
                    PlaylistAnalysisPeriod.drift_rolling_mean,
                    PlaylistAnalysisPeriod.drift_rolling_std,
                    PlaylistAnalysisPeriod.drift_rolling_z,
                    PlaylistAnalysisPeriod.dispersion_std,
                    PlaylistAnalysisPeriod.dispersion_p25,
                    PlaylistAnalysisPeriod.dispersion_p75,
                    PlaylistAnalysisPeriod.projection_x,
                    PlaylistAnalysisPeriod.projection_y,
                    PlaylistAnalysisPeriod.projection_z,
                )
                .where(PlaylistAnalysisPeriod.analysis_run_id == last_ready_run_id)
                .order_by(PlaylistAnalysisPeriod.period_date.asc(), PlaylistAnalysisPeriod.id.asc())
            )
            .all()
        )
        return [
            PlaylistAnalysisPeriodOut(
                id=row.id,
                period_date=row.period_date,
                video_count=row.video_count,
                drift_score=row.drift_score,
                dispersion_score=row.dispersion_score,
                drift_rolling_mean=row.drift_rolling_mean,
                drift_rolling_std=row.drift_rolling_std,
                drift_rolling_z=row.drift_rolling_z,
                dispersion_std=row.dispersion_std,
                dispersion_p25=row.dispersion_p25,
                dispersion_p75=row.dispersion_p75,
                projection_x=row.projection_x,
                projection_y=row.projection_y,
                projection_z=row.projection_z,
            )
            for row in rows
        ]


@router.get("/playlists/{playlist_id}/analysis/signals", response_model=list[PlaylistAnalysisSignalOut])
def get_playlist_analysis_signals(
    playlist_id: uuid.UUID,
    granularity: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[PlaylistAnalysisSignalOut]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_run_id(session, playlist_id)
        if not last_ready_run_id:
            return []
        stmt = select(PlaylistAnalysisSignal).where(PlaylistAnalysisSignal.analysis_run_id == last_ready_run_id)
        if granularity:
            try:
                normalized = normalize_granularity(granularity)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            stmt = stmt.where(PlaylistAnalysisSignal.granularity == normalized)
        if since is not None:
            stmt = stmt.where(PlaylistAnalysisSignal.period_date >= since)
        if until is not None:
            stmt = stmt.where(PlaylistAnalysisSignal.period_date <= until)
        if since is not None and until is not None and since > until:
            raise HTTPException(status_code=400, detail="invalid date range")
        rows = (
            session.execute(
                stmt.order_by(
                    PlaylistAnalysisSignal.granularity.asc(),
                    PlaylistAnalysisSignal.period_date.asc(),
                    PlaylistAnalysisSignal.id.asc(),
                )
            )
            .scalars()
            .all()
        )
        return [
            PlaylistAnalysisSignalOut(
                id=row.id,
                granularity=row.granularity,
                period_date=row.period_date,
                rolling_window=row.rolling_window,
                video_count=row.video_count,
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
                linked_event_id=row.linked_event_id,
            )
            for row in rows
        ]


@router.get("/playlists/{playlist_id}/analysis/candidates", response_model=list[PlaylistAnalysisCandidateOut])
def get_playlist_analysis_candidates(playlist_id: uuid.UUID) -> list[PlaylistAnalysisCandidateOut]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_run_id(session, playlist_id)
        if not last_ready_run_id:
            return []
        rows = (
            session.execute(
                select(PlaylistAnalysisCandidate)
                .where(PlaylistAnalysisCandidate.analysis_run_id == last_ready_run_id)
                .order_by(PlaylistAnalysisCandidate.candidate_date.asc(), PlaylistAnalysisCandidate.id.asc())
            )
            .scalars()
            .all()
        )
        return [_candidate_out(row) for row in rows]


@router.get("/playlists/{playlist_id}/analysis/events", response_model=list[PlaylistAnalysisCandidateOut])
def get_playlist_analysis_events(playlist_id: uuid.UUID) -> list[PlaylistAnalysisCandidateOut]:
    return get_playlist_analysis_candidates(playlist_id)


@router.get("/playlists/{playlist_id}/analysis/candidates/{candidate_id}", response_model=PlaylistAnalysisCandidateDetailOut)
def get_playlist_analysis_candidate(playlist_id: uuid.UUID, candidate_id: uuid.UUID) -> PlaylistAnalysisCandidateDetailOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_run_id(session, playlist_id)
        if not last_ready_run_id:
            raise HTTPException(status_code=404, detail="analysis snapshot not found")
        candidate = session.get(PlaylistAnalysisCandidate, candidate_id)
        if not candidate or candidate.analysis_run_id != last_ready_run_id:
            raise HTTPException(status_code=404, detail="candidate not found")
        return _candidate_detail_out(candidate)


@router.patch("/playlists/{playlist_id}/analysis/candidates/{candidate_id}", response_model=PlaylistAnalysisCandidateDetailOut)
def patch_playlist_analysis_candidate(
    playlist_id: uuid.UUID,
    candidate_id: uuid.UUID,
    payload: PlaylistAnalysisCandidatePatch,
) -> PlaylistAnalysisCandidateDetailOut:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_run_id(session, playlist_id)
        if not last_ready_run_id:
            raise HTTPException(status_code=404, detail="analysis snapshot not found")
        candidate = session.get(PlaylistAnalysisCandidate, candidate_id)
        if not candidate or candidate.analysis_run_id != last_ready_run_id:
            raise HTTPException(status_code=404, detail="candidate not found")
        if payload.status is not None:
            status = str(payload.status or "").strip().lower()
            if status not in {"draft", "confirmed", "rejected"}:
                raise HTTPException(status_code=400, detail="invalid status")
            candidate.status = status
        if payload.event_type is not None:
            event_type = str(payload.event_type or "").strip().lower()
            if event_type not in {"burst", "transition", "regime"}:
                raise HTTPException(status_code=400, detail="invalid event_type")
            candidate.event_type = event_type
        for field in ["candidate_date", "effective_trade_date"]:
            if field in payload.model_fields_set:
                setattr(candidate, field, getattr(payload, field))
        session.flush([candidate])
        return _candidate_detail_out(candidate)


@router.get("/playlists/{playlist_id}/analysis/evidence", response_model=list[PlaylistAnalysisEvidenceOut])
def get_playlist_analysis_evidence(
    playlist_id: uuid.UUID,
    event_id: uuid.UUID | None = None,
) -> list[PlaylistAnalysisEvidenceOut]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_run_id(session, playlist_id)
        if not last_ready_run_id:
            return []
        stmt = select(PlaylistAnalysisCandidate).where(PlaylistAnalysisCandidate.analysis_run_id == last_ready_run_id)
        if event_id is not None:
            stmt = stmt.where(PlaylistAnalysisCandidate.id == event_id)
        candidates = (
            session.execute(stmt.order_by(PlaylistAnalysisCandidate.candidate_date.asc(), PlaylistAnalysisCandidate.id.asc()))
            .scalars()
            .all()
        )
        evidence_rows: list[PlaylistAnalysisEvidenceOut] = []
        for candidate in candidates:
            evidence = candidate.evidence_json if isinstance(candidate.evidence_json, dict) else {}
            for item in evidence.get("videos") or []:
                evidence_rows.append(
                    PlaylistAnalysisEvidenceOut(
                        event_id=candidate.id,
                        video_id=str(item.get("video_id") or ""),
                        media_id=str(item.get("media_id") or "") or None,
                        title=item.get("title"),
                        media_name=item.get("media_name"),
                        published_at=item.get("published_at"),
                        distance_to_centroid=item.get("distance_to_centroid"),
                        shift_score=item.get("shift_score"),
                    )
                )
        return evidence_rows


@router.get("/playlists/{playlist_id}/analysis/export/explicit-event-windows")
def export_playlist_analysis_windows(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        if not session.get(Playlist, playlist_id):
            raise HTTPException(status_code=404, detail="playlist not found")
        last_ready_run_id = _playlist_last_ready_run_id(session, playlist_id)
        if not last_ready_run_id:
            return {"legacy": True, "window_mode": "explicit_event", "explicit_event_windows": []}
        rows = (
            session.execute(
                select(PlaylistAnalysisCandidate)
                .where(
                    PlaylistAnalysisCandidate.analysis_run_id == last_ready_run_id,
                    PlaylistAnalysisCandidate.status == "confirmed",
                )
                .order_by(PlaylistAnalysisCandidate.candidate_date.asc(), PlaylistAnalysisCandidate.id.asc())
            )
            .scalars()
            .all()
        )
        windows = []
        for row in rows:
            windows.append(
                {
                    "event_id": str(row.id),
                    "event_date": row.candidate_date.isoformat(),
                    "effective_trade_date": row.effective_trade_date.isoformat(),
                    "train_start": row.train_start.isoformat() if row.train_start else None,
                    "train_end": row.train_end.isoformat() if row.train_end else None,
                    "valid_start": row.valid_start.isoformat() if row.valid_start else None,
                    "valid_end": row.valid_end.isoformat() if row.valid_end else None,
                    "test_start": row.test_start.isoformat() if row.test_start else None,
                    "test_end": row.test_end.isoformat() if row.test_end else None,
                }
            )
        return {"legacy": True, "window_mode": "explicit_event", "explicit_event_windows": windows}


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
            mark_playlist_analysis_dirty(session, playlist_id)
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
            mark_playlist_analysis_dirty(session, playlist_id)
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
            mark_playlist_analysis_dirty(session, playlist_id)
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
    timeline_at: Any | None = None
    duration_sec: int | None = None
    status: str
    error_message: str | None = None
    media_name: str | None = None
    media_avatar_asset: AssetRef | None = None


class PlaylistPeriodCountOut(BaseModel):
    period_start: date
    count: int


def _playlist_video_ts_expr():
    return Video.published_at


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


def _playlist_video_out(session, video: Video, media: Media, *, timeline_at: Any | None = None) -> PlaylistVideoOut:
    return PlaylistVideoOut(
        id=video.id,
        media_id=video.media_id,
        url=video.url,
        title=video.title,
        description=_playlist_video_description(video),
        thumbnail_url=video.thumbnail_url,
        published_at=video.published_at,
        timeline_at=timeline_at or video.published_at,
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
def list_playlist_videos_by_date(playlist_id: uuid.UUID, date: date) -> list[PlaylistVideoOut]:
    with session_scope() as session:
        _ensure_playlist_published_at_backfilled(session)
        media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        if not media_ids:
            return []
        start, end = day_bounds_utc(date)
        ts_expr = _playlist_video_ts_expr()
        admitted_expr = playback_admitted_video_expr()
        rows = (
            session.execute(
                select(Video, Media, ts_expr.label("timeline_at"))
                .join(Media, Media.id == Video.media_id)
                .where(Video.media_id.in_(list(media_ids)), admitted_expr, ts_expr >= start, ts_expr < end)
                .order_by(ts_expr.asc(), Video.created_at.asc(), Video.id.asc())
            )
            .all()
        )

        out: list[PlaylistVideoOut] = []
        for v, m, timeline_at in rows:
            out.append(_playlist_video_out(session, v, m, timeline_at=timeline_at))
        return out


@router.get("/playlists/{playlist_id}/video_counts_by_period", response_model=list[PlaylistPeriodCountOut])
def list_playlist_video_counts_by_period(
    playlist_id: uuid.UUID,
    granularity: str = "day",
    start: date = ...,
    end: date = ...,
) -> list[PlaylistPeriodCountOut]:
    try:
        g = normalize_granularity(granularity)
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
        ts_expr = _playlist_video_ts_expr()
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
) -> list[PlaylistVideoOut]:
    try:
        g = normalize_granularity(granularity)
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
        ts_expr = _playlist_video_ts_expr()
        admitted_expr = playback_admitted_video_expr()
        rows = (
            session.execute(
                select(Video, Media, ts_expr.label("timeline_at"))
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
        for v, m, timeline_at in rows:
            out.append(_playlist_video_out(session, v, m, timeline_at=timeline_at))
        return out
