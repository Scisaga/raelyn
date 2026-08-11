from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import time
import uuid
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session, aliased

from raelyn.jobs.enqueue import enqueue_job
from raelyn.jobs.reschedule import JobTerminalFailure
from raelyn.models import (
    Job,
    MarketEvent,
    MarketEventEmbedding,
    PlaylistMedia,
    Video,
)
from raelyn.services.embeddings import (
    EmbeddingError,
    EmbeddingOverBudgetError,
    EmbeddingSpec,
    EmbeddingTransientError,
    embed_texts,
    embedding_spec,
    validate_embedding_vector,
)
from raelyn.services.event_analysis import event_embedding_texts
from raelyn.services.job_cancellation import JobCancelRequested
from raelyn.services.worker_role_pause import clear_worker_role_pause, get_worker_role_pause
from raelyn.timeutil import utcnow


EVENT_EMBEDDING_BACKFILL_BATCH_SIZE = 64
EVENT_EMBEDDING_BACKFILL_LEASE_SECONDS = 3600


@dataclass(frozen=True)
class EventEmbeddingCandidate:
    event_id: uuid.UUID
    text: str
    checksum: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _owned_running_job(
    session: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    lock: bool,
) -> Job:
    statement = select(Job).where(
        Job.id == job_id,
        Job.status == "running",
        Job.worker_id == worker_id,
        Job.execution_token == execution_token,
    )
    if lock:
        statement = statement.with_for_update()
    owned_job = session.execute(statement).scalar_one_or_none()
    if owned_job is None:
        raise JobTerminalFailure("event embedding backfill lost job ownership")
    if owned_job.cancel_requested_at is not None:
        raise JobCancelRequested("cancel requested")
    return owned_job


def _ready_target_join_condition(target, *, model: str, dim: int):
    return and_(
        target.event_id == MarketEvent.id,
        target.embedding_model == model,
        target.embedding_dim == dim,
        target.status == "ready",
        target.vector.is_not(None),
    )


def _load_candidate_events(
    session: Session,
    *,
    source_model: str,
    target_model: str,
    embedding_dim: int,
    batch_size: int,
) -> list[MarketEvent]:
    source = aliased(MarketEventEmbedding)
    target = aliased(MarketEventEmbedding)
    events = (
        session.execute(
            select(MarketEvent)
            .join(source, source.event_id == MarketEvent.id)
            .outerjoin(
                target,
                _ready_target_join_condition(target, model=target_model, dim=embedding_dim),
            )
            .where(
                MarketEvent.status == "accepted",
                source.embedding_model == source_model,
                source.embedding_dim == embedding_dim,
                source.status == "ready",
                target.id.is_(None),
            )
            .limit(batch_size)
        )
        .scalars()
        .all()
    )
    if events:
        return events

    target = aliased(MarketEventEmbedding)
    return (
        session.execute(
            select(MarketEvent)
            .outerjoin(
                target,
                _ready_target_join_condition(target, model=target_model, dim=embedding_dim),
            )
            .where(MarketEvent.status == "accepted", target.id.is_(None))
            .limit(batch_size)
        )
        .scalars()
        .all()
    )


def _load_candidates(
    session: Session,
    *,
    source_model: str,
    target_model: str,
    embedding_dim: int,
    batch_size: int,
) -> list[EventEmbeddingCandidate]:
    events = _load_candidate_events(
        session,
        source_model=source_model,
        target_model=target_model,
        embedding_dim=embedding_dim,
        batch_size=batch_size,
    )
    texts = event_embedding_texts(session, events)
    return [
        EventEmbeddingCandidate(
            event_id=event.id,
            text=texts[event.id],
            checksum=_sha256_text(texts[event.id]),
        )
        for event in events
    ]


