from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from raelyn.models import (
    Asset,
    Brief,
    BriefReference,
    DomainObservationCursor,
    EventMapCanonical,
    EventMapCanonicalHistoryMember,
    EventMapCanonicalHistoryRevision,
    EventMapCanonicalIdentity,
    EventMapCanonicalLineage,
    EventMapChange,
    EventMapEntityIndex,
    EventMapRecordRevision,
    EventMapSnapshot,
    EventMapState,
    EventMapStory,
    EventMapStoryHistoryEvidence,
    EventMapStoryHistoryRevision,
    EventMapStoryIdentity,
    EventMapStoryMember,
    EventMapStoryEdge,
    EventMapTopic,
    EventMapTopicMember,
    MarketEvent,
    MarketEventEvidence,
    Media,
    Playlist,
    PlaylistMedia,
    StoryReadState,
    Video,
)
from raelyn.services.transcripts import pick_transcript_asset
from raelyn.timeutil import utcnow


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _web_url(
    playlist_id: uuid.UUID,
    *,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    snapshot_id: uuid.UUID | None = None,
) -> str:
    params = [f"domain_id={playlist_id}"]
    if object_type and object_id:
        params.append(f"{object_type}_id={object_id}")
    if snapshot_id:
        params.append(f"snapshot_id={snapshot_id}")
    return f"/field?{'&'.join(params)}"


def current_snapshot(session: Session, playlist_id: uuid.UUID) -> EventMapSnapshot | None:
    state = session.get(EventMapState, playlist_id)
    if state is None or state.current_snapshot_id is None:
        return None
    snapshot = session.get(EventMapSnapshot, state.current_snapshot_id)
    return snapshot if snapshot is not None and snapshot.status == "ready" else None


def observation_snapshot(
    session: Session,
    playlist_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
) -> EventMapSnapshot | None:
    """解析观测快照；显式指定的历史快照绝不静默回退到 current。"""
    if snapshot_id is None:
        return current_snapshot(session, playlist_id)
    snapshot = session.get(EventMapSnapshot, snapshot_id)
    if snapshot is None or snapshot.playlist_id != playlist_id or snapshot.status != "ready":
        raise LookupError("domain snapshot not found")
    return snapshot


def domain_directory(session: Session) -> list[dict[str, Any]]:
    playlists = session.execute(select(Playlist).order_by(Playlist.updated_at.desc(), Playlist.id.asc())).scalars().all()
    output: list[dict[str, Any]] = []
    for playlist in playlists:
        snapshot = current_snapshot(session, playlist.id)
        cursor = session.get(DomainObservationCursor, playlist.id)
        media_count = int(
            session.execute(
                select(func.count()).select_from(PlaylistMedia).where(PlaylistMedia.playlist_id == playlist.id)
            ).scalar_one()
            or 0
        )
        change_count = 0
        if snapshot is not None:
            statement = select(func.count()).select_from(EventMapChange).where(
                EventMapChange.playlist_id == playlist.id
            )
            if cursor and cursor.observed_at:
                statement = statement.where(EventMapChange.observed_at > cursor.observed_at)
            change_count = int(session.execute(statement).scalar_one() or 0)
        preview_points: list[dict[str, Any]] = []
        if snapshot is not None and int(snapshot.canonical_count or 0) > 0:
            stride = max(1, int(snapshot.canonical_count or 0) // 96)
            preview_rows = session.execute(
                select(
                    EventMapCanonical.x,
                    EventMapCanonical.y,
                    EventMapCanonical.event_type_code,
                )
                .where(
                    EventMapCanonical.snapshot_id == snapshot.id,
                    EventMapCanonical.point_index % stride == 0,
                )
                .order_by(EventMapCanonical.point_index.asc())
                .limit(96)
            ).all()
            if preview_rows:
                xs = [float(row.x) for row in preview_rows]
                ys = [float(row.y) for row in preview_rows]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                span_x = max(max_x - min_x, 1e-6)
                span_y = max(max_y - min_y, 1e-6)
                preview_points = [
                    {
                        "x": round(4 + (float(row.x) - min_x) / span_x * 92, 3),
                        "y": round(4 + (float(row.y) - min_y) / span_y * 36, 3),
                        "event_type_code": int(row.event_type_code),
                    }
                    for row in preview_rows
                ]
        output.append(
            {
                "id": str(playlist.id),
                "name": playlist.name,
                "description": playlist.description,
                "media_count": media_count,
                "snapshot": snapshot_summary(snapshot),
                "preview_points": preview_points,
                "unobserved_change_count": change_count,
                "last_observed_at": _iso(cursor.observed_at) if cursor else None,
                "web_url": _web_url(playlist.id),
            }
        )
    return output


def snapshot_summary(snapshot: EventMapSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "id": str(snapshot.id),
        "parent_snapshot_id": str(snapshot.parent_snapshot_id) if snapshot.parent_snapshot_id else None,
        "status": snapshot.status,
        "observed_at": _iso(snapshot.finished_at),
        "layout_continuity": snapshot.layout_continuity,
        "event_count": int(snapshot.canonical_count or 0),
        "record_count": int(snapshot.member_count or 0),
        "story_count": int(snapshot.story_count or 0),
        "topic_count": int(snapshot.topic_count or 0),
        "entity_count": int(snapshot.entity_count or 0),
        "bounds": snapshot.bounds,
        "monthly_distribution": snapshot.monthly_distribution or [],
    }


def cursor_payload(cursor: DomainObservationCursor | None, playlist_id: uuid.UUID) -> dict[str, Any]:
    if cursor is None:
        return {
            "playlist_id": str(playlist_id),
            "snapshot_id": None,
            "observed_at": None,
            "event_time_start": None,
            "event_time_end": None,
            "view_mode": "now",
            "last_page": "field",
            "camera_state": None,
            "filter_state": None,
            "selected_object_type": None,
            "selected_object_id": None,
            "last_change_id": None,
        }
    return {
        "playlist_id": str(cursor.playlist_id),
        "snapshot_id": str(cursor.snapshot_id) if cursor.snapshot_id else None,
        "observed_at": _iso(cursor.observed_at),
        "event_time_start": _iso(cursor.event_time_start),
        "event_time_end": _iso(cursor.event_time_end),
        "view_mode": cursor.view_mode,
        "last_page": cursor.last_page,
        "camera_state": cursor.camera_state,
        "filter_state": cursor.filter_state,
        "selected_object_type": cursor.selected_object_type,
        "selected_object_id": str(cursor.selected_object_id) if cursor.selected_object_id else None,
        "last_change_id": str(cursor.last_change_id) if cursor.last_change_id else None,
        "updated_at": _iso(cursor.updated_at),
    }


def domain_observation(session: Session, playlist_id: uuid.UUID) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if playlist is None:
        raise LookupError("domain not found")
    snapshot = current_snapshot(session, playlist_id)
    cursor = session.get(DomainObservationCursor, playlist_id)
    observed_after = cursor.observed_at if cursor else None
    change_statement = select(func.count()).select_from(EventMapChange).where(
        EventMapChange.playlist_id == playlist_id
    )
    if observed_after:
        change_statement = change_statement.where(EventMapChange.observed_at > observed_after)
    unobserved = int(session.execute(change_statement).scalar_one() or 0)
    media_ids = select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)
    total_videos = int(
        session.execute(select(func.count(Video.id)).where(Video.media_id.in_(media_ids))).scalar_one() or 0
    )
    transcript_videos = int(
        session.execute(
            select(func.count(func.distinct(Asset.video_id))).where(
                Asset.video_id.in_(select(Video.id).where(Video.media_id.in_(media_ids))),
                Asset.type == "transcript",
                Asset.format == "txt",
            )
        ).scalar_one()
        or 0
    )
    event_videos = int(
        session.execute(
            select(func.count(func.distinct(MarketEvent.source_video_id)))
            .join(Video, Video.id == MarketEvent.source_video_id)
            .where(Video.media_id.in_(media_ids))
        ).scalar_one()
        or 0
    )
    total_events = int(
        session.execute(
            select(func.count(MarketEvent.id))
            .join(Video, Video.id == MarketEvent.source_video_id)
            .where(Video.media_id.in_(media_ids))
        ).scalar_one()
        or 0
    )
    verified_events = int(
        session.execute(
            select(func.count(func.distinct(MarketEventEvidence.event_id)))
            .join(MarketEvent, MarketEvent.id == MarketEventEvidence.event_id)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .where(
                Video.media_id.in_(media_ids),
                MarketEventEvidence.evidence_json["verified"].as_boolean().is_(True),
            )
        ).scalar_one()
        or 0
    )
    status_reasons = {
        str(status or "unknown"): int(count or 0)
        for status, count in session.execute(
            select(Video.status, func.count(Video.id))
            .where(Video.media_id.in_(media_ids))
            .group_by(Video.status)
        ).all()
    }
    skipped = dict(snapshot.skipped_reason_counts or {}) if snapshot else {"snapshot_not_ready": total_events}
    coverage = {
        "source_processing": {
            "numerator": transcript_videos,
            "denominator": total_videos,
            "failed_or_skipped": status_reasons,
        },
        "event_extraction": {
            "numerator": event_videos,
            "denominator": total_videos,
            "failed_or_skipped": {"without_event": max(0, total_videos - event_videos)},
        },
        "field_admission": {
            "numerator": int(snapshot.input_record_count or 0) if snapshot else 0,
            "denominator": total_events,
            "failed_or_skipped": skipped,
        },
        "evidence_verification": {
            "numerator": verified_events,
            "denominator": total_events,
            "failed_or_skipped": {"unverified_or_missing": max(0, total_events - verified_events)},
        },
    }
    return {
        "domain": {
            "id": str(playlist.id),
            "name": playlist.name,
            "description": playlist.description,
        },
        "snapshot": snapshot_summary(snapshot),
        "cursor": cursor_payload(cursor, playlist_id),
        "unobserved_change_count": unobserved,
        "coverage": coverage,
        "time_basis": {
            "field_playback": "event_occurrence",
            "changes": "system_observed_at",
            "baseline": "user_observation_cursor",
        },
        "web_url": _web_url(playlist_id),
    }


