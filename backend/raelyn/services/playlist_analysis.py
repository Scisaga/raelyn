from __future__ import annotations

import hashlib
import heapq
import math
import re
import uuid
from array import array
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
from raelyn.jobs.progress import set_job_progress
from raelyn.jobs.reschedule import JobTerminalFailure
from raelyn.models import (
    Asset,
    Job,
    Media,
    PlaylistAnalysisCandidate,
    PlaylistAnalysisPeriod,
    PlaylistAnalysisRun,
    PlaylistAnalysisSignal,
    PlaylistAnalysisState,
    PlaylistMedia,
    Video,
    VideoEmbedding,
)
from raelyn.services.embeddings import (
    EmbeddingOverBudgetError,
    EmbeddingSpec,
    EmbeddingTransientError,
    embed_texts,
    embedding_spec,
)
from raelyn.services.job_cancellation import JobCancelRequested, raise_if_job_cancel_requested
from raelyn.services.s3 import s3_get_bytes
from raelyn.services.periods import period_end_inclusive, period_start
from raelyn.services.transcripts import pick_transcript_asset, read_text_asset
from raelyn.timeutil import utcnow


ROLLING_WINDOW = 20
SIGNAL_GRANULARITY_WINDOWS = {"day": 20, "week": 12, "month": 12}
DETECTION_METHOD = "two_window_centroid_drift_v1"
OPEN_TOPIC_BURST_METHOD = "open_topic_burst_v1"
MONTH_BOUNDARY_WINDOW = 2
WEEK_BOUNDARY_WINDOW = 4
MONTH_BOUNDARY_Z_THRESHOLD = 2.0
WEEK_BOUNDARY_Z_THRESHOLD = 1.5
MONTH_CANDIDATE_MIN_GAP = 3
OPEN_TOPIC_BURST_WINDOW_DAYS = 3
OPEN_TOPIC_BURST_MIN_VIDEO_COUNT = 8
OPEN_TOPIC_BURST_MIN_MEDIA_COUNT = 3
OPEN_TOPIC_BURST_MEDIA_CONTEXT_DAYS = 90
OPEN_TOPIC_BURST_MIN_MEDIA_COVERAGE_RATIO = 0.6
OPEN_TOPIC_BURST_SPARSE_MEDIA_MIN_RATE_MULTIPLIER = 2.5
OPEN_TOPIC_BURST_MIN_ACTIVE_DAYS = 2
OPEN_TOPIC_BURST_MIN_COHESION = 0.78
OPEN_TOPIC_BURST_COMPONENT_MAX_WINDOW_ITEMS = 128
OPEN_TOPIC_BURST_COMPONENT_FULL_SCAN_MAX_ITEMS = 5000
OPEN_TOPIC_BURST_MERGE_MIN_COHESION = 0.68
OPEN_TOPIC_BURST_SEMANTIC_KEY_TOP_DIMS = 6
OPEN_TOPIC_BURST_SEMANTIC_KEY_SCAN_DIMS = 256
OPEN_TOPIC_BURST_SEMANTIC_MAX_KEYS_PER_ITEM = 6
OPEN_TOPIC_BURST_SEMANTIC_MIN_BUCKET_VIDEO_COUNT = 8
OPEN_TOPIC_BURST_SEMANTIC_MAX_CANDIDATE_WINDOWS_PER_BUCKET = 24
OPEN_TOPIC_BURST_SHORT_MAX_WINDOWS_PER_BUCKET = 4
OPEN_TOPIC_BURST_SUSTAINED_MAX_WINDOWS_PER_BUCKET = 3
OPEN_TOPIC_BURST_SUSTAINED_WINDOW_DAYS = 14
OPEN_TOPIC_BURST_SUSTAINED_STEP_DAYS = 7
OPEN_TOPIC_BURST_SUSTAINED_MIN_VIDEO_COUNT = 12
OPEN_TOPIC_BURST_SUSTAINED_MIN_ACTIVE_DAYS = 5
OPEN_TOPIC_BURST_SUSTAINED_MIN_COHESION = 0.68
OPEN_TOPIC_BURST_SUSTAINED_MAX_PROVISIONAL = 2048
OPEN_TOPIC_BURST_MAX_EVENTS = 64
OPEN_TOPIC_BURST_YEAR_CAP_MIN_YEAR_COUNT = 4
OPEN_TOPIC_BURST_YEAR_CAP_MIN_EVENTS = 8
OPEN_TOPIC_BURST_YEAR_CAP_MULTIPLIER = 1.5
ANALYSIS_PROGRESS_TOTAL = 10000
ANALYSIS_LEASE_SECONDS = 4 * 60 * 60
ANALYSIS_LEASE_REFRESH_SECONDS = 60


@dataclass(frozen=True)
class CoverageStats:
    video_total: int
    video_embedded: int
    video_skipped: int
    video_failed: int


@dataclass(frozen=True)
class EmbeddingBackfillItem:
    video_id: uuid.UUID
    text: str
    checksum: str


@dataclass(frozen=True)
class EmbeddingBackfillStats:
    selected: int
    embedded: int
    skipped_over_budget: int
    scanned: int = 0
    skipped_empty: int = 0


@dataclass(frozen=True)
class EmbeddingBackfillBatchWrite:
    item: EmbeddingBackfillItem
    status: str
    vector: list[float] | None = None
    skip_reason: str | None = None


@dataclass(frozen=True)
class EmbeddingBackfillBatchResult:
    selected: int
    embedded: int
    skipped_over_budget: int
    writes: tuple[EmbeddingBackfillBatchWrite, ...]


@dataclass(frozen=True)
class EmbeddingBackfillCandidate:
    video_id: uuid.UUID
    embedding_status: str | None
    text_checksum: str | None
    has_vector: bool


@dataclass(frozen=True)
class EmbeddingBackfillTranscriptCandidate:
    video_id: uuid.UUID
    embedding_status: str | None
    text_checksum: str | None
    has_vector: bool
    s3_bucket: str
    s3_key: str


@dataclass(frozen=True)
class EmbeddingBackfillFetchResult:
    item: EmbeddingBackfillItem | None
    skipped_empty: bool = False


@dataclass(frozen=True)
class AnalysisEmbeddingItem:
    video_id: uuid.UUID
    media_id: uuid.UUID
    title: str | None
    media_name: str | None
    published_at: datetime
    vector: list[float]


@dataclass(frozen=True)
class OpenTopicBurstItem:
    video_id: uuid.UUID
    media_id: uuid.UUID
    title: str | None
    media_name: str | None
    published_at: datetime
    day: date
    vector: list[float]
    terms: tuple[str, ...] = ()
    semantic_keys: tuple[tuple[int, ...], ...] = ()


@dataclass
class AnalysisPeriodPayload:
    granularity: str
    period_date: date
    rolling_window: int
    video_count: int
    ready_embedding_count: int
    centroid: list[float]
    dispersion_mean: float = 0.0
    dispersion_std: float = 0.0
    dispersion_p25: float = 0.0
    dispersion_p75: float = 0.0
    drift_score: float | None = None
    drift_rolling_mean: float | None = None
    drift_rolling_std: float | None = None
    drift_rolling_z: float | None = None
    projection_id: str = ""
    projection_method: str = "pca"
    projection_x: float = 0.0
    projection_y: float = 0.0
    projection_z: float = 0.0
    projection_explained_variance_ratio: list[float] | None = None

    @property
    def dispersion_score(self) -> float:
        return self.dispersion_mean


@dataclass(frozen=True)
class BoundaryDetection:
    granularity: str
    breakpoint_date: date
    before_start: date
    before_end: date
    after_start: date
    after_end: date
    boundary_score: float
    boundary_z: float
    before_centroid: list[float]
    after_centroid: list[float]


@dataclass(frozen=True)
class CandidateDetection:
    boundary: BoundaryDetection
    source_month_boundary: BoundaryDetection
    event_type: str
    supporting_granularities: list[str]

    @property
    def candidate_date(self) -> date:
        return self.boundary.breakpoint_date

    @property
    def event_start(self) -> date:
        return self.boundary.before_start

    @property
    def event_end(self) -> date:
        return self.boundary.after_end


@dataclass(frozen=True)
class OpenTopicBurstDetection:
    candidate_date: date
    event_start: date
    event_end: date
    video_count: int
    media_count: int
    active_days: int
    score: float
    confidence: float
    cohesion: float
    centroid: list[float]
    representative_title: str
    top_terms: list[str]
    count_by_day: Counter[date]
    window_days: int = OPEN_TOPIC_BURST_WINDOW_DAYS
    evidence_min_similarity: float = OPEN_TOPIC_BURST_MIN_COHESION
    available_media_count: int = OPEN_TOPIC_BURST_MIN_MEDIA_COUNT
    required_media_count: int = OPEN_TOPIC_BURST_MIN_MEDIA_COUNT


def analysis_spec() -> EmbeddingSpec:
    return embedding_spec()


