from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from raelyn.models import Video, VideoTimeEvidence

TIME_BASIS_VALUES = {"content", "platform"}
CONTENT_PUBLISHED_AT_ROLE = "content_published_at"
CONTENT_TIME_CANDIDATE_MIN_CONFIDENCE = 0.8
TRUSTED_CONTENT_TIME_CANDIDATE_SOURCES = (
    "codex_batch_publish_time_inference",
    "external_title_search",
)

TimeBasis = Literal["content", "platform"]


@dataclass(frozen=True)
class ResolvedVideoTimeline:
    platform_published_at: datetime | None
    content_published_at: datetime | None
    timeline_at: datetime | None
    time_source: str | None
    time_status: str | None
    time_confidence: float | None


def normalize_time_basis(value: str | None, *, default: TimeBasis = "content") -> TimeBasis:
    basis = str(value or default).strip().lower()
    if basis not in TIME_BASIS_VALUES:
        raise ValueError("time_basis must be one of: content, platform")
    return cast(TimeBasis, basis)


def _selected_content_time_filter(video_model: Any = Video):
    full_date = and_(
        VideoTimeEvidence.date_year.is_not(None),
        VideoTimeEvidence.date_month.is_not(None),
        VideoTimeEvidence.date_day.is_not(None),
        VideoTimeEvidence.precision.in_(["day", "second"]),
    )
    adopted = or_(
        VideoTimeEvidence.status == "accepted",
        and_(
            VideoTimeEvidence.status == "candidate",
            VideoTimeEvidence.source.in_(list(TRUSTED_CONTENT_TIME_CANDIDATE_SOURCES)),
            VideoTimeEvidence.confidence >= CONTENT_TIME_CANDIDATE_MIN_CONFIDENCE,
        ),
    )
    return and_(
        VideoTimeEvidence.video_id == video_model.id,
        VideoTimeEvidence.time_role == CONTENT_PUBLISHED_AT_ROLE,
        full_date,
        adopted,
    )


def _selected_content_time_ordering():
    return (
        case((VideoTimeEvidence.status == "accepted", 0), else_=1).asc(),
        VideoTimeEvidence.confidence.desc().nullslast(),
        VideoTimeEvidence.updated_at.desc().nullslast(),
        VideoTimeEvidence.id.desc(),
    )


def selected_content_time_field_expr(field: Any, video_model: Any = Video):
    return (
        select(field)
        .where(_selected_content_time_filter(video_model))
        .order_by(*_selected_content_time_ordering())
        .limit(1)
        .correlate(video_model)
        .scalar_subquery()
    )


def content_published_at_expr(video_model: Any = Video):
    selected_date = func.make_timestamptz(
        VideoTimeEvidence.date_year,
        VideoTimeEvidence.date_month,
        VideoTimeEvidence.date_day,
        0,
        0,
        0,
        literal("UTC"),
    )
    return selected_content_time_field_expr(selected_date, video_model)


def timeline_time_expr(video_model: Any = Video, *, time_basis: str | None = "content"):
    basis = normalize_time_basis(time_basis)
    if basis == "platform":
        return video_model.published_at
    return func.coalesce(content_published_at_expr(video_model), video_model.published_at)


def timeline_source_expr(video_model: Any = Video, *, time_basis: str | None = "content"):
    basis = normalize_time_basis(time_basis)
    if basis == "platform":
        return case((video_model.published_at.is_(None), None), else_=literal("video.published_at"))
    fallback_source = func.coalesce(
        selected_content_time_field_expr(VideoTimeEvidence.source, video_model),
        literal("video.published_at"),
    )
    return case((timeline_time_expr(video_model, time_basis=basis).is_(None), None), else_=fallback_source)


def timeline_status_expr(video_model: Any = Video, *, time_basis: str | None = "content"):
    basis = normalize_time_basis(time_basis)
    if basis == "platform":
        return case((video_model.published_at.is_(None), None), else_=literal("platform"))
    fallback_status = func.coalesce(
        selected_content_time_field_expr(VideoTimeEvidence.status, video_model),
        literal("platform_fallback"),
    )
    return case((timeline_time_expr(video_model, time_basis=basis).is_(None), None), else_=fallback_status)


def timeline_confidence_expr(video_model: Any = Video, *, time_basis: str | None = "content"):
    basis = normalize_time_basis(time_basis)
    if basis == "platform":
        return literal(None)
    return selected_content_time_field_expr(VideoTimeEvidence.confidence, video_model)


def resolve_video_timeline(session: Session, video: Video) -> ResolvedVideoTimeline:
    row = (
        session.execute(
            select(
                VideoTimeEvidence.date_year,
                VideoTimeEvidence.date_month,
                VideoTimeEvidence.date_day,
                VideoTimeEvidence.source,
                VideoTimeEvidence.status,
                VideoTimeEvidence.confidence,
            )
            .where(_selected_content_time_filter(video))
            .order_by(*_selected_content_time_ordering())
            .limit(1)
        )
        .one_or_none()
    )
    content_published_at = None
    time_source = "video.published_at"
    time_status = "platform_fallback"
    time_confidence = None
    if row:
        year, month, day, source, status, confidence = row
        content_published_at = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
        time_source = str(source or "")
        time_status = str(status or "")
        time_confidence = float(confidence) if confidence is not None else None

    return ResolvedVideoTimeline(
        platform_published_at=video.published_at,
        content_published_at=content_published_at,
        timeline_at=content_published_at or video.published_at,
        time_source=time_source if content_published_at else ("video.published_at" if video.published_at else None),
        time_status=time_status if content_published_at else ("platform_fallback" if video.published_at else None),
        time_confidence=time_confidence if content_published_at else None,
    )