def update_observation_cursor(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    values: dict[str, Any],
) -> DomainObservationCursor:
    if session.get(Playlist, playlist_id) is None:
        raise LookupError("domain not found")
    snapshot_id = values.get("snapshot_id")
    if snapshot_id is not None:
        snapshot = session.get(EventMapSnapshot, snapshot_id)
        if snapshot is None or snapshot.playlist_id != playlist_id or snapshot.status != "ready":
            raise ValueError("snapshot does not belong to this domain or is not ready")
    cursor = session.get(DomainObservationCursor, playlist_id)
    if cursor is None:
        cursor = DomainObservationCursor(playlist_id=playlist_id)
        session.add(cursor)
    allowed = {
        "snapshot_id",
        "observed_at",
        "event_time_start",
        "event_time_end",
        "view_mode",
        "last_page",
        "camera_state",
        "filter_state",
        "selected_object_type",
        "selected_object_id",
        "last_change_id",
    }
    for key, value in values.items():
        if key in allowed:
            setattr(cursor, key, value)
    if cursor.observed_at is None and cursor.snapshot_id:
        snapshot = session.get(EventMapSnapshot, cursor.snapshot_id)
        cursor.observed_at = snapshot.finished_at if snapshot and snapshot.finished_at else utcnow()
    session.flush()
    return cursor


def change_payload(change: EventMapChange) -> dict[str, Any]:
    snapshot_context = change.to_snapshot_id if change.after_revision is not None else change.from_snapshot_id
    return {
        "id": str(change.id),
        "playlist_id": str(change.playlist_id),
        "from_snapshot_id": str(change.from_snapshot_id) if change.from_snapshot_id else None,
        "to_snapshot_id": str(change.to_snapshot_id),
        "object_type": change.object_type,
        "object_id": str(change.object_id),
        "change_type": change.change_type,
        "occurred_at": _iso(change.occurred_at),
        "observed_at": _iso(change.observed_at),
        "before_revision": change.before_revision,
        "after_revision": change.after_revision,
        "evidence_revision_ids": change.evidence_revision_ids or [],
        "time_basis": {
            "occurred_at": "event_occurrence" if change.occurred_at else None,
            "observed_at": "system_cognition",
        },
        "web_url": _web_url(
            change.playlist_id,
            object_type=change.object_type if change.object_type in {"canonical", "story"} else None,
            object_id=change.object_id,
            snapshot_id=snapshot_context,
        ),
    }


