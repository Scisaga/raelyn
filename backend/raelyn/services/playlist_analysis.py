from __future__ import annotations

import hashlib
import math
import re
import uuid
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
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
MONTH_BOUNDARY_WINDOW = 2
WEEK_BOUNDARY_WINDOW = 4
MONTH_BOUNDARY_Z_THRESHOLD = 2.0
WEEK_BOUNDARY_Z_THRESHOLD = 1.5
MONTH_CANDIDATE_MIN_GAP = 3


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


def _checkpoint_analysis_task(session: Session, job: Job | None) -> None:
    ensure_analysis_resource_budget()
    if job is not None:
        raise_if_job_cancel_requested(session, job)


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
            Job.status == "pending",
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
    counts: dict[str, int] = {}
    for item in evidence.get("videos") or []:
        title = str(item.get("title") or "").lower()
        for token in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", title):
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

        timezone = _analysis_timezone()
        batch_size = _analysis_stream_batch_size()
        vector_sums: dict[tuple[str, date], list[float]] = {}
        signal_counts: dict[tuple[str, date], int] = defaultdict(int)

        for batch in _iter_playlist_ready_embedding_batches(session, playlist_id, batch_size=batch_size):
            _checkpoint_analysis_task(session, job)
            for item in batch:
                day_value = _local_analysis_day(item, timezone)
                normalized = _normalize_vector(item.vector)
                for granularity in SIGNAL_GRANULARITY_WINDOWS:
                    signal_key = (granularity, period_start(day_value, granularity))
                    if signal_key not in vector_sums:
                        vector_sums[signal_key] = [0.0] * len(normalized)
                    for index, value in enumerate(normalized):
                        vector_sums[signal_key][index] += float(value)
                    signal_counts[signal_key] += 1
            _checkpoint_analysis_task(session, job)

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
        dispersion_values: dict[tuple[str, date], list[float]] = defaultdict(list)
        for batch in _iter_playlist_ready_embedding_batches(session, playlist_id, batch_size=batch_size):
            _checkpoint_analysis_task(session, job)
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

        period_payloads = signals_by_granularity["day"]
        candidate_detections = _select_candidate_detections(signals_by_granularity)

        candidate_by_date = {detection.candidate_date: detection for detection in candidate_detections}
        candidate_evidence: dict[date, list[dict[str, Any]]] = {detection.candidate_date: [] for detection in candidate_detections}
        day_to_candidate_dates: dict[date, list[date]] = defaultdict(list)
        for detection in candidate_detections:
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
                        detection = candidate_by_date[candidate_day]
                        _rank_candidate_evidence(
                            candidate_evidence[candidate_day],
                            item,
                            centroid=detection.boundary.after_centroid,
                            previous_centroid=detection.boundary.before_centroid,
                        )
                _checkpoint_analysis_task(session, job)

        session.execute(delete(PlaylistAnalysisPeriod).where(PlaylistAnalysisPeriod.analysis_run_id == run.id))
        session.execute(delete(PlaylistAnalysisSignal).where(PlaylistAnalysisSignal.analysis_run_id == run.id))
        session.execute(delete(PlaylistAnalysisCandidate).where(PlaylistAnalysisCandidate.analysis_run_id == run.id))

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

        session.flush(candidate_models)
        for candidate_model, keys in candidate_signal_keys.items():
            for signal_key in keys:
                signal_model = signal_model_by_key.get(signal_key)
                if signal_model is not None:
                    signal_model.linked_event_id = candidate_model.id

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
