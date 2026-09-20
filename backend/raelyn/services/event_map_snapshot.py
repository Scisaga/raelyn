from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import pickle
import shutil
import tempfile
from types import SimpleNamespace
import uuid
from typing import Any, Callable, Iterable, Iterator, Sequence

import numpy as np
from sqlalchemy import and_, case, func, insert, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.progress import set_job_progress
from raelyn.jobs.reschedule import JobReschedule, JobTerminalFailure
from raelyn.models import (
    EventMapCanonical,
    EventMapCanonicalHistoryMember,
    EventMapCanonicalHistoryRevision,
    EventMapCanonicalIdentity,
    EventMapCanonicalLineage,
    EventMapCanonicalMember,
    EventMapEntityIndex,
    EventMapProjectionAnchor,
    EventMapRecordRevision,
    EventMapSnapshot,
    EventMapState,
    EventMapStory,
    EventMapStoryEdge,
    EventMapStoryHistoryEvidence,
    EventMapStoryHistoryRevision,
    EventMapStoryIdentity,
    EventMapStoryMember,
    EventMapTopic,
    EventMapTopicMember,
    Job,
    MarketEvent,
    MarketEventEmbedding,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
    Playlist,
    PlaylistMedia,
    Video,
    EventMapChange,
)
from raelyn.services.embeddings import embedding_spec
from raelyn.services.event_map_domain import (
    EVENT_MAP_CANONICAL_VERSION,
    EVENT_MAP_STORY_VERSION,
    EVENT_MAP_TOPIC_VERSION,
    EventMapEntityRef,
    EventMapEvidenceRef,
    EventMapRecord,
    EventMapRelationRef,
    build_event_map_stories,
    build_event_map_topics,
    canonicalize_event_map_records,
    event_map_canonical_centroid,
)
from raelyn.services.event_map_projection import (
    EVENT_MAP_PRECISION_CODES,
    EVENT_MAP_PROJECTION_METHOD,
    EVENT_MAP_PROJECTION_SEED,
    EVENT_MAP_PROJECTION_VERSION,
    EventMapProjectionStaging,
    _event_time_days,
    _process_tree_rss_bytes,
    compute_event_map_layout,
    compute_event_map_neighbors,
    compute_event_map_reduction,
    stage_event_map_projection,
)
from raelyn.services.job_cancellation import JobCancelRequested, raise_if_job_cancel_requested
from raelyn.services.pg_lock import lock_key
from raelyn.timeutil import utcnow


EVENT_MAP_LAYOUT_VERSION = EVENT_MAP_PROJECTION_VERSION
EVENT_MAP_NEIGHBOR_COUNT = 32
EVENT_MAP_ANCHOR_LIMIT = 4096
EVENT_MAP_MIN_TEMP_FREE_BYTES = 5 * 1024 * 1024 * 1024
EVENT_MAP_INSERT_BATCH_SIZE = 2000
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_REVISION_NAMESPACE = uuid.UUID("7b2dc245-efbf-45a5-bafb-ce249f18fd35")
_CANONICAL_NAMESPACE = uuid.UUID("0953638d-05dd-42a3-a3a0-a7649b36e7e3")
_STORY_IDENTITY_NAMESPACE = uuid.UUID("f339dbbc-c4d2-4635-811a-9a53644145e4")
_CHANGE_NAMESPACE = uuid.UUID("195f0e28-5b28-4f49-9d40-ec6ae6b384db")
_HISTORY_NAMESPACE = uuid.UUID("e2a889f7-89e6-4438-aae2-1bf3a2992df5")
STORY_MATERIAL_CHANGE_TYPES = frozenset(
    {
        "story_added",
        "story_members_changed",
        "story_relations_changed",
        "story_evidence_changed",
        "story_correction_added",
        "story_maturity_changed",
    }
)
_STORY_RELATION_METRIC_KEYS = frozenset(
    {
        "cosine",
        "claim_overlap",
        "supporting_revision_ids",
    }
)


@dataclass(frozen=True)
class StagedEventMapInputs:
    projection: EventMapProjectionStaging
    records: list[EventMapRecord]
    revisions_path: Path
    input_fingerprint: str
    embedding_checksum: str
    skipped_reason_counts: dict[str, int]


@dataclass(frozen=True)
class FrozenEventMapState:
    input_generation: int
    parent_snapshot_id: uuid.UUID | None
    active_dirty_job_id: uuid.UUID | None


@dataclass(frozen=True)
class PreviousEventMapSnapshot:
    snapshot: EventMapSnapshot | None
    event_to_canonical: dict[uuid.UUID, uuid.UUID]
    canonical_member_counts: dict[uuid.UUID, int]
    canonical_coordinates: dict[uuid.UUID, tuple[float, float, float]]
    revision_embeddings: dict[uuid.UUID, str]
    anchor_ids: list[uuid.UUID]
    anchor_vectors: np.ndarray
    anchor_coordinates: np.ndarray