def list_changes(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    after_snapshot_id: uuid.UUID | None = None,
    object_type: str | None = None,
    change_type: str | None = None,
    cursor_id: uuid.UUID | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if session.get(Playlist, playlist_id) is None:
        raise LookupError("domain not found")
    statement = select(EventMapChange).where(EventMapChange.playlist_id == playlist_id)
    if after_snapshot_id:
        baseline = session.execute(
            select(EventMapSnapshot.finished_at).where(EventMapSnapshot.id == after_snapshot_id)
        ).scalar_one_or_none()
        if baseline is None:
            baseline = session.execute(
                select(func.min(EventMapChange.observed_at)).where(
                    EventMapChange.playlist_id == playlist_id,
                    EventMapChange.to_snapshot_id != after_snapshot_id,
                )
            ).scalar_one_or_none()
        if baseline:
            statement = statement.where(EventMapChange.observed_at > baseline)
    if object_type:
        statement = statement.where(EventMapChange.object_type == object_type)
    if change_type:
        statement = statement.where(EventMapChange.change_type == change_type)
    if cursor_id:
        cursor_row = session.get(EventMapChange, cursor_id)
        if cursor_row is None or cursor_row.playlist_id != playlist_id:
            raise ValueError("invalid change cursor")
        statement = statement.where(
            or_(
                EventMapChange.observed_at < cursor_row.observed_at,
                and_(EventMapChange.observed_at == cursor_row.observed_at, EventMapChange.id < cursor_row.id),
            )
        )
    rows = session.execute(
        statement.order_by(EventMapChange.observed_at.desc(), EventMapChange.id.desc()).limit(limit + 1)
    ).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [change_payload(row) for row in rows],
        "next_cursor": str(rows[-1].id) if has_more and rows else None,
        "time_basis": "system_cognition",
    }


def observation_feed(session: Session, playlist_id: uuid.UUID, *, limit: int = 30) -> dict[str, Any]:
    snapshot = current_snapshot(session, playlist_id)
    if session.get(Playlist, playlist_id) is None:
        raise LookupError("domain not found")
    cursor = session.get(DomainObservationCursor, playlist_id)
    observed_after = cursor.observed_at if cursor and cursor.observed_at else None
    event_after = cursor.event_time_end if cursor and cursor.event_time_end else None

    occurred: list[dict[str, Any]] = []
    if snapshot and event_after:
        rows = session.execute(
            select(EventMapCanonical)
            .where(
                EventMapCanonical.snapshot_id == snapshot.id,
                EventMapCanonical.event_time_start > event_after,
            )
            .order_by(EventMapCanonical.event_time_start.desc(), EventMapCanonical.canonical_id.desc())
            .limit(limit)
        ).scalars().all()
        occurred = [
            {
                "object_type": "canonical",
                "object_id": str(row.canonical_id),
                "title": row.title,
                "occurred_at": _iso(row.event_time_start),
                "observed_at": _iso(snapshot.finished_at),
                "time_basis": "event_occurrence",
                "web_url": _web_url(playlist_id, object_type="canonical", object_id=row.canonical_id, snapshot_id=snapshot.id),
            }
            for row in rows
        ]

    statement = select(EventMapChange).where(EventMapChange.playlist_id == playlist_id)
    if observed_after:
        statement = statement.where(EventMapChange.observed_at > observed_after)
    changes = session.execute(
        statement.order_by(EventMapChange.observed_at.desc(), EventMapChange.id.desc()).limit(limit * 4)
    ).scalars().all()
    mapped = [change_payload(row) for row in changes if row.change_type.startswith("canonical_")][:limit]
    stories = [change_payload(row) for row in changes if row.object_type == "story"][:limit]

    review: list[dict[str, Any]] = []
    if snapshot:
        rows = session.execute(
            select(EventMapCanonical)
            .where(
                EventMapCanonical.snapshot_id == snapshot.id,
                EventMapCanonical.has_uncertainty.is_(True),
            )
            .order_by(EventMapCanonical.event_time_start.desc())
            .limit(limit)
        ).scalars().all()
        review = [
            {
                "object_type": "canonical",
                "object_id": str(row.canonical_id),
                "title": row.title,
                "reasons": row.uncertainty_flags or [],
                "observed_at": _iso(snapshot.finished_at),
                "time_basis": "system_cognition",
                "web_url": _web_url(playlist_id, object_type="canonical", object_id=row.canonical_id, snapshot_id=snapshot.id),
            }
            for row in rows
        ]
    return {
        "newly_occurred": occurred,
        "newly_mapped": mapped,
        "story_updates": stories,
        "needs_review": review,
        "definitions": {
            "newly_occurred": "事件发生时间晚于用户观察窗口终点",
            "newly_mapped": "系统在用户上次观察后首次纳入或修订",
            "story_updates": "稳定故事身份在系统认知时间上的修订",
            "needs_review": "当前快照中带不确定性标记的真实对象",
        },
    }


def canonical_history(session: Session, playlist_id: uuid.UUID, canonical_id: uuid.UUID) -> dict[str, Any]:
    identity = session.get(EventMapCanonicalIdentity, canonical_id)
    if identity is None or identity.playlist_id != playlist_id:
        raise LookupError("canonical not found")
    revisions = session.execute(
        select(EventMapCanonicalHistoryRevision)
        .where(
            EventMapCanonicalHistoryRevision.playlist_id == playlist_id,
            EventMapCanonicalHistoryRevision.canonical_id == canonical_id,
        )
        .order_by(EventMapCanonicalHistoryRevision.observed_at.asc(), EventMapCanonicalHistoryRevision.id.asc())
    ).scalars().all()
    lineage = session.execute(
        select(EventMapCanonicalLineage).where(
            or_(
                EventMapCanonicalLineage.predecessor_canonical_id == canonical_id,
                EventMapCanonicalLineage.successor_canonical_id == canonical_id,
            )
        ).order_by(EventMapCanonicalLineage.created_at.asc())
    ).scalars().all()
    changes = session.execute(
        select(EventMapChange)
        .where(
            EventMapChange.playlist_id == playlist_id,
            EventMapChange.object_type == "canonical",
            EventMapChange.object_id == canonical_id,
        )
        .order_by(EventMapChange.observed_at.asc(), EventMapChange.id.asc())
    ).scalars().all()
    return {
        "canonical_id": str(canonical_id),
        "status": identity.status,
        "created_snapshot_id": str(identity.created_snapshot_id) if identity.created_snapshot_id else None,
        "retired_snapshot_id": str(identity.retired_snapshot_id) if identity.retired_snapshot_id else None,
        "revisions": [
            {
                "id": str(row.id),
                "snapshot_id": str(row.snapshot_id),
                "occurred_at": _iso(row.occurred_at),
                "observed_at": _iso(row.observed_at),
                "revision": row.revision,
            }
            for row in revisions
        ],
        "lineage": [
            {
                "snapshot_id": str(row.snapshot_id),
                "predecessor_canonical_id": str(row.predecessor_canonical_id),
                "successor_canonical_id": str(row.successor_canonical_id),
                "relation_type": row.relation_type,
                "confidence": row.confidence,
            }
            for row in lineage
        ],
        "changes": [change_payload(row) for row in changes],
        "web_url": _web_url(playlist_id, object_type="canonical", object_id=canonical_id),
    }