def validate_embedding_batch(
    vectors: list[list[float]],
    *,
    expected_count: int,
    model: str,
    dim: int,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingError(
            f"embedding response count {len(vectors)} does not match request count {expected_count}"
        )
    spec = EmbeddingSpec(model=model, dim=dim)
    return [validate_embedding_vector(vector, spec) for vector in vectors]


def _accepted_and_ready_counts(
    session: Session,
    *,
    target_model: str,
    embedding_dim: int,
) -> tuple[int, int]:
    accepted_total = int(
        session.execute(
            select(func.count(MarketEvent.id)).where(MarketEvent.status == "accepted")
        ).scalar_one()
        or 0
    )
    ready_total = int(
        session.execute(
            select(func.count(MarketEvent.id))
            .join(MarketEventEmbedding, MarketEventEmbedding.event_id == MarketEvent.id)
            .where(
                MarketEvent.status == "accepted",
                MarketEventEmbedding.embedding_model == target_model,
                MarketEventEmbedding.embedding_dim == embedding_dim,
                MarketEventEmbedding.status == "ready",
                MarketEventEmbedding.vector.is_not(None),
            )
        ).scalar_one()
        or 0
    )
    return accepted_total, ready_total


def _initial_result(accepted_total: int, ready_before: int) -> dict[str, Any]:
    return {
        "accepted_total": accepted_total,
        "ready_before": ready_before,
        "converted": 0,
        "inserted": 0,
        "batches": 0,
        "remaining": max(0, accepted_total - ready_before),
        "failed": 0,
        "skipped": 0,
        "items_per_second": 0.0,
        "elapsed_seconds": 0.0,
        "eta_seconds": None,
        "last_batch_items": 0,
        "last_batch_seconds": 0.0,
        "affected_playlists": [],
    }


def _normalize_result(result: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _initial_result(0, 0)
    if isinstance(result, dict):
        normalized.update(result)
    return normalized


def _initialize_checkpoint(
    session: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    target_model: str,
    embedding_dim: int,
) -> dict[str, Any]:
    owned_job = _owned_running_job(
        session,
        job_id=job_id,
        worker_id=worker_id,
        execution_token=execution_token,
        lock=True,
    )
    if isinstance(owned_job.result, dict) and "accepted_total" in owned_job.result:
        return _normalize_result(owned_job.result)
    accepted_total, ready_before = _accepted_and_ready_counts(
        session,
        target_model=target_model,
        embedding_dim=embedding_dim,
    )
    result = _initial_result(accepted_total, ready_before)
    owned_job.result = result
    owned_job.progress_current = ready_before
    owned_job.progress_total = accepted_total
    owned_job.lease_expires_at = utcnow() + timedelta(seconds=EVENT_EMBEDDING_BACKFILL_LEASE_SECONDS)
    session.commit()
    return result


def _apply_batch(
    session: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    source_model: str,
    target_model: str,
    embedding_dim: int,
    candidates: list[EventEmbeddingCandidate],
    vectors: list[list[float]],
    batch_elapsed_seconds: float,
) -> dict[str, Any]:
    owned_job = _owned_running_job(
        session,
        job_id=job_id,
        worker_id=worker_id,
        execution_token=execution_token,
        lock=True,
    )
    event_ids = [candidate.event_id for candidate in candidates]
    events = (
        session.execute(
            select(MarketEvent)
            .where(MarketEvent.id.in_(event_ids), MarketEvent.status == "accepted")
            .with_for_update()
        )
        .scalars()
        .all()
    )
    events_by_id = {event.id: event for event in events}
    current_texts = event_embedding_texts(session, events)
    embedding_rows = (
        session.execute(
            select(MarketEventEmbedding)
            .where(
                MarketEventEmbedding.event_id.in_(event_ids),
                MarketEventEmbedding.embedding_dim == embedding_dim,
                MarketEventEmbedding.embedding_model.in_((source_model, target_model)),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    embeddings_by_key = {
        (row.event_id, row.embedding_model): row for row in embedding_rows
    }

    converted = 0
    inserted = 0
    skipped = 0
    generated_at = utcnow()
    for candidate, vector in zip(candidates, vectors, strict=True):
        event = events_by_id.get(candidate.event_id)
        current_text = current_texts.get(candidate.event_id)
        if event is None or current_text is None or _sha256_text(current_text) != candidate.checksum:
            skipped += 1
            continue

        source = embeddings_by_key.get((candidate.event_id, source_model))
        target = embeddings_by_key.get((candidate.event_id, target_model))
        if target is not None and target.status == "ready" and target.vector is not None:
            if source is not None and source is not target:
                session.delete(source)
            skipped += 1
            continue

        if target is not None:
            embedding = target
            if source is not None and source is not target:
                session.delete(source)
                converted += 1
            else:
                inserted += 1
        elif source is not None:
            embedding = source
            converted += 1
        else:
            embedding = MarketEventEmbedding(
                event_id=candidate.event_id,
                embedding_model=target_model,
                embedding_dim=embedding_dim,
                text_checksum=candidate.checksum,
            )
            session.add(embedding)
            inserted += 1

        embedding.embedding_model = target_model
        embedding.embedding_dim = embedding_dim
        embedding.status = "ready"
        embedding.vector = vector
        embedding.text_checksum = candidate.checksum
        embedding.skip_reason = None
        embedding.generated_at = generated_at

    result = _normalize_result(owned_job.result)
    result["converted"] = int(result["converted"] or 0) + converted
    result["inserted"] = int(result["inserted"] or 0) + inserted
    result["skipped"] = int(result["skipped"] or 0) + skipped
    result["batches"] = int(result["batches"] or 0) + 1
    result["elapsed_seconds"] = round(
        float(result["elapsed_seconds"] or 0.0) + max(0.0, batch_elapsed_seconds),
        3,
    )
    completed = int(result["ready_before"] or 0) + int(result["converted"] or 0) + int(result["inserted"] or 0)
    accepted_total = int(result["accepted_total"] or 0)
    remaining = max(0, accepted_total - completed)
    elapsed = float(result["elapsed_seconds"] or 0.0)
    processed = int(result["converted"] or 0) + int(result["inserted"] or 0)
    throughput = processed / elapsed if elapsed > 0 else 0.0
    result.update(
        {
            "remaining": remaining,
            "items_per_second": round(throughput, 3),
            "eta_seconds": round(remaining / throughput, 1) if throughput > 0 else None,
            "last_batch_items": converted + inserted,
            "last_batch_seconds": round(max(0.0, batch_elapsed_seconds), 3),
        }
    )
    owned_job.result = result
    owned_job.progress_current = min(completed, accepted_total)
    owned_job.progress_total = accepted_total
    owned_job.lease_expires_at = utcnow() + timedelta(seconds=EVENT_EMBEDDING_BACKFILL_LEASE_SECONDS)
    session.commit()
    return result


def _remaining_count(
    session: Session,
    *,
    target_model: str,
    embedding_dim: int,
) -> int:
    target = aliased(MarketEventEmbedding)
    return int(
        session.execute(
            select(func.count(MarketEvent.id))
            .outerjoin(
                target,
                _ready_target_join_condition(target, model=target_model, dim=embedding_dim),
            )
            .where(MarketEvent.status == "accepted", target.id.is_(None))
        ).scalar_one()
        or 0
    )


def _affected_playlist_ids(session: Session) -> list[uuid.UUID]:
    return list(
        session.execute(
            select(PlaylistMedia.playlist_id)
            .join(Video, Video.media_id == PlaylistMedia.media_id)
            .join(MarketEvent, MarketEvent.source_video_id == Video.id)
            .where(MarketEvent.status == "accepted")
            .distinct()
            .order_by(PlaylistMedia.playlist_id.asc())
        ).scalars()
    )


def _finalize_backfill(
    session: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    execution_token: uuid.UUID,
    source_model: str,
    target_model: str,
    embedding_dim: int,
) -> dict[str, Any] | None:
    owned_job = _owned_running_job(
        session,
        job_id=job_id,
        worker_id=worker_id,
        execution_token=execution_token,
        lock=True,
    )
    remaining = _remaining_count(
        session,
        target_model=target_model,
        embedding_dim=embedding_dim,
    )
    if remaining:
        session.commit()
        return None

    accepted_total, ready_total = _accepted_and_ready_counts(
        session,
        target_model=target_model,
        embedding_dim=embedding_dim,
    )
    if ready_total != accepted_total:
        raise JobTerminalFailure(
            f"event embedding backfill final count mismatch: accepted={accepted_total}, ready={ready_total}"
        )

    session.execute(
        delete(MarketEventEmbedding).where(
            MarketEventEmbedding.embedding_model == source_model,
            MarketEventEmbedding.embedding_dim == embedding_dim,
        )
    )
    playlist_ids = _affected_playlist_ids(session)
    for playlist_id in playlist_ids:
        enqueue_job(
            session,
            type_="playlist.mark_event_map_dirty",
            params={
                "playlist_id": str(playlist_id),
                "reason": "event_embedding_backfill_completed",
                "source_job_id": str(job_id),
            },
            priority=10,
        )

    analysis_pause = get_worker_role_pause(session, "analysis")
    analysis_resumed = analysis_pause.get("reason") == "embedding_model_migration"
    if analysis_resumed:
        clear_worker_role_pause(session, role="analysis")

    result = _normalize_result(owned_job.result)
    elapsed = float(result["elapsed_seconds"] or 0.0)
    processed = int(result["converted"] or 0) + int(result["inserted"] or 0)
    result.update(
        {
            "accepted_total": accepted_total,
            "remaining": 0,
            "items_per_second": round(processed / elapsed, 3) if elapsed > 0 else 0.0,
            "eta_seconds": 0.0,
            "affected_playlists": [str(playlist_id) for playlist_id in playlist_ids],
            "analysis_resumed": analysis_resumed,
        }
    )
    owned_job.result = result
    owned_job.progress_current = accepted_total
    owned_job.progress_total = accepted_total
    owned_job.lease_expires_at = utcnow() + timedelta(seconds=EVENT_EMBEDDING_BACKFILL_LEASE_SECONDS)
    session.commit()
    return result


def backfill_event_embeddings(session: Session, *, job: Job) -> dict[str, Any]:
    params = dict(job.params or {})
    source_model = str(params.get("source_model") or "").strip()
    target_model = str(params.get("target_model") or "").strip()
    embedding_dim = int(params.get("embedding_dim") or 0)
    batch_size = int(params.get("batch_size") or 0)
    if not source_model or not target_model or source_model == target_model:
        raise JobTerminalFailure("event embedding backfill source/target model is invalid")
    if embedding_dim <= 0 or batch_size != EVENT_EMBEDDING_BACKFILL_BATCH_SIZE:
        raise JobTerminalFailure("event embedding backfill requires embedding_dim > 0 and batch_size=64")
    active_spec = embedding_spec()
    if active_spec.model != target_model or active_spec.dim != embedding_dim:
        raise JobTerminalFailure(
            "event embedding backfill target does not match the active embedding configuration"
        )
    worker_id = str(job.worker_id or "").strip()
    execution_token = job.execution_token
    if not worker_id or execution_token is None or job.status != "running":
        raise JobTerminalFailure("event embedding backfill requires an owned running job")

    job_id = job.id
    _initialize_checkpoint(
        session,
        job_id=job_id,
        worker_id=worker_id,
        execution_token=execution_token,
        target_model=target_model,
        embedding_dim=embedding_dim,
    )
    while True:
        batch_started = time.perf_counter()
        _owned_running_job(
            session,
            job_id=job_id,
            worker_id=worker_id,
            execution_token=execution_token,
            lock=False,
        )
        candidates = _load_candidates(
            session,
            source_model=source_model,
            target_model=target_model,
            embedding_dim=embedding_dim,
            batch_size=batch_size,
        )
        session.commit()
        if not candidates:
            result = _finalize_backfill(
                session,
                job_id=job_id,
                worker_id=worker_id,
                execution_token=execution_token,
                source_model=source_model,
                target_model=target_model,
                embedding_dim=embedding_dim,
            )
            if result is not None:
                return result
            continue

        try:
            vectors = embed_texts([candidate.text for candidate in candidates])
            vectors = validate_embedding_batch(
                vectors,
                expected_count=len(candidates),
                model=target_model,
                dim=embedding_dim,
            )
        except EmbeddingTransientError:
            raise
        except (EmbeddingOverBudgetError, EmbeddingError) as exc:
            raise JobTerminalFailure(str(exc)) from exc

        _apply_batch(
            session,
            job_id=job_id,
            worker_id=worker_id,
            execution_token=execution_token,
            source_model=source_model,
            target_model=target_model,
            embedding_dim=embedding_dim,
            candidates=candidates,
            vectors=vectors,
            batch_elapsed_seconds=time.perf_counter() - batch_started,
        )
