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
    time_evidence: Mapped[list["VideoTimeEvidence"]] = relationship(back_populates="video", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("provider", "provider_video_id", name="video_provider_video_id_ux"),)


class VideoTimeEvidence(Base):
    __tablename__ = "video_time_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video.id", ondelete="CASCADE"), nullable=False)

    time_role: Mapped[str] = mapped_column(String, nullable=False, default="content_published_at")
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_version: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_key: Mapped[str] = mapped_column(String, nullable=False, default="")

    date_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_start: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_end: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    precision: Mapped[str] = mapped_column(String, nullable=False, default="unknown")

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reliability_flags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    video: Mapped["Video"] = relationship(back_populates="time_evidence")

    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "time_role",
            "source",
            "evidence_key",
            name="video_time_evidence_source_ux",
        ),
    )


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


class MarketEvent(Base):
    __tablename__ = "market_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_time_start: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time_end: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_precision: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    available_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, default="other")
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[str | None] = mapped_column(String, nullable=True)
    magnitude: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    surprise_or_delta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    source_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video.id", ondelete="CASCADE"), nullable=False)
    transcript_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id", ondelete="SET NULL"), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    source_hash: Mapped[str] = mapped_column(String, nullable=False, default="")
    event_key: Mapped[str] = mapped_column(String, nullable=False, default="")
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_video_id",
            "source_hash",
            "prompt_version",
            "event_key",
            name="market_event_source_event_ux",
        ),
    )


class MarketEventEvidence(Base):
    __tablename__ = "market_event_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("market_event.id", ondelete="CASCADE"), nullable=False)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video.id", ondelete="CASCADE"), nullable=False)
    transcript_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id", ondelete="SET NULL"), nullable=True)
    evidence_key: Mapped[str] = mapped_column(String, nullable=False, default="")
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("event_id", "video_id", "evidence_key", name="market_event_evidence_ux"),)


class MarketEventEntity(Base):
    __tablename__ = "market_event_entity"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("market_event.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String, nullable=False, default="")
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("event_id", "entity_type", "normalized_key", "role", name="market_event_entity_ux"),
    )


class MarketEventRelation(Base):
    __tablename__ = "market_event_relation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("market_event.id", ondelete="CASCADE"), nullable=False)
    source_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("market_event_entity.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("market_event_entity.id", ondelete="SET NULL"),
        nullable=True,
    )
    relation_type: Mapped[str] = mapped_column(String, nullable=False, default="mentions")
    direction: Mapped[str | None] = mapped_column(String, nullable=True)
    magnitude: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class MarketEventEmbedding(Base):
    __tablename__ = "market_event_embedding"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("market_event.id", ondelete="CASCADE"), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    vector: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    text_checksum: Mapped[str] = mapped_column(String, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    generated_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("event_id", "embedding_model", "embedding_dim", name="market_event_embedding_ux"),)


class VideoEventExtractionRun(Base):
    __tablename__ = "video_event_extraction_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video.id", ondelete="CASCADE"), nullable=False)
    transcript_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id", ondelete="SET NULL"), nullable=True)
    source_hash: Mapped[str] = mapped_column(String, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="succeeded")
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "source_hash",
            "prompt_version",
            "extraction_model",
            name="video_event_extraction_run_ux",
        ),
    )


class EventRegimeRun(Base):
    __tablename__ = "event_regime_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlist.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    analysis_clock: Mapped[str] = mapped_column(String, nullable=False, default="day")
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    event_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_embedded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class EventRegimeState(Base):
    __tablename__ = "event_regime_state"

    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlist.id", ondelete="CASCADE"), primary_key=True)
    analysis_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_ready_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_regime_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_requested_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_built_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class EventRegimeSignal(Base):
    __tablename__ = "event_regime_signal"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    regime_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_regime_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    granularity: Mapped[str] = mapped_column(String, nullable=False)
    period_date: Mapped[Any] = mapped_column(Date, nullable=False)
    rolling_window: Mapped[int] = mapped_column(Integer, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
    linked_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_regime_candidate.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "regime_run_id",
            "granularity",
            "period_date",
            "rolling_window",
            name="event_regime_signal_ux",
        ),
    )


class EventRegimeCandidate(Base):
    __tablename__ = "event_regime_candidate"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    regime_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_regime_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_date: Mapped[Any] = mapped_column(Date, nullable=False)
    effective_trade_date: Mapped[Any] = mapped_column(Date, nullable=False)
    peak_date: Mapped[Any | None] = mapped_column(Date, nullable=True)
    event_start: Mapped[Any | None] = mapped_column(Date, nullable=True)
    event_end: Mapped[Any | None] = mapped_column(Date, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, default="regime_shift")
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    dispersion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_rolling_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_terms: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    evidence_event_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
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

    __table_args__ = (UniqueConstraint("regime_run_id", "candidate_date", name="event_regime_candidate_ux"),)


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
    active_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