def transcript_checksum(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _analysis_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(str(settings.timezone or "Asia/Shanghai").strip() or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _available_memory_bytes() -> int | None:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except OSError:
        return None
    except Exception:
        return None
    return None


def _resident_memory_bytes() -> int | None:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except OSError:
        return None
    except Exception:
        return None
    return None


def ensure_analysis_resource_budget() -> None:
    try:
        maximum_rss = int(settings.analysis_max_rss_bytes or 0)
    except Exception:
        maximum_rss = 0
    if maximum_rss > 0:
        rss = _resident_memory_bytes()
        if rss is not None and rss > maximum_rss:
            raise JobTerminalFailure(
                f"analysis aborted: resident memory {rss} bytes exceeds limit {maximum_rss} bytes"
            )

    try:
        minimum = int(settings.analysis_min_available_memory_bytes or 0)
    except Exception:
        minimum = 0
    if minimum <= 0:
        return

    available = _available_memory_bytes()
    if available is None or available >= minimum:
        return
    raise JobTerminalFailure(
        f"analysis aborted: available memory {available} bytes is below required {minimum} bytes"
    )


def ensure_analysis_memory_budget() -> None:
    ensure_analysis_resource_budget()


def _refresh_analysis_job_lease(job: Job | None) -> None:
    if job is None or not getattr(job, "id", None):
        return
    now = utcnow()
    previous = getattr(job, "_analysis_lease_refreshed_at", None)
    if previous is not None and (now - previous).total_seconds() < ANALYSIS_LEASE_REFRESH_SECONDS:
        return
    lease_expires_at = now + timedelta(seconds=ANALYSIS_LEASE_SECONDS)
    set_job_progress(
        job_id=job.id,
        current=getattr(job, "progress_current", None),
        total=getattr(job, "progress_total", None),
        lease_expires_at=lease_expires_at,
    )
    job.lease_expires_at = lease_expires_at
    setattr(job, "_analysis_lease_refreshed_at", now)


def _set_analysis_progress(job: Job | None, current: int, *, total: int = ANALYSIS_PROGRESS_TOTAL) -> None:
    if job is None or not getattr(job, "id", None):
        return
    value = max(0, min(int(total), int(current)))
    now = utcnow()
    lease_expires_at = now + timedelta(seconds=ANALYSIS_LEASE_SECONDS)
    set_job_progress(job_id=job.id, current=value, total=total, lease_expires_at=lease_expires_at)
    job.progress_current = value
    job.progress_total = total
    job.lease_expires_at = lease_expires_at
    setattr(job, "_analysis_lease_refreshed_at", now)


def _checkpoint_analysis_task(session: Session, job: Job | None) -> None:
    ensure_analysis_resource_budget()
    if job is not None:
        raise_if_job_cancel_requested(session, job)
        _refresh_analysis_job_lease(job)


def _video_playlist_ids(session: Session, video_id: uuid.UUID) -> list[uuid.UUID]:
    video = session.get(Video, video_id)
    if not video:
        return []
    return list(
        session.execute(select(PlaylistMedia.playlist_id).where(PlaylistMedia.media_id == video.media_id)).scalars().all()
    )


def ensure_playlist_analysis_state(session: Session, playlist_id: uuid.UUID) -> PlaylistAnalysisState:
    state = session.get(PlaylistAnalysisState, playlist_id)
    if state:
        return state
    state = PlaylistAnalysisState(playlist_id=playlist_id, analysis_dirty=False)
    session.add(state)
    session.flush([state])
    return state


def mark_playlist_analysis_dirty(session: Session, playlist_id: uuid.UUID, *, changed_at: datetime | None = None) -> None:
    state = ensure_playlist_analysis_state(session, playlist_id)
    ts = changed_at or utcnow()
    state.analysis_dirty = True
    state.last_requested_at = ts
    session.flush([state])


def mark_playlists_analysis_dirty_for_video(session: Session, video_id: uuid.UUID, *, changed_at: datetime | None = None) -> None:
    for playlist_id in _video_playlist_ids(session, video_id):
        mark_playlist_analysis_dirty(session, playlist_id, changed_at=changed_at)


def mark_playlist_analysis_dirty_for_media(session: Session, media_id: uuid.UUID, *, changed_at: datetime | None = None) -> None:
    playlist_ids = session.execute(select(PlaylistMedia.playlist_id).where(PlaylistMedia.media_id == media_id)).scalars().all()
    for playlist_id in playlist_ids:
        mark_playlist_analysis_dirty(session, playlist_id, changed_at=changed_at)


def schedule_video_embedding_refresh(
    session: Session,
    *,
    video_id: uuid.UUID,
    text_checksum_value: str,
    priority: int = 0,
    parent_job_id: str | None = None,
) -> uuid.UUID:
    spec = analysis_spec()
    existing = session.execute(
        select(VideoEmbedding).where(
            VideoEmbedding.video_id == video_id,
            VideoEmbedding.transcript_variant == spec.transcript_variant,
            VideoEmbedding.embedding_model == spec.model,
            VideoEmbedding.embedding_dim == spec.dim,
        )
    ).scalar_one_or_none()
    if existing and existing.status == "ready" and existing.text_checksum == text_checksum_value and existing.vector:
        return existing.id
    return enqueue_job(
        session,
        type_="video.embed_transcript",
        params={"video_id": str(video_id), "text_checksum": text_checksum_value},
        priority=priority,
        parent_job_id=parent_job_id,
    )


def _embedding_batch_size(value: Any | None = None) -> int:
    configured = value if value is not None else settings.embedding_batch_size
    try:
        batch_size = int(configured or 16)
    except Exception:
        batch_size = 16
    try:
        max_size = int(settings.embedding_batch_max_size or 64)
    except Exception:
        max_size = 64
    max_size = max(1, max_size)
    return max(1, min(batch_size, max_size))


def _embedding_transcript_prefetch_workers(value: Any | None = None) -> int:
    configured = value if value is not None else settings.embedding_transcript_prefetch_workers
    try:
        workers = int(configured or 4)
    except Exception:
        workers = 4
    return max(1, workers)


def _embedding_batch_max_chars(value: Any | None = None) -> int:
    configured = value if value is not None else settings.embedding_batch_max_chars
    try:
        max_chars = int(configured or 60000)
    except Exception:
        max_chars = 60000
    return max(1, max_chars)


def _embedding_backfill_http_inflight(value: Any | None = None) -> int:
    configured = value if value is not None else settings.embedding_backfill_http_inflight
    try:
        inflight = int(configured or 1)
    except Exception:
        inflight = 1
    return max(1, inflight)


def _analysis_stream_batch_size(value: Any | None = None) -> int:
    configured = value if value is not None else settings.analysis_stream_batch_size
    try:
        batch_size = int(configured or 500)
    except Exception:
        batch_size = 500
    return max(1, batch_size)


def video_embedding_needs_refresh(
    embedding: VideoEmbedding | None,
    *,
    text_checksum_value: str,
) -> bool:
    if embedding is None:
        return True
    status = str(getattr(embedding, "status", "") or "").strip().lower()
    if status in {"failed", "skipped_over_budget"}:
        return True
    if str(getattr(embedding, "text_checksum", "") or "").strip() != text_checksum_value:
        return True
    if status != "ready":
        return True
    return not bool(getattr(embedding, "vector", None))


def _upsert_video_embedding(
    session: Session,
    *,
    video_id: uuid.UUID,
    text_checksum_value: str,
    status: str,
    vector: list[float] | None = None,
    skip_reason: str | None = None,
) -> VideoEmbedding:
    spec = analysis_spec()
    embedding = session.execute(
        select(VideoEmbedding).where(
            VideoEmbedding.video_id == video_id,
            VideoEmbedding.transcript_variant == spec.transcript_variant,
            VideoEmbedding.embedding_model == spec.model,
            VideoEmbedding.embedding_dim == spec.dim,
        )
    ).scalar_one_or_none()
    if embedding is None:
        embedding = VideoEmbedding(
            video_id=video_id,
            transcript_variant=spec.transcript_variant,
            embedding_model=spec.model,
            embedding_dim=spec.dim,
            status=status,
            text_checksum=text_checksum_value,
        )
        session.add(embedding)
    embedding.status = status
    embedding.vector = vector
    embedding.text_checksum = text_checksum_value
    embedding.skip_reason = skip_reason
    embedding.generated_at = utcnow()
    session.flush([embedding])
    return embedding


def _playlist_embedding_backfill_playlist_prefix(playlist_id: uuid.UUID) -> str:
    return f"playlist_embedding_backfill:{playlist_id}:"


def _playlist_embedding_backfill_dedupe_prefix(playlist_id: uuid.UUID) -> str:
    spec = analysis_spec()
    return f"{_playlist_embedding_backfill_playlist_prefix(playlist_id)}{spec.transcript_variant}:{spec.model}:{spec.dim}:"


def _playlist_embedding_backfill_dedupe_key(playlist_id: uuid.UUID, *, force: bool) -> str:
    mode = "force" if force else "missing"
    return f"{_playlist_embedding_backfill_dedupe_prefix(playlist_id)}{mode}"


def active_playlist_embedding_backfill_job(session: Session, playlist_id: uuid.UUID) -> Job | None:
    return session.execute(
        select(Job)
        .where(
            Job.type == "playlist.backfill_embeddings",
            Job.dedupe_key.like(f"{_playlist_embedding_backfill_playlist_prefix(playlist_id)}%"),
            Job.status.in_(["pending", "running"]),
        )
        .order_by(
            case((Job.status == "running", 0), else_=1),
            Job.started_at.asc().nullslast(),
            Job.scheduled_for.asc(),
            Job.created_at.asc(),
            Job.id.asc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def pending_playlist_embedding_backfill_job(session: Session, playlist_id: uuid.UUID, *, force: bool) -> Job | None:
    return session.execute(
        select(Job)
        .where(
            Job.dedupe_key == _playlist_embedding_backfill_dedupe_key(playlist_id, force=force),
            Job.status.in_(["pending", "running"]),
        )
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def request_playlist_embedding_backfill(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    force: bool = False,
    batch_size: int | None = None,
    priority: int = 1,
) -> tuple[uuid.UUID, bool]:
    active = active_playlist_embedding_backfill_job(session, playlist_id)
    if active:
        return active.id, False
    pending = pending_playlist_embedding_backfill_job(session, playlist_id, force=force)
    if pending:
        return pending.id, False
    job_id = enqueue_job(
        session,
        type_="playlist.backfill_embeddings",
        params={
            "playlist_id": str(playlist_id),
            "force": bool(force),
            "batch_size": _embedding_batch_size(batch_size),
        },
        priority=priority,
    )
    return job_id, True


def active_playlist_analysis_run(session: Session, playlist_id: uuid.UUID) -> PlaylistAnalysisRun | None:
    return session.execute(
        select(PlaylistAnalysisRun)
        .where(
            PlaylistAnalysisRun.playlist_id == playlist_id,
            PlaylistAnalysisRun.status.in_(["pending", "running"]),
        )
        .order_by(PlaylistAnalysisRun.created_at.desc(), PlaylistAnalysisRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _analysis_job_dedupe_key(playlist_id: uuid.UUID) -> str:
    spec = analysis_spec()
    return f"playlist_analysis:{playlist_id}:day:{spec.transcript_variant}:{spec.model}:{spec.dim}"


def pending_playlist_analysis_job(session: Session, playlist_id: uuid.UUID) -> Job | None:
    return session.execute(
        select(Job)
        .where(
            Job.dedupe_key == _analysis_job_dedupe_key(playlist_id),
            Job.status.in_(["pending", "running"]),
        )
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def request_playlist_analysis_rebuild(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    priority: int = 1,
) -> tuple[uuid.UUID | None, bool]:
    state = ensure_playlist_analysis_state(session, playlist_id)
    state.last_requested_at = utcnow()
    session.flush([state])

    active = active_playlist_analysis_run(session, playlist_id)
    if active:
        return None, False
    pending = pending_playlist_analysis_job(session, playlist_id)
    if pending:
        return pending.id, False

    job_id = enqueue_job(
        session,
        type_="playlist.build_analysis_snapshot",
        params={"playlist_id": str(playlist_id)},
        priority=priority,
    )
    return job_id, True


def prune_playlist_analysis_runs(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    keep_run_ids: list[uuid.UUID] | tuple[uuid.UUID, ...] | set[uuid.UUID] | None = None,
) -> int:
    keep_ids = [run_id for run_id in (keep_run_ids or []) if run_id]
    stmt = delete(PlaylistAnalysisRun).where(
        PlaylistAnalysisRun.playlist_id == playlist_id,
        PlaylistAnalysisRun.status.notin_(["pending", "running"]),
    )
    if keep_ids:
        stmt = stmt.where(PlaylistAnalysisRun.id.notin_(keep_ids))
    result = session.execute(stmt)
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount) if isinstance(rowcount, int) else 0


def _dot(left: list[float], right: list[float]) -> float:
    return float(sum(float(a) * float(b) for a, b in zip(left, right)))


def _l2_norm(vector: list[float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = _l2_norm(vector)
    if norm <= 1e-12:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def _normalize_matrix(vectors: list[list[float]]) -> list[list[float]]:
    return [_normalize_vector([float(value) for value in vector]) for vector in vectors]


def _mean_vector(normalized_vectors: list[list[float]]) -> list[float]:
    if not normalized_vectors:
        return []
    dim = len(normalized_vectors[0])
    mean = [0.0] * dim
    for vector in normalized_vectors:
        for index, value in enumerate(vector):
            mean[index] += float(value)
    count = float(len(normalized_vectors))
    mean = [value / count for value in mean]
    return _normalize_vector(mean)


def _rolling_mean_std_and_z(values: list[float], *, window: int) -> tuple[list[float | None], list[float | None], list[float | None]]:
    means: list[float | None] = []
    std_values: list[float | None] = []
    z_values: list[float | None] = []
    history: list[float] = []
    for value in values:
        history.append(float(value))
        window_values = history[-window:]
        mean = float(sum(window_values) / len(window_values))
        means.append(mean)
        if len(window_values) < 2:
            std_values.append(None)
            z_values.append(None)
            continue
        variance = sum((item - mean) ** 2 for item in window_values) / len(window_values)
        std = math.sqrt(variance)
        std_values.append(std)
        if std <= 1e-12:
            z_values.append(0.0)
            continue
        z_values.append((float(value) - mean) / std)
    return means, std_values, z_values


def _rolling_mean_and_z(values: list[float], *, window: int) -> tuple[list[float | None], list[float | None]]:
    means, _, z_values = _rolling_mean_std_and_z(values, window=window)
    return means, z_values


def _pca_projection(vectors: list[list[float]], *, components_count: int = 3) -> tuple[list[tuple[float, float, float]], list[float]]:
    if not vectors:
        return [], [0.0, 0.0, 0.0]
    if len(vectors) == 1:
        return [(0.0, 0.0, 0.0)], [0.0, 0.0, 0.0]
    dim = len(vectors[0])
    mean = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dim)]
    centered = [[float(vector[index]) - mean[index] for index in range(dim)] for vector in vectors]
    total_variance = sum(_dot(row, row) for row in centered)

    def cov_mul(candidate: list[float]) -> list[float]:
        scores = [_dot(row, candidate) for row in centered]
        result = [0.0] * dim
        for row, score in zip(centered, scores):
            for index, value in enumerate(row):
                result[index] += value * score
        return result

    components: list[list[float]] = []
    eigenvalues: list[float] = []
    resolved_components_count = max(1, min(3, int(components_count or 3)))
    for axis in range(resolved_components_count):
        guess = [0.0] * dim
        guess[axis % max(1, dim)] = 1.0
        guess = _normalize_vector(guess)
        for _ in range(24):
            candidate = cov_mul(guess)
            for basis in components:
                overlap = _dot(candidate, basis)
                candidate = [value - overlap * basis[index] for index, value in enumerate(candidate)]
            norm = _l2_norm(candidate)
            if norm <= 1e-12:
                break
            guess = [value / norm for value in candidate]
        component = guess if _l2_norm(guess) > 1e-12 else [0.0] * dim
        components.append(component)
        eigenvalues.append(float(max(0.0, _dot(component, cov_mul(component)))))

    while len(components) < 3:
        components.append([0.0] * dim)
        eigenvalues.append(0.0)

    ratios = [
        float(value / total_variance) if total_variance > 1e-12 else 0.0
        for value in eigenvalues[:3]
    ]
    return [
        (_dot(row, components[0]), _dot(row, components[1]), _dot(row, components[2]))
        for row in centered
    ], ratios


def _pca_2d(vectors: list[list[float]]) -> list[tuple[float, float]]:
    points, _ = _pca_projection(vectors, components_count=2)
    return [(x, y) for x, y, _ in points]


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * max(0.0, min(1.0, percentile))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return _percentile(sorted(values), 0.5)


def _robust_z_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    median = _median(values)
    deviations = [abs(float(value) - median) for value in values]
    mad = _median(deviations)
    if mad > 1e-12:
        return [(float(value) - median) / (1.4826 * mad) for value in values]
    mean = float(sum(values) / len(values))
    variance = sum((float(value) - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    if std <= 1e-12:
        return [0.0 for _ in values]
    return [(float(value) - mean) / std for value in values]


def _month_distance(left: date, right: date) -> int:
    return (right.year - left.year) * 12 + (right.month - left.month)


def _date_range(start: date, end: date) -> list[date]:
    if start > end:
        return []
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


_TITLE_TERM_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "after",
    "amid",
    "over",
    "into",
    "this",
    "that",
    "says",
    "said",
    "will",
    "are",
    "you",
    "need",
    "know",
    "stories",
    "reuters",
    "market",
    "talk",
    "wall",
    "street",
    "journal",
    "完整版",
    "錢線百分百",
    "钱线百分百",
    "非凡財經新聞",
    "非凡财经新闻",
}


def _normalized_title_text(title: str | None) -> str:
    return str(title or "").strip().lower()


def _title_terms(title: str | None) -> list[str]:
    text = _normalized_title_text(title)
    if not text:
        return []
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text):
        if token not in _TITLE_TERM_STOPWORDS:
            terms.append(token)
    for token in _chinese_title_ngrams(str(title or "")):
        if token not in _TITLE_TERM_STOPWORDS:
            terms.append(token)
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        normalized = str(term or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _chinese_title_ngrams(title: str | None) -> list[str]:
    tokens: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", str(title or "")):
        for size in (2, 3, 4):
            if len(run) < size:
                continue
            for start in range(0, len(run) - size + 1):
                tokens.append(run[start : start + size])
    return tokens[:32]


def _open_topic_burst_item_terms(item: OpenTopicBurstItem) -> tuple[str, ...]:
    if item.terms:
        return item.terms
    return tuple(_title_terms(item.title))


def _open_topic_burst_media_name(item: OpenTopicBurstItem) -> str:
    return str(item.media_name or item.media_id or "").strip() or "unknown"


def _open_topic_burst_required_media_count(available_media_count: int) -> int:
    available = max(1, int(available_media_count))
    coverage_required = int(math.ceil(float(available) * OPEN_TOPIC_BURST_MIN_MEDIA_COVERAGE_RATIO))
    return max(1, min(OPEN_TOPIC_BURST_MIN_MEDIA_COUNT, coverage_required))


def _open_topic_burst_context_range(start: date, end: date) -> tuple[date, date]:
    window_days = max(1, (end - start).days + 1)
    context_padding_days = max(0, OPEN_TOPIC_BURST_MEDIA_CONTEXT_DAYS - window_days)
    before_days = context_padding_days // 2
    after_days = context_padding_days - before_days
    return start - timedelta(days=before_days), end + timedelta(days=after_days)


def _open_topic_burst_media_by_day(items_by_day: dict[date, list[OpenTopicBurstItem]]) -> dict[date, set[str]]:
    return {
        day_value: {_open_topic_burst_media_name(item) for item in items}
        for day_value, items in items_by_day.items()
    }


def _candidate_event_date(
    *,
    event_start: date,
    event_end: date,
    count_by_day: Counter[date],
    occupied_dates: set[date],
) -> date | None:
    days = _date_range(event_start, event_end)
    ranked = sorted(days, key=lambda item: (-int(count_by_day.get(item, 0)), item))
    for day_value in ranked:
        if day_value not in occupied_dates:
            return day_value
    return None


def _open_topic_burst_window_items(
    items_by_day: dict[date, list[OpenTopicBurstItem]],
    *,
    start: date,
    end: date,
) -> list[OpenTopicBurstItem]:
    items: list[OpenTopicBurstItem] = []
    for day_value in _date_range(start, end):
        items.extend(items_by_day.get(day_value, []))
    return items


def _open_topic_burst_components(items: list[OpenTopicBurstItem]) -> list[list[OpenTopicBurstItem]]:
    if not items:
        return []
    vectors = [item.vector for item in items]
    visited: set[int] = set()
    components: list[list[OpenTopicBurstItem]] = []
    for start_index in range(len(items)):
        if start_index in visited:
            continue
        stack = [start_index]
        visited.add(start_index)
        indexes: list[int] = []
        while stack:
            index = stack.pop()
            indexes.append(index)
            vector = vectors[index]
            for candidate_index, candidate_vector in enumerate(vectors):
                if candidate_index in visited:
                    continue
                if _dot(vector, candidate_vector) < OPEN_TOPIC_BURST_MIN_COHESION:
                    continue
                visited.add(candidate_index)
                stack.append(candidate_index)
        components.append([items[index] for index in indexes])
    return components


def _open_topic_burst_terms(items: list[OpenTopicBurstItem], *, limit: int = 8) -> list[str]:
    counts: Counter[str] = Counter()
    for item in items:
        for term in _open_topic_burst_item_terms(item):
            counts[term] += 1
    return [
        str(term)
        for term, _ in sorted(counts.items(), key=lambda item: (-item[1], str(item[0]).lower()))
    ][:limit]


def _open_topic_burst_detection_from_items(
    items: list[OpenTopicBurstItem],
    *,
    event_start: date,
    event_end: date,
    min_video_count: int = OPEN_TOPIC_BURST_MIN_VIDEO_COUNT,
    min_media_count: int = OPEN_TOPIC_BURST_MIN_MEDIA_COUNT,
    min_active_days: int = OPEN_TOPIC_BURST_MIN_ACTIVE_DAYS,
    min_cohesion: float = OPEN_TOPIC_BURST_MIN_COHESION,
    window_days: int | None = None,
    available_media_count: int | None = None,
    required_media_count: int | None = None,
) -> OpenTopicBurstDetection | None:
    if len(items) < min_video_count:
        return None
    media_names = {_open_topic_burst_media_name(item) for item in items}
    resolved_available_media_count = max(len(media_names), int(available_media_count or len(media_names)))
    resolved_required_media_count = int(required_media_count or min_media_count)
    if len(media_names) < resolved_required_media_count:
        return None
    count_by_day: Counter[date] = Counter(item.day for item in items)
    active_days = len([day_value for day_value, count in count_by_day.items() if int(count) > 0])
    if active_days < min_active_days:
        return None

    centroid = _mean_vector([item.vector for item in items])
    if not centroid:
        return None
    similarities = [float(_dot(item.vector, centroid)) for item in items]
    cohesion = float(sum(similarities) / len(similarities))
    if cohesion < min_cohesion:
        return None

    representative_index = sorted(
        range(len(items)),
        key=lambda index: (-similarities[index], str(items[index].title or ""), str(items[index].media_name or "")),
    )[0]
    representative_title = str(items[representative_index].title or "").strip()
    resolved_window_days = int(window_days or max(1, (event_end - event_start).days + 1))
    score = min(
        5.0,
        1.0
        + math.log1p(len(items))
        + min(1.5, len(media_names) / 4.0)
        + max(0.0, (cohesion - min_cohesion) * 3.0)
        - max(0.0, float(resolved_window_days - OPEN_TOPIC_BURST_WINDOW_DAYS) / 28.0),
    )
    confidence = max(
        0.0,
        min(1.0, 0.25 + len(media_names) / 10.0 + active_days / 10.0 + max(0.0, cohesion - min_cohesion)),
    )
    return OpenTopicBurstDetection(
        candidate_date=event_start,
        event_start=event_start,
        event_end=event_end,
        video_count=len(items),
        media_count=len(media_names),
        active_days=active_days,
        score=float(score),
        confidence=float(confidence),
        cohesion=cohesion,
        centroid=centroid,
        representative_title=representative_title,
        top_terms=_open_topic_burst_terms(items)[:8],
        count_by_day=count_by_day,
        window_days=resolved_window_days,
        evidence_min_similarity=min_cohesion,
        available_media_count=resolved_available_media_count,
        required_media_count=resolved_required_media_count,
    )


def _open_topic_burst_ranges_overlap(left: OpenTopicBurstDetection, right: OpenTopicBurstDetection) -> bool:
    return left.event_start <= right.event_end and right.event_start <= left.event_end


def _open_topic_burst_detections_match(left: OpenTopicBurstDetection, right: OpenTopicBurstDetection) -> bool:
    if not _open_topic_burst_ranges_overlap(left, right):
        return False
    return _dot(left.centroid, right.centroid) >= OPEN_TOPIC_BURST_MERGE_MIN_COHESION


def _open_topic_burst_replacement_key(detection: OpenTopicBurstDetection) -> tuple[float, int, int, int, int]:
    return (
        float(detection.score),
        -int(detection.window_days),
        int(detection.video_count),
        int(detection.media_count),
        -detection.event_start.toordinal(),
    )


def _replace_open_topic_burst_candidate_date(
    detection: OpenTopicBurstDetection,
    *,
    candidate_date: date,
) -> OpenTopicBurstDetection:
    return OpenTopicBurstDetection(
        candidate_date=candidate_date,
        event_start=detection.event_start,
        event_end=detection.event_end,
        video_count=detection.video_count,
        media_count=detection.media_count,
        active_days=detection.active_days,
        score=detection.score,
        confidence=detection.confidence,
        cohesion=detection.cohesion,
        centroid=detection.centroid,
        representative_title=detection.representative_title,
        top_terms=detection.top_terms,
        count_by_day=detection.count_by_day,
        window_days=detection.window_days,
        evidence_min_similarity=detection.evidence_min_similarity,
        available_media_count=detection.available_media_count,
        required_media_count=detection.required_media_count,
    )


def _open_topic_burst_final_sort_key(detection: OpenTopicBurstDetection) -> tuple[float, date, str]:
    return (-float(detection.score), detection.candidate_date, detection.representative_title)


def _open_topic_burst_year_cap(year_count: int, *, max_events: int = OPEN_TOPIC_BURST_MAX_EVENTS) -> int | None:
    if year_count < OPEN_TOPIC_BURST_YEAR_CAP_MIN_YEAR_COUNT:
        return None
    scaled_cap = int(math.ceil(float(max_events) * OPEN_TOPIC_BURST_YEAR_CAP_MULTIPLIER / float(max(1, year_count))))
    return max(OPEN_TOPIC_BURST_YEAR_CAP_MIN_EVENTS, scaled_cap)


def _limit_open_topic_burst_detections_by_year(
    detections: list[OpenTopicBurstDetection],
    *,
    max_events: int = OPEN_TOPIC_BURST_MAX_EVENTS,
) -> list[OpenTopicBurstDetection]:
    sorted_detections = sorted(detections, key=_open_topic_burst_final_sort_key)
    years = {detection.candidate_date.year for detection in sorted_detections}
    year_cap = _open_topic_burst_year_cap(len(years), max_events=max_events)
    if year_cap is None:
        return sorted_detections[:max_events]

    selected: list[OpenTopicBurstDetection] = []
    selected_by_year: Counter[int] = Counter()
    for detection in sorted_detections:
        year = detection.candidate_date.year
        if selected_by_year[year] >= year_cap:
            continue
        selected.append(detection)
        selected_by_year[year] += 1
        if len(selected) >= max_events:
            break
    return selected


def _open_topic_burst_semantic_keys_from_vector(vector: list[float]) -> tuple[tuple[int, ...], ...]:
    ranked: list[tuple[float, int, int]] = []
    vector_length = len(vector)
    if vector_length <= 0:
        return ()
    stride = max(1, math.ceil(vector_length / OPEN_TOPIC_BURST_SEMANTIC_KEY_SCAN_DIMS))
    for index in range(0, vector_length, stride):
        raw_value = vector[index]
        value = float(raw_value)
        magnitude = abs(value)
        if magnitude <= 0.0:
            continue
        signed_index = index + 1 if value >= 0 else -(index + 1)
        entry = (magnitude, -index, signed_index)
        if len(ranked) < OPEN_TOPIC_BURST_SEMANTIC_KEY_TOP_DIMS:
            heapq.heappush(ranked, entry)
        elif entry > ranked[0]:
            heapq.heapreplace(ranked, entry)
    ranked.sort(reverse=True)
    signed_dims = [signed_index for _, _, signed_index in ranked]
    if not signed_dims:
        return ()
    if len(signed_dims) == 1:
        return ((signed_dims[0],),)
    keys: list[tuple[int, ...]] = []
    for left_index, left in enumerate(signed_dims[:4]):
        for right in signed_dims[left_index + 1 : 4]:
            keys.append((left, right))
            if len(keys) >= OPEN_TOPIC_BURST_SEMANTIC_MAX_KEYS_PER_ITEM:
                return tuple(keys)
    if len(signed_dims) >= 3 and len(keys) < OPEN_TOPIC_BURST_SEMANTIC_MAX_KEYS_PER_ITEM:
        keys.append(tuple(signed_dims[:3]))
    return tuple(keys[:OPEN_TOPIC_BURST_SEMANTIC_MAX_KEYS_PER_ITEM])


def _open_topic_burst_item_semantic_keys(item: OpenTopicBurstItem) -> tuple[tuple[int, ...], ...]:
    if item.semantic_keys:
        return item.semantic_keys
    return _open_topic_burst_semantic_keys_from_vector(item.vector)


def _open_topic_burst_semantic_candidate_starts(
    items: list[OpenTopicBurstItem],
    *,
    window_days: int,
    step_days: int,
) -> list[date]:
    unique_days = sorted({item.day for item in items})
    if step_days <= 1:
        offsets = range(window_days)
    else:
        offsets = sorted({0, min(step_days, window_days - 1), window_days - 1})
    starts: set[date] = set()
    for day_value in unique_days:
        for offset in offsets:
            starts.add(day_value - timedelta(days=int(offset)))
    return sorted(starts)


def _open_topic_burst_semantic_candidate_windows(
    items: list[OpenTopicBurstItem],
    *,
    window_days: int,
    step_days: int,
    min_video_count: int,
    min_active_days: int,
) -> list[tuple[date, int, int]]:
    starts = _open_topic_burst_semantic_candidate_starts(items, window_days=window_days, step_days=step_days)
    if not starts:
        return []

    count_by_day: Counter[date] = Counter(item.day for item in items)
    active_days = sorted(count_by_day)
    ranked: list[tuple[int, int, int, date, int, int]] = []
    left_index = 0
    right_index = 0
    active_left = 0
    active_right = 0
    for start in starts:
        end = start + timedelta(days=window_days - 1)
        while left_index < len(items) and items[left_index].day < start:
            left_index += 1
        if right_index < left_index:
            right_index = left_index
        while right_index < len(items) and items[right_index].day <= end:
            right_index += 1
        video_count = right_index - left_index
        if video_count < min_video_count:
            continue

        while active_left < len(active_days) and active_days[active_left] < start:
            active_left += 1
        if active_right < active_left:
            active_right = active_left
        while active_right < len(active_days) and active_days[active_right] <= end:
            active_right += 1
        active_count = active_right - active_left
        if active_count < min_active_days:
            continue

        ranked.append((video_count, active_count, -start.toordinal(), start, left_index, right_index))
        if len(ranked) > OPEN_TOPIC_BURST_SEMANTIC_MAX_CANDIDATE_WINDOWS_PER_BUCKET * 4:
            ranked = sorted(ranked, reverse=True)[:OPEN_TOPIC_BURST_SEMANTIC_MAX_CANDIDATE_WINDOWS_PER_BUCKET]

    return [
        (start, left_index, right_index)
        for _, _, _, start, left_index, right_index in sorted(ranked, reverse=True)[
            :OPEN_TOPIC_BURST_SEMANTIC_MAX_CANDIDATE_WINDOWS_PER_BUCKET
        ]
    ]


def _open_topic_burst_sparse_media_rate_pass(
    *,
    window_video_count: int,
    context_video_count: int,
    window_days: int,
    required_media_count: int,
) -> bool:
    if required_media_count >= OPEN_TOPIC_BURST_MIN_MEDIA_COUNT:
        return True
    if context_video_count <= window_video_count:
        return True

    resolved_window_days = max(1, int(window_days))
    context_days = max(resolved_window_days + 1, OPEN_TOPIC_BURST_MEDIA_CONTEXT_DAYS)
    background_video_count = max(0, int(context_video_count) - int(window_video_count))
    background_days = max(1, context_days - resolved_window_days)
    window_rate = float(window_video_count) / float(resolved_window_days)
    background_rate = float(background_video_count) / float(background_days)
    return window_rate >= background_rate * OPEN_TOPIC_BURST_SPARSE_MEDIA_MIN_RATE_MULTIPLIER


def _open_topic_burst_context_video_count(items: list[OpenTopicBurstItem], *, start: date, end: date) -> int:
    context_start, context_end = _open_topic_burst_context_range(start, end)
    return len([item for item in items if context_start <= item.day <= context_end])


def _open_topic_burst_semantic_bucket_detections(
    items_by_day: dict[date, list[OpenTopicBurstItem]],
    *,
    days: list[date],
    window_days: int,
    step_days: int,
    min_video_count: int,
    min_active_days: int,
    min_cohesion: float,
    max_windows_per_bucket: int,
    max_provisional: int,
    media_requirement_for_window: Callable[[date, date], tuple[int, int]] | None = None,
) -> list[OpenTopicBurstDetection]:
    if not days:
        return []
    items_by_key: dict[tuple[int, ...], list[OpenTopicBurstItem]] = defaultdict(list)
    for day_value in days:
        for item in items_by_day.get(day_value, []):
            for semantic_key in _open_topic_burst_item_semantic_keys(item):
                items_by_key[semantic_key].append(item)

    provisional: list[OpenTopicBurstDetection] = []
    eligible_items = [
        (semantic_key, key_items)
        for semantic_key, key_items in items_by_key.items()
        if len(key_items) >= max(OPEN_TOPIC_BURST_SEMANTIC_MIN_BUCKET_VIDEO_COUNT, min_video_count)
    ]
    ranked_keys = sorted(eligible_items, key=lambda entry: (-len(entry[1]), entry[0]))
    for _, key_items in ranked_keys:
        key_items.sort(key=lambda item: (item.day, str(item.title or ""), str(item.video_id)))
        selected_windows = 0
        for start, left_index, right_index in _open_topic_burst_semantic_candidate_windows(
            key_items,
            window_days=window_days,
            step_days=step_days,
            min_video_count=min_video_count,
            min_active_days=min_active_days,
        ):
            window_items = key_items[left_index:right_index]
            event_end = start + timedelta(days=window_days - 1)
            available_media_count = None
            required_media_count = None
            if media_requirement_for_window is not None:
                available_media_count, required_media_count = media_requirement_for_window(start, event_end)
                context_video_count = _open_topic_burst_context_video_count(key_items, start=start, end=event_end)
                if not _open_topic_burst_sparse_media_rate_pass(
                    window_video_count=len(window_items),
                    context_video_count=context_video_count,
                    window_days=window_days,
                    required_media_count=required_media_count,
                ):
                    continue
            detection = _open_topic_burst_detection_from_items(
                window_items,
                event_start=start,
                event_end=event_end,
                min_video_count=min_video_count,
                min_media_count=OPEN_TOPIC_BURST_MIN_MEDIA_COUNT,
                min_active_days=min_active_days,
                min_cohesion=min_cohesion,
                window_days=window_days,
                available_media_count=available_media_count,
                required_media_count=required_media_count,
            )
            if detection is None:
                continue
            provisional.append(detection)
            selected_windows += 1
            if len(provisional) >= max_provisional:
                return sorted(provisional, key=_open_topic_burst_replacement_key, reverse=True)[:max_provisional]
            if selected_windows >= max_windows_per_bucket:
                break
    return provisional


def _select_open_topic_burst_detections(
    items_by_day: dict[date, list[OpenTopicBurstItem]],
    *,
    occupied_dates: set[date] | None = None,
) -> list[OpenTopicBurstDetection]:
    days = sorted(items_by_day)
    if not days:
        return []

    provisional: list[OpenTopicBurstDetection] = []
    total_items = sum(len(items) for items in items_by_day.values())
    media_by_day = _open_topic_burst_media_by_day(items_by_day)
    media_requirement_cache: dict[tuple[date, date], tuple[int, int]] = {}

    def media_requirement_for_window(start: date, end: date) -> tuple[int, int]:
        key = (start, end)
        cached = media_requirement_cache.get(key)
        if cached is not None:
            return cached

        context_start, context_end = _open_topic_burst_context_range(start, end)
        media_names: set[str] = set()
        for day_value in _date_range(context_start, context_end):
            media_names.update(media_by_day.get(day_value, set()))

        available_media_count = max(1, len(media_names))
        required_media_count = _open_topic_burst_required_media_count(available_media_count)
        media_requirement_cache[key] = (available_media_count, required_media_count)
        return media_requirement_cache[key]

    if total_items <= OPEN_TOPIC_BURST_COMPONENT_FULL_SCAN_MAX_ITEMS:
        for start in days:
            end = start + timedelta(days=OPEN_TOPIC_BURST_WINDOW_DAYS - 1)
            window_items = _open_topic_burst_window_items(items_by_day, start=start, end=end)
            if len(window_items) < OPEN_TOPIC_BURST_MIN_VIDEO_COUNT:
                continue
            if len(window_items) > OPEN_TOPIC_BURST_COMPONENT_MAX_WINDOW_ITEMS:
                continue
            for component in _open_topic_burst_components(window_items):
                available_media_count, required_media_count = media_requirement_for_window(start, end)
                detection = _open_topic_burst_detection_from_items(
                    component,
                    event_start=start,
                    event_end=end,
                    available_media_count=available_media_count,
                    required_media_count=required_media_count,
                )
                if detection is not None:
                    provisional.append(detection)
    provisional.extend(
        _open_topic_burst_semantic_bucket_detections(
            items_by_day,
            days=days,
            window_days=OPEN_TOPIC_BURST_WINDOW_DAYS,
            step_days=1,
            min_video_count=OPEN_TOPIC_BURST_MIN_VIDEO_COUNT,
            min_active_days=OPEN_TOPIC_BURST_MIN_ACTIVE_DAYS,
            min_cohesion=OPEN_TOPIC_BURST_MIN_COHESION,
            max_windows_per_bucket=OPEN_TOPIC_BURST_SHORT_MAX_WINDOWS_PER_BUCKET,
            max_provisional=OPEN_TOPIC_BURST_SUSTAINED_MAX_PROVISIONAL,
            media_requirement_for_window=media_requirement_for_window,
        )
    )
    provisional.extend(
        _open_topic_burst_semantic_bucket_detections(
            items_by_day,
            days=days,
            window_days=OPEN_TOPIC_BURST_SUSTAINED_WINDOW_DAYS,
            step_days=OPEN_TOPIC_BURST_SUSTAINED_STEP_DAYS,
            min_video_count=OPEN_TOPIC_BURST_SUSTAINED_MIN_VIDEO_COUNT,
            min_active_days=OPEN_TOPIC_BURST_SUSTAINED_MIN_ACTIVE_DAYS,
            min_cohesion=OPEN_TOPIC_BURST_SUSTAINED_MIN_COHESION,
            max_windows_per_bucket=OPEN_TOPIC_BURST_SUSTAINED_MAX_WINDOWS_PER_BUCKET,
            max_provisional=OPEN_TOPIC_BURST_SUSTAINED_MAX_PROVISIONAL,
            media_requirement_for_window=media_requirement_for_window,
        )
    )
    if len(provisional) > OPEN_TOPIC_BURST_SUSTAINED_MAX_PROVISIONAL:
        provisional = sorted(provisional, key=_open_topic_burst_replacement_key, reverse=True)[
            :OPEN_TOPIC_BURST_SUSTAINED_MAX_PROVISIONAL
        ]

    merged: list[OpenTopicBurstDetection] = []
    for detection in sorted(provisional, key=lambda item: (item.event_start, -item.score, item.representative_title)):
        matched_index = None
        for index, existing in enumerate(merged):
            if not _open_topic_burst_detections_match(existing, detection):
                continue
            matched_index = index
            break
        if matched_index is None:
            merged.append(detection)
            continue
        existing = merged[matched_index]
        if _open_topic_burst_replacement_key(detection) > _open_topic_burst_replacement_key(existing):
            merged[matched_index] = detection

    occupied = set(occupied_dates or set())
    selected: list[OpenTopicBurstDetection] = []
    for detection in sorted(merged, key=lambda item: (-item.score, item.event_start, item.representative_title)):
        candidate_date = _candidate_event_date(
            event_start=detection.event_start,
            event_end=detection.event_end,
            count_by_day=detection.count_by_day,
            occupied_dates=occupied,
        )
        if candidate_date is None:
            continue
        occupied.add(candidate_date)
        selected.append(_replace_open_topic_burst_candidate_date(detection, candidate_date=candidate_date))
    return _limit_open_topic_burst_detections_by_year(selected)


def _open_topic_burst_metadata(detection: OpenTopicBurstDetection) -> dict[str, Any]:
    return {
        "method": OPEN_TOPIC_BURST_METHOD,
        "breakpoint_date": detection.candidate_date.isoformat(),
        "granularity": "event",
        "video_count": detection.video_count,
        "media_count": detection.media_count,
        "available_media_count": detection.available_media_count,
        "required_media_count": detection.required_media_count,
        "active_days": detection.active_days,
        "cohesion": float(detection.cohesion),
        "window_days": int(detection.window_days),
        "evidence_min_similarity": float(detection.evidence_min_similarity),
        "representative_title": detection.representative_title,
        "top_terms": list(detection.top_terms),
    }


def _two_window_boundary_detections(
    payloads: list[AnalysisPeriodPayload],
    *,
    granularity: str,
    window: int,
) -> list[BoundaryDetection]:
    if window <= 0 or len(payloads) < window * 2:
        return []
    scored: list[tuple[int, float, list[float], list[float]]] = []
    for idx in range(window, len(payloads) - window + 1):
        before_payloads = payloads[idx - window : idx]
        after_payloads = payloads[idx : idx + window]
        if any(not item.centroid for item in before_payloads + after_payloads):
            continue
        before_centroid = _mean_vector([item.centroid for item in before_payloads])
        after_centroid = _mean_vector([item.centroid for item in after_payloads])
        score = float(max(0.0, 1.0 - _dot(before_centroid, after_centroid)))
        scored.append((idx, score, before_centroid, after_centroid))
    if not scored:
        return []
    z_scores = _robust_z_scores([item[1] for item in scored])
    detections = []
    for (idx, score, before_centroid, after_centroid), z_value in zip(scored, z_scores):
        before_start = payloads[idx - window].period_date
        before_end = period_end_inclusive(payloads[idx - 1].period_date, granularity)
        after_start = payloads[idx].period_date
        after_end = period_end_inclusive(payloads[idx + window - 1].period_date, granularity)
        detections.append(
            BoundaryDetection(
                granularity=granularity,
                breakpoint_date=after_start,
                before_start=before_start,
                before_end=before_end,
                after_start=after_start,
                after_end=after_end,
                boundary_score=score,
                boundary_z=float(z_value),
                before_centroid=before_centroid,
                after_centroid=after_centroid,
            )
        )
    return detections


def _select_month_boundaries(boundaries: list[BoundaryDetection]) -> list[BoundaryDetection]:
    selected: list[BoundaryDetection] = []
    for boundary in sorted(boundaries, key=lambda item: item.breakpoint_date):
        if boundary.boundary_z < MONTH_BOUNDARY_Z_THRESHOLD:
            continue
        if not selected:
            selected.append(boundary)
            continue
        previous = selected[-1]
        if _month_distance(previous.breakpoint_date, boundary.breakpoint_date) < MONTH_CANDIDATE_MIN_GAP:
            if previous.boundary_z < boundary.boundary_z:
                selected[-1] = boundary
            continue
        selected.append(boundary)
    return selected


def _refine_month_boundary_with_week(
    month_boundary: BoundaryDetection,
    week_boundaries: list[BoundaryDetection],
) -> BoundaryDetection | None:
    candidates = [
        boundary
        for boundary in week_boundaries
        if boundary.boundary_z >= WEEK_BOUNDARY_Z_THRESHOLD
        and abs((boundary.breakpoint_date - month_boundary.breakpoint_date).days) <= 45
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (-item.boundary_z, abs((item.breakpoint_date - month_boundary.breakpoint_date).days), item.breakpoint_date),
    )[0]


def _select_candidate_detections(
    signals_by_granularity: dict[str, list[AnalysisPeriodPayload]],
) -> list[CandidateDetection]:
    month_boundaries = _two_window_boundary_detections(
        signals_by_granularity.get("month", []),
        granularity="month",
        window=MONTH_BOUNDARY_WINDOW,
    )
    week_boundaries = _two_window_boundary_detections(
        signals_by_granularity.get("week", []),
        granularity="week",
        window=WEEK_BOUNDARY_WINDOW,
    )
    detections: list[CandidateDetection] = []
    for month_boundary in _select_month_boundaries(month_boundaries):
        week_boundary = _refine_month_boundary_with_week(month_boundary, week_boundaries)
        if week_boundary is not None:
            detections.append(
                CandidateDetection(
                    boundary=week_boundary,
                    source_month_boundary=month_boundary,
                    event_type="regime",
                    supporting_granularities=["month", "week"],
                )
            )
            continue
        detections.append(
            CandidateDetection(
                boundary=month_boundary,
                source_month_boundary=month_boundary,
                event_type="transition",
                supporting_granularities=["month"],
            )
        )
    return detections


def _candidate_detection_metadata(detection: CandidateDetection) -> dict[str, Any]:
    boundary = detection.boundary
    month_boundary = detection.source_month_boundary
    return {
        "method": DETECTION_METHOD,
        "breakpoint_date": detection.candidate_date.isoformat(),
        "granularity": boundary.granularity,
        "boundary_score": float(boundary.boundary_score),
        "boundary_z": float(boundary.boundary_z),
        "before_start": boundary.before_start.isoformat(),
        "before_end": boundary.before_end.isoformat(),
        "after_start": boundary.after_start.isoformat(),
        "after_end": boundary.after_end.isoformat(),
        "supporting_granularities": list(detection.supporting_granularities),
        "source_month_breakpoint_date": month_boundary.breakpoint_date.isoformat(),
        "source_month_boundary_score": float(month_boundary.boundary_score),
        "source_month_boundary_z": float(month_boundary.boundary_z),
    }


def next_china_trading_day(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _candidate_summary(evidence: dict[str, Any]) -> str:
    preview = str(evidence.get("preview") or "").strip()
    return preview or "候选事件暂无证据摘要"


def _candidate_top_terms(evidence: dict[str, Any], *, limit: int = 8) -> list[str]:
    detection = evidence.get("detection") if isinstance(evidence.get("detection"), dict) else {}
    detection_terms = detection.get("top_terms") if isinstance(detection.get("top_terms"), list) else []
    if detection_terms:
        return [str(item) for item in detection_terms if str(item or "").strip()][:limit]
    counts: dict[str, int] = {}
    for item in evidence.get("videos") or []:
        for token in _title_terms(str(item.get("title") or "")):
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _ in ranked[:limit]]


def _playlist_videos_with_embeddings(session: Session, playlist_id: uuid.UUID) -> list[tuple[Video, Media, VideoEmbedding | None]]:
    spec = analysis_spec()
    rows = (
        session.execute(
            select(Video, Media, VideoEmbedding)
            .select_from(PlaylistMedia)
            .join(Media, Media.id == PlaylistMedia.media_id)
            .join(Video, Video.media_id == PlaylistMedia.media_id)
            .outerjoin(
                VideoEmbedding,
                (
                    (VideoEmbedding.video_id == Video.id)
                    & (VideoEmbedding.transcript_variant == spec.transcript_variant)
                    & (VideoEmbedding.embedding_model == spec.model)
                    & (VideoEmbedding.embedding_dim == spec.dim)
                ),
            )
            .where(PlaylistMedia.playlist_id == playlist_id)
            .order_by(Video.published_at.asc().nulls_last(), Video.created_at.asc(), Video.id.asc())
        )
        .all()
    )
    return [(video, media, embedding) for video, media, embedding in rows]


def playlist_coverage_stats(session: Session, playlist_id: uuid.UUID) -> CoverageStats:
    spec = analysis_spec()
    media_ids = list(
        session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
    )
    if not media_ids:
        return CoverageStats(video_total=0, video_embedded=0, video_skipped=0, video_failed=0)

    total = session.execute(
        select(func.count())
        .select_from(Video)
        .where(Video.media_id.in_(media_ids))
    ).scalar_one()

    status_rows = session.execute(
        select(VideoEmbedding.status, func.count())
        .select_from(VideoEmbedding)
        .join(Video, Video.id == VideoEmbedding.video_id)
        .where(
            Video.media_id.in_(media_ids),
            VideoEmbedding.transcript_variant == spec.transcript_variant,
            VideoEmbedding.embedding_model == spec.model,
            VideoEmbedding.embedding_dim == spec.dim,
            VideoEmbedding.status.in_(["ready", "skipped", "skipped_over_budget", "failed"]),
        )
        .group_by(VideoEmbedding.status)
    ).all()
    counts = {str(status or ""): int(count or 0) for status, count in status_rows}
    return CoverageStats(
        video_total=int(total or 0),
        video_embedded=counts.get("ready", 0),
        video_skipped=counts.get("skipped", 0) + counts.get("skipped_over_budget", 0),
        video_failed=counts.get("failed", 0),
    )


def _published_at_filter_for_local_days(days: set[date], timezone: ZoneInfo):
    clauses = []
    for day_value in sorted(days):
        local_start = datetime.combine(day_value, time.min, tzinfo=timezone)
        local_end = local_start + timedelta(days=1)
        start_utc = local_start.astimezone(dt_timezone.utc)
        end_utc = local_end.astimezone(dt_timezone.utc)
        clauses.append((Video.published_at >= start_utc) & (Video.published_at < end_utc))
    return or_(*clauses)


def _iter_playlist_ready_embedding_batches(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    batch_size: int | None = None,
    only_days: set[date] | None = None,
    analysis_timezone: ZoneInfo | None = None,
):
    spec = analysis_spec()
    resolved_batch_size = _analysis_stream_batch_size(batch_size)
    day_filter = None
    if only_days is not None:
        if not only_days:
            return
        day_filter = _published_at_filter_for_local_days(only_days, analysis_timezone or _analysis_timezone())

    last_published_at: datetime | None = None
    last_video_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(
                Video.id,
                Video.media_id,
                Video.title,
                Media.name,
                Video.published_at,
                VideoEmbedding.vector,
            )
            .select_from(PlaylistMedia)
            .join(Media, Media.id == PlaylistMedia.media_id)
            .join(Video, Video.media_id == PlaylistMedia.media_id)
            .join(
                VideoEmbedding,
                (
                    (VideoEmbedding.video_id == Video.id)
                    & (VideoEmbedding.transcript_variant == spec.transcript_variant)
                    & (VideoEmbedding.embedding_model == spec.model)
                    & (VideoEmbedding.embedding_dim == spec.dim)
                ),
            )
            .where(
                PlaylistMedia.playlist_id == playlist_id,
                Video.published_at.is_not(None),
                VideoEmbedding.status == "ready",
                VideoEmbedding.vector.is_not(None),
            )
            .order_by(Video.published_at.asc(), Video.id.asc())
            .limit(resolved_batch_size)
        )
        if day_filter is not None:
            stmt = stmt.where(day_filter)
        if last_published_at is not None and last_video_id is not None:
            stmt = stmt.where(
                or_(
                    Video.published_at > last_published_at,
                    (Video.published_at == last_published_at) & (Video.id > last_video_id),
                )
            )

        rows = session.execute(stmt).all()
        if not rows:
            break

        batch: list[AnalysisEmbeddingItem] = []
        for video_id, media_id, title, media_name, published_at, vector in rows:
            if not published_at or not vector:
                continue
            batch.append(
                AnalysisEmbeddingItem(
                    video_id=video_id,
                    media_id=media_id,
                    title=title,
                    media_name=media_name,
                    published_at=published_at,
                    vector=[float(value) for value in vector],
                )
            )
        if batch:
            yield batch

        last_published_at = rows[-1][4]
        last_video_id = rows[-1][0]
        if len(rows) < resolved_batch_size:
            break


def _local_analysis_day(item: AnalysisEmbeddingItem, timezone: ZoneInfo) -> date:
    return item.published_at.astimezone(timezone).date()


def _evidence_sort_key(item: dict[str, Any]) -> tuple[float, float]:
    return (-(item.get("shift_score") or 0.0), item.get("distance_to_centroid") or 0.0)


def _candidate_evidence_item(
    item: AnalysisEmbeddingItem,
    *,
    centroid: list[float],
    previous_centroid: list[float] | None,
) -> dict[str, Any]:
    vector = _normalize_vector([float(value) for value in item.vector])
    centroid_similarity = _dot(vector, centroid)
    shift_score = None
    if previous_centroid is not None:
        shift_score = float(_dot(vector, centroid) - _dot(vector, previous_centroid))
    return {
        "video_id": str(item.video_id),
        "media_id": str(item.media_id),
        "title": item.title,
        "media_name": item.media_name,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "distance_to_centroid": float(max(0.0, 1.0 - centroid_similarity)),
        "shift_score": shift_score,
    }


def _candidate_evidence_payload_from_items(evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_items = sorted(evidence_items, key=_evidence_sort_key)
    preview = []
    for item in evidence_items[:5]:
        title = str(item.get("title") or "").strip()
        media_name = str(item.get("media_name") or "").strip()
        preview.append(" / ".join(part for part in [title, media_name] if part))
    return {
        "preview": "；".join(preview[:3]),
        "videos": evidence_items[:5],
    }


def _compact_title_key(title: str | None) -> str:
    text = re.sub(r"\|.*$", "", str(title or "").lower())
    text = re.sub(r"【[^】]+】", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)
    return text[:80]


def _diverse_evidence_items(evidence_items: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    sorted_items = sorted(evidence_items, key=_evidence_sort_key)
    selected: list[dict[str, Any]] = []
    media_counts: dict[str, int] = {}
    title_keys: set[str] = set()
    for item in sorted_items:
        media_name = str(item.get("media_name") or item.get("media_id") or "").strip()
        title_key = _compact_title_key(item.get("title"))
        if title_key and title_key in title_keys:
            continue
        if media_name and media_counts.get(media_name, 0) >= 2:
            continue
        selected.append(item)
        if media_name:
            media_counts[media_name] = media_counts.get(media_name, 0) + 1
        if title_key:
            title_keys.add(title_key)
        if len(selected) >= limit:
            return selected
    for item in sorted_items:
        if item in selected:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _open_topic_burst_evidence_item(
    item: AnalysisEmbeddingItem,
    detection: OpenTopicBurstDetection,
) -> dict[str, Any] | None:
    vector = _normalize_vector([float(value) for value in item.vector])
    if not vector:
        return None
    similarity = float(_dot(vector, detection.centroid))
    if similarity < detection.evidence_min_similarity:
        return None
    terms = _title_terms(item.title)
    detection_terms = set(detection.top_terms)
    matched_terms = [term for term in terms if term in detection_terms]
    return {
        "video_id": str(item.video_id),
        "media_id": str(item.media_id),
        "title": item.title,
        "media_name": item.media_name,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "distance_to_centroid": float(max(0.0, 1.0 - similarity)),
        "shift_score": similarity,
        "matched_terms": matched_terms[:8],
    }


def _open_topic_burst_evidence_payload_from_items(
    evidence_items: list[dict[str, Any]],
    detection: OpenTopicBurstDetection,
) -> dict[str, Any]:
    selected = _diverse_evidence_items(evidence_items, limit=5)
    representative_title = detection.representative_title
    if not representative_title:
        for item in selected:
            representative_title = str(item.get("title") or "").strip()
            if representative_title:
                break
    preview_parts = [
        f"事件候选：{detection.event_start.isoformat()} ~ {detection.event_end.isoformat()}",
        f"{detection.video_count} 条视频",
        f"{detection.media_count} 个来源",
        f"聚合度 {detection.cohesion:.2f}",
    ]
    if representative_title:
        preview_parts.append(f"代表标题：{representative_title}")
    return {
        "preview": "；".join(preview_parts),
        "videos": selected,
    }


def _candidate_evidence_payload(
    items: list[dict[str, Any]],
    *,
    centroid,
    previous_centroid,
) -> dict[str, Any]:
    evidence_items: list[dict[str, Any]] = []
    for item in items:
        evidence_items.append(
            _candidate_evidence_item(
                AnalysisEmbeddingItem(
                    video_id=item["video_id"],
                    media_id=item["media_id"],
                    title=item["title"],
                    media_name=item["media_name"],
                    published_at=item["published_at"],
                    vector=item["vector"],
                ),
                centroid=centroid,
                previous_centroid=previous_centroid,
            )
        )
    return _candidate_evidence_payload_from_items(evidence_items)


def _rank_candidate_evidence(
    ranked_items: list[dict[str, Any]],
    item: AnalysisEmbeddingItem,
    *,
    centroid: list[float],
    previous_centroid: list[float] | None,
    limit: int = 5,
) -> None:
    ranked_items.append(_candidate_evidence_item(item, centroid=centroid, previous_centroid=previous_centroid))
    ranked_items.sort(key=_evidence_sort_key)
    del ranked_items[limit:]


def _rank_open_topic_burst_evidence(
    ranked_items: list[dict[str, Any]],
    item: AnalysisEmbeddingItem,
    *,
    detection: OpenTopicBurstDetection,
    limit: int = 30,
) -> None:
    evidence_item = _open_topic_burst_evidence_item(item, detection)
    if evidence_item is None:
        return
    ranked_items.append(evidence_item)
    ranked_items.sort(key=_evidence_sort_key)
    del ranked_items[limit:]


def _playlist_ready_embedding_items(session: Session, playlist_id: uuid.UUID) -> list[AnalysisEmbeddingItem]:
    items: list[AnalysisEmbeddingItem] = []
    for batch in _iter_playlist_ready_embedding_batches(session, playlist_id):
        items.extend(batch)
    return items


def build_playlist_analysis_snapshot(session: Session, playlist_id: uuid.UUID, *, job: Job | None = None) -> dict[str, Any]:
    state = ensure_playlist_analysis_state(session, playlist_id)
    run: PlaylistAnalysisRun | None = None
    try:
        _checkpoint_analysis_task(session, job)
        coverage = playlist_coverage_stats(session, playlist_id)
        _checkpoint_analysis_task(session, job)
        spec = analysis_spec()

        run = PlaylistAnalysisRun(
            playlist_id=playlist_id,
            status="pending",
            analysis_clock="day",
            embedding_model=spec.model,
            embedding_dim=spec.dim,
            transcript_variant=spec.transcript_variant,
            video_total=coverage.video_total,
            video_embedded=coverage.video_embedded,
            video_skipped=coverage.video_skipped,
            video_failed=coverage.video_failed,
        )
        session.add(run)
        session.flush([run])
        run.status = "running"
        run.started_at = utcnow()
        session.flush([run])
        _set_analysis_progress(job, 100)

        timezone = _analysis_timezone()
        batch_size = _analysis_stream_batch_size()
        vector_sums: dict[tuple[str, date], list[float]] = {}
        signal_counts: dict[tuple[str, date], int] = defaultdict(int)
        open_topic_items_by_day: dict[date, list[OpenTopicBurstItem]] = defaultdict(list)
        embedded_total = max(1, int(coverage.video_embedded or 0))
        first_pass_count = 0

        for batch in _iter_playlist_ready_embedding_batches(session, playlist_id, batch_size=batch_size):
            _checkpoint_analysis_task(session, job)
            first_pass_count += len(batch)
            for item in batch:
                day_value = _local_analysis_day(item, timezone)
                normalized = _normalize_vector(item.vector)
                compact_normalized = array("f", normalized)
                topic_terms = tuple(_title_terms(item.title))
                open_topic_items_by_day[day_value].append(
                    OpenTopicBurstItem(
                        video_id=item.video_id,
                        media_id=item.media_id,
                        title=item.title,
                        media_name=item.media_name,
                        published_at=item.published_at,
                        day=day_value,
                        vector=compact_normalized,
                        terms=topic_terms,
                        semantic_keys=_open_topic_burst_semantic_keys_from_vector(compact_normalized),
                    )
                )
                for granularity in SIGNAL_GRANULARITY_WINDOWS:
                    signal_key = (granularity, period_start(day_value, granularity))
                    if signal_key not in vector_sums:
                        vector_sums[signal_key] = [0.0] * len(normalized)
                    for index, value in enumerate(normalized):
                        vector_sums[signal_key][index] += float(value)
                    signal_counts[signal_key] += 1
            _checkpoint_analysis_task(session, job)
            _set_analysis_progress(job, 500 + int(min(1.0, first_pass_count / embedded_total) * 1700))

        _set_analysis_progress(job, 2300)
        signals_by_granularity: dict[str, list[AnalysisPeriodPayload]] = {granularity: [] for granularity in SIGNAL_GRANULARITY_WINDOWS}
        signals_by_key: dict[tuple[str, date], AnalysisPeriodPayload] = {}
        centroid_by_key: dict[tuple[str, date], list[float]] = {}
        for granularity, rolling_window in SIGNAL_GRANULARITY_WINDOWS.items():
            for _, period_date in sorted(key for key in signal_counts if key[0] == granularity):
                signal_key = (granularity, period_date)
                count = int(signal_counts[signal_key])
                centroid = _normalize_vector([value / float(count) for value in vector_sums[signal_key]]) if count else []
                centroid_by_key[signal_key] = centroid
                payload = AnalysisPeriodPayload(
                    granularity=granularity,
                    period_date=period_date,
                    rolling_window=rolling_window,
                    video_count=count,
                    ready_embedding_count=count,
                    centroid=centroid,
                )
                signals_by_granularity[granularity].append(payload)
                signals_by_key[signal_key] = payload

        _checkpoint_analysis_task(session, job)
        _set_analysis_progress(job, 2800)
        dispersion_values: dict[tuple[str, date], list[float]] = defaultdict(list)
        dispersion_count = 0
        for batch in _iter_playlist_ready_embedding_batches(session, playlist_id, batch_size=batch_size):
            _checkpoint_analysis_task(session, job)
            dispersion_count += len(batch)
            for item in batch:
                day_value = _local_analysis_day(item, timezone)
                normalized = _normalize_vector(item.vector)
                for granularity in SIGNAL_GRANULARITY_WINDOWS:
                    signal_key = (granularity, period_start(day_value, granularity))
                    centroid = centroid_by_key.get(signal_key)
                    if centroid is None:
                        continue
                    dispersion_values[signal_key].append(float(max(0.0, 1.0 - _dot(normalized, centroid))))
            _checkpoint_analysis_task(session, job)
            _set_analysis_progress(job, 2800 + int(min(1.0, dispersion_count / embedded_total) * 1400))

        _set_analysis_progress(job, 4300)
        for signal_key, values in dispersion_values.items():
            payload = signals_by_key.get(signal_key)
            if payload is None or not values:
                continue
            sorted_values = sorted(values)
            mean_value = float(sum(values) / len(values))
            variance = sum((value - mean_value) ** 2 for value in values) / len(values)
            payload.dispersion_mean = mean_value
            payload.dispersion_std = float(math.sqrt(variance))
            payload.dispersion_p25 = _percentile(sorted_values, 0.25)
            payload.dispersion_p75 = _percentile(sorted_values, 0.75)

        for granularity, payloads in signals_by_granularity.items():
            drift_scores: list[float | None] = []
            previous_centroid = None
            for payload in payloads:
                centroid = payload.centroid
                if previous_centroid is None:
                    drift = None
                else:
                    drift = float(max(0.0, 1.0 - _dot(previous_centroid, centroid)))
                drift_scores.append(drift)
                previous_centroid = centroid

            drift_series = [float(item or 0.0) for item in drift_scores]
            rolling_mean, rolling_std, rolling_z = _rolling_mean_std_and_z(
                drift_series,
                window=SIGNAL_GRANULARITY_WINDOWS[granularity],
            )
            projections, explained_ratio = _pca_projection([payload.centroid for payload in payloads], components_count=3)
            projection_id = f"pca3_{granularity}_global_v1"

            for idx, payload in enumerate(payloads):
                payload.drift_score = drift_scores[idx]
                payload.drift_rolling_mean = rolling_mean[idx]
                payload.drift_rolling_std = rolling_std[idx]
                payload.drift_rolling_z = rolling_z[idx]
                payload.projection_id = projection_id
                payload.projection_method = "pca"
                payload.projection_explained_variance_ratio = explained_ratio
                projection = projections[idx] if idx < len(projections) else (0.0, 0.0, 0.0)
                payload.projection_x = projection[0]
                payload.projection_y = projection[1]
                payload.projection_z = projection[2]

        _set_analysis_progress(job, 5600)
        period_payloads = signals_by_granularity["day"]
        candidate_detections = _select_candidate_detections(signals_by_granularity)
        _set_analysis_progress(job, 6000)
        event_detections = _select_open_topic_burst_detections(
            open_topic_items_by_day,
            occupied_dates={detection.candidate_date for detection in candidate_detections},
        )
        _set_analysis_progress(job, 6600)

        candidate_by_date = {detection.candidate_date: detection for detection in candidate_detections}
        event_by_date = {detection.candidate_date: detection for detection in event_detections}
        candidate_evidence: dict[date, list[dict[str, Any]]] = {
            detection.candidate_date: [] for detection in candidate_detections
        }
        for detection in event_detections:
            candidate_evidence[detection.candidate_date] = []
        day_to_candidate_dates: dict[date, list[date]] = defaultdict(list)
        for detection in candidate_detections:
            for day_value in _date_range(detection.event_start, detection.event_end):
                day_to_candidate_dates[day_value].append(detection.candidate_date)
        for detection in event_detections:
            for day_value in _date_range(detection.event_start, detection.event_end):
                day_to_candidate_dates[day_value].append(detection.candidate_date)
        if day_to_candidate_dates:
            for batch in _iter_playlist_ready_embedding_batches(
                session,
                playlist_id,
                batch_size=batch_size,
                only_days=set(day_to_candidate_dates),
                analysis_timezone=timezone,
            ):
                _checkpoint_analysis_task(session, job)
                for item in batch:
                    day_value = _local_analysis_day(item, timezone)
                    for candidate_day in day_to_candidate_dates.get(day_value, []):
                        event_detection = event_by_date.get(candidate_day)
                        if event_detection is not None:
                            _rank_open_topic_burst_evidence(
                                candidate_evidence[candidate_day],
                                item,
                                detection=event_detection,
                            )
                            continue
                        detection = candidate_by_date[candidate_day]
                        _rank_candidate_evidence(
                            candidate_evidence[candidate_day],
                            item,
                            centroid=detection.boundary.after_centroid,
                            previous_centroid=detection.boundary.before_centroid,
                        )
                _checkpoint_analysis_task(session, job)
        _set_analysis_progress(job, 7600)

        session.execute(delete(PlaylistAnalysisPeriod).where(PlaylistAnalysisPeriod.analysis_run_id == run.id))
        session.execute(delete(PlaylistAnalysisSignal).where(PlaylistAnalysisSignal.analysis_run_id == run.id))
        session.execute(delete(PlaylistAnalysisCandidate).where(PlaylistAnalysisCandidate.analysis_run_id == run.id))

        _set_analysis_progress(job, 8000)
        period_models: list[PlaylistAnalysisPeriod] = []
        for payload in period_payloads:
            period_model = PlaylistAnalysisPeriod(
                analysis_run_id=run.id,
                period_date=payload.period_date,
                video_count=payload.video_count,
                centroid_vector=[float(value) for value in payload.centroid],
                drift_score=payload.drift_score,
                dispersion_score=payload.dispersion_mean,
                drift_rolling_mean=payload.drift_rolling_mean,
                drift_rolling_std=payload.drift_rolling_std,
                drift_rolling_z=payload.drift_rolling_z,
                dispersion_std=payload.dispersion_std,
                dispersion_p25=payload.dispersion_p25,
                dispersion_p75=payload.dispersion_p75,
                projection_x=payload.projection_x,
                projection_y=payload.projection_y,
                projection_z=payload.projection_z,
            )
            period_models.append(period_model)
            session.add(period_model)

        signal_models: list[PlaylistAnalysisSignal] = []
        signal_model_by_key: dict[tuple[str, date], PlaylistAnalysisSignal] = {}
        for granularity, payloads in signals_by_granularity.items():
            for payload in payloads:
                signal_model = PlaylistAnalysisSignal(
                    analysis_run_id=run.id,
                    granularity=granularity,
                    period_date=payload.period_date,
                    rolling_window=payload.rolling_window,
                    video_count=payload.video_count,
                    ready_embedding_count=payload.ready_embedding_count,
                    centroid_vector=[float(value) for value in payload.centroid],
                    drift_score=payload.drift_score,
                    drift_rolling_mean=payload.drift_rolling_mean,
                    drift_rolling_std=payload.drift_rolling_std,
                    drift_rolling_z=payload.drift_rolling_z,
                    dispersion_mean=payload.dispersion_mean,
                    dispersion_std=payload.dispersion_std,
                    dispersion_p25=payload.dispersion_p25,
                    dispersion_p75=payload.dispersion_p75,
                    projection_id=payload.projection_id,
                    projection_method=payload.projection_method,
                    projection_x=payload.projection_x,
                    projection_y=payload.projection_y,
                    projection_z=payload.projection_z,
                    projection_explained_variance_ratio=payload.projection_explained_variance_ratio,
                )
                signal_models.append(signal_model)
                signal_model_by_key[(granularity, payload.period_date)] = signal_model
                session.add(signal_model)

        _set_analysis_progress(job, 9000)
        candidate_models: list[PlaylistAnalysisCandidate] = []
        candidate_signal_keys: dict[PlaylistAnalysisCandidate, list[tuple[str, date]]] = {}
        for detection in candidate_detections:
            boundary = detection.boundary
            candidate_day = detection.candidate_date
            signal_payload = signals_by_key.get((boundary.granularity, boundary.breakpoint_date))
            uncertainty = signal_payload.dispersion_mean if signal_payload is not None else 0.0
            effective_trade_date = next_china_trading_day(candidate_day)
            evidence = _candidate_evidence_payload_from_items(candidate_evidence.get(candidate_day, []))
            evidence["detection"] = _candidate_detection_metadata(detection)
            evidence_video_ids = [str(item.get("video_id")) for item in evidence.get("videos") or [] if item.get("video_id")]
            score = float(boundary.boundary_z)
            candidate_model = PlaylistAnalysisCandidate(
                analysis_run_id=run.id,
                candidate_date=candidate_day,
                effective_trade_date=effective_trade_date,
                peak_date=candidate_day,
                event_start=detection.event_start,
                event_end=detection.event_end,
                event_type=detection.event_type,
                status="draft",
                score=score,
                confidence=float(max(0.0, min(1.0, score / 4.0))),
                uncertainty=uncertainty,
                drift_score=boundary.boundary_score,
                dispersion_score=uncertainty,
                drift_rolling_z=boundary.boundary_z,
                summary=_candidate_summary(evidence),
                top_terms=_candidate_top_terms(evidence),
                evidence_video_ids=evidence_video_ids,
                evidence_json=evidence,
                available_at=utcnow(),
            )
            candidate_models.append(candidate_model)
            signal_keys = [
                (boundary.granularity, boundary.breakpoint_date),
                ("month", detection.source_month_boundary.breakpoint_date),
                ("day", candidate_day),
            ]
            seen_signal_keys = set()
            candidate_signal_keys[candidate_model] = []
            for signal_key in signal_keys:
                if signal_key in seen_signal_keys:
                    continue
                seen_signal_keys.add(signal_key)
                candidate_signal_keys[candidate_model].append(signal_key)
            session.add(candidate_model)

        for detection in event_detections:
            candidate_day = detection.candidate_date
            signal_payload = signals_by_key.get(("day", candidate_day))
            uncertainty = 1.0 - detection.confidence
            if signal_payload is not None and signal_payload.dispersion_mean is not None:
                uncertainty = max(0.0, min(1.0, uncertainty + float(signal_payload.dispersion_mean or 0.0) / 4.0))
            effective_trade_date = next_china_trading_day(candidate_day)
            evidence = _open_topic_burst_evidence_payload_from_items(
                candidate_evidence.get(candidate_day, []),
                detection,
            )
            evidence["detection"] = _open_topic_burst_metadata(detection)
            evidence_video_ids = [str(item.get("video_id")) for item in evidence.get("videos") or [] if item.get("video_id")]
            candidate_model = PlaylistAnalysisCandidate(
                analysis_run_id=run.id,
                candidate_date=candidate_day,
                effective_trade_date=effective_trade_date,
                peak_date=candidate_day,
                event_start=detection.event_start,
                event_end=detection.event_end,
                event_type="burst",
                status="draft",
                score=float(detection.score),
                confidence=float(detection.confidence),
                uncertainty=float(uncertainty),
                drift_score=None,
                dispersion_score=float(uncertainty),
                drift_rolling_z=None,
                summary=_candidate_summary(evidence),
                top_terms=_candidate_top_terms(evidence),
                evidence_video_ids=evidence_video_ids,
                evidence_json=evidence,
                available_at=utcnow(),
            )
            candidate_models.append(candidate_model)
            candidate_signal_keys[candidate_model] = [("day", candidate_day)]
            session.add(candidate_model)

        session.flush(candidate_models)
        for candidate_model, keys in candidate_signal_keys.items():
            for signal_key in keys:
                signal_model = signal_model_by_key.get(signal_key)
                if signal_model is not None:
                    signal_model.linked_event_id = candidate_model.id

        _set_analysis_progress(job, 9700)
        run.status = "ready"
        run.finished_at = utcnow()
        run.updated_at = utcnow()
        state.last_ready_run_id = run.id
        state.last_built_at = run.finished_at
        state.last_error = None
        state.analysis_dirty = False
        session.flush()
        pruned_run_count = prune_playlist_analysis_runs(session, playlist_id, keep_run_ids=[run.id] if run.id else [])
        session.flush()

        return {
            "analysis_run_id": str(run.id),
            "period_count": len(period_models),
            "signal_count": len(signal_models),
            "candidate_count": len(candidate_models),
            "pruned_run_count": pruned_run_count,
            "coverage": {
                "video_total": coverage.video_total,
                "video_embedded": coverage.video_embedded,
                "video_skipped": coverage.video_skipped,
                "video_failed": coverage.video_failed,
            },
        }
    except JobCancelRequested:
        if run is not None:
            run.status = "canceled"
            run.finished_at = utcnow()
        state.analysis_dirty = True
        session.flush()
        raise
    except Exception as exc:
        if run is not None:
            run.status = "failed"
            run.finished_at = utcnow()
        state.last_error = str(exc)[:1000]
        state.analysis_dirty = True
        session.flush()
        raise


def fetch_plain_transcript_for_embedding(session: Session, video_id: uuid.UUID) -> tuple[str, str] | None:
    spec = analysis_spec()
    asset = pick_transcript_asset(session, video_id, variant=spec.transcript_variant, format_="txt")
    if not asset:
        return None
    text, _ = read_text_asset(asset)
    if not str(text or "").strip():
        return None
    checksum = transcript_checksum(text)
    return text, checksum


def _playlist_backfill_items(session: Session, playlist_id: uuid.UUID, *, force: bool) -> list[EmbeddingBackfillItem]:
    items: list[EmbeddingBackfillItem] = []
    for video, _, embedding in _playlist_videos_with_embeddings(session, playlist_id):
        transcript_payload = fetch_plain_transcript_for_embedding(session, video.id)
        if not transcript_payload:
            continue
        text, checksum = transcript_payload
        if force or video_embedding_needs_refresh(embedding, text_checksum_value=checksum):
            items.append(EmbeddingBackfillItem(video_id=video.id, text=text, checksum=checksum))
    return items


def _iter_playlist_backfill_candidates(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    force: bool = False,
    scan_batch_size: int = 500,
):
    spec = analysis_spec()
    batch_size = max(1, int(scan_batch_size or 500))
    last_video_id: uuid.UUID | None = None
    asset_priority = case(
        ((Asset.source == "subtitle") & (Asset.language == "zh"), 0),
        ((Asset.source == "qwen3-asr") & (Asset.language == "zh"), 1),
        ((Asset.source == "speaches") & (Asset.language == "zh"), 2),
        else_=3,
    )
    ranked_assets = (
        select(
            Asset.video_id.label("video_id"),
            Asset.s3_bucket.label("s3_bucket"),
            Asset.s3_key.label("s3_key"),
            func.row_number()
            .over(partition_by=Asset.video_id, order_by=(asset_priority.asc(), Asset.created_at.desc(), Asset.id.asc()))
            .label("asset_rank"),
        )
        .where(
            Asset.type == "transcript",
            Asset.format == "txt",
            Asset.variant == spec.transcript_variant,
            Asset.s3_bucket.is_not(None),
            Asset.s3_key.is_not(None),
            Asset.s3_bucket != "",
            Asset.s3_key != "",
        )
        .subquery()
    )
    while True:
        stmt = (
            select(
                Video.id,
                VideoEmbedding.status,
                VideoEmbedding.text_checksum,
                VideoEmbedding.vector.is_not(None),
                ranked_assets.c.s3_bucket,
                ranked_assets.c.s3_key,
            )
            .select_from(PlaylistMedia)
            .join(Video, Video.media_id == PlaylistMedia.media_id)
            .join(ranked_assets, (ranked_assets.c.video_id == Video.id) & (ranked_assets.c.asset_rank == 1))
            .outerjoin(
                VideoEmbedding,
                (
                    (VideoEmbedding.video_id == Video.id)
                    & (VideoEmbedding.transcript_variant == spec.transcript_variant)
                    & (VideoEmbedding.embedding_model == spec.model)
                    & (VideoEmbedding.embedding_dim == spec.dim)
                ),
            )
            .where(PlaylistMedia.playlist_id == playlist_id)
            .order_by(Video.id.asc())
            .limit(batch_size)
        )
        if not force:
            stmt = stmt.where(
                or_(
                    VideoEmbedding.id.is_(None),
                    VideoEmbedding.status != "ready",
                    VideoEmbedding.vector.is_(None),
                )
            )
        if last_video_id is not None:
            stmt = stmt.where(Video.id > last_video_id)
        rows = session.execute(stmt).all()
        if not rows:
            break
        for video_id, status, text_checksum_value, has_vector, s3_bucket, s3_key in rows:
            yield EmbeddingBackfillTranscriptCandidate(
                video_id=video_id,
                embedding_status=status,
                text_checksum=text_checksum_value,
                has_vector=bool(has_vector),
                s3_bucket=str(s3_bucket or ""),
                s3_key=str(s3_key or ""),
            )
        last_video_id = rows[-1][0]
        if len(rows) < batch_size:
            break


def _resolve_backfill_transcript_candidate(
    session: Session,
    candidate: EmbeddingBackfillCandidate,
) -> EmbeddingBackfillTranscriptCandidate | None:
    spec = analysis_spec()
    asset = pick_transcript_asset(session, candidate.video_id, variant=spec.transcript_variant, format_="txt")
    if not asset:
        return None
    bucket = str(getattr(asset, "s3_bucket", "") or "").strip()
    key = str(getattr(asset, "s3_key", "") or "").strip()
    if not bucket or not key:
        return None
    return EmbeddingBackfillTranscriptCandidate(
        video_id=candidate.video_id,
        embedding_status=candidate.embedding_status,
        text_checksum=candidate.text_checksum,
        has_vector=candidate.has_vector,
        s3_bucket=bucket,
        s3_key=key,
    )


def _read_backfill_transcript_item(
    candidate: EmbeddingBackfillTranscriptCandidate,
    *,
    force: bool,
) -> EmbeddingBackfillFetchResult:
    raw = s3_get_bytes(bucket=candidate.s3_bucket, key=candidate.s3_key)
    text = raw.decode("utf-8", errors="ignore")
    if not str(text or "").strip():
        return EmbeddingBackfillFetchResult(item=None, skipped_empty=True)
    checksum = transcript_checksum(text)
    if force:
        return EmbeddingBackfillFetchResult(
            item=EmbeddingBackfillItem(video_id=candidate.video_id, text=text, checksum=checksum)
        )
    status = str(candidate.embedding_status or "").strip().lower()
    if status != "ready" or not candidate.has_vector:
        return EmbeddingBackfillFetchResult(
            item=EmbeddingBackfillItem(video_id=candidate.video_id, text=text, checksum=checksum)
        )
    return EmbeddingBackfillFetchResult(item=None)


def _write_backfill_batch(
    session: Session,
    items: list[EmbeddingBackfillItem],
    *,
    skip_reason: str | None = None,
) -> EmbeddingBackfillStats:
    result = _prepare_backfill_batch_result(items, skip_reason=skip_reason)
    return _apply_backfill_batch_result(session, result)


def _backfill_batch_result_for_skip(
    items: list[EmbeddingBackfillItem],
    *,
    skip_reason: str,
) -> EmbeddingBackfillBatchResult:
    writes = tuple(
        EmbeddingBackfillBatchWrite(
            item=item,
            status="skipped_over_budget",
            vector=None,
            skip_reason=skip_reason[:500],
        )
        for item in items
    )
    return EmbeddingBackfillBatchResult(
        selected=len(items),
        embedded=0,
        skipped_over_budget=len(items),
        writes=writes,
    )


def _prepare_backfill_batch_result(
    items: list[EmbeddingBackfillItem],
    *,
    skip_reason: str | None = None,
) -> EmbeddingBackfillBatchResult:
    if not items:
        return EmbeddingBackfillBatchResult(selected=0, embedded=0, skipped_over_budget=0, writes=())
    if skip_reason is not None:
        return _backfill_batch_result_for_skip(items, skip_reason=skip_reason)

    try:
        vectors = embed_texts([item.text for item in items])
    except EmbeddingOverBudgetError as exc:
        if len(items) <= 1:
            return _backfill_batch_result_for_skip(items, skip_reason=str(exc) or "embedding input too long")
        midpoint = max(1, len(items) // 2)
        left = _prepare_backfill_batch_result(items[:midpoint])
        right = _prepare_backfill_batch_result(items[midpoint:])
        return EmbeddingBackfillBatchResult(
            selected=left.selected + right.selected,
            embedded=left.embedded + right.embedded,
            skipped_over_budget=left.skipped_over_budget + right.skipped_over_budget,
            writes=left.writes + right.writes,
        )

    writes = tuple(
        EmbeddingBackfillBatchWrite(
            item=item,
            status="ready",
            vector=vector,
            skip_reason=None,
        )
        for item, vector in zip(items, vectors)
    )
    return EmbeddingBackfillBatchResult(
        selected=len(items),
        embedded=len(items),
        skipped_over_budget=0,
        writes=writes,
    )


def _apply_backfill_batch_result(session: Session, result: EmbeddingBackfillBatchResult) -> EmbeddingBackfillStats:
    for write in result.writes:
        _upsert_video_embedding(
            session,
            video_id=write.item.video_id,
            text_checksum_value=write.item.checksum,
            status=write.status,
            vector=write.vector,
            skip_reason=write.skip_reason,
        )
    return EmbeddingBackfillStats(
        selected=result.selected,
        embedded=result.embedded,
        skipped_over_budget=result.skipped_over_budget,
    )


def _embed_backfill_batch(session: Session, items: list[EmbeddingBackfillItem]) -> EmbeddingBackfillStats:
    return _write_backfill_batch(session, items)


def backfill_playlist_embeddings(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    force: bool = False,
    batch_size: int | None = None,
    job: Job | None = None,
) -> dict[str, Any]:
    batch_size_value = _embedding_batch_size(batch_size)
    batch_max_chars = _embedding_batch_max_chars()
    prefetch_workers = _embedding_transcript_prefetch_workers()
    http_inflight = _embedding_backfill_http_inflight()

    previous_result = job.result if job is not None and isinstance(job.result, dict) else {}
    if job is not None:
        job.progress_total = None
        job.progress_current = 0
        session.flush([job])

    scanned = 0
    candidate_selected = 0
    transcript_loaded = 0
    prepared = 0
    selected = int(previous_result.get("selected") or 0)
    embedded = int(previous_result.get("embedded") or 0)
    skipped_empty = 0
    skipped_over_budget = int(previous_result.get("skipped_over_budget") or 0)
    embedding_batches = int(previous_result.get("embedding_batches") or 0)
    remote_errors = int(previous_result.get("remote_errors") or 0)
    last_committed_video_id = str(previous_result.get("last_committed_video_id") or "").strip() or None
    batch: list[EmbeddingBackfillItem] = []
    batch_chars = 0
    transcript_pending: set[Future[EmbeddingBackfillFetchResult]] = set()
    embedding_pending: set[Future[EmbeddingBackfillBatchResult]] = set()

    def result_payload(*, ok: bool, partial: bool = False) -> dict[str, Any]:
        return {
            "ok": ok,
            "playlist_id": str(playlist_id),
            "scanned": scanned,
            "candidate_selected": candidate_selected,
            "transcript_loaded": transcript_loaded,
            "prepared": prepared + len(batch),
            "selected": selected,
            "embedded": embedded,
            "skipped_empty": skipped_empty,
            "skipped_over_budget": skipped_over_budget,
            "embedding_batches": embedding_batches,
            "remote_errors": remote_errors,
            "last_committed_video_id": last_committed_video_id,
            "batch_size": batch_size_value,
            "batch_max_chars": batch_max_chars,
            "prefetch_workers": prefetch_workers,
            "http_inflight": http_inflight,
            "force": bool(force),
            "partial": partial,
        }

    def persist_progress(*, include_batch: bool = True, commit: bool = False) -> None:
        if job is None:
            return
        job.progress_current = scanned
        job.progress_total = None
        if include_batch:
            job.result = result_payload(ok=False, partial=True)
        session.flush([job])
        if commit:
            session.commit()
        raise_if_job_cancel_requested(session, job)

    def apply_embedding_result(result: EmbeddingBackfillBatchResult) -> None:
        nonlocal selected, embedded, skipped_over_budget, embedding_batches, last_committed_video_id
        if not result.writes:
            return
        batch_stats = _apply_backfill_batch_result(session, result)
        selected += batch_stats.selected
        embedded += batch_stats.embedded
        skipped_over_budget += batch_stats.skipped_over_budget
        embedding_batches += 1
        last_committed_video_id = str(result.writes[-1].item.video_id)
        if batch_stats.embedded or batch_stats.skipped_over_budget:
            mark_playlist_analysis_dirty(session, playlist_id)
        persist_progress(commit=True)

    def drain_embedding(*, wait_for_one: bool = True) -> None:
        nonlocal embedding_pending, remote_errors
        if not embedding_pending:
            return
        return_when = FIRST_COMPLETED if wait_for_one else ALL_COMPLETED
        done, remaining = wait(embedding_pending, return_when=return_when)
        embedding_pending = set(remaining)
        for future in done:
            try:
                apply_embedding_result(future.result())
            except EmbeddingTransientError:
                remote_errors += 1
                persist_progress(commit=True)
                raise

    def submit_batch(embedding_executor: ThreadPoolExecutor) -> None:
        nonlocal batch, batch_chars, prepared
        if not batch:
            return
        while len(embedding_pending) >= http_inflight:
            drain_embedding(wait_for_one=True)
        items = batch
        batch = []
        batch_chars = 0
        prepared += len(items)
        persist_progress(commit=True)
        embedding_pending.add(embedding_executor.submit(_prepare_backfill_batch_result, items))

    def add_to_batch(item: EmbeddingBackfillItem, embedding_executor: ThreadPoolExecutor) -> None:
        nonlocal batch_chars
        item_chars = len(item.text or "")
        if batch and (len(batch) >= batch_size_value or batch_chars + item_chars > batch_max_chars):
            submit_batch(embedding_executor)
        batch.append(item)
        batch_chars += item_chars
        if len(batch) >= batch_size_value or batch_chars >= batch_max_chars:
            submit_batch(embedding_executor)

    def handle_fetch_result(result: EmbeddingBackfillFetchResult, embedding_executor: ThreadPoolExecutor) -> None:
        nonlocal skipped_empty, transcript_loaded
        if result.skipped_empty:
            skipped_empty += 1
        if result.item is None:
            return
        transcript_loaded += 1
        add_to_batch(result.item, embedding_executor)

    def drain_transcripts(embedding_executor: ThreadPoolExecutor) -> None:
        nonlocal transcript_pending
        if not transcript_pending:
            return
        done, remaining = wait(transcript_pending, return_when=FIRST_COMPLETED)
        transcript_pending = set(remaining)
        for future in done:
            handle_fetch_result(future.result(), embedding_executor)

    with ThreadPoolExecutor(max_workers=prefetch_workers) as transcript_executor:
        with ThreadPoolExecutor(max_workers=http_inflight) as embedding_executor:
            for candidate in _iter_playlist_backfill_candidates(session, playlist_id, force=bool(force)):
                scanned += 1
                candidate_selected += 1
                if job is not None:
                    raise_if_job_cancel_requested(session, job)
                transcript_pending.add(
                    transcript_executor.submit(_read_backfill_transcript_item, candidate, force=bool(force))
                )
                while len(transcript_pending) >= prefetch_workers:
                    drain_transcripts(embedding_executor)
                if scanned % max(100, batch_size_value) == 0:
                    persist_progress(commit=True)

            while transcript_pending:
                drain_transcripts(embedding_executor)

            submit_batch(embedding_executor)
            while embedding_pending:
                drain_embedding(wait_for_one=True)

    if job is not None:
        job.result = result_payload(ok=True)
        session.flush([job])
    return result_payload(ok=True)