def canonical_directory(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    event_time_start: datetime | None = None,
    event_time_end: datetime | None = None,
    event_type: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """当前不可变快照的线性替代视图，只读取类型化热列。"""

    snapshot = current_snapshot(session, playlist_id)
    if snapshot is None:
        return {"snapshot_id": None, "items": []}
    statement = select(
        EventMapCanonical.canonical_id,
        EventMapCanonical.point_index,
        EventMapCanonical.title,
        EventMapCanonical.summary,
        EventMapCanonical.event_type,
        EventMapCanonical.event_time_start,
        EventMapCanonical.event_time_end,
        EventMapCanonical.member_count,
        EventMapCanonical.has_uncertainty,
    ).where(EventMapCanonical.snapshot_id == snapshot.id)
    if event_time_start is not None:
        statement = statement.where(EventMapCanonical.event_time_end >= event_time_start)
    if event_time_end is not None:
        statement = statement.where(EventMapCanonical.event_time_start <= event_time_end)
    if event_type:
        statement = statement.where(EventMapCanonical.event_type == event_type)
    rows = session.execute(
        statement.order_by(
            EventMapCanonical.event_time_start.desc(),
            EventMapCanonical.point_index.asc(),
        ).limit(limit).offset(offset)
    ).mappings().all()
    return {
        "snapshot_id": str(snapshot.id),
        "items": [
            {
                "canonical_id": str(row["canonical_id"]),
                "point_index": int(row["point_index"]),
                "title": row["title"],
                "summary": row["summary"],
                "event_type": row["event_type"],
                "event_time_start": _iso(row["event_time_start"]),
                "event_time_end": _iso(row["event_time_end"]),
                "member_count": int(row["member_count"] or 0),
                "has_uncertainty": bool(row["has_uncertainty"]),
                "web_url": _web_url(
                    playlist_id,
                    object_type="canonical",
                    object_id=row["canonical_id"],
                    snapshot_id=snapshot.id,
                ),
            }
            for row in rows
        ],
    }


def topic_detail(
    session: Session,
    playlist_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    limit: int = 50,
    snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    snapshot = observation_snapshot(session, playlist_id, snapshot_id)
    if snapshot is None:
        raise LookupError("domain field is not ready")
    topic = session.get(EventMapTopic, (snapshot.id, topic_id))
    if topic is None:
        raise LookupError("topic not found")
    rows = session.execute(
        select(
            EventMapCanonical.canonical_id,
            EventMapCanonical.point_index,
            EventMapCanonical.title,
            EventMapCanonical.event_type,
            EventMapCanonical.event_time_start,
            EventMapCanonical.event_time_end,
            EventMapCanonical.member_count,
        )
        .join(
            EventMapTopicMember,
            and_(
                EventMapTopicMember.snapshot_id == EventMapCanonical.snapshot_id,
                EventMapTopicMember.canonical_id == EventMapCanonical.canonical_id,
            ),
        )
        .where(
            EventMapCanonical.snapshot_id == snapshot.id,
            EventMapTopicMember.topic_id == topic_id,
            EventMapTopicMember.level == topic.level,
        )
        .order_by(EventMapCanonical.event_time_start.desc(), EventMapCanonical.point_index.asc())
        .limit(limit)
    ).mappings().all()
    return {
        "topic_id": str(topic.topic_id),
        "snapshot_id": str(snapshot.id),
        "level": int(topic.level),
        "parent_topic_id": str(topic.parent_topic_id) if topic.parent_topic_id else None,
        "label": topic.label,
        "top_terms": topic.top_terms or [],
        "center": {"x": topic.center_x, "y": topic.center_y, "z": topic.center_z},
        "radius": topic.radius,
        "canonical_count": int(topic.canonical_count or 0),
        "record_count": int(topic.member_count or 0),
        "representatives": [
            {
                "canonical_id": str(row["canonical_id"]),
                "point_index": int(row["point_index"]),
                "title": row["title"],
                "event_type": row["event_type"],
                "event_time_start": _iso(row["event_time_start"]),
                "event_time_end": _iso(row["event_time_end"]),
                "member_count": int(row["member_count"] or 0),
                "web_url": _web_url(
                    playlist_id,
                    object_type="canonical",
                    object_id=row["canonical_id"],
                    snapshot_id=snapshot.id,
                ),
            }
            for row in rows
        ],
        "web_url": f"/field?domain_id={playlist_id}&topic_id={topic_id}&snapshot_id={snapshot.id}",
    }


def story_directory(session: Session, playlist_id: uuid.UUID) -> list[dict[str, Any]]:
    snapshot = current_snapshot(session, playlist_id)
    if snapshot is None:
        return []
    rows = session.execute(
        select(EventMapStory, StoryReadState)
        .outerjoin(StoryReadState, StoryReadState.story_identity_id == EventMapStory.story_identity_id)
        .where(EventMapStory.snapshot_id == snapshot.id)
        .order_by(EventMapStory.event_time_end.desc().nullslast(), EventMapStory.story_id.asc())
    ).all()
    output: list[dict[str, Any]] = []
    for story, read_state in rows:
        identity_id = story.story_identity_id or story.story_id
        output.append(
            {
                "story_identity_id": str(identity_id),
                "story_id": str(story.story_id),
                "snapshot_id": str(story.snapshot_id),
                "title": story.title,
                "summary": story.summary,
                "event_time_start": _iso(story.event_time_start),
                "event_time_end": _iso(story.event_time_end),
                "canonical_count": int(story.canonical_count or 0),
                "followed": bool(read_state.followed) if read_state else False,
                "last_read_at": _iso(read_state.last_read_at) if read_state else None,
                "unread": not read_state or read_state.last_read_snapshot_id != snapshot.id,
                "web_url": _web_url(playlist_id, object_type="story", object_id=identity_id, snapshot_id=snapshot.id),
            }
        )
    return output


def _story_revision_delta(
    previous: EventMapStoryHistoryRevision | None,
    current: EventMapStoryHistoryRevision,
) -> dict[str, Any]:
    """生成用户可读的结构差异；历史正文仍保持不可变。"""
    previous_members = {str(value) for value in (previous.member_ids or [])} if previous else set()
    current_members = {str(value) for value in (current.member_ids or [])}
    previous_evidence = {str(value) for value in (previous.evidence_revision_ids or [])} if previous else set()
    current_evidence = {str(value) for value in (current.evidence_revision_ids or [])}

    def edge_key(edge: dict[str, Any]) -> str:
        return "|".join(
            (
                str(edge.get("source_canonical_id") or ""),
                str(edge.get("target_canonical_id") or ""),
                str(edge.get("relation_type") or ""),
            )
        )

    previous_edges = {edge_key(dict(edge)): dict(edge) for edge in (previous.edges or [])} if previous else {}
    current_edges = {edge_key(dict(edge)): dict(edge) for edge in (current.edges or [])}
    return {
        "baseline": previous is None,
        "members_added": sorted(current_members - previous_members),
        "members_removed": sorted(previous_members - current_members),
        "edges_added": [current_edges[key] for key in sorted(current_edges.keys() - previous_edges.keys())],
        "edges_removed": [previous_edges[key] for key in sorted(previous_edges.keys() - current_edges.keys())],
        "evidence_added": sorted(current_evidence - previous_evidence),
        "evidence_removed": sorted(previous_evidence - current_evidence),
    }


def story_history(
    session: Session,
    playlist_id: uuid.UUID,
    identity_id: uuid.UUID,
    *,
    snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    identity = session.get(EventMapStoryIdentity, identity_id)
    if identity is None or identity.playlist_id != playlist_id:
        raise LookupError("story not found")
    revisions = session.execute(
        select(EventMapStoryHistoryRevision)
        .where(
            EventMapStoryHistoryRevision.playlist_id == playlist_id,
            EventMapStoryHistoryRevision.story_identity_id == identity_id,
        )
        .order_by(EventMapStoryHistoryRevision.observed_at.asc(), EventMapStoryHistoryRevision.id.asc())
    ).scalars().all()
    changes = session.execute(
        select(EventMapChange)
        .where(
            EventMapChange.playlist_id == playlist_id,
            EventMapChange.object_type == "story",
            EventMapChange.object_id == identity_id,
        )
        .order_by(EventMapChange.observed_at.asc(), EventMapChange.id.asc())
    ).scalars().all()
    read_state = session.get(StoryReadState, identity_id)
    trajectory = {"snapshot_id": None, "nodes": [], "edges": []}
    snapshot = observation_snapshot(session, playlist_id, snapshot_id)
    selected_revision = next(
        (revision for revision in reversed(revisions) if snapshot is not None and revision.snapshot_id == snapshot.id),
        None,
    )
    if snapshot_id is not None and selected_revision is None:
        raise LookupError("story not found in domain snapshot")
    if snapshot is not None and selected_revision is not None:
        member_ids = [uuid.UUID(str(value)) for value in (selected_revision.member_ids or [])]
        canonical_rows = session.execute(
            select(EventMapCanonical).where(
                EventMapCanonical.snapshot_id == snapshot.id,
                EventMapCanonical.canonical_id.in_(member_ids),
            )
        ).scalars().all() if member_ids else []
        point_by_id = {row.canonical_id: row for row in canonical_rows}
        trajectory = {
            "snapshot_id": str(snapshot.id),
            "nodes": [
                {
                    "canonical_id": str(canonical_id),
                    "point_index": int(point_by_id[canonical_id].point_index),
                    "title": point_by_id[canonical_id].title,
                    "occurred_at": _iso(point_by_id[canonical_id].event_time_start),
                }
                for canonical_id in member_ids
                if canonical_id in point_by_id
            ],
            "edges": [
                {
                    **dict(edge),
                    "source_point_index": int(point_by_id[source_id].point_index),
                    "target_point_index": int(point_by_id[target_id].point_index),
                }
                for edge in (selected_revision.edges or [])
                for source_id, target_id in [
                    (
                        uuid.UUID(str(edge["source_canonical_id"])),
                        uuid.UUID(str(edge["target_canonical_id"])),
                    )
                ]
                if source_id in point_by_id and target_id in point_by_id
            ],
        }
    return {
        "story_identity_id": str(identity_id),
        "status": identity.status,
        "followed": bool(read_state.followed) if read_state else False,
        "last_read_snapshot_id": str(read_state.last_read_snapshot_id) if read_state and read_state.last_read_snapshot_id else None,
        "last_read_at": _iso(read_state.last_read_at) if read_state else None,
        "last_position": read_state.last_position if read_state else None,
        "revisions": [
            {
                "id": str(row.id),
                "snapshot_id": str(row.snapshot_id),
                "story_id": str(row.story_id),
                "title": row.title,
                "summary": row.summary,
                "story_type": row.story_type,
                "event_time_start": _iso(row.event_time_start),
                "event_time_end": _iso(row.event_time_end),
                "member_ids": row.member_ids or [],
                "edges": row.edges or [],
                "evidence_revision_ids": row.evidence_revision_ids or [],
                "method_version": row.method_version,
                "observed_at": _iso(row.observed_at),
                "delta": _story_revision_delta(revisions[index - 1] if index else None, row),
            }
            for index, row in enumerate(revisions)
        ],
        "changes": [change_payload(row) for row in changes],
        "current_trajectory": trajectory,
        "brief_references": object_brief_references(
            session,
            playlist_id=playlist_id,
            object_type="story",
            object_id=identity_id,
        ),
        "web_url": _web_url(playlist_id, object_type="story", object_id=identity_id),
    }


def update_story_read_state(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    identity_id: uuid.UUID,
    followed: bool | None,
    snapshot_id: uuid.UUID | None,
    position: int | None,
    mark_read: bool,
) -> StoryReadState:
    identity = session.get(EventMapStoryIdentity, identity_id)
    if identity is None or identity.playlist_id != playlist_id:
        raise LookupError("story not found")
    if snapshot_id:
        revision = session.execute(
            select(EventMapStoryHistoryRevision.id).where(
                EventMapStoryHistoryRevision.story_identity_id == identity_id,
                EventMapStoryHistoryRevision.snapshot_id == snapshot_id,
            )
        ).scalar_one_or_none()
        if revision is None:
            raise ValueError("story revision not found in snapshot")
    state = session.get(StoryReadState, identity_id)
    if state is None:
        state = StoryReadState(story_identity_id=identity_id)
        session.add(state)
    if followed is not None:
        state.followed = followed
    if mark_read:
        if snapshot_id is None:
            snapshot = current_snapshot(session, playlist_id)
            snapshot_id = snapshot.id if snapshot else None
        state.last_read_snapshot_id = snapshot_id
        state.last_read_at = utcnow()
    if position is not None:
        state.last_position = max(0, position)
    session.flush()
    return state


def structured_brief(session: Session, brief_id: uuid.UUID) -> dict[str, Any]:
    brief = session.get(Brief, brief_id)
    if brief is None:
        raise LookupError("brief not found")
    asset = session.get(Asset, brief.markdown_asset_id) if brief.markdown_asset_id else None
    refs = session.execute(
        select(BriefReference)
        .where(BriefReference.brief_id == brief_id)
        .order_by(BriefReference.position.asc())
    ).scalars().all()
    return {
        "id": str(brief.id),
        "playlist_id": str(brief.playlist_id),
        "granularity": brief.granularity,
        "period_start": brief.period_start.isoformat(),
        "status": brief.status,
        "snapshot_id": str(brief.snapshot_id) if brief.snapshot_id else None,
        "generation_basis": brief.generation_basis,
        "markdown_asset_id": str(asset.id) if asset else None,
        "markdown_url": f"/api/assets/{asset.id}/content" if asset else None,
        "references": [brief_reference_payload(row, brief.playlist_id) for row in refs],
        "web_url": f"/briefs?domain_id={brief.playlist_id}&brief_id={brief.id}",
    }


def brief_reference_payload(row: BriefReference, playlist_id: uuid.UUID) -> dict[str, Any]:
    context = dict(row.context or {})
    return {
        "id": str(row.id),
        "brief_id": str(row.brief_id),
        "anchor": row.anchor,
        "position": row.position,
        "object_type": row.object_type,
        "object_id": str(row.object_id),
        "snapshot_id": str(row.snapshot_id) if row.snapshot_id else None,
        "event_time_start": _iso(row.event_time_start),
        "event_time_end": _iso(row.event_time_end),
        "evidence_revision_id": str(row.evidence_revision_id) if row.evidence_revision_id else None,
        "label": row.label,
        "context": context,
        "web_url": context.get("web_url") or _web_url(playlist_id, object_type=row.object_type, object_id=row.object_id, snapshot_id=row.snapshot_id),
    }


def object_brief_references(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    object_type: str,
    object_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    if object_type == "topic":
        snapshot = observation_snapshot(session, playlist_id, snapshot_id)
        if snapshot is None:
            return []
        topic = session.get(EventMapTopic, (snapshot.id, object_id))
        if topic is None:
            return []
        canonical_ids = select(EventMapTopicMember.canonical_id).where(
            EventMapTopicMember.snapshot_id == snapshot.id,
            EventMapTopicMember.topic_id == object_id,
            EventMapTopicMember.level == topic.level,
        )
        rows = session.execute(
            select(BriefReference)
            .join(Brief, Brief.id == BriefReference.brief_id)
            .where(
                Brief.playlist_id == playlist_id,
                BriefReference.object_type == "canonical",
                BriefReference.object_id.in_(canonical_ids),
            )
            .order_by(Brief.period_start.desc(), BriefReference.position.asc())
        ).scalars().all()
        return [brief_reference_payload(row, playlist_id) for row in rows]
    rows = session.execute(
        select(BriefReference)
        .join(Brief, Brief.id == BriefReference.brief_id)
        .where(
            Brief.playlist_id == playlist_id,
            BriefReference.object_type == object_type,
            BriefReference.object_id == object_id,
        )
        .order_by(Brief.period_start.desc(), BriefReference.position.asc())
    ).scalars().all()
    return [brief_reference_payload(row, playlist_id) for row in rows]


def _story_evidence_references(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    revision_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    if not revision_ids:
        return []
    rows = session.execute(
        select(EventMapStoryHistoryEvidence, EventMapStoryHistoryRevision)
        .join(
            EventMapStoryHistoryRevision,
            EventMapStoryHistoryRevision.id == EventMapStoryHistoryEvidence.history_revision_id,
        )
        .where(
            EventMapStoryHistoryEvidence.playlist_id == playlist_id,
            EventMapStoryHistoryEvidence.record_revision_id.in_(revision_ids),
        )
        .order_by(EventMapStoryHistoryRevision.observed_at.desc())
    ).all()
    output: list[dict[str, Any]] = []
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for evidence, revision in rows:
        key = (evidence.story_identity_id, evidence.edge_id)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "story_identity_id": str(evidence.story_identity_id),
                "story_title": revision.title,
                "edge_id": str(evidence.edge_id),
                "source_canonical_id": str(evidence.source_canonical_id),
                "target_canonical_id": str(evidence.target_canonical_id),
                "relation_type": evidence.relation_type,
                "evidence_revision_id": str(evidence.record_revision_id),
                "snapshot_id": str(evidence.snapshot_id),
                "web_url": _web_url(
                    playlist_id,
                    object_type="story",
                    object_id=evidence.story_identity_id,
                    snapshot_id=evidence.snapshot_id,
                ),
            }
        )
    return output


def evidence_context(session: Session, playlist_id: uuid.UUID, revision_id: uuid.UUID) -> dict[str, Any]:
    revision = session.get(EventMapRecordRevision, revision_id)
    if revision is None:
        raise LookupError("evidence revision not found")
    video = session.get(Video, revision.source_video_id) if revision.source_video_id else None
    if video is None:
        raise LookupError("source record not found")
    media_in_domain = session.execute(
        select(PlaylistMedia.media_id).where(
            PlaylistMedia.playlist_id == playlist_id,
            PlaylistMedia.media_id == video.media_id,
        )
    ).scalar_one_or_none()
    if media_in_domain is None:
        raise LookupError("evidence does not belong to domain")
    media = session.get(Media, video.media_id)
    current_transcript_asset = pick_transcript_asset(session, video.id, variant="plain")
    canonical_ids = session.execute(
        select(EventMapCanonicalHistoryMember.canonical_id)
        .where(
            EventMapCanonicalHistoryMember.playlist_id == playlist_id,
            EventMapCanonicalHistoryMember.record_revision_id == revision_id,
        )
        .distinct()
    ).scalars().all()
    brief_refs = session.execute(
        select(BriefReference)
        .join(Brief, Brief.id == BriefReference.brief_id)
        .where(
            Brief.playlist_id == playlist_id,
            BriefReference.evidence_revision_id == revision_id,
        )
    ).scalars().all()
    evidence = []
    for index, item in enumerate(revision.evidence_json or []):
        payload = dict(item)
        provenance = payload.get("evidence_json") if isinstance(payload.get("evidence_json"), dict) else payload
        frozen_transcript_asset_id = payload.get("transcript_asset_id")
        if frozen_transcript_asset_id is None:
            source_version_status = "not_applicable" if provenance.get("source_kind") != "transcript" else "unknown"
        elif current_transcript_asset is None:
            source_version_status = "unavailable"
        elif str(frozen_transcript_asset_id) == str(current_transcript_asset.id):
            source_version_status = "current"
        else:
            source_version_status = "superseded"
        evidence.append(
            {
                "id": f"{revision_id}:{index}",
                "text": payload.get("evidence_text") or payload.get("text"),
                "source_id": provenance.get("source_id"),
                "source_kind": provenance.get("source_kind"),
                "source_label": provenance.get("source_label"),
                "char_start": provenance.get("char_start"),
                "char_end": provenance.get("char_end"),
                "source_sha256": provenance.get("source_sha256"),
                "transcript_asset_id": str(frozen_transcript_asset_id) if frozen_transcript_asset_id else None,
                "current_transcript_asset_id": str(current_transcript_asset.id) if current_transcript_asset else None,
                "source_version_status": source_version_status,
                "verified": provenance.get("verified") is True,
                "playback_position_seconds": provenance.get("start_seconds"),
                "playback_mapping": "exact_segment" if provenance.get("start_seconds") is not None else "char_range_only",
            }
        )
    return {
        "revision_id": str(revision.id),
        "event_id": str(revision.event_id) if revision.event_id else None,
        "source": {
            "video_id": str(video.id),
            "title": video.title,
            "url": video.url,
            "player_url": f"/video?video_id={video.id}",
            "media_id": str(video.media_id),
            "media_name": media.name if media else None,
        },
        "evidence": evidence,
        "canonical_ids": [str(value) for value in canonical_ids],
        "story_references": _story_evidence_references(
            session,
            playlist_id=playlist_id,
            revision_ids=[revision_id],
        ),
        "brief_references": [brief_reference_payload(row, playlist_id) for row in brief_refs],
        "transcript_mapping_policy": "有精确分段时间时返回播放秒数；否则保持字符区间，不做比例估算。",
    }


def source_semantic_references(session: Session, playlist_id: uuid.UUID, video_id: uuid.UUID) -> dict[str, Any]:
    video = session.get(Video, video_id)
    if video is None:
        raise LookupError("source record not found")
    belongs = session.execute(
        select(PlaylistMedia.media_id).where(
            PlaylistMedia.playlist_id == playlist_id,
            PlaylistMedia.media_id == video.media_id,
        )
    ).scalar_one_or_none()
    if belongs is None:
        raise LookupError("source record does not belong to domain")
    revisions = session.execute(
        select(EventMapRecordRevision).where(EventMapRecordRevision.source_video_id == video_id)
    ).scalars().all()
    revision_ids = [row.id for row in revisions]
    brief_refs = session.execute(
        select(BriefReference)
        .join(Brief, Brief.id == BriefReference.brief_id)
        .where(
            Brief.playlist_id == playlist_id,
            BriefReference.evidence_revision_id.in_(revision_ids),
        )
    ).scalars().all() if revision_ids else []
    canonical_history = session.execute(
        select(
            EventMapCanonicalHistoryMember.canonical_id,
            EventMapCanonicalHistoryRevision.revision,
        )
        .join(
            EventMapCanonicalHistoryRevision,
            EventMapCanonicalHistoryRevision.id == EventMapCanonicalHistoryMember.history_revision_id,
        )
        .where(
            EventMapCanonicalHistoryMember.playlist_id == playlist_id,
            EventMapCanonicalHistoryMember.record_revision_id.in_(revision_ids),
        )
        .order_by(EventMapCanonicalHistoryRevision.observed_at.desc())
    ).all() if revision_ids else []
    canonical_by_id: dict[uuid.UUID, dict[str, Any]] = {}
    for canonical_id, revision in canonical_history:
        if canonical_id not in canonical_by_id:
            canonical_by_id[canonical_id] = dict(revision or {})
    return {
        "video_id": str(video_id),
        "canonical_references": [
            {
                "canonical_id": str(canonical_id),
                "title": payload.get("title"),
                "web_url": _web_url(playlist_id, object_type="canonical", object_id=canonical_id),
            }
            for canonical_id, payload in canonical_by_id.items()
        ],
        "story_references": _story_evidence_references(
            session,
            playlist_id=playlist_id,
            revision_ids=revision_ids,
        ),
        "brief_references": [brief_reference_payload(row, playlist_id) for row in brief_refs],
    }


def semantic_search(
    session: Session,
    *,
    query: str,
    playlist_id: uuid.UUID | None,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    needle = f"%{query.strip()}%"
    groups: dict[str, list[dict[str, Any]]] = {
        "domains": [],
        "topics": [],
        "canonicals": [],
        "stories": [],
        "entities": [],
        "briefs": [],
        "sources": [],
        "records": [],
    }
    playlists = session.execute(
        select(Playlist).where(Playlist.name.ilike(needle)).order_by(Playlist.updated_at.desc()).limit(limit)
    ).scalars().all()
    groups["domains"] = [{"id": str(row.id), "title": row.name, "domain_id": str(row.id), "web_url": _web_url(row.id)} for row in playlists]

    domain_ids = [playlist_id] if playlist_id else [row.id for row in session.execute(select(Playlist.id)).all()]
    for domain_id in domain_ids[:100]:
        snapshot = current_snapshot(session, domain_id)
        if snapshot is None:
            continue
        remaining = max(0, limit - len(groups["canonicals"]))
        if remaining:
            rows = session.execute(
                select(EventMapCanonical)
                .where(
                    EventMapCanonical.snapshot_id == snapshot.id,
                    or_(EventMapCanonical.title.ilike(needle), EventMapCanonical.summary.ilike(needle)),
                )
                .limit(remaining)
            ).scalars().all()
            groups["canonicals"].extend({"id": str(row.canonical_id), "title": row.title, "summary": row.summary, "domain_id": str(domain_id), "occurred_at": _iso(row.event_time_start), "web_url": _web_url(domain_id, object_type="canonical", object_id=row.canonical_id, snapshot_id=snapshot.id)} for row in rows)
        remaining = max(0, limit - len(groups["stories"]))
        if remaining:
            rows = session.execute(
                select(EventMapStory)
                .where(EventMapStory.snapshot_id == snapshot.id, or_(EventMapStory.title.ilike(needle), EventMapStory.summary.ilike(needle)))
                .limit(remaining)
            ).scalars().all()
            groups["stories"].extend({"id": str(row.story_identity_id or row.story_id), "title": row.title, "summary": row.summary, "domain_id": str(domain_id), "web_url": _web_url(domain_id, object_type="story", object_id=row.story_identity_id or row.story_id, snapshot_id=snapshot.id)} for row in rows)
        remaining = max(0, limit - len(groups["topics"]))
        if remaining:
            rows = session.execute(select(EventMapTopic).where(EventMapTopic.snapshot_id == snapshot.id, EventMapTopic.label.ilike(needle)).limit(remaining)).scalars().all()
            groups["topics"].extend({"id": str(row.topic_id), "title": row.label, "domain_id": str(domain_id), "web_url": f"/field?domain_id={domain_id}&topic_id={row.topic_id}&snapshot_id={snapshot.id}"} for row in rows)
        remaining = max(0, limit - len(groups["entities"]))
        if remaining:
            rows = session.execute(
                select(
                    EventMapEntityIndex.entity_type,
                    EventMapEntityIndex.normalized_key,
                    EventMapEntityIndex.name,
                    func.count(func.distinct(EventMapEntityIndex.canonical_id)).label("canonical_count"),
                )
                .where(
                    EventMapEntityIndex.snapshot_id == snapshot.id,
                    or_(
                        EventMapEntityIndex.name.ilike(needle),
                        EventMapEntityIndex.normalized_key.ilike(needle),
                    ),
                )
                .group_by(
                    EventMapEntityIndex.entity_type,
                    EventMapEntityIndex.normalized_key,
                    EventMapEntityIndex.name,
                )
                .order_by(func.count(func.distinct(EventMapEntityIndex.canonical_id)).desc())
                .limit(remaining)
            ).all()
            groups["entities"].extend(
                {
                    "id": f"{row.entity_type}:{row.normalized_key}",
                    "title": row.name,
                    "entity_type": row.entity_type,
                    "normalized_key": row.normalized_key,
                    "canonical_count": int(row.canonical_count or 0),
                    "domain_id": str(domain_id),
                    "web_url": "/field?" + urlencode(
                        {
                            "domain_id": str(domain_id),
                            "entity_type": row.entity_type,
                            "entity_key": row.normalized_key,
                            "entity_name": row.name,
                            "snapshot_id": str(snapshot.id),
                        }
                    ),
                }
                for row in rows
            )

    media_scope = select(Media)
    video_scope = select(Video)
    if playlist_id:
        media_ids = select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)
        media_scope = media_scope.where(Media.id.in_(media_ids))
        video_scope = video_scope.where(Video.media_id.in_(media_ids))
    media_rows = session.execute(media_scope.where(or_(Media.name.ilike(needle), Media.description.ilike(needle))).limit(limit)).scalars().all()
    groups["sources"] = [{"id": str(row.id), "title": row.name, "provider": row.provider, "domain_id": str(playlist_id) if playlist_id else None, "web_url": f"/library?tab=sources&media_id={row.id}"} for row in media_rows]
    video_rows = session.execute(video_scope.where(or_(Video.title.ilike(needle), Video.description.ilike(needle))).limit(limit)).scalars().all()
    groups["records"] = [{"id": str(row.id), "title": row.title, "provider": row.provider, "domain_id": str(playlist_id) if playlist_id else None, "web_url": f"/video?video_id={row.id}"} for row in video_rows]
    brief_scope = (
        select(Brief)
        .outerjoin(BriefReference, BriefReference.brief_id == Brief.id)
        .where(
            or_(
                BriefReference.label.ilike(needle),
                Brief.granularity.ilike(needle),
                cast(Brief.period_start, String).ilike(needle),
            )
        )
    )
    if playlist_id:
        brief_scope = brief_scope.where(Brief.playlist_id == playlist_id)
    brief_rows = session.execute(
        brief_scope.distinct().order_by(Brief.period_start.desc()).limit(limit)
    ).scalars().all()
    labels = {"day": "日简报", "week": "周简报", "month": "月简报"}
    groups["briefs"] = [
        {
            "id": str(row.id),
            "title": f"{labels.get(row.granularity, row.granularity)} · {row.period_start.isoformat()}",
            "domain_id": str(row.playlist_id),
            "web_url": f"/briefs?domain_id={row.playlist_id}&brief_id={row.id}",
        }
        for row in brief_rows
    ]
    return groups
