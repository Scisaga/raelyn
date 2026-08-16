from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
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


class EventMapState(Base):
    __tablename__ = "event_map_state"

    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="SET NULL"),
        nullable=True,
    )
    dirty_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    built_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    active_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job.id", ondelete="SET NULL"),
        nullable=True,
    )
    first_dirty_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dirty_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_requested_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_built_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class EventMapSnapshot(Base):
    __tablename__ = "event_map_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    parent_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="SET NULL"),
        nullable=True,
    )
    input_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    input_fingerprint: Mapped[str] = mapped_column(String, nullable=False, default="")
    build_key: Mapped[str] = mapped_column(String, nullable=False, default="")
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    canonical_algorithm_version: Mapped[str] = mapped_column(String, nullable=False, default="canonical-v1")
    story_algorithm_version: Mapped[str] = mapped_column(String, nullable=False, default="story-v1")
    topic_algorithm_version: Mapped[str] = mapped_column(String, nullable=False, default="topic-v1")
    layout_algorithm_version: Mapped[str] = mapped_column(String, nullable=False, default="layout-v1")
    projection_method: Mapped[str | None] = mapped_column(String, nullable=True)
    projection_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    layout_continuity: Mapped[str] = mapped_column(String, nullable=False, default="rebased")
    alignment_transform: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    alignment_residual: Mapped[float | None] = mapped_column(Float, nullable=True)
    bounds: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    type_categories: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    monthly_distribution: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    input_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    canonical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    story_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_reason_counts: Mapped[dict[str, int] | None] = mapped_column(JSONB, nullable=True)
    peak_rss_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    temp_disk_peak_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "execution_token", name="event_map_snapshot_job_execution_ux"),
        Index("event_map_snapshot_parent_idx", "parent_snapshot_id"),
        Index(
            "event_map_snapshot_ready_build_ux",
            "playlist_id",
            "build_key",
            unique=True,
            postgresql_where=text("status = 'ready' and build_key <> ''"),
            sqlite_where=text("status = 'ready' and build_key <> ''"),
        ),
    )


class EventMapRecordRevision(Base):
    __tablename__ = "event_map_record_revision"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("market_event.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video.id", ondelete="SET NULL"),
        nullable=True,
    )
    embedding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("market_event_embedding.id", ondelete="SET NULL"),
        nullable=True,
    )
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    embedding_checksum: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_time_start: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    event_time_end: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    time_precision: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str | None] = mapped_column(String, nullable=True)
    entities_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    source_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "content_hash",
            "embedding_checksum",
            name="event_map_record_revision_content_ux",
        ),
    )


class EventMapCanonicalIdentity(Base):
    __tablename__ = "event_map_canonical_identity"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="SET NULL"),
        nullable=True,
    )
    retired_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        Index("event_map_canonical_identity_created_snapshot_idx", "created_snapshot_id"),
        Index("event_map_canonical_identity_retired_snapshot_idx", "retired_snapshot_id"),
    )


class DomainObservationCursor(Base):
    """单用户在一个观测域中的持久观察位置。"""

    __tablename__ = "domain_observation_cursor"

    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        primary_key=True,
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    observed_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time_start: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time_end: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_mode: Mapped[str] = mapped_column(String, nullable=False, default="now")
    last_page: Mapped[str] = mapped_column(String, nullable=False, default="field")
    camera_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    filter_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    selected_object_type: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_change_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class EventMapChange(Base):
    """独立于快照保留周期的、可重复读取的语义对象变化。"""

    __tablename__ = "event_map_change"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    before_revision: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_revision: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence_revision_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "to_snapshot_id",
            "object_type",
            "object_id",
            "change_type",
            name="event_map_change_snapshot_object_type_ux",
        ),
        Index("event_map_change_feed_idx", "playlist_id", "observed_at", "id"),
        Index("event_map_change_object_idx", "playlist_id", "object_type", "object_id", "observed_at"),
    )


class EventMapCanonicalHistoryRevision(Base):
    """可长期保留的 canonical 修订，用于快照被裁剪后的历史解释。"""

    __tablename__ = "event_map_canonical_history_revision"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_canonical_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "canonical_id", name="event_map_canonical_history_snapshot_ux"),
        Index("event_map_canonical_history_object_idx", "playlist_id", "canonical_id", "observed_at"),
    )


