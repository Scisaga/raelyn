from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from raelyn.timeutil import utcnow


class Base(DeclarativeBase):
    pass


class Media(Base):
    __tablename__ = "media"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_media_id: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    name: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    sync_cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    last_profile_sync_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_video_sync_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    videos: Mapped[list["Video"]] = relationship(back_populates="media", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("provider", "provider_media_id", name="media_provider_media_id_ux"),)


class Video(Base):
    __tablename__ = "video"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_video_id: Mapped[str] = mapped_column(String, nullable=False)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)

    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String, nullable=True)
    published_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    like_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    raw_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String, nullable=False, default="discovered")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    media: Mapped["Media"] = relationship(back_populates="videos")
    assets: Mapped[list["Asset"]] = relationship(back_populates="video", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("provider", "provider_video_id", name="video_provider_video_id_ux"),)


class Asset(Base):
    __tablename__ = "asset"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video.id", ondelete="CASCADE"), nullable=True)

    type: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    variant: Mapped[str | None] = mapped_column(String, nullable=True)

    s3_bucket: Mapped[str] = mapped_column(String, nullable=False)
    s3_key: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    # "metadata" is a reserved attribute name on Declarative models; keep the DB column name.
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    video: Mapped["Video"] = relationship(back_populates="assets")

    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "type",
            "format",
            "language",
            "source",
            "variant",
            name="asset_ux",
        ),
    )


class Playlist(Base):
    __tablename__ = "playlist"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    background_s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id"), nullable=True)
    background_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id"), nullable=True)
    brief_granularity: Mapped[str] = mapped_column(String, nullable=False, default="day")
    brief_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    media: Mapped[list["PlaylistMedia"]] = relationship(back_populates="playlist", cascade="all, delete-orphan")


class PlaylistMedia(Base):
    __tablename__ = "playlist_media"

    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlist.id", ondelete="CASCADE"), primary_key=True)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    playlist: Mapped["Playlist"] = relationship(back_populates="media")
    media: Mapped["Media"] = relationship()


class VideoEmbedding(Base):
    __tablename__ = "video_embedding"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video.id", ondelete="CASCADE"), nullable=False)
    transcript_variant: Mapped[str] = mapped_column(String, nullable=False, default="plain")
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    vector: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    text_checksum: Mapped[str] = mapped_column(String, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    generated_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "transcript_variant",
            "embedding_model",
            "embedding_dim",
            name="video_embedding_ux",
        ),
    )


class PlaylistAnalysisRun(Base):
    __tablename__ = "playlist_analysis_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlist.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    analysis_clock: Mapped[str] = mapped_column(String, nullable=False, default="day")
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_variant: Mapped[str] = mapped_column(String, nullable=False, default="plain")
    video_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    video_embedded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    video_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    video_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class PlaylistAnalysisState(Base):
    __tablename__ = "playlist_analysis_state"

    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlist.id", ondelete="CASCADE"), primary_key=True)
    analysis_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_ready_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist_analysis_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_requested_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_built_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class PlaylistAnalysisPeriod(Base):
    __tablename__ = "playlist_analysis_period"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_date: Mapped[Any] = mapped_column(Date, nullable=False)
    video_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    centroid_vector: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("analysis_run_id", "period_date", name="playlist_analysis_period_ux"),)


class PlaylistAnalysisSignal(Base):
    __tablename__ = "playlist_analysis_signal"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    granularity: Mapped[str] = mapped_column(String, nullable=False)
    period_date: Mapped[Any] = mapped_column(Date, nullable=False)
    rolling_window: Mapped[int] = mapped_column(Integer, nullable=False)
    video_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ready_embedding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    centroid_vector: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_id: Mapped[str | None] = mapped_column(String, nullable=True)
    projection_method: Mapped[str | None] = mapped_column(String, nullable=True)
    projection_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_explained_variance_ratio: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    linked_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist_analysis_candidate.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "granularity",
            "period_date",
            "rolling_window",
            name="playlist_analysis_signal_ux",
        ),
    )


class PlaylistAnalysisCandidate(Base):
    __tablename__ = "playlist_analysis_candidate"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_date: Mapped[Any] = mapped_column(Date, nullable=False)
    effective_trade_date: Mapped[Any] = mapped_column(Date, nullable=False)
    peak_date: Mapped[Any | None] = mapped_column(Date, nullable=True)
    event_start: Mapped[Any | None] = mapped_column(Date, nullable=True)
    event_end: Mapped[Any | None] = mapped_column(Date, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, default="burst")
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_terms: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    evidence_video_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    available_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    train_start: Mapped[Any | None] = mapped_column(Date, nullable=True)
    train_end: Mapped[Any | None] = mapped_column(Date, nullable=True)
    valid_start: Mapped[Any | None] = mapped_column(Date, nullable=True)
    valid_end: Mapped[Any | None] = mapped_column(Date, nullable=True)
    test_start: Mapped[Any | None] = mapped_column(Date, nullable=True)
    test_end: Mapped[Any | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("analysis_run_id", "candidate_date", name="playlist_analysis_candidate_ux"),)


class DailyBrief(Base):
    __tablename__ = "daily_brief"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlist.id", ondelete="CASCADE"), nullable=False)
    brief_date: Mapped[Any] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    markdown_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id"), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("playlist_id", "brief_date", name="daily_brief_ux"),)


class Brief(Base):
    __tablename__ = "brief"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlist.id", ondelete="CASCADE"), nullable=False)
    granularity: Mapped[str] = mapped_column(String, nullable=False, default="day")
    period_start: Mapped[Any] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    markdown_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id"), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("playlist_id", "granularity", "period_start", name="brief_ux"),)


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    progress_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stack: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    scheduled_for: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)

    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("job.id", ondelete="SET NULL"), nullable=True)


class JobEvent(Base):
    __tablename__ = "job_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"

    # worker_id is formatted as "{hostname}:{pid}:{nonce}" (see raelyn.worker._worker_id).
    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