class _DiskRowBuffer:
    """把待写数据库的行顺序落到临时盘，避免百万级 Python dict 同时驻留。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._count = 0
        self._writer = path.open("wb")

    def append(self, row: dict[str, Any]) -> None:
        if self._writer is None:
            raise RuntimeError("event map row buffer is already closed")
        pickle.dump(row, self._writer, protocol=pickle.HIGHEST_PROTOCOL)
        self._count += 1

    def close(self) -> None:
        writer = self._writer
        if writer is not None:
            writer.flush()
            writer.close()
            self._writer = None

    def iter_batches(self, batch_size: int) -> Iterator[list[dict[str, Any]]]:
        self.close()
        with self.path.open("rb") as handle:
            while True:
                batch: list[dict[str, Any]] = []
                for _ in range(max(1, int(batch_size))):
                    try:
                        batch.append(pickle.load(handle))
                    except EOFError:
                        break
                if not batch:
                    return
                yield batch

    def discard(self) -> None:
        self.close()
        self.path.unlink(missing_ok=True)

    def __len__(self) -> int:
        return self._count


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _stable_json(value: Any) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _embedding_vector_checksum(vector: Any, dimension: int) -> str:
    values = np.asarray(vector, dtype=np.float32)
    if values.ndim != 1 or len(values) != int(dimension):
        raise RuntimeError(f"event map embedding dimension {values.size} does not match {dimension}")
    if not np.isfinite(values).all():
        raise RuntimeError("event map embedding contains NaN or infinity")
    return hashlib.sha256(values.tobytes()).hexdigest()


def _day_datetime(day: int, *, end: bool = False) -> datetime:
    target = date.fromordinal(_EPOCH_ORDINAL + int(day))
    return datetime.combine(target, time.max if end else time.min, tzinfo=timezone.utc)


def _normalized_event_interval(start: datetime, end: datetime | None, precision: str) -> tuple[datetime, datetime, int, int]:
    start_day, end_day = _event_time_days(start, end, precision)
    normalized = str(precision or "unknown").strip().lower()
    if normalized in {"month", "year"}:
        return _day_datetime(start_day), _day_datetime(end_day, end=True), start_day, end_day
    event_end = end if end is not None and end >= start else start
    return start, event_end, start_day, end_day


def _temp_disk_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _raise_if_resource_limit_exceeded() -> int:
    peak = _process_tree_rss_bytes(__import__("os").getpid())
    hard_limit = max(0, int(settings.analysis_max_rss_bytes or 0))
    controlled_limit = max(1, int(hard_limit * 11 / 12)) if hard_limit > 0 else 0
    if controlled_limit > 0 and peak > controlled_limit:
        raise JobTerminalFailure(
            f"event map process-tree RSS {peak} exceeds configured maximum {hard_limit}"
        )
    available = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    minimum = max(0, int(settings.analysis_min_available_memory_bytes or 0))
    if available > 0 and minimum > 0 and available < minimum:
        raise JobTerminalFailure(
            f"event map available memory {available} is below configured minimum {minimum}"
        )
    return peak


@contextmanager
def _event_map_snapshot_reader(session: Session) -> Iterator[Session | Connection]:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        yield session
        return
    engine = bind.engine if isinstance(bind, Connection) else bind
    with engine.connect() as raw_connection:
        connection = raw_connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            yield connection


def _frozen_event_map_state(
    reader: Session | Connection,
    *,
    playlist_id: uuid.UUID,
) -> FrozenEventMapState:
    state_row = reader.execute(
        select(EventMapState.dirty_generation, EventMapState.current_snapshot_id)
        .where(EventMapState.playlist_id == playlist_id)
        .limit(1)
    ).one_or_none()
    active_dirty_job_id = reader.execute(
        select(Job.id)
        .where(
            Job.type == "playlist.mark_event_map_dirty",
            Job.status.in_(["pending", "running"]),
            Job.params["playlist_id"].as_string() == str(playlist_id),
        )
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    return FrozenEventMapState(
        input_generation=int(state_row[0] or 0) if state_row is not None else 0,
        parent_snapshot_id=state_row[1] if state_row is not None else None,
        active_dirty_job_id=active_dirty_job_id,
    )


@contextmanager
def _event_map_build_lock(session: Session) -> Iterator[bool]:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        with nullcontext(True) as acquired:
            yield acquired
        return
    engine = bind.engine if isinstance(bind, Connection) else bind
    # session-level advisory lock 必须与独立物理连接同生命周期；构建中的 worker
    # Session 会多次 commit，不能依赖它从连接池再次取到同一条连接来 unlock。
    with engine.connect() as lock_connection:
        key = lock_key("event-map:global-build")
        acquired = bool(
            lock_connection.execute(
                text("select pg_try_advisory_lock(:key)"),
                {"key": key},
            ).scalar_one()
        )
        try:
            yield acquired
        finally:
            if acquired:
                lock_connection.execute(
                    text("select pg_advisory_unlock(:key)"),
                    {"key": key},
                )
            lock_connection.commit()


def _coverage(session: Session | Connection, playlist_id: uuid.UUID, model: str, dimension: int) -> tuple[int, int, dict[str, int]]:
    ready = and_(
        MarketEventEmbedding.status == "ready",
        MarketEventEmbedding.vector.is_not(None),
    )
    total, ready_count, eligible, missing_time, failed = session.execute(
        select(
            func.count(MarketEvent.id),
            func.coalesce(func.sum(case((ready, 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((and_(ready, MarketEvent.event_time_start.is_not(None)), 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((and_(ready, MarketEvent.event_time_start.is_(None)), 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((MarketEventEmbedding.status.in_(["failed", "skipped_over_budget"]), 1), else_=0)
                ),
                0,
            ),
        )
        .select_from(MarketEvent)
        .join(Video, Video.id == MarketEvent.source_video_id)
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .outerjoin(
            MarketEventEmbedding,
            and_(
                MarketEventEmbedding.event_id == MarketEvent.id,
                MarketEventEmbedding.embedding_model == model,
                MarketEventEmbedding.embedding_dim == dimension,
            ),
        )
        .where(PlaylistMedia.playlist_id == playlist_id, MarketEvent.status == "accepted")
    ).one()
    total_count = int(total or 0)
    ready_value = int(ready_count or 0)
    eligible_count = int(eligible or 0)
    failed_value = int(failed or 0)
    skipped = _skipped_reason_counts(
        total=total_count,
        ready=ready_value,
        eligible=eligible_count,
        failed=failed_value,
    )
    return total_count, eligible_count, {key: value for key, value in skipped.items() if value > 0}


def _skipped_reason_counts(*, total: int, ready: int, eligible: int, failed: int) -> dict[str, int]:
    return {
        "missing_event_time": max(0, int(ready) - int(eligible)),
        "embedding_failed": max(0, int(failed)),
        "embedding_not_ready": max(0, int(total) - int(ready) - int(failed)),
    }


def _base_event_batch(
    session: Session | Connection,
    *,
    playlist_id: uuid.UUID,
    model: str,
    dimension: int,
    after_event_id: uuid.UUID | None,
    batch_size: int,
) -> list[dict[str, Any]]:
    clauses: list[Any] = [
        PlaylistMedia.playlist_id == playlist_id,
        MarketEvent.status == "accepted",
        MarketEvent.event_time_start.is_not(None),
        MarketEventEmbedding.status == "ready",
        MarketEventEmbedding.vector.is_not(None),
    ]
    if after_event_id is not None:
        clauses.append(MarketEvent.id > after_event_id)
    rows = session.execute(
        select(
            MarketEvent.id.label("event_id"),
            MarketEvent.source_video_id,
            MarketEvent.event_time_start,
            MarketEvent.event_time_end,
            MarketEvent.time_precision,
            MarketEvent.event_type,
            MarketEvent.title,
            MarketEvent.summary,
            MarketEvent.direction,
            MarketEvent.magnitude,
            MarketEvent.surprise_or_delta,
            MarketEvent.confidence,
            MarketEvent.event_key,
            MarketEvent.raw_payload,
            MarketEventEmbedding.id.label("embedding_id"),
            MarketEventEmbedding.vector,
            Video.provider,
            Video.provider_video_id,
            Video.title.label("video_title"),
            Video.url.label("video_url"),
            Video.published_at.label("video_published_at"),
        )
        .join(Video, Video.id == MarketEvent.source_video_id)
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .join(
            MarketEventEmbedding,
            and_(
                MarketEventEmbedding.event_id == MarketEvent.id,
                MarketEventEmbedding.embedding_model == model,
                MarketEventEmbedding.embedding_dim == dimension,
            ),
        )
        .where(*clauses)
        .order_by(MarketEvent.id.asc())
        .limit(batch_size)
    ).mappings()
    return [dict(row) for row in rows]


def _children_for_batch(
    session: Session | Connection,
    event_ids: Sequence[uuid.UUID],
) -> tuple[dict[uuid.UUID, list[dict[str, Any]]], dict[uuid.UUID, list[dict[str, Any]]], dict[uuid.UUID, list[dict[str, Any]]]]:
    entities: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    evidence: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    relations: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    if not event_ids:
        return entities, evidence, relations
    for row in session.execute(
        select(
            MarketEventEntity.id,
            MarketEventEntity.event_id,
            MarketEventEntity.entity_type,
            MarketEventEntity.name,
            MarketEventEntity.normalized_key,
            MarketEventEntity.role,
            MarketEventEntity.confidence,
            MarketEventEntity.source_text,
        )
        .where(MarketEventEntity.event_id.in_(event_ids))
        .order_by(MarketEventEntity.event_id.asc(), MarketEventEntity.id.asc())
    ).mappings():
        entities[row["event_id"]].append(_json_value(dict(row)))
    for row in session.execute(
        select(
            MarketEventEvidence.id,
            MarketEventEvidence.event_id,
            MarketEventEvidence.video_id,
            MarketEventEvidence.transcript_asset_id,
            MarketEventEvidence.evidence_key,
            MarketEventEvidence.evidence_text,
            MarketEventEvidence.evidence_json,
            MarketEventEvidence.confidence,
        )
        .where(MarketEventEvidence.event_id.in_(event_ids))
        .order_by(MarketEventEvidence.event_id.asc(), MarketEventEvidence.id.asc())
    ).mappings():
        evidence[row["event_id"]].append(_json_value(dict(row)))
    for row in session.execute(
        select(
            MarketEventRelation.id,
            MarketEventRelation.event_id,
            MarketEventRelation.source_entity_id,
            MarketEventRelation.target_entity_id,
            MarketEventRelation.relation_type,
            MarketEventRelation.direction,
            MarketEventRelation.magnitude,
            MarketEventRelation.confidence,
            MarketEventRelation.evidence_text,
            MarketEventRelation.raw_payload,
        )
        .where(MarketEventRelation.event_id.in_(event_ids))
        .order_by(MarketEventRelation.event_id.asc(), MarketEventRelation.id.asc())
    ).mappings():
        relations[row["event_id"]].append(_json_value(dict(row)))
    return entities, evidence, relations


def _stage_inputs(
    reader: Session | Connection,
    *,
    playlist_id: uuid.UUID,
    model: str,
    dimension: int,
    directory: Path,
    batch_size: int,
    checkpoint: Callable[[int], None],
) -> StagedEventMapInputs:
    _total, eligible_count, skipped = _coverage(reader, playlist_id, model, dimension)
    revisions_path = directory / "event-map-record-revisions.jsonl"
    records: list[EventMapRecord] = []
    input_hash = hashlib.sha256()
    embedding_hash = hashlib.sha256()

    def rows() -> Iterator[Any]:
        after_event_id: uuid.UUID | None = None
        processed = 0
        with revisions_path.open("w", encoding="utf-8") as revisions_file:
            while True:
                base_rows = _base_event_batch(
                    reader,
                    playlist_id=playlist_id,
                    model=model,
                    dimension=dimension,
                    after_event_id=after_event_id,
                    batch_size=batch_size,
                )
                if not base_rows:
                    break
                event_ids = [uuid.UUID(str(row["event_id"])) for row in base_rows]
                entities_by_event, evidence_by_event, relations_by_event = _children_for_batch(reader, event_ids)
                for row in base_rows:
                    event_id = uuid.UUID(str(row["event_id"]))
                    embedding_id = uuid.UUID(str(row["embedding_id"]))
                    start, end, start_day, end_day = _normalized_event_interval(
                        row["event_time_start"],
                        row["event_time_end"],
                        str(row["time_precision"] or "unknown"),
                    )
                    entity_rows = entities_by_event.get(event_id, [])
                    evidence_rows = evidence_by_event.get(event_id, [])
                    relation_rows = relations_by_event.get(event_id, [])
                    source_json = {
                        "video_id": str(row["source_video_id"]),
                        "provider": row["provider"],
                        "provider_video_id": row["provider_video_id"],
                        "title": row["video_title"],
                        "url": row["video_url"],
                        "published_at": _json_value(row["video_published_at"]),
                        "relations": relation_rows,
                    }
                    content = {
                        "title": row["title"],
                        "summary": row["summary"],
                        "event_time_start": start.isoformat(),
                        "event_time_end": end.isoformat(),
                        "time_precision": str(row["time_precision"] or "unknown"),
                        "event_type": str(row["event_type"] or "other"),
                        "direction": row["direction"],
                        "magnitude": row["magnitude"],
                        "surprise_or_delta": row["surprise_or_delta"],
                        "confidence": row["confidence"],
                        "event_key": row["event_key"],
                        "raw_payload": row["raw_payload"],
                        "entities": entity_rows,
                        "source": source_json,
                        "evidence": evidence_rows,
                    }
                    content_hash = _sha256_text(_stable_json(content))
                    embedding_checksum = _embedding_vector_checksum(row["vector"], dimension)
                    revision_id = uuid.uuid5(
                        _REVISION_NAMESPACE,
                        f"{event_id}:{content_hash}:{embedding_checksum}",
                    )
                    revision_row = {
                        "id": str(revision_id),
                        "event_id": str(event_id),
                        "source_video_id": str(row["source_video_id"]),
                        "embedding_id": str(embedding_id),
                        "content_hash": content_hash,
                        "embedding_checksum": embedding_checksum,
                        "title": row["title"],
                        "summary": row["summary"],
                        "event_time_start": start.isoformat(),
                        "event_time_end": end.isoformat(),
                        "time_precision": str(row["time_precision"] or "unknown"),
                        "event_type": str(row["event_type"] or "other"),
                        "direction": row["direction"],
                        "entities_json": entity_rows,
                        "source_json": source_json,
                        "evidence_json": evidence_rows,
                    }
                    revisions_file.write(_stable_json(revision_row) + "\n")
                    entities = tuple(
                        EventMapEntityRef(
                            str(entity.get("entity_type") or "other"),
                            str(entity.get("normalized_key") or ""),
                            str(entity.get("name") or ""),
                            str(entity.get("role") or "other"),
                            float(entity["confidence"]) if entity.get("confidence") is not None else None,
                        )
                        for entity in entity_rows
                    )
                    entity_key_by_id = {
                        str(entity.get("id")): value.canonical_key
                        for entity, value in zip(entity_rows, entities, strict=True)
                    }
                    relations = tuple(
                        EventMapRelationRef(
                            relation_type=str(relation.get("relation_type") or "mentions"),
                            confidence=(
                                float(relation["confidence"])
                                if relation.get("confidence") is not None
                                else None
                            ),
                            evidence_text=str(relation.get("evidence_text") or "") or None,
                            source_claim=str((relation.get("raw_payload") or {}).get("cause") or "") or None,
                            target_claim=str((relation.get("raw_payload") or {}).get("effect") or "") or None,
                            source_entity_key=entity_key_by_id.get(str(relation.get("source_entity_id"))),
                            target_entity_key=entity_key_by_id.get(str(relation.get("target_entity_id"))),
                        )
                        for relation in relation_rows
                    )
                    evidence = tuple(
                        EventMapEvidenceRef(
                            evidence_id=uuid.UUID(str(item["id"])),
                            video_id=uuid.UUID(str(item["video_id"])),
                            evidence_text=str(item.get("evidence_text") or ""),
                            confidence=(
                                float(item["confidence"])
                                if item.get("confidence") is not None
                                else None
                            ),
                        )
                        for item in evidence_rows
                        if item.get("id") and item.get("video_id") and str(item.get("evidence_text") or "").strip()
                    )
                    records.append(
                        EventMapRecord(
                            event_id=event_id,
                            revision_id=revision_id,
                            vector_index=processed,
                            title=str(row["title"] or ""),
                            summary=str(row["summary"] or ""),
                            event_type=str(row["event_type"] or "other"),
                            direction=str(row["direction"] or "unknown"),
                            start_day=start_day,
                            end_day=end_day,
                            time_precision=str(row["time_precision"] or "unknown").strip().lower(),
                            source_video_id=uuid.UUID(str(row["source_video_id"])),
                            entities=entities,
                            relations=relations,
                            evidence=evidence,
                        )
                    )
                    input_hash.update(f"{event_id}:{content_hash}:{embedding_checksum}\n".encode("utf-8"))
                    embedding_hash.update(f"{event_id}:{embedding_checksum}\n".encode("utf-8"))
                    processed += 1
                    yield SimpleNamespace(
                        event_id=event_id,
                        vector=row["vector"],
                        event_time_start=start,
                        event_time_end=end,
                        time_precision=row["time_precision"],
                        event_type=row["event_type"],
                    )
                after_event_id = event_ids[-1]
                checkpoint(processed, eligible_count)

    projection = stage_event_map_projection(
        rows(),
        directory=directory,
        expected_count=eligible_count,
        embedding_dim=dimension,
        batch_size=batch_size,
        checkpoint=lambda processed: checkpoint(processed, eligible_count),
    )
    return StagedEventMapInputs(
        projection=projection,
        records=records,
        revisions_path=revisions_path,
        input_fingerprint=input_hash.hexdigest(),
        embedding_checksum=embedding_hash.hexdigest(),
        skipped_reason_counts=skipped,
    )


def _previous_snapshot_data(session: Session, snapshot_id: uuid.UUID | None, embedding_dim: int) -> PreviousEventMapSnapshot:
    if snapshot_id is None:
        return PreviousEventMapSnapshot(None, {}, {}, {}, {}, [], np.empty((0, embedding_dim), dtype=np.float32), np.empty((0, 3), dtype=np.float32))
    snapshot = session.get(EventMapSnapshot, snapshot_id)
    if snapshot is None or snapshot.status != "ready":
        return PreviousEventMapSnapshot(None, {}, {}, {}, {}, [], np.empty((0, embedding_dim), dtype=np.float32), np.empty((0, 3), dtype=np.float32))

    event_to_canonical: dict[uuid.UUID, uuid.UUID] = {}
    revision_embeddings: dict[uuid.UUID, str] = {}
    member_counts: Counter[uuid.UUID] = Counter()
    for row in session.execute(
        select(
            EventMapRecordRevision.event_id,
            EventMapRecordRevision.embedding_checksum,
            EventMapCanonicalMember.canonical_id,
        )
        .join(
            EventMapCanonicalMember,
            EventMapCanonicalMember.record_revision_id == EventMapRecordRevision.id,
        )
        .where(EventMapCanonicalMember.snapshot_id == snapshot_id)
    ).mappings():
        event_id = row["event_id"]
        if event_id is None:
            continue
        canonical_id = row["canonical_id"]
        event_to_canonical[event_id] = canonical_id
        revision_embeddings[event_id] = str(row["embedding_checksum"] or "")
        member_counts[canonical_id] += 1

    coordinates = {
        row["canonical_id"]: (float(row["x"]), float(row["y"]), float(row["z"]))
        for row in session.execute(
            select(
                EventMapCanonical.canonical_id,
                EventMapCanonical.x,
                EventMapCanonical.y,
                EventMapCanonical.z,
            ).where(EventMapCanonical.snapshot_id == snapshot_id)
        ).mappings()
    }
    anchor_rows = list(
        session.execute(
            select(
                EventMapProjectionAnchor.canonical_id,
                EventMapProjectionAnchor.x,
                EventMapProjectionAnchor.y,
                EventMapProjectionAnchor.z,
                EventMapProjectionAnchor.centroid_vector,
            )
            .where(EventMapProjectionAnchor.snapshot_id == snapshot_id)
            .order_by(EventMapProjectionAnchor.anchor_rank.asc())
        ).mappings()
    )
    anchor_ids: list[uuid.UUID] = []
    anchor_vectors: list[np.ndarray] = []
    anchor_coordinates: list[tuple[float, float, float]] = []
    for row in anchor_rows:
        vector = np.frombuffer(row["centroid_vector"], dtype=np.float32)
        if len(vector) != embedding_dim:
            continue
        anchor_ids.append(row["canonical_id"])
        anchor_vectors.append(vector.copy())
        anchor_coordinates.append((float(row["x"]), float(row["y"]), float(row["z"])))
    return PreviousEventMapSnapshot(
        snapshot=snapshot,
        event_to_canonical=event_to_canonical,
        canonical_member_counts=dict(member_counts),
        canonical_coordinates=coordinates,
        revision_embeddings=revision_embeddings,
        anchor_ids=anchor_ids,
        anchor_vectors=np.stack(anchor_vectors) if anchor_vectors else np.empty((0, embedding_dim), dtype=np.float32),
        anchor_coordinates=np.asarray(anchor_coordinates, dtype=np.float32).reshape((-1, 3)),
    )


def _assign_canonical_identities(
    *,
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    records: Sequence[EventMapRecord],
    groups: Sequence[Any],
    previous: PreviousEventMapSnapshot,
) -> tuple[list[uuid.UUID], list[str], list[dict[str, Any]]]:
    predecessor_counts: list[Counter[uuid.UUID]] = []
    for group in groups:
        counts = Counter(
            previous.event_to_canonical[records[index].event_id]
            for index in group.member_indices
            if records[index].event_id in previous.event_to_canonical
        )
        predecessor_counts.append(counts)
    successor_count_by_predecessor: Counter[uuid.UUID] = Counter()
    for counts in predecessor_counts:
        successor_count_by_predecessor.update(counts.keys())

    proposals: list[tuple[float, int, uuid.UUID]] = []
    for group_index, (group, counts) in enumerate(zip(groups, predecessor_counts, strict=True)):
        # merge 和 split 都会改变真实事件身份；只有一对一延续才允许复用 ID。
        if len(counts) != 1:
            continue
        candidate, overlap = next(iter(counts.items()))
        if successor_count_by_predecessor[candidate] != 1:
            continue
        new_ratio = overlap / max(1, len(group.member_indices))
        old_ratio = overlap / max(1, previous.canonical_member_counts.get(candidate, 0))
        if new_ratio >= 0.5 and old_ratio >= 0.5:
            proposals.append((min(new_ratio, old_ratio), group_index, candidate))

    accepted: dict[int, uuid.UUID] = {}
    used_previous: set[uuid.UUID] = set()
    for _score, group_index, candidate in sorted(
        proposals,
        key=lambda item: (-item[0], str(item[2]), item[1]),
    ):
        if candidate in used_previous:
            continue
        accepted[group_index] = candidate
        used_previous.add(candidate)

    canonical_ids: list[uuid.UUID] = []
    identity_states: list[str] = []
    for group_index, group in enumerate(groups):
        retained = accepted.get(group_index)
        if retained is not None:
            canonical_ids.append(retained)
            identity_states.append("retained")
            continue
        member_key = ",".join(sorted(str(records[index].event_id) for index in group.member_indices))
        canonical_ids.append(uuid.uuid5(_CANONICAL_NAMESPACE, f"{playlist_id}:{snapshot_id}:{member_key}"))
        identity_states.append("new")

    lineage_rows: list[dict[str, Any]] = []
    for group_index, counts in enumerate(predecessor_counts):
        successor = canonical_ids[group_index]
        if identity_states[group_index] == "retained":
            continue
        for predecessor, overlap in sorted(counts.items(), key=lambda item: str(item[0])):
            if predecessor == successor:
                continue
            if len(counts) > 1:
                relation_type = "merge"
            elif successor_count_by_predecessor[predecessor] > 1:
                relation_type = "split"
            else:
                relation_type = "replaced"
            lineage_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "predecessor_canonical_id": predecessor,
                    "successor_canonical_id": successor,
                    "relation_type": relation_type,
                    "confidence": overlap / max(1, len(groups[group_index].member_indices)),
                }
            )
    return canonical_ids, identity_states, lineage_rows


def _snapshot_is_incremental_compatible(previous: PreviousEventMapSnapshot, model: str, dimension: int) -> bool:
    snapshot = previous.snapshot
    return bool(
        snapshot
        and snapshot.embedding_model == model
        and snapshot.embedding_dim == dimension
        and snapshot.canonical_algorithm_version == EVENT_MAP_CANONICAL_VERSION
        and snapshot.topic_algorithm_version == EVENT_MAP_TOPIC_VERSION
        and snapshot.layout_algorithm_version == EVENT_MAP_LAYOUT_VERSION
        and len(previous.anchor_vectors) > 0
    )


def _changed_input_count(records: Sequence[EventMapRecord], revision_embeddings: dict[uuid.UUID, str], revisions_path: Path) -> int:
    current_embeddings: dict[uuid.UUID, str] = {}
    with revisions_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            current_embeddings[uuid.UUID(row["event_id"])] = str(row["embedding_checksum"] or "")
    changed = len(set(current_embeddings) ^ set(revision_embeddings))
    changed += sum(
        1
        for event_id in set(current_embeddings) & set(revision_embeddings)
        if current_embeddings[event_id] != revision_embeddings[event_id]
    )
    return changed


def _stage_canonical_centroids(
    directory: Path,
    groups: Sequence[Any],
    records: Sequence[EventMapRecord],
    raw_vectors: np.ndarray,
    canonical_ids: Sequence[uuid.UUID],
    batch_size: int,
    checkpoint: Any,
) -> EventMapProjectionStaging:
    count = len(groups)
    dimension = int(raw_vectors.shape[1]) if raw_vectors.ndim == 2 else 0
    if len(canonical_ids) != count:
        raise ValueError("canonical id count does not match canonical groups")
    vectors_path = directory / "event-map-canonical-centroids.float32"
    reduced_path = directory / "event-map-canonical-centroids-pca.float32"
    coordinates_path = directory / "event-map-canonical-coordinates.float32"
    if not count:
        vectors_path.touch()
        checkpoint(0)
        return EventMapProjectionStaging(
            directory=directory,
            vectors_path=vectors_path,
            reduced_path=reduced_path,
            coordinates_path=coordinates_path,
            count=0,
            embedding_dim=dimension,
            event_ids=canonical_ids,
            event_start_days=np.empty(0, dtype=np.int32),
            event_end_days=np.empty(0, dtype=np.int32),
            event_type_codes=np.empty(0, dtype=np.uint8),
            time_precision_codes=np.empty(0, dtype=np.uint8),
            categories=[],
        )
    output = np.memmap(vectors_path, dtype=np.float32, mode="w+", shape=(count, dimension))
    for group_index, group in enumerate(groups):
        output[group_index] = event_map_canonical_centroid(group, records, raw_vectors)
        processed = group_index + 1
        if processed % max(1, batch_size) == 0:
            output.flush()
            checkpoint(processed)
    output.flush()
    checkpoint(count)
    return EventMapProjectionStaging(
        directory=directory,
        vectors_path=vectors_path,
        reduced_path=reduced_path,
        coordinates_path=coordinates_path,
        count=count,
        embedding_dim=dimension,
        event_ids=canonical_ids,
        event_start_days=np.zeros(count, dtype=np.int32),
        event_end_days=np.zeros(count, dtype=np.int32),
        event_type_codes=np.zeros(count, dtype=np.uint8),
        time_precision_codes=np.zeros(count, dtype=np.uint8),
        categories=[],
    )


def _anchored_coordinates(
    *,
    canonical_ids: Sequence[uuid.UUID],
    previous: PreviousEventMapSnapshot,
    canonical_vectors: np.ndarray,
) -> np.ndarray:
    if canonical_vectors.ndim != 2 or canonical_vectors.shape[0] != len(canonical_ids):
        raise ValueError("canonical vector row count does not match canonical ids")
    coordinates = np.zeros((len(canonical_ids), 3), dtype=np.float32)
    anchor_vectors = np.asarray(previous.anchor_vectors, dtype=np.float32)
    anchor_norms = np.linalg.norm(anchor_vectors, axis=1, keepdims=True)
    anchor_norms[anchor_norms == 0] = 1.0
    anchor_vectors = anchor_vectors / anchor_norms
    for group_index, canonical_id in enumerate(canonical_ids):
        retained = previous.canonical_coordinates.get(canonical_id)
        if retained is not None:
            coordinates[group_index] = retained
            continue
        vector = np.asarray(canonical_vectors[group_index], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector = vector / norm
        similarities = anchor_vectors @ vector
        neighbor_count = min(8, len(similarities))
        top = np.argpartition(similarities, -neighbor_count)[-neighbor_count:]
        ordered = top[np.argsort(-similarities[top], kind="stable")]
        weights = np.maximum(0.001, similarities[ordered] - float(np.min(similarities[ordered])) + 0.001)
        weights /= float(np.sum(weights))
        coordinate = np.sum(previous.anchor_coordinates[ordered] * weights[:, None], axis=0)
        digest = hashlib.sha256(str(canonical_id).encode("utf-8")).digest()
        azimuth = int.from_bytes(digest[:4], "big") / (2**32) * math.tau
        vertical = int.from_bytes(digest[4:8], "big") / (2**32) * 2.0 - 1.0
        planar = math.sqrt(max(0.0, 1.0 - vertical * vertical))
        span = np.ptp(previous.anchor_coordinates, axis=0)
        jitter = max(1e-4, float(np.linalg.norm(span)) * 0.0005)
        direction = np.asarray(
            [math.cos(azimuth) * planar, math.sin(azimuth) * planar, vertical],
            dtype=np.float32,
        )
        coordinates[group_index] = coordinate + direction * jitter
    return coordinates


def _coordinate_bounds(coordinates: np.ndarray) -> dict[str, float]:
    if len(coordinates) == 0:
        return {
            "min_x": 0.0,
            "max_x": 0.0,
            "min_y": 0.0,
            "max_y": 0.0,
            "min_z": 0.0,
            "max_z": 0.0,
        }
    return {
        "min_x": float(np.min(coordinates[:, 0])),
        "max_x": float(np.max(coordinates[:, 0])),
        "min_y": float(np.min(coordinates[:, 1])),
        "max_y": float(np.max(coordinates[:, 1])),
        "min_z": float(np.min(coordinates[:, 2])),
        "max_z": float(np.max(coordinates[:, 2])),
    }


def _monthly_distribution(canonical_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical_counts: Counter[str] = Counter()
    record_counts: Counter[str] = Counter()
    for row in canonical_rows:
        current = date.fromordinal(_EPOCH_ORDINAL + int(row["event_start_day"]))
        end = date.fromordinal(_EPOCH_ORDINAL + int(row["event_end_day"]))
        current = date(current.year, current.month, 1)
        end = date(end.year, end.month, 1)
        while current <= end:
            key = current.isoformat()[:7]
            canonical_counts[key] += 1
            record_counts[key] += int(row.get("member_count") or 0)
            current = date(current.year + (1 if current.month == 12 else 0), 1 if current.month == 12 else current.month + 1, 1)
    return [
        {
            "month": month,
            "canonical_count": canonical_counts[month],
            "record_count": record_counts[month],
        }
        for month in sorted(canonical_counts)
    ]


def _insert_ignore(session: Session, model: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        session.execute(dialect_insert(model).values(rows).on_conflict_do_nothing())
        return
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        session.execute(dialect_insert(model).values(rows).on_conflict_do_nothing())
        return
    session.execute(insert(model), rows)


def _insert_ignore_batched(session: Session, model: Any, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), EVENT_MAP_INSERT_BATCH_SIZE):
        _insert_ignore(session, model, rows[start : start + EVENT_MAP_INSERT_BATCH_SIZE])


def _insert_rows(
    session: Session,
    model: Any,
    rows: Sequence[dict[str, Any]] | _DiskRowBuffer,
    *,
    checkpoint: Any,
    start_progress: int,
    end_progress: int,
    ignore_conflicts: bool = False,
) -> None:
    total = len(rows)
    if total == 0:
        checkpoint(end_progress)
        if isinstance(rows, _DiskRowBuffer):
            rows.discard()
        return
    if isinstance(rows, _DiskRowBuffer):
        batches = rows.iter_batches(EVENT_MAP_INSERT_BATCH_SIZE)
    else:
        batches = (
            list(rows[start : start + EVENT_MAP_INSERT_BATCH_SIZE])
            for start in range(0, total, EVENT_MAP_INSERT_BATCH_SIZE)
        )
    complete = 0
    for batch in batches:
        if ignore_conflicts:
            _insert_ignore(session, model, batch)
        else:
            session.execute(insert(model), batch)
        session.flush()
        complete += len(batch)
        progress = start_progress + int((end_progress - start_progress) * complete / total)
        checkpoint(progress)
    session.commit()
    if isinstance(rows, _DiskRowBuffer):
        rows.discard()


def _insert_record_revisions(session: Session, path: Path, checkpoint: Any) -> int:
    rows: list[dict[str, Any]] = []
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            rows.append(
                {
                    "id": uuid.UUID(raw["id"]),
                    "event_id": uuid.UUID(raw["event_id"]),
                    "source_video_id": uuid.UUID(raw["source_video_id"]),
                    "embedding_id": uuid.UUID(raw["embedding_id"]),
                    "content_hash": raw["content_hash"],
                    "embedding_checksum": raw["embedding_checksum"],
                    "title": raw.get("title"),
                    "summary": raw.get("summary"),
                    "event_time_start": datetime.fromisoformat(raw["event_time_start"]),
                    "event_time_end": datetime.fromisoformat(raw["event_time_end"]),
                    "time_precision": raw["time_precision"],
                    "event_type": raw["event_type"],
                    "direction": raw.get("direction"),
                    "entities_json": raw.get("entities_json") or [],
                    "source_json": raw.get("source_json") or {},
                    "evidence_json": raw.get("evidence_json") or [],
                }
            )
            if len(rows) >= EVENT_MAP_INSERT_BATCH_SIZE:
                _insert_ignore(session, EventMapRecordRevision, rows)
                count += len(rows)
                rows.clear()
                checkpoint(count)
    if rows:
        _insert_ignore(session, EventMapRecordRevision, rows)
        count += len(rows)
        checkpoint(count)
    session.flush()
    return count


def _select_anchor_indices(coordinates: np.ndarray, canonical_ids: Sequence[uuid.UUID]) -> list[int]:
    if len(coordinates) <= EVENT_MAP_ANCHOR_LIMIT:
        return list(range(len(coordinates)))
    bounds = _coordinate_bounds(coordinates)
    span_x = max(bounds["max_x"] - bounds["min_x"], 1e-6)
    span_y = max(bounds["max_y"] - bounds["min_y"], 1e-6)
    span_z = max(bounds["max_z"] - bounds["min_z"], 1e-6)
    by_cell: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for index, (x, y, z) in enumerate(coordinates):
        cell_x = min(15, max(0, int((float(x) - bounds["min_x"]) / span_x * 16)))
        cell_y = min(15, max(0, int((float(y) - bounds["min_y"]) / span_y * 16)))
        cell_z = min(15, max(0, int((float(z) - bounds["min_z"]) / span_z * 16)))
        by_cell[(cell_x, cell_y, cell_z)].append(index)
    selected: list[int] = []
    for cell, members in sorted(by_cell.items()):
        center = np.asarray(
            [
                bounds["min_x"] + (cell[0] + 0.5) / 16 * span_x,
                bounds["min_y"] + (cell[1] + 0.5) / 16 * span_y,
                bounds["min_z"] + (cell[2] + 0.5) / 16 * span_z,
            ],
            dtype=np.float32,
        )
        selected.append(
            min(
                members,
                key=lambda index: (
                    float(np.sum(np.square(coordinates[index] - center))),
                    str(canonical_ids[index]),
                ),
            )
        )
    if len(selected) < EVENT_MAP_ANCHOR_LIMIT:
        remaining = sorted(set(range(len(coordinates))) - set(selected), key=lambda index: str(canonical_ids[index]))
        step = max(1, len(remaining) // max(1, EVENT_MAP_ANCHOR_LIMIT - len(selected)))
        selected.extend(remaining[::step][: EVENT_MAP_ANCHOR_LIMIT - len(selected)])
    return sorted(set(selected), key=lambda index: str(canonical_ids[index]))[:EVENT_MAP_ANCHOR_LIMIT]


def _assign_story_identities(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    parent_snapshot_id: uuid.UUID | None,
    stories: Sequence[Any],
    canonical_ids: Sequence[uuid.UUID],
) -> tuple[list[uuid.UUID], list[str]]:
    """只在同一算法版本、同一叙事锚点内延续故事身份。"""

    current_members = [
        frozenset(canonical_ids[index] for index in story.member_group_indices)
        for story in stories
    ]
    if not parent_snapshot_id or not stories:
        return (
            [uuid.uuid5(_STORY_IDENTITY_NAMESPACE, f"{snapshot_id}:{index}") for index in range(len(stories))],
            ["new"] * len(stories),
        )
    parent_snapshot = session.get(EventMapSnapshot, parent_snapshot_id)
    if parent_snapshot is None or parent_snapshot.story_algorithm_version != EVENT_MAP_STORY_VERSION:
        return (
            [uuid.uuid5(_STORY_IDENTITY_NAMESPACE, f"{snapshot_id}:{index}") for index in range(len(stories))],
            ["new"] * len(stories),
        )

    rows = session.execute(
        select(
            EventMapStory.story_id,
            EventMapStory.story_identity_id,
            EventMapStory.anchor_key,
            EventMapStoryMember.canonical_id,
        )
        .join(
            EventMapStoryMember,
            and_(
                EventMapStoryMember.snapshot_id == EventMapStory.snapshot_id,
                EventMapStoryMember.story_id == EventMapStory.story_id,
            ),
        )
        .where(EventMapStory.snapshot_id == parent_snapshot_id)
    ).all()
    previous_members: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    previous_anchors: dict[uuid.UUID, str] = {}
    for story_id, identity_id, anchor_key, canonical_id in rows:
        stable_id = identity_id or story_id
        previous_members[stable_id].add(canonical_id)
        previous_anchors[stable_id] = str(anchor_key or "")

    candidates: list[tuple[float, int, str, int, uuid.UUID]] = []
    for current_index, members in enumerate(current_members):
        for identity_id, old_members in previous_members.items():
            if previous_anchors.get(identity_id) != str(stories[current_index].anchor_key or ""):
                continue
            overlap = len(members & old_members)
            union = len(members | old_members)
            jaccard = overlap / union if union else 0.0
            containment = overlap / max(1, min(len(members), len(old_members)))
            score = max(jaccard, containment)
            if overlap == 0 or score < 0.5:
                continue
            candidates.append((score, overlap, str(identity_id), current_index, identity_id))

    assigned: dict[int, uuid.UUID] = {}
    used_previous: set[uuid.UUID] = set()
    for _score, _overlap, _stable_key, current_index, identity_id in sorted(
        candidates,
        key=lambda item: (-item[0], -item[1], item[2], item[3]),
    ):
        if current_index in assigned or identity_id in used_previous:
            continue
        assigned[current_index] = identity_id
        used_previous.add(identity_id)

    identities: list[uuid.UUID] = []
    states: list[str] = []
    for index in range(len(stories)):
        identity_id = assigned.get(index)
        if identity_id is None:
            identity_id = uuid.uuid5(_STORY_IDENTITY_NAMESPACE, f"{snapshot_id}:{index}")
            states.append("new")
        else:
            states.append("retained")
        identities.append(identity_id)
    return identities, states


def _relink_legacy_story_identities_for_bootstrap(
    session: Session,
    *,
    snapshot_id: uuid.UUID,
    parent_snapshot_id: uuid.UUID | None,
) -> int:
    """把迁移前相邻快照的一次性 story_id 保守连接为稳定身份。"""

    if parent_snapshot_id is None:
        return 0

    def memberships(target_snapshot_id: uuid.UUID) -> dict[uuid.UUID, tuple[uuid.UUID, set[uuid.UUID]]]:
        rows = session.execute(
            select(
                EventMapStory.story_id,
                EventMapStory.story_identity_id,
                EventMapStoryMember.canonical_id,
            )
            .join(
                EventMapStoryMember,
                and_(
                    EventMapStoryMember.snapshot_id == EventMapStory.snapshot_id,
                    EventMapStoryMember.story_id == EventMapStory.story_id,
                ),
            )
            .where(EventMapStory.snapshot_id == target_snapshot_id)
        ).all()
        output: dict[uuid.UUID, tuple[uuid.UUID, set[uuid.UUID]]] = {}
        for story_id, identity_id, canonical_id in rows:
            stable_id, members = output.setdefault(story_id, (identity_id or story_id, set()))
            members.add(canonical_id)
            output[story_id] = (stable_id, members)
        return output

    previous = memberships(parent_snapshot_id)
    current = memberships(snapshot_id)
    candidates: list[tuple[float, int, str, str, uuid.UUID, uuid.UUID]] = []
    for current_story_id, (current_identity_id, current_members) in current.items():
        # 新构建已经明确写入稳定身份时不再参与旧数据回填。
        if current_identity_id != current_story_id:
            continue
        for _previous_story_id, (previous_identity_id, previous_members) in previous.items():
            overlap = len(current_members & previous_members)
            union = len(current_members | previous_members)
            score = overlap / union if union else 0.0
            exact_singleton = len(current_members) == len(previous_members) == overlap == 1
            if not exact_singleton and (overlap < 2 or score < 0.6):
                continue
            candidates.append(
                (
                    score,
                    overlap,
                    str(previous_identity_id),
                    str(current_story_id),
                    current_story_id,
                    previous_identity_id,
                )
            )

    assigned_current: set[uuid.UUID] = set()
    used_previous: set[uuid.UUID] = set()
    for _score, _overlap, _previous_key, _current_key, current_story_id, previous_identity_id in sorted(
        candidates,
        key=lambda item: (-item[0], -item[1], item[2], item[3]),
    ):
        if current_story_id in assigned_current or previous_identity_id in used_previous:
            continue
        session.execute(
            update(EventMapStory)
            .where(
                EventMapStory.snapshot_id == snapshot_id,
                EventMapStory.story_id == current_story_id,
            )
            .values(story_identity_id=previous_identity_id)
        )
        assigned_current.add(current_story_id)
        used_previous.add(previous_identity_id)
    if assigned_current:
        session.flush()
    return len(assigned_current)


def _build_snapshot_rows(
    *,
    directory: Path,
    snapshot: EventMapSnapshot,
    playlist_id: uuid.UUID,
    staged: StagedEventMapInputs,
    groups: Sequence[Any],
    canonical_ids: Sequence[uuid.UUID],
    identity_states: Sequence[str],
    lineage_rows: list[dict[str, Any]],
    topics: Sequence[Any],
    topic_by_group: Sequence[int],
    stories: Sequence[Any],
    coordinates: np.ndarray,
    canonical_vectors: np.ndarray,
    checkpoint: Callable[[int], None],
    story_identity_ids: Sequence[uuid.UUID] | None = None,
    story_identity_states: Sequence[str] | None = None,
) -> dict[str, _DiskRowBuffer]:
    records = staged.records
    if story_identity_ids is None:
        story_identity_ids = [
            uuid.uuid5(_STORY_IDENTITY_NAMESPACE, f"{snapshot.id}:{index}")
            for index in range(len(stories))
        ]
    if story_identity_states is None:
        story_identity_states = ["new"] * len(stories)
    buffers: dict[str, _DiskRowBuffer] = {}

    def row_buffer(name: str) -> _DiskRowBuffer:
        buffer = _DiskRowBuffer(directory / f"event-map-{name}.rows")
        buffers[name] = buffer
        return buffer

    category_by_value = {
        str(item["value"]): int(item["code"])
        for item in staged.projection.categories
    }
    fallback_type_code = category_by_value.get("other", 0)
    identity_rows = row_buffer("identity")
    canonical_rows = row_buffer("canonical")
    member_rows = row_buffer("member")
    for group_index, group in enumerate(groups):
        canonical_id = canonical_ids[group_index]
        representative = records[group.representative_index]
        member_records = [records[index] for index in group.member_indices]
        intersection_start = max(record.start_day for record in member_records)
        intersection_end = min(record.end_day for record in member_records)
        if intersection_start > intersection_end:
            intersection_start = representative.start_day
            intersection_end = representative.end_day
            group.uncertainty_flags.add("time_interval_disagreement")
        start = _day_datetime(intersection_start)
        end = _day_datetime(intersection_end, end=True)
        vector = np.asarray(canonical_vectors[group_index], dtype=np.float32)
        vector_checksum = hashlib.sha256(vector.tobytes()).hexdigest()
        decision_scores = [
            decision.score
            for decision in group.decisions.values()
            if decision.score is not None
        ]
        runner_margins = [
            decision.score - decision.runner_up_score
            for decision in group.decisions.values()
            if decision.score is not None and decision.runner_up_score is not None
        ]
        reason_codes = sorted(
            set(reason for decision in group.decisions.values() for reason in decision.reasons)
        )
        identity_rows.append(
            {
                "id": canonical_id,
                "playlist_id": playlist_id,
                "status": "active",
                "created_snapshot_id": snapshot.id,
            }
        )
        canonical_rows.append(
            {
                "snapshot_id": snapshot.id,
                "canonical_id": canonical_id,
                "representative_revision_id": representative.revision_id,
                "title": representative.title or None,
                "summary": representative.summary or None,
                "event_type": representative.event_type,
                "event_time_start": start,
                "event_time_end": end,
                "time_precision": representative.time_precision,
                "time_basis": {
                    "method": "member_interval_intersection_v1",
                    "representative_event_id": str(representative.event_id),
                },
                "event_start_day": intersection_start,
                "event_end_day": intersection_end,
                "event_type_code": category_by_value.get(representative.event_type, fallback_type_code),
                "time_precision_code": EVENT_MAP_PRECISION_CODES.get(representative.time_precision, 0),
                "member_count": len(group.member_indices),
                "identity_state": identity_states[group_index],
                "decision_score": min(decision_scores) if decision_scores else None,
                "runner_up_margin": min(runner_margins) if runner_margins else None,
                "reason_codes": reason_codes,
                "time_disagreement_count": sum(
                    1
                    for record in member_records
                    if record.start_day != representative.start_day or record.end_day != representative.end_day
                ),
                "centroid_checksum": vector_checksum,
                "point_index": group_index,
                "x": float(coordinates[group_index, 0]),
                "y": float(coordinates[group_index, 1]),
                "z": float(coordinates[group_index, 2]),
                "uncertainty_flags": sorted(group.uncertainty_flags),
                "has_uncertainty": bool(group.uncertainty_flags),
            }
        )
        for record_index in group.member_indices:
            record = records[record_index]
            decision = group.decisions[record_index]
            margin = (
                decision.score - decision.runner_up_score
                if decision.score is not None and decision.runner_up_score is not None
                else None
            )
            member_rows.append(
                {
                    "snapshot_id": snapshot.id,
                    "record_revision_id": record.revision_id,
                    "canonical_id": canonical_id,
                    "is_representative": record_index == group.representative_index,
                    "assignment_kind": decision.rule,
                    "decision_score": decision.score,
                    "runner_up_canonical_id": None,
                    "runner_up_score": decision.runner_up_score,
                    "runner_up_margin": margin,
                    "rule_version": EVENT_MAP_CANONICAL_VERSION,
                    "reason_codes": list(decision.reasons),
                }
            )
        if (group_index + 1) % EVENT_MAP_INSERT_BATCH_SIZE == 0:
            checkpoint(6550)

    topic_ids = [uuid.uuid5(snapshot.id, f"topic:{topic.topic_index}") for topic in topics]
    topic_rows = row_buffer("topic")
    topic_member_rows = row_buffer("topic-member")
    for topic in topics:
        members = list(topic.member_group_indices)
        topic_id = topic_ids[topic.topic_index]
        label_coordinate = np.mean(coordinates[members], axis=0)
        radius = float(
            np.max(np.linalg.norm(coordinates[members] - label_coordinate, axis=1))
        ) if len(members) > 1 else 0.0
        anchor_group_index = min(
            members,
            key=lambda index: (
                float(np.sum(np.square(coordinates[index] - label_coordinate))),
                str(canonical_ids[index]),
            ),
        )
        level = 0 if topic.parent_topic_index is None else 1
        topic_rows.append(
            {
                "snapshot_id": snapshot.id,
                "topic_id": topic_id,
                "level": level,
                "parent_topic_id": topic_ids[topic.parent_topic_index] if topic.parent_topic_index is not None else None,
                "predecessor_topic_id": None,
                "label": topic.label,
                "top_terms": [part.strip() for part in topic.label.split("·") if part.strip()],
                "centroid_vector": np.asarray(topic.center, dtype=np.float32).tobytes(),
                "anchor_canonical_id": canonical_ids[anchor_group_index],
                "center_x": float(label_coordinate[0]),
                "center_y": float(label_coordinate[1]),
                "center_z": float(label_coordinate[2]),
                "radius": radius,
                "stability": None,
                "assignment_margin": None,
                "canonical_count": len(members),
                "member_count": sum(len(groups[index].member_indices) for index in members),
            }
        )
        for group_index in members:
            topic_member_rows.append(
                {
                    "snapshot_id": snapshot.id,
                    "canonical_id": canonical_ids[group_index],
                    "level": level,
                    "topic_id": topic_id,
                    "score": None,
                    "margin": None,
                }
            )
        checkpoint(6600)

    story_identity_rows = row_buffer("story-identity")
    story_rows = row_buffer("story")
    story_member_rows = row_buffer("story-member")
    story_edge_rows = row_buffer("story-edge")
    for story in stories:
        story_id = uuid.uuid5(snapshot.id, f"story:{story.story_index}")
        story_identity_id = story_identity_ids[story.story_index]
        if story_identity_states[story.story_index] == "new":
            stable_title = str(story.label or "").strip()
            if not stable_title:
                raise JobTerminalFailure("new story identity is missing its stable title")
            story_identity_rows.append(
                {
                    "id": story_identity_id,
                    "playlist_id": playlist_id,
                    "status": "active",
                    "stable_title": stable_title,
                    "created_snapshot_id": snapshot.id,
                    "last_material_snapshot_id": snapshot.id,
                    "last_material_changed_at": snapshot.created_at,
                }
            )
        story_records = [records[groups[index].representative_index] for index in story.member_group_indices]
        story_rows.append(
            {
                "snapshot_id": snapshot.id,
                "story_id": story_id,
                "story_identity_id": story_identity_id,
                "title": story.label,
                "summary": story.summary,
                "story_type": story.story_type,
                "anchor_key": story.anchor_key,
                "maturity": story.maturity,
                "quality_score": story.quality_score,
                "event_time_start": _day_datetime(min(record.start_day for record in story_records)),
                "event_time_end": _day_datetime(max(record.end_day for record in story_records), end=True),
                "canonical_count": len(story.member_group_indices),
            }
        )
        for position, group_index in enumerate(story.member_group_indices):
            story_member_rows.append(
                {
                    "snapshot_id": snapshot.id,
                    "story_id": story_id,
                    "position": position,
                    "canonical_id": canonical_ids[group_index],
                }
            )
        for edge in story.edges:
            evidence_revision_ids = [
                str(value)
                for value in edge.evidence.get("supporting_revision_ids", [])
            ]
            story_edge_rows.append(
                {
                    "snapshot_id": snapshot.id,
                    "edge_id": uuid.uuid5(
                        story_id,
                        f"{edge.source_group_index}:{edge.target_group_index}:{edge.relation_type}",
                    ),
                    "story_id": story_id,
                    "source_canonical_id": canonical_ids[edge.source_group_index],
                    "target_canonical_id": canonical_ids[edge.target_group_index],
                    "relation_type": edge.relation_type,
                    "direction": "forward",
                    "score": edge.confidence,
                    "status": "automatic",
                    "evidence_revision_ids": evidence_revision_ids,
                    "evidence_json": edge.evidence,
                    "method_version": EVENT_MAP_STORY_VERSION,
                }
            )
        if (story.story_index + 1) % EVENT_MAP_INSERT_BATCH_SIZE == 0:
            checkpoint(6625)

    anchor_rows = row_buffer("anchor")
    for rank, group_index in enumerate(_select_anchor_indices(coordinates, canonical_ids)):
        vector = np.asarray(canonical_vectors[group_index], dtype=np.float32)
        anchor_rows.append(
            {
                "snapshot_id": snapshot.id,
                "anchor_rank": rank,
                "canonical_id": canonical_ids[group_index],
                "x": float(coordinates[group_index, 0]),
                "y": float(coordinates[group_index, 1]),
                "z": float(coordinates[group_index, 2]),
                "centroid_vector": vector.tobytes(),
                "vector_checksum": hashlib.sha256(vector.tobytes()).hexdigest(),
                "inherited_parent_anchor_rank": None,
            }
        )

    entity_rows = row_buffer("entity")
    for group_index, group in enumerate(groups):
        entities: dict[tuple[str, str], dict[str, Any]] = {}
        for record_index in group.member_indices:
            record_keys: set[tuple[str, str]] = set()
            for entity in records[record_index].entities:
                canonical_key = entity.canonical_key
                if not canonical_key or ":" not in canonical_key:
                    continue
                entity_type, normalized_key = canonical_key.split(":", 1)
                if not normalized_key:
                    continue
                key = (entity_type, normalized_key)
                value = entities.setdefault(
                    key,
                    {
                        "name": str(entity.name or entity.key or normalized_key),
                        "record_count": 0,
                    },
                )
                if key not in record_keys:
                    value["record_count"] += 1
                    record_keys.add(key)
        for (entity_type, normalized_key), value in sorted(entities.items()):
            entity_rows.append(
                {
                    "snapshot_id": snapshot.id,
                    "canonical_id": canonical_ids[group_index],
                    "entity_type": entity_type,
                    "normalized_key": normalized_key,
                    "name": value["name"],
                    "point_index": group_index,
                    "record_count": value["record_count"],
                }
            )
        if (group_index + 1) % EVENT_MAP_INSERT_BATCH_SIZE == 0:
            checkpoint(6675)

    lineage_buffer = row_buffer("lineage")
    for row in lineage_rows:
        lineage_buffer.append(row)
    lineage_rows.clear()

    result = {
        "identity": identity_rows,
        "canonical": canonical_rows,
        "member": member_rows,
        "entity": entity_rows,
        "lineage": lineage_buffer,
        "topic": topic_rows,
        "topic_member": topic_member_rows,
        "story_identity": story_identity_rows,
        "story": story_rows,
        "story_member": story_member_rows,
        "story_edge": story_edge_rows,
        "anchor": anchor_rows,
    }
    for buffer in result.values():
        buffer.close()
    checkpoint(6700)
    return result


def _canonical_revision_payloads(
    session: Session,
    *,
    snapshot_id: uuid.UUID,
) -> dict[uuid.UUID, dict[str, Any]]:
    payloads: dict[uuid.UUID, dict[str, Any]] = {}
    for row in session.execute(
        select(
            EventMapCanonical.canonical_id,
            EventMapCanonical.title,
            EventMapCanonical.summary,
            EventMapCanonical.event_type,
            EventMapCanonical.event_time_start,
            EventMapCanonical.event_time_end,
            EventMapCanonical.time_precision,
            EventMapCanonical.member_count,
            EventMapCanonical.identity_state,
            EventMapCanonical.uncertainty_flags,
            EventMapCanonical.point_index,
            EventMapCanonical.x,
            EventMapCanonical.y,
            EventMapCanonical.z,
        ).where(EventMapCanonical.snapshot_id == snapshot_id)
    ).mappings():
        canonical_id = row["canonical_id"]
        payloads[canonical_id] = {
            "canonical_id": str(canonical_id),
            "title": row["title"],
            "summary": row["summary"],
            "event_type": row["event_type"],
            "event_time_start": _json_value(row["event_time_start"]),
            "event_time_end": _json_value(row["event_time_end"]),
            "time_precision": row["time_precision"],
            "member_count": int(row["member_count"] or 0),
            "identity_state": row["identity_state"],
            "uncertainty_flags": list(row["uncertainty_flags"] or []),
            "point_index": int(row["point_index"]),
            "coordinates": [float(row["x"]), float(row["y"]), float(row["z"])],
            "member_revision_ids": [],
            "evidence_revision_ids": [],
        }
    for row in session.execute(
        select(
            EventMapCanonicalMember.canonical_id,
            EventMapRecordRevision.id,
            EventMapRecordRevision.evidence_json,
        )
        .join(
            EventMapRecordRevision,
            EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id,
        )
        .where(EventMapCanonicalMember.snapshot_id == snapshot_id)
        .order_by(EventMapCanonicalMember.canonical_id, EventMapRecordRevision.id)
    ).mappings():
        payload = payloads.get(row["canonical_id"])
        if payload is None:
            continue
        revision_id = str(row["id"])
        payload["member_revision_ids"].append(revision_id)
        if row["evidence_json"]:
            payload["evidence_revision_ids"].append(revision_id)
    return payloads


def _story_revision_payloads(
    session: Session,
    *,
    snapshot_id: uuid.UUID,
) -> dict[uuid.UUID, dict[str, Any]]:
    payloads: dict[uuid.UUID, dict[str, Any]] = {}
    story_id_to_identity: dict[uuid.UUID, uuid.UUID] = {}
    for row in session.execute(
        select(EventMapStory).where(EventMapStory.snapshot_id == snapshot_id)
    ).scalars():
        identity_id = row.story_identity_id or row.story_id
        story_id_to_identity[row.story_id] = identity_id
        payloads[identity_id] = {
            "story_identity_id": str(identity_id),
            "story_id": str(row.story_id),
            "title": row.title,
            "summary": row.summary,
            "story_type": row.story_type,
            "anchor_key": row.anchor_key,
            "maturity": row.maturity,
            "quality_score": row.quality_score,
            "event_time_start": _json_value(row.event_time_start),
            "event_time_end": _json_value(row.event_time_end),
            "member_ids": [],
            "edges": [],
            "evidence_revision_ids": [],
        }
    for row in session.execute(
        select(EventMapStoryMember)
        .where(EventMapStoryMember.snapshot_id == snapshot_id)
        .order_by(EventMapStoryMember.story_id, EventMapStoryMember.position)
    ).scalars():
        identity_id = story_id_to_identity.get(row.story_id)
        if identity_id in payloads:
            payloads[identity_id]["member_ids"].append(str(row.canonical_id))
    for row in session.execute(
        select(EventMapStoryEdge)
        .where(EventMapStoryEdge.snapshot_id == snapshot_id)
        .order_by(EventMapStoryEdge.story_id, EventMapStoryEdge.edge_id)
    ).scalars():
        identity_id = story_id_to_identity.get(row.story_id)
        if identity_id not in payloads:
            continue
        evidence_ids = sorted(str(item) for item in (row.evidence_revision_ids or []))
        payloads[identity_id]["edges"].append(
            {
                "edge_id": str(
                    uuid.uuid5(
                        identity_id,
                        f"{row.source_canonical_id}:{row.target_canonical_id}:{row.relation_type}",
                    )
                ),
                "source_canonical_id": str(row.source_canonical_id),
                "target_canonical_id": str(row.target_canonical_id),
                "relation_type": row.relation_type,
                "direction": row.direction,
                "score": row.score,
                "status": row.status,
                "evidence_revision_ids": evidence_ids,
                "evidence": dict(row.evidence_json or {}),
            }
        )
        payloads[identity_id]["evidence_revision_ids"].extend(evidence_ids)
    for payload in payloads.values():
        payload["evidence_revision_ids"] = sorted(set(payload["evidence_revision_ids"]))
    return payloads


def _archive_payloads(
    session: Session,
    *,
    snapshot_id: uuid.UUID | None,
    object_type: str,
) -> dict[uuid.UUID, dict[str, Any]]:
    if snapshot_id is None:
        return {}
    if object_type == "canonical":
        rows = session.execute(
            select(EventMapCanonicalHistoryRevision).where(
                EventMapCanonicalHistoryRevision.snapshot_id == snapshot_id
            )
        ).scalars().all()
        if rows:
            return {row.canonical_id: dict(row.revision or {}) for row in rows}
        return _canonical_revision_payloads(session, snapshot_id=snapshot_id)
    rows = session.execute(
        select(EventMapStoryHistoryRevision).where(
            EventMapStoryHistoryRevision.snapshot_id == snapshot_id
        )
    ).scalars().all()
    if rows:
        return {
            row.story_identity_id: {
                "story_identity_id": str(row.story_identity_id),
                "story_id": str(row.story_id),
                "title": row.title,
                "summary": row.summary,
                "story_type": row.story_type,
                "anchor_key": row.anchor_key,
                "maturity": row.maturity,
                "quality_score": row.quality_score,
                "event_time_start": _json_value(row.event_time_start),
                "event_time_end": _json_value(row.event_time_end),
                "member_ids": list(row.member_ids or []),
                "edges": list(row.edges or []),
                "evidence_revision_ids": list(row.evidence_revision_ids or []),
            }
            for row in rows
        }
    return _story_revision_payloads(session, snapshot_id=snapshot_id)


def _change_row(
    *,
    playlist_id: uuid.UUID,
    from_snapshot_id: uuid.UUID | None,
    to_snapshot_id: uuid.UUID,
    object_type: str,
    object_id: uuid.UUID,
    change_type: str,
    occurred_at: datetime | None,
    observed_at: datetime,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid5(
            _CHANGE_NAMESPACE,
            f"{to_snapshot_id}:{object_type}:{object_id}:{change_type}",
        ),
        "playlist_id": playlist_id,
        "from_snapshot_id": from_snapshot_id,
        "to_snapshot_id": to_snapshot_id,
        "object_type": object_type,
        "object_id": object_id,
        "change_type": change_type,
        "occurred_at": occurred_at,
        "observed_at": observed_at,
        "before_revision": before,
        "after_revision": after,
        "evidence_revision_ids": list((after or before or {}).get("evidence_revision_ids") or []),
    }


def _parse_payload_datetime(payload: dict[str, Any], key: str) -> datetime | None:
    raw = payload.get(key)
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _canonical_history_member_rows(
    *,
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    payloads: dict[uuid.UUID, dict[str, Any]],
) -> list[dict[str, Any]]:
    """把长期修订中的成员引用投影为可索引关系；JSONB 仍保留为修订正文。"""

    rows: list[dict[str, Any]] = []
    for canonical_id, payload in payloads.items():
        history_revision_id = uuid.uuid5(
            _HISTORY_NAMESPACE,
            f"canonical:{snapshot_id}:{canonical_id}",
        )
        evidence_ids = set(payload.get("evidence_revision_ids") or [])
        for raw_revision_id in payload.get("member_revision_ids") or []:
            record_revision_id = uuid.UUID(str(raw_revision_id))
            rows.append(
                {
                    "history_revision_id": history_revision_id,
                    "record_revision_id": record_revision_id,
                    "playlist_id": playlist_id,
                    "canonical_id": canonical_id,
                    "snapshot_id": snapshot_id,
                    "is_evidence": str(record_revision_id) in evidence_ids,
                }
            )
    return rows


def _story_history_evidence_rows(
    *,
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    payloads: dict[uuid.UUID, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for story_identity_id, payload in payloads.items():
        history_revision_id = uuid.uuid5(
            _HISTORY_NAMESPACE,
            f"story:{snapshot_id}:{story_identity_id}",
        )
        for edge in payload.get("edges") or []:
            edge_id = uuid.UUID(str(edge["edge_id"]))
            source_id = uuid.UUID(str(edge["source_canonical_id"]))
            target_id = uuid.UUID(str(edge["target_canonical_id"]))
            for raw_revision_id in edge.get("evidence_revision_ids") or []:
                rows.append(
                    {
                        "history_revision_id": history_revision_id,
                        "edge_id": edge_id,
                        "record_revision_id": uuid.UUID(str(raw_revision_id)),
                        "playlist_id": playlist_id,
                        "story_identity_id": story_identity_id,
                        "snapshot_id": snapshot_id,
                        "source_canonical_id": source_id,
                        "target_canonical_id": target_id,
                        "relation_type": str(edge.get("relation_type") or "related"),
                    }
                )
    return rows


def _story_has_new_correction(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    """只把新增且显式标为 corrects 的有证据关系识别为纠正。"""

    def edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(edge.get("source_canonical_id") or ""),
            str(edge.get("target_canonical_id") or ""),
            str(edge.get("relation_type") or ""),
        )

    previous = {edge_key(dict(edge)) for edge in (before.get("edges") or [])}
    return any(
        edge_key(dict(edge)) not in previous
        and str(edge.get("relation_type") or "").strip().lower() == "corrects"
        and bool(edge.get("evidence_revision_ids"))
        for edge in (after.get("edges") or [])
    )


def _story_relation_material_projection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """只比较关系事实与判定依据，排除可重复计算的分数和支持记录集合。"""

    projected: list[dict[str, Any]] = []
    for raw_edge in payload.get("edges") or []:
        edge = dict(raw_edge)
        evidence = {
            str(key): value
            for key, value in dict(edge.get("evidence") or {}).items()
            if str(key) not in _STORY_RELATION_METRIC_KEYS
        }
        projected.append(
            {
                "source_canonical_id": str(edge.get("source_canonical_id") or ""),
                "target_canonical_id": str(edge.get("target_canonical_id") or ""),
                "relation_type": str(edge.get("relation_type") or ""),
                "direction": str(edge.get("direction") or ""),
                "status": str(edge.get("status") or ""),
                "evidence": evidence,
            }
        )
    return sorted(
        projected,
        key=lambda edge: (
            edge["source_canonical_id"],
            edge["target_canonical_id"],
            edge["relation_type"],
            edge["direction"],
            edge["status"],
            json.dumps(edge["evidence"], ensure_ascii=False, sort_keys=True),
        ),
    )


def _story_support_material_projection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """按关系保留支持记录集合，避免相同并集在关系间移动时被误判为无变化。"""

    projected = [
        {
            "source_canonical_id": str(edge.get("source_canonical_id") or ""),
            "target_canonical_id": str(edge.get("target_canonical_id") or ""),
            "relation_type": str(edge.get("relation_type") or ""),
            "evidence_revision_ids": sorted(
                {str(value) for value in (edge.get("evidence_revision_ids") or [])}
            ),
        }
        for edge in (payload.get("edges") or [])
    ]
    return sorted(
        projected,
        key=lambda edge: (
            edge["source_canonical_id"],
            edge["target_canonical_id"],
            edge["relation_type"],
        ),
    )


def _story_change_types(
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> list[str]:
    """区分会恢复未读的事实变化与只供审计的文案变化。"""

    if before is None:
        return ["story_added"]

    change_types: list[str] = []
    if list(before.get("member_ids") or []) != list(after.get("member_ids") or []):
        change_types.append("story_members_changed")
    if _story_relation_material_projection(before) != _story_relation_material_projection(after):
        change_types.append("story_relations_changed")
        if _story_has_new_correction(before, after):
            change_types.append("story_correction_added")
    if _story_support_material_projection(before) != _story_support_material_projection(after):
        change_types.append("story_evidence_changed")
    if str(before.get("maturity") or "") != str(after.get("maturity") or ""):
        change_types.append("story_maturity_changed")
    if before.get("summary") != after.get("summary") or before.get("title") != after.get("title"):
        change_types.append("story_summary_changed")
    return change_types


def _persist_v2_history_and_changes(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    parent_snapshot_id: uuid.UUID | None,
    observed_at: datetime,
    layout_continuity: str,
) -> None:
    """在原子发布前写入可长期保留的对象修订和幂等变化集。"""

    current_canonicals = _canonical_revision_payloads(session, snapshot_id=snapshot_id)
    previous_canonicals = _archive_payloads(
        session,
        snapshot_id=parent_snapshot_id,
        object_type="canonical",
    )
    canonical_history_rows = [
        {
            "id": uuid.uuid5(_HISTORY_NAMESPACE, f"canonical:{snapshot_id}:{canonical_id}"),
            "playlist_id": playlist_id,
            "canonical_id": canonical_id,
            "snapshot_id": snapshot_id,
            "revision": payload,
            "occurred_at": _parse_payload_datetime(payload, "event_time_start"),
            "observed_at": observed_at,
        }
        for canonical_id, payload in current_canonicals.items()
    ]
    _insert_ignore_batched(session, EventMapCanonicalHistoryRevision, canonical_history_rows)
    _insert_ignore_batched(
        session,
        EventMapCanonicalHistoryMember,
        _canonical_history_member_rows(
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
            payloads=current_canonicals,
        ),
    )

    changes: list[dict[str, Any]] = []
    for canonical_id, after in current_canonicals.items():
        before = previous_canonicals.get(canonical_id)
        occurred_at = _parse_payload_datetime(after, "event_time_start")
        if before is None:
            changes.append(
                _change_row(
                    playlist_id=playlist_id,
                    from_snapshot_id=parent_snapshot_id,
                    to_snapshot_id=snapshot_id,
                    object_type="canonical",
                    object_id=canonical_id,
                    change_type="canonical_added",
                    occurred_at=occurred_at,
                    observed_at=observed_at,
                    before=None,
                    after=after,
                )
            )
            continue
        content_keys = ("title", "summary", "event_type", "event_time_start", "event_time_end", "time_precision")
        if any(before.get(key) != after.get(key) for key in content_keys):
            changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="canonical", object_id=canonical_id, change_type="canonical_updated", occurred_at=occurred_at, observed_at=observed_at, before=before, after=after))
        if set(before.get("member_revision_ids") or []) != set(after.get("member_revision_ids") or []):
            changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="canonical", object_id=canonical_id, change_type="canonical_members_changed", occurred_at=occurred_at, observed_at=observed_at, before=before, after=after))
        if set(before.get("evidence_revision_ids") or []) != set(after.get("evidence_revision_ids") or []):
            changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="canonical", object_id=canonical_id, change_type="canonical_evidence_changed", occurred_at=occurred_at, observed_at=observed_at, before=before, after=after))

    lineage_by_predecessor: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in session.execute(
        select(EventMapCanonicalLineage).where(EventMapCanonicalLineage.snapshot_id == snapshot_id)
    ).scalars():
        detail = {
            "predecessor_canonical_id": str(row.predecessor_canonical_id),
            "successor_canonical_id": str(row.successor_canonical_id),
            "relation_type": row.relation_type,
            "confidence": row.confidence,
        }
        lineage_by_predecessor[row.predecessor_canonical_id].append(detail)
        successor_after = current_canonicals.get(row.successor_canonical_id)
        changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="canonical", object_id=row.successor_canonical_id, change_type=f"canonical_{row.relation_type}", occurred_at=_parse_payload_datetime(successor_after or {}, "event_time_start"), observed_at=observed_at, before={"lineage": detail}, after=successor_after))

    retired_canonical_ids = set(previous_canonicals) - set(current_canonicals)
    for canonical_id in retired_canonical_ids:
        before = previous_canonicals[canonical_id]
        after = {"lineage": lineage_by_predecessor.get(canonical_id, [])}
        changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="canonical", object_id=canonical_id, change_type="canonical_retired", occurred_at=_parse_payload_datetime(before, "event_time_start"), observed_at=observed_at, before=before, after=after))
    if retired_canonical_ids:
        session.execute(
            update(EventMapCanonicalIdentity)
            .where(EventMapCanonicalIdentity.id.in_(retired_canonical_ids))
            .values(status="retired", retired_snapshot_id=snapshot_id, updated_at=observed_at)
        )

    current_stories = _story_revision_payloads(session, snapshot_id=snapshot_id)
    previous_stories = _archive_payloads(
        session,
        snapshot_id=parent_snapshot_id,
        object_type="story",
    )
    story_history_rows: list[dict[str, Any]] = []
    for identity_id, payload in current_stories.items():
        evidence_ids = sorted(set(payload.get("evidence_revision_ids") or []))
        story_history_rows.append(
            {
                "id": uuid.uuid5(_HISTORY_NAMESPACE, f"story:{snapshot_id}:{identity_id}"),
                "playlist_id": playlist_id,
                "story_identity_id": identity_id,
                "snapshot_id": snapshot_id,
                "story_id": uuid.UUID(payload["story_id"]),
                "title": payload["title"],
                "summary": payload.get("summary"),
                "story_type": payload.get("story_type") or "trajectory",
                "anchor_key": payload.get("anchor_key"),
                "maturity": payload.get("maturity") or "emerging",
                "quality_score": payload.get("quality_score"),
                "event_time_start": _parse_payload_datetime(payload, "event_time_start"),
                "event_time_end": _parse_payload_datetime(payload, "event_time_end"),
                "member_ids": list(payload.get("member_ids") or []),
                "edges": list(payload.get("edges") or []),
                "evidence_revision_ids": evidence_ids,
                "method_version": EVENT_MAP_STORY_VERSION,
                "observed_at": observed_at,
            }
        )
        before = previous_stories.get(identity_id)
        occurred_at = _parse_payload_datetime(payload, "event_time_end")
        change_types = _story_change_types(before, payload)
        for change_type in change_types:
            changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="story", object_id=identity_id, change_type=change_type, occurred_at=occurred_at, observed_at=observed_at, before=before, after=payload))
        if any(change_type in STORY_MATERIAL_CHANGE_TYPES for change_type in change_types):
            session.execute(
                update(EventMapStoryIdentity)
                .where(EventMapStoryIdentity.id == identity_id)
                .values(
                    last_material_snapshot_id=snapshot_id,
                    last_material_changed_at=observed_at,
                    updated_at=observed_at,
                )
            )
    _insert_ignore_batched(session, EventMapStoryHistoryRevision, story_history_rows)
    _insert_ignore_batched(
        session,
        EventMapStoryHistoryEvidence,
        _story_history_evidence_rows(
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
            payloads=current_stories,
        ),
    )

    retired_story_ids = set(previous_stories) - set(current_stories)
    for identity_id in retired_story_ids:
        before = previous_stories[identity_id]
        changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="story", object_id=identity_id, change_type="story_retired", occurred_at=_parse_payload_datetime(before, "event_time_end"), observed_at=observed_at, before=before, after=None))
    if retired_story_ids:
        session.execute(
            update(EventMapStoryIdentity)
            .where(EventMapStoryIdentity.id.in_(retired_story_ids))
            .values(status="retired", retired_snapshot_id=snapshot_id, updated_at=observed_at)
        )

    if parent_snapshot_id and layout_continuity == "rebased":
        changes.append(_change_row(playlist_id=playlist_id, from_snapshot_id=parent_snapshot_id, to_snapshot_id=snapshot_id, object_type="field", object_id=playlist_id, change_type="layout_rebased", occurred_at=None, observed_at=observed_at, before={"snapshot_id": str(parent_snapshot_id)}, after={"snapshot_id": str(snapshot_id), "layout_continuity": layout_continuity}))
    _insert_ignore_batched(session, EventMapChange, changes)
    session.flush()


def bootstrap_v2_event_map_history(session: Session) -> int:
    """为部署前仍保留的 ready 快照补齐 V2 长期历史；只做幂等加法写入。"""

    snapshots = session.execute(
        select(EventMapSnapshot)
        .where(EventMapSnapshot.status == "ready")
        .order_by(
            EventMapSnapshot.playlist_id.asc(),
            EventMapSnapshot.finished_at.asc().nullsfirst(),
            EventMapSnapshot.id.asc(),
        )
    ).scalars().all()
    completed = 0
    for snapshot in snapshots:
        exists = session.execute(
            select(EventMapCanonicalHistoryRevision.id)
            .where(EventMapCanonicalHistoryRevision.snapshot_id == snapshot.id)
            .limit(1)
        ).scalar_one_or_none()
        if exists is None:
            _relink_legacy_story_identities_for_bootstrap(
                session,
                snapshot_id=snapshot.id,
                parent_snapshot_id=snapshot.parent_snapshot_id,
            )
        if exists is not None:
            # 历史关系索引以观测域开头；仅按 snapshot_id 检查会扫描跨域历史。
            membership_exists = session.execute(
                select(EventMapCanonicalHistoryMember.history_revision_id)
                .where(
                    EventMapCanonicalHistoryMember.playlist_id == snapshot.playlist_id,
                    EventMapCanonicalHistoryMember.snapshot_id == snapshot.id,
                )
                .limit(1)
            ).scalar_one_or_none()
            if membership_exists is None:
                revisions = session.execute(
                    select(EventMapCanonicalHistoryRevision).where(
                        EventMapCanonicalHistoryRevision.snapshot_id == snapshot.id
                    )
                ).scalars().all()
                _insert_ignore_batched(
                    session,
                    EventMapCanonicalHistoryMember,
                    _canonical_history_member_rows(
                        playlist_id=snapshot.playlist_id,
                        snapshot_id=snapshot.id,
                        payloads={row.canonical_id: dict(row.revision or {}) for row in revisions},
                    ),
                )
                completed += 1
            story_evidence_exists = session.execute(
                select(EventMapStoryHistoryEvidence.history_revision_id)
                .where(
                    EventMapStoryHistoryEvidence.playlist_id == snapshot.playlist_id,
                    EventMapStoryHistoryEvidence.snapshot_id == snapshot.id,
                )
                .limit(1)
            ).scalar_one_or_none()
            if story_evidence_exists is None:
                story_revisions = session.execute(
                    select(EventMapStoryHistoryRevision).where(
                        EventMapStoryHistoryRevision.snapshot_id == snapshot.id
                    )
                ).scalars().all()
                _insert_ignore_batched(
                    session,
                    EventMapStoryHistoryEvidence,
                    _story_history_evidence_rows(
                        playlist_id=snapshot.playlist_id,
                        snapshot_id=snapshot.id,
                        payloads={
                            row.story_identity_id: {
                                "edges": list(row.edges or []),
                            }
                            for row in story_revisions
                        },
                    ),
                )
                if story_revisions:
                    completed += 1
            continue
        observed_at = snapshot.finished_at or snapshot.created_at or utcnow()
        _persist_v2_history_and_changes(
            session,
            playlist_id=snapshot.playlist_id,
            snapshot_id=snapshot.id,
            parent_snapshot_id=snapshot.parent_snapshot_id,
            observed_at=observed_at,
            layout_continuity=snapshot.layout_continuity,
        )
        completed += 1
    return completed


def _ensure_event_map_state(session: Session, playlist_id: uuid.UUID) -> EventMapState:
    state = session.get(EventMapState, playlist_id, with_for_update=True)
    if state is not None:
        return state
    state = EventMapState(playlist_id=playlist_id)
    try:
        with session.begin_nested():
            session.add(state)
            session.flush([state])
    except IntegrityError:
        state = session.get(EventMapState, playlist_id, with_for_update=True)
        if state is None:
            raise
    return state


def _create_or_resume_snapshot(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    input_generation: int,
    parent_snapshot_id: uuid.UUID | None,
    job: Job | None,
    worker_id: str,
    execution_token: uuid.UUID | None,
    model: str,
    dimension: int,
) -> tuple[EventMapSnapshot, int, uuid.UUID | None]:
    if job is not None:
        if not worker_id or execution_token is None:
            raise JobTerminalFailure("event map build requires an owned execution")
        owned_job_id = session.execute(
            select(Job.id)
            .where(
                Job.id == job.id,
                Job.status == "running",
                Job.worker_id == worker_id,
                Job.execution_token == execution_token,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if owned_job_id is None:
            session.rollback()
            raise JobTerminalFailure("event map build ownership changed before staging")
    state = _ensure_event_map_state(session, playlist_id)
    job_id = job.id if job else None
    job_attempt = int(getattr(job, "attempt", 0) or 0) if job else 0
    snapshot = None
    if job_id is not None:
        if execution_token is None:
            raise JobTerminalFailure("event map build requires an execution token")
        snapshot = session.execute(
            select(EventMapSnapshot).where(
                EventMapSnapshot.job_id == job_id,
                EventMapSnapshot.execution_token == execution_token,
            )
        ).scalar_one_or_none()
    if snapshot is not None and snapshot.status == "ready":
        return snapshot, input_generation, parent_snapshot_id
    if snapshot is None:
        snapshot = EventMapSnapshot(
            playlist_id=playlist_id,
            job_id=job_id,
            job_attempt=job_attempt,
            execution_token=execution_token,
            status="running",
            parent_snapshot_id=parent_snapshot_id,
            input_generation=input_generation,
            input_fingerprint="",
            build_key=str(uuid.uuid4()),
            embedding_model=model,
            embedding_dim=dimension,
            canonical_algorithm_version=EVENT_MAP_CANONICAL_VERSION,
            story_algorithm_version=EVENT_MAP_STORY_VERSION,
            topic_algorithm_version=EVENT_MAP_TOPIC_VERSION,
            layout_algorithm_version=EVENT_MAP_LAYOUT_VERSION,
            projection_method=EVENT_MAP_PROJECTION_METHOD,
            projection_seed=EVENT_MAP_PROJECTION_SEED,
            started_at=utcnow(),
        )
        session.add(snapshot)
    else:
        snapshot.status = "running"
        snapshot.parent_snapshot_id = parent_snapshot_id
        snapshot.input_generation = input_generation
        snapshot.error_message = None
        snapshot.started_at = utcnow()
        snapshot.finished_at = None
    state.last_error = None
    session.flush([snapshot, state])
    session.commit()
    return snapshot, input_generation, parent_snapshot_id


def _checkpoint(
    session: Session,
    job: Job | None,
    progress: int,
    *,
    peak: list[int],
    claimed_worker_id: str,
    claimed_execution_token: uuid.UUID | None,
) -> None:
    if job is not None:
        raise_if_job_cancel_requested(session, job)
        if not claimed_worker_id or claimed_execution_token is None or not set_job_progress(
            job_id=job.id,
            worker_id=claimed_worker_id,
            execution_token=claimed_execution_token,
            current=max(0, min(10000, int(progress))),
            total=10000,
            lease_expires_at=utcnow() + timedelta(hours=1),
        ):
            raise JobTerminalFailure("event map build ownership changed while checkpointing")
    peak[0] = max(peak[0], _raise_if_resource_limit_exceeded())


def _finalize_failed_snapshot(
    session: Session,
    *,
    snapshot_id: uuid.UUID | None,
    playlist_id: uuid.UUID,
    job: Job | None,
    claimed_worker_id: str,
    claimed_execution_token: uuid.UUID | None,
    status: str,
    message: str,
) -> bool:
    session.rollback()
    if job is not None:
        if not claimed_worker_id or claimed_execution_token is None:
            return False
        owned_job_id = session.execute(
            select(Job.id)
            .where(
                Job.id == job.id,
                Job.status == "running",
                Job.worker_id == claimed_worker_id,
                Job.execution_token == claimed_execution_token,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if owned_job_id is None:
            session.rollback()
            return False
    snapshot = session.get(EventMapSnapshot, snapshot_id) if snapshot_id else None
    if snapshot is not None and job is not None and (
        snapshot.job_id != job.id or snapshot.execution_token != claimed_execution_token
    ):
        session.rollback()
        return False
    now = utcnow()
    if snapshot is not None and snapshot.status != "ready":
        snapshot.status = status
        snapshot.error_message = message[:2000]
        snapshot.finished_at = now
        snapshot.updated_at = now
    state = session.get(EventMapState, playlist_id, with_for_update=True)
    if state is not None and (job is None or state.active_job_id == job.id):
        if job is not None:
            state.active_job_id = None
        state.last_error = message[:2000]
        state.updated_at = now
    session.flush()
    return True


def _build_event_map_snapshot_locked(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    job: Job | None,
) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if playlist is None:
        return {"skipped": "playlist not found"}
    claimed_worker_id = str(job.worker_id or "").strip() if job is not None else ""
    claimed_execution_token = job.execution_token if job is not None else None
    if job is not None and (
        job.status != "running" or not claimed_worker_id or claimed_execution_token is None
    ):
        raise JobTerminalFailure("event map build requires a currently owned running job")
    spec = embedding_spec()
    snapshot_id: uuid.UUID | None = None
    input_generation = 0
    parent_snapshot_id: uuid.UUID | None = None
    peak_rss = [0]
    batch_size = max(1, int(settings.analysis_stream_batch_size or EVENT_MAP_INSERT_BATCH_SIZE))
    max_rss = max(0, int(settings.analysis_max_rss_bytes or 0))
    min_available = max(0, int(settings.analysis_min_available_memory_bytes or 0))

    try:
        with tempfile.TemporaryDirectory(prefix=f"raelyn-event-map-{playlist_id}-") as raw_directory:
            directory = Path(raw_directory)
            if shutil.disk_usage(directory).free < EVENT_MAP_MIN_TEMP_FREE_BYTES:
                raise JobTerminalFailure("event map build requires at least 5 GiB temporary free space")

            def progress(value: int) -> None:
                _checkpoint(
                    session,
                    job,
                    value,
                    peak=peak_rss,
                    claimed_worker_id=claimed_worker_id,
                    claimed_execution_token=claimed_execution_token,
                )

            with _event_map_snapshot_reader(session) as reader:
                frozen = _frozen_event_map_state(reader, playlist_id=playlist_id)
                if frozen.active_dirty_job_id is not None:
                    return {
                        "ok": False,
                        "outcome": "superseded_by_active_dirty",
                        "playlist_id": str(playlist_id),
                        "active_dirty_job_id": str(frozen.active_dirty_job_id),
                        "frozen_generation": int(frozen.input_generation),
                    }
                input_generation = int(frozen.input_generation)
                parent_snapshot_id = frozen.parent_snapshot_id
                snapshot, _input_generation, _parent_snapshot_id = _create_or_resume_snapshot(
                    session,
                    playlist_id=playlist_id,
                    input_generation=input_generation,
                    parent_snapshot_id=parent_snapshot_id,
                    job=job,
                    worker_id=claimed_worker_id,
                    execution_token=claimed_execution_token,
                    model=spec.model,
                    dimension=spec.dim,
                )
                if snapshot.status == "ready":
                    return {
                        "ok": True,
                        "cached": True,
                        "outcome": "reused_execution_snapshot",
                        "playlist_id": str(playlist_id),
                        "snapshot_id": str(snapshot.id),
                        "frozen_generation": input_generation,
                    }
                snapshot_id = snapshot.id
                progress(0)
                staged = _stage_inputs(
                    reader,
                    playlist_id=playlist_id,
                    model=spec.model,
                    dimension=spec.dim,
                    directory=directory,
                    batch_size=batch_size,
                    checkpoint=lambda processed, total: progress(
                        int(2000 * processed / max(1, total))
                    ),
                )
            if snapshot_id is None:
                raise JobTerminalFailure("event map snapshot was not created before staging")
            snapshot = session.get(EventMapSnapshot, snapshot_id)
            if snapshot is None:
                raise JobTerminalFailure("event map snapshot disappeared during build")
            snapshot.input_fingerprint = staged.input_fingerprint
            snapshot.embedding_checksum = staged.embedding_checksum
            snapshot.input_record_count = len(staged.records)
            snapshot.skipped_reason_counts = staged.skipped_reason_counts
            snapshot.build_key = _sha256_text(
                ":".join(
                    (
                        str(playlist_id),
                        staged.input_fingerprint,
                        spec.model,
                        str(spec.dim),
                        EVENT_MAP_CANONICAL_VERSION,
                        EVENT_MAP_STORY_VERSION,
                        EVENT_MAP_TOPIC_VERSION,
                        EVENT_MAP_LAYOUT_VERSION,
                    )
                )
            )
            ready_duplicate = session.execute(
                select(EventMapSnapshot).where(
                    EventMapSnapshot.playlist_id == playlist_id,
                    EventMapSnapshot.build_key == snapshot.build_key,
                    EventMapSnapshot.status == "ready",
                    EventMapSnapshot.id != snapshot.id,
                )
            ).scalar_one_or_none()
            if ready_duplicate is not None:
                progress(10000)
                now = utcnow()
                snapshot.status = "canceled"
                snapshot.error_message = f"reused ready snapshot {ready_duplicate.id}"
                snapshot.finished_at = now
                state = session.get(EventMapState, playlist_id, with_for_update=True)
                if state is None:
                    raise JobTerminalFailure("event map state disappeared while reusing ready snapshot")
                if job is not None:
                    owned_job = session.execute(
                        select(Job)
                        .where(
                            Job.id == job.id,
                            Job.status == "running",
                            Job.worker_id == claimed_worker_id,
                            Job.execution_token == claimed_execution_token,
                        )
                        .with_for_update()
                    ).scalar_one_or_none()
                    if owned_job is None or state.active_job_id != job.id:
                        raise JobTerminalFailure(
                            "event map build lost job ownership before ready snapshot reuse"
                        )
                    if owned_job.cancel_requested_at is not None:
                        raise JobCancelRequested(
                            "cancel requested before ready event map snapshot reuse"
                        )
                state.current_snapshot_id = ready_duplicate.id
                state.built_generation = input_generation
                state.last_built_at = now
                state.last_error = None
                if job is not None and state.active_job_id == job.id:
                    state.active_job_id = None
                if int(state.dirty_generation or 0) <= input_generation:
                    state.first_dirty_at = None
                    state.last_dirty_at = None
                session.flush([snapshot, state])
                return {
                    "ok": True,
                    "cached": True,
                    "outcome": "reused_ready",
                    "playlist_id": str(playlist_id),
                    "snapshot_id": str(ready_duplicate.id),
                    "input_generation": input_generation,
                    "frozen_generation": input_generation,
                    "live_generation": int(state.dirty_generation or 0),
                }
            session.flush([snapshot])
            session.commit()

            reduction = compute_event_map_reduction(
                staged.projection,
                batch_size=batch_size,
                checkpoint=lambda processed: progress(
                    2000 + int(1800 * processed / max(1, len(staged.records) * 2))
                ),
                cancel_check=lambda: progress(3800),
                max_rss_bytes=max_rss,
                min_available_memory_bytes=min_available,
            )
            peak_rss[0] = max(peak_rss[0], reduction.peak_rss_bytes)
            progress(3900)
            neighbors = compute_event_map_neighbors(
                reduction,
                directory=directory,
                neighbor_count=EVENT_MAP_NEIGHBOR_COUNT,
                cancel_check=lambda: progress(4200),
                max_rss_bytes=max_rss,
                min_available_memory_bytes=min_available,
            )
            peak_rss[0] = max(peak_rss[0], neighbors.peak_rss_bytes)
            progress(4700)

            raw_vectors = np.memmap(
                staged.projection.vectors_path,
                dtype=np.float32,
                mode="r",
                shape=(len(staged.records), spec.dim),
            ) if staged.records else np.empty((0, spec.dim), dtype=np.float32)
            neighbor_indices = np.memmap(
                neighbors.indices_path,
                dtype=np.int32,
                mode="r",
                shape=(neighbors.count, neighbors.neighbor_count),
            ) if neighbors.count else np.empty((0, 0), dtype=np.int32)
            groups = canonicalize_event_map_records(
                staged.records,
                raw_vectors,
                neighbor_indices,
                checkpoint=lambda processed, total: progress(
                    4700 + int(500 * processed / max(1, total))
                ),
                checkpoint_interval=batch_size,
            )
            progress(5200)

            previous = _previous_snapshot_data(session, parent_snapshot_id, spec.dim)
            canonical_ids, identity_states, lineage_rows = _assign_canonical_identities(
                playlist_id=playlist_id,
                snapshot_id=snapshot_id,
                records=staged.records,
                groups=groups,
                previous=previous,
            )
            canonical_staging = _stage_canonical_centroids(
                directory,
                groups,
                staged.records,
                raw_vectors,
                canonical_ids,
                batch_size,
                lambda processed: progress(
                    5200 + int(200 * processed / max(1, len(groups)))
                ),
            )
            canonical_reduction = compute_event_map_reduction(
                canonical_staging,
                batch_size=batch_size,
                checkpoint=lambda processed: progress(
                    5400 + int(400 * processed / max(1, len(groups) * 2))
                ),
                cancel_check=lambda: progress(5800),
                max_rss_bytes=max_rss,
                min_available_memory_bytes=min_available,
            )
            peak_rss[0] = max(peak_rss[0], canonical_reduction.peak_rss_bytes)
            canonical_vectors = np.memmap(
                canonical_staging.vectors_path,
                dtype=np.float32,
                mode="r",
                shape=(len(groups), spec.dim),
            ) if groups else np.empty((0, spec.dim), dtype=np.float32)
            canonical_reduced_vectors = np.memmap(
                canonical_reduction.reduced_path,
                dtype=np.float32,
                mode="r",
                shape=(len(groups), canonical_reduction.dimension),
            ) if groups else np.empty((0, 0), dtype=np.float32)
            progress(5800)
            changed_count = _changed_input_count(
                staged.records,
                previous.revision_embeddings,
                staged.revisions_path,
            )
            incremental_threshold = max(1000, math.ceil(max(1, len(previous.revision_embeddings)) * 0.02))
            anchored = (
                _snapshot_is_incremental_compatible(previous, spec.model, spec.dim)
                and changed_count <= incremental_threshold
            )
            if anchored:
                coordinates = _anchored_coordinates(
                    canonical_ids=canonical_ids,
                    previous=previous,
                    canonical_vectors=canonical_vectors,
                )
                layout_continuity = "anchored"
                bounds = _coordinate_bounds(coordinates)
            else:
                layout = compute_event_map_layout(
                    reduced_path=canonical_reduction.reduced_path,
                    count=len(groups),
                    reduced_dim=canonical_reduction.dimension,
                    coordinates_path=directory / "event-map-canonical-coordinates.float32",
                    cancel_check=lambda: progress(5900),
                    max_rss_bytes=max_rss,
                    min_available_memory_bytes=min_available,
                )
                peak_rss[0] = max(peak_rss[0], layout.peak_rss_bytes)
                coordinates = np.memmap(
                    layout.coordinates_path,
                    dtype=np.float32,
                    mode="r",
                    shape=(len(groups), 3),
                ).copy() if groups else np.empty((0, 3), dtype=np.float32)
                layout_continuity = "rebased"
                bounds = layout.bounds
            progress(6100)

            topics, topic_by_group = build_event_map_topics(
                groups,
                staged.records,
                canonical_reduced_vectors,
            )
            stories = build_event_map_stories(groups, staged.records, raw_vectors)
            story_identity_ids, story_identity_states = _assign_story_identities(
                session,
                playlist_id=playlist_id,
                snapshot_id=snapshot_id,
                parent_snapshot_id=parent_snapshot_id,
                stories=stories,
                canonical_ids=canonical_ids,
            )
            progress(6500)
            snapshot = session.get(EventMapSnapshot, snapshot_id)
            if snapshot is None:
                raise JobTerminalFailure("event map snapshot disappeared before persistence")
            rows = _build_snapshot_rows(
                directory=directory,
                snapshot=snapshot,
                playlist_id=playlist_id,
                staged=staged,
                groups=groups,
                canonical_ids=canonical_ids,
                identity_states=identity_states,
                lineage_rows=lineage_rows,
                topics=topics,
                topic_by_group=topic_by_group,
                stories=stories,
                story_identity_ids=story_identity_ids,
                story_identity_states=story_identity_states,
                coordinates=coordinates,
                canonical_vectors=canonical_vectors,
                checkpoint=progress,
            )
            monthly_distribution = _monthly_distribution(
                row
                for batch in rows["canonical"].iter_batches(EVENT_MAP_INSERT_BATCH_SIZE)
                for row in batch
            )
            temp_disk_peak = _temp_disk_bytes(directory)
            input_record_count = len(staged.records)
            canonical_count = len(groups)
            actual_member_count = len(rows["member"])
            topic_count = len(rows["topic"])
            story_count = len(rows["story"])
            type_categories = list(staged.projection.categories)
            staged.records.clear()
            del groups, topics, topic_by_group, stories, story_identity_ids, story_identity_states
            del coordinates, canonical_vectors, canonical_reduced_vectors, raw_vectors
            del previous, neighbor_indices, canonical_ids, identity_states

            revision_count = _insert_record_revisions(
                session,
                staged.revisions_path,
                lambda count: progress(6700 + int(400 * count / max(1, input_record_count))),
            )
            session.commit()
            _insert_rows(session, EventMapCanonicalIdentity, rows["identity"], checkpoint=progress, start_progress=7100, end_progress=7200, ignore_conflicts=True)
            _insert_rows(session, EventMapCanonical, rows["canonical"], checkpoint=progress, start_progress=7200, end_progress=7500)
            _insert_rows(session, EventMapCanonicalMember, rows["member"], checkpoint=progress, start_progress=7500, end_progress=7850)
            _insert_rows(session, EventMapEntityIndex, rows["entity"], checkpoint=progress, start_progress=7850, end_progress=8100)
            _insert_rows(session, EventMapCanonicalLineage, rows["lineage"], checkpoint=progress, start_progress=8100, end_progress=8150)
            _insert_rows(session, EventMapTopic, rows["topic"], checkpoint=progress, start_progress=8150, end_progress=8300)
            _insert_rows(session, EventMapTopicMember, rows["topic_member"], checkpoint=progress, start_progress=8300, end_progress=8450)
            _insert_rows(session, EventMapStoryIdentity, rows["story_identity"], checkpoint=progress, start_progress=8440, end_progress=8450, ignore_conflicts=True)
            _insert_rows(session, EventMapStory, rows["story"], checkpoint=progress, start_progress=8450, end_progress=8500)
            _insert_rows(session, EventMapStoryMember, rows["story_member"], checkpoint=progress, start_progress=8500, end_progress=8580)
            _insert_rows(session, EventMapStoryEdge, rows["story_edge"], checkpoint=progress, start_progress=8580, end_progress=8660)
            _insert_rows(session, EventMapProjectionAnchor, rows["anchor"], checkpoint=progress, start_progress=8660, end_progress=9500)

            distinct_entities = (
                select(EventMapEntityIndex.entity_type, EventMapEntityIndex.normalized_key)
                .where(EventMapEntityIndex.snapshot_id == snapshot_id)
                .distinct()
                .subquery()
            )
            entity_count = int(
                session.execute(select(func.count()).select_from(distinct_entities)).scalar_one() or 0
            )

            temp_disk_peak = max(temp_disk_peak, _temp_disk_bytes(directory))
            progress(10000)
            snapshot = session.get(EventMapSnapshot, snapshot_id)
            state = session.get(EventMapState, playlist_id, with_for_update=True)
            if snapshot is None or state is None:
                raise JobTerminalFailure("event map state disappeared before atomic switch")
            if job is not None:
                owned_job = session.execute(
                    select(Job)
                    .where(Job.id == job.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).scalar_one_or_none()
                if (
                    owned_job is None
                    or owned_job.status != "running"
                    or str(owned_job.worker_id or "").strip() != claimed_worker_id
                    or owned_job.execution_token != claimed_execution_token
                    or snapshot.execution_token != claimed_execution_token
                    or state.active_job_id != job.id
                ):
                    raise JobTerminalFailure("event map build lost job ownership before atomic switch")
                if owned_job.cancel_requested_at is not None:
                    raise JobCancelRequested("cancel requested before event map snapshot switch")
            if revision_count != input_record_count or actual_member_count != input_record_count:
                raise JobTerminalFailure(
                    f"event map member coverage mismatch: input={input_record_count} revisions={revision_count} members={actual_member_count}"
                )
            if len(rows["canonical"]) != canonical_count:
                raise JobTerminalFailure("event map canonical count mismatch")
            finished_at = utcnow()
            _persist_v2_history_and_changes(
                session,
                playlist_id=playlist_id,
                snapshot_id=snapshot.id,
                parent_snapshot_id=parent_snapshot_id,
                observed_at=finished_at,
                layout_continuity=layout_continuity,
            )
            snapshot.status = "ready"
            snapshot.layout_continuity = layout_continuity
            snapshot.alignment_transform = {"method": "anchor_interpolation_v1"} if anchored else None
            snapshot.alignment_residual = 0.0 if anchored else None
            snapshot.bounds = bounds
            snapshot.type_categories = type_categories
            snapshot.monthly_distribution = monthly_distribution
            snapshot.member_count = actual_member_count
            snapshot.canonical_count = canonical_count
            snapshot.story_count = story_count
            snapshot.topic_count = topic_count
            snapshot.entity_count = entity_count
            snapshot.peak_rss_bytes = peak_rss[0]
            snapshot.temp_disk_peak_bytes = temp_disk_peak
            snapshot.finished_at = finished_at
            snapshot.error_message = None
            state.current_snapshot_id = snapshot.id
            state.built_generation = input_generation
            state.last_built_at = snapshot.finished_at
            state.last_error = None
            if job is not None and state.active_job_id == job.id:
                state.active_job_id = None
            if int(state.dirty_generation or 0) <= input_generation:
                state.first_dirty_at = None
                state.last_dirty_at = None
            session.flush([snapshot, state])
            return {
                "ok": True,
                "outcome": "built",
                "playlist_id": str(playlist_id),
                "snapshot_id": str(snapshot.id),
                "input_generation": input_generation,
                "frozen_generation": input_generation,
                "live_generation": int(state.dirty_generation or 0),
                "dirty_generation": int(state.dirty_generation or 0),
                "input_record_count": input_record_count,
                "canonical_count": canonical_count,
                "topic_count": topic_count,
                "story_count": story_count,
                "entity_count": entity_count,
                "layout_continuity": layout_continuity,
                "changed_input_count": changed_count,
                "incremental_threshold": incremental_threshold,
                "peak_rss_bytes": peak_rss[0],
                "temp_disk_peak_bytes": temp_disk_peak,
                "skipped_reason_counts": staged.skipped_reason_counts,
            }
    except JobCancelRequested:
        _finalize_failed_snapshot(
            session,
            snapshot_id=snapshot_id,
            playlist_id=playlist_id,
            job=job,
            claimed_worker_id=claimed_worker_id,
            claimed_execution_token=claimed_execution_token,
            status="canceled",
            message="event map build canceled",
        )
        raise
    except JobTerminalFailure as exc:
        _finalize_failed_snapshot(
            session,
            snapshot_id=snapshot_id,
            playlist_id=playlist_id,
            job=job,
            claimed_worker_id=claimed_worker_id,
            claimed_execution_token=claimed_execution_token,
            status="failed",
            message=exc.reason,
        )
        raise
    except Exception as exc:
        message = f"event map build failed: {type(exc).__name__}: {exc}"
        _finalize_failed_snapshot(
            session,
            snapshot_id=snapshot_id,
            playlist_id=playlist_id,
            job=job,
            claimed_worker_id=claimed_worker_id,
            claimed_execution_token=claimed_execution_token,
            status="failed",
            message=message,
        )
        raise JobTerminalFailure(message) from exc


def build_event_map_snapshot(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    job: Job | None = None,
) -> dict[str, Any]:
    with _event_map_build_lock(session) as acquired:
        if not acquired:
            raise JobReschedule(delay_seconds=60, reason="another event map build holds the global resource slot")
        return _build_event_map_snapshot_locked(session, playlist_id=playlist_id, job=job)