class EventMapCanonicalHistoryMember(Base):
    """长期 canonical 修订与来源修订的正规化关系，避免反查时扫描 JSONB。"""

    __tablename__ = "event_map_canonical_history_member"

    history_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_canonical_history_revision.id", ondelete="CASCADE"),
        primary_key=True,
    )
    record_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_record_revision.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_canonical_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    is_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index(
            "event_map_canonical_history_member_record_idx",
            "playlist_id",
            "record_revision_id",
            "canonical_id",
        ),
        Index(
            "event_map_canonical_history_member_object_idx",
            "playlist_id",
            "canonical_id",
            "snapshot_id",
        ),
    )


class EventMapCanonical(Base):
    __tablename__ = "event_map_canonical"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="CASCADE"),
        primary_key=True,
    )
    canonical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_canonical_identity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    representative_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_record_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    event_time_start: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    event_time_end: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    time_precision: Mapped[str] = mapped_column(String, nullable=False)
    time_basis: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    event_start_day: Mapped[int] = mapped_column(Integer, nullable=False)
    event_end_day: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type_code: Mapped[int] = mapped_column(Integer, nullable=False)
    time_precision_code: Mapped[int] = mapped_column(Integer, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    identity_state: Mapped[str] = mapped_column(String, nullable=False, default="new")
    decision_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    runner_up_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    time_disagreement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    centroid_checksum: Mapped[str] = mapped_column(String, nullable=False)
    point_index: Mapped[int] = mapped_column(Integer, nullable=False)
    x: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    y: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    z: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    uncertainty_flags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    has_uncertainty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "point_index", name="event_map_canonical_point_index_ux"),
        Index("event_map_canonical_review_idx", "snapshot_id", "has_uncertainty", "event_start_day"),
    )


class EventMapEntityIndex(Base):
    __tablename__ = "event_map_entity_index"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String, primary_key=True)
    normalized_key: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    point_index: Mapped[int] = mapped_column(Integer, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "snapshot_id",
            "point_index",
            "entity_type",
            "normalized_key",
            name="event_map_entity_index_point_key_ux",
        ),
        CheckConstraint("record_count > 0", name="event_map_entity_index_record_count_ck"),
        Index(
            "event_map_entity_index_key_idx",
            "snapshot_id",
            "entity_type",
            "normalized_key",
            "point_index",
        ),
    )


class EventMapCanonicalMember(Base):
    __tablename__ = "event_map_canonical_member"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    record_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_record_revision.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    is_representative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assignment_kind: Mapped[str] = mapped_column(String, nullable=False, default="singleton")
    decision_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    runner_up_canonical_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    runner_up_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    runner_up_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_version: Mapped[str] = mapped_column(String, nullable=False, default="canonical-v1")
    reason_codes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
            ondelete="CASCADE",
        ),
    )


class EventMapCanonicalLineage(Base):
    __tablename__ = "event_map_canonical_lineage"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="CASCADE"),
        primary_key=True,
    )
    predecessor_canonical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_canonical_identity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    successor_canonical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_canonical_identity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relation_type: Mapped[str] = mapped_column(String, nullable=False, primary_key=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "predecessor_canonical_id <> successor_canonical_id or relation_type = 'retained'",
            name="event_map_canonical_lineage_distinct_ck",
        ),
    )


class EventMapTopic(Base):
    __tablename__ = "event_map_topic"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_topic_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    predecessor_topic_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    top_terms: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    centroid_vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    anchor_canonical_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    center_x: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    center_y: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    center_z: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    radius: Mapped[float] = mapped_column(Float(precision=24), nullable=False, default=0.0)
    stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    assignment_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "anchor_canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
        ),
        Index("event_map_topic_anchor_idx", "snapshot_id", "anchor_canonical_id"),
    )


class EventMapTopicMember(Base):
    __tablename__ = "event_map_topic_member"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["event_map_topic.snapshot_id", "event_map_topic.topic_id"],
            ondelete="CASCADE",
        ),
        Index(
            "event_map_topic_member_level_canonical_idx",
            "snapshot_id",
            "level",
            "canonical_id",
        ),
        Index(
            "event_map_topic_member_topic_idx",
            "snapshot_id",
            "topic_id",
            "level",
        ),
    )


class EventMapStoryIdentity(Base):
    __tablename__ = "event_map_story_identity"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    retired_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        Index("event_map_story_identity_playlist_idx", "playlist_id", "status", "created_at"),
    )


class EventMapStory(Base):
    __tablename__ = "event_map_story"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="CASCADE"),
        primary_key=True,
    )
    story_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_story_identity.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_type: Mapped[str] = mapped_column(String, nullable=False, default="sequence")
    event_time_start: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time_end: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canonical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "story_identity_id", name="event_map_story_snapshot_identity_ux"),
        Index("event_map_story_identity_idx", "story_identity_id", "snapshot_id"),
    )


class EventMapStoryMember(Base):
    __tablename__ = "event_map_story_member"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    story_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "story_id"],
            ["event_map_story.snapshot_id", "event_map_story.story_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("snapshot_id", "story_id", "canonical_id", name="event_map_story_member_canonical_ux"),
    )


class EventMapStoryEdge(Base):
    __tablename__ = "event_map_story_edge"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    edge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="automatic")
    evidence_revision_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    method_version: Mapped[str] = mapped_column(String, nullable=False, default="story-v1")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "story_id"],
            ["event_map_story.snapshot_id", "event_map_story.story_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "source_canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "target_canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "snapshot_id",
            "source_canonical_id",
            "target_canonical_id",
            "relation_type",
            name="event_map_story_edge_relation_ux",
        ),
        CheckConstraint("source_canonical_id <> target_canonical_id", name="event_map_story_edge_distinct_ck"),
        Index("event_map_story_edge_story_idx", "snapshot_id", "story_id"),
    )


class EventMapStoryHistoryRevision(Base):
    """故事稳定身份的长期修订档案，不受大快照裁剪影响。"""

    __tablename__ = "event_map_story_history_revision"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    story_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_story_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    story_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_type: Mapped[str] = mapped_column(String, nullable=False, default="sequence")
    event_time_start: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time_end: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    member_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_revision_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    method_version: Mapped[str] = mapped_column(String, nullable=False)
    observed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "story_identity_id", name="event_map_story_history_snapshot_ux"),
        Index("event_map_story_history_object_idx", "playlist_id", "story_identity_id", "observed_at"),
    )


class EventMapStoryHistoryEvidence(Base):
    """长期故事边到来源修订的正规化关系，支持来源记录反查故事。"""

    __tablename__ = "event_map_story_history_evidence"

    history_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_story_history_revision.id", ondelete="CASCADE"),
        primary_key=True,
    )
    edge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    record_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_record_revision.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    story_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_story_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index(
            "event_map_story_history_evidence_record_idx",
            "playlist_id",
            "record_revision_id",
            "story_identity_id",
        ),
        Index(
            "event_map_story_history_evidence_story_idx",
            "playlist_id",
            "story_identity_id",
            "snapshot_id",
        ),
    )


class StoryReadState(Base):
    __tablename__ = "story_read_state"

    story_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_story_identity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    followed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_read_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_read_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class EventMapProjectionAnchor(Base):
    __tablename__ = "event_map_projection_anchor"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_snapshot.id", ondelete="CASCADE"),
        primary_key=True,
    )
    anchor_rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    x: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    y: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    z: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    centroid_vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    vector_checksum: Mapped[str] = mapped_column(String, nullable=False)
    inherited_parent_anchor_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "canonical_id"],
            ["event_map_canonical.snapshot_id", "event_map_canonical.canonical_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("snapshot_id", "canonical_id", name="event_map_projection_anchor_canonical_ux"),
    )


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
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    generation_basis: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    markdown_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id"), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("playlist_id", "granularity", "period_start", name="brief_ux"),)


class BriefReference(Base):
    __tablename__ = "brief_reference"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brief_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brief.id", ondelete="CASCADE"),
        nullable=False,
    )
    anchor: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_time_start: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_time_end: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_map_record_revision.id", ondelete="SET NULL"),
        nullable=True,
    )
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("brief_id", "anchor", name="brief_reference_anchor_ux"),
        UniqueConstraint("brief_id", "position", name="brief_reference_position_ux"),
        Index("brief_reference_object_idx", "object_type", "object_id", "brief_id"),
        Index("brief_reference_evidence_idx", "evidence_revision_id", "brief_id"),
    )


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
    execution_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

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
