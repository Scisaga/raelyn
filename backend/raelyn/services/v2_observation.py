from __future__ import annotations

from datetime import date, datetime, timedelta
import uuid
from typing import Any, Literal
from urllib.parse import urlencode

from sqlalchemy import String, and_, case, cast, func, or_, select, text, true
from sqlalchemy.orm import Session, aliased

from raelyn.api.asset_refs import build_asset_ref
from raelyn.models import (
    Asset,
    Brief,
    BriefReference,
    DomainObservationCursor,
    EventMapCanonical,
    EventMapCanonicalIdentity,
    EventMapCanonicalLineage,
    EventMapCanonicalMember,
    EventMapEntityIndex,
    EventMapRecordRevision,
    EventMapSnapshot,
    EventMapState,
    EventMapStory,
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
    VideoEventExtractionRun,
)
from raelyn.services.event_analysis import event_extraction_spec
from raelyn.services.transcripts import TRANSCRIPT_SOURCE_LANGUAGE_ORDER, pick_transcript_asset
from raelyn.services.video_time import resolve_video_timeline
from raelyn.timeutil import utcnow


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)



_STORY_RELATION_LABELS = {
    "continuation": "阶段推进",
    "causes": "因果承接",
    "response": "事件响应",
    "corrects": "事实纠正",
}

_EVENT_HIGHLIGHT_MAX_CARDS_PER_MEDIA = 2


def _datetime_after(left: Any, right: Any) -> bool:
    """比较数据库时间；SQLite 测试会丢掉 timezone 标记。"""

    if left is None:
        return False
    if right is None:
        return True
    left_value = left.replace(tzinfo=None) if getattr(left, "tzinfo", None) is not None else left
    right_value = right.replace(tzinfo=None) if getattr(right, "tzinfo", None) is not None else right
    return bool(left_value > right_value)




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
    playlist_ids = [playlist.id for playlist in playlists]
    media_count_by_playlist: dict[uuid.UUID, int] = {}
    video_count_by_playlist: dict[uuid.UUID, int] = {}
    media_preview_by_playlist: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if playlist_ids:
        media_count_by_playlist = {
            playlist_id: int(count or 0)
            for playlist_id, count in session.execute(
                select(PlaylistMedia.playlist_id, func.count(PlaylistMedia.media_id))
                .where(PlaylistMedia.playlist_id.in_(playlist_ids))
                .group_by(PlaylistMedia.playlist_id)
            ).all()
        }
        video_count_by_playlist = {
            playlist_id: int(count or 0)
            for playlist_id, count in session.execute(
                select(PlaylistMedia.playlist_id, func.count())
                .select_from(PlaylistMedia)
                .join(Video, Video.media_id == PlaylistMedia.media_id)
                .where(PlaylistMedia.playlist_id.in_(playlist_ids))
                .group_by(PlaylistMedia.playlist_id)
            ).all()
        }
        media_rows = session.execute(
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
            .order_by(PlaylistMedia.playlist_id.asc(), PlaylistMedia.added_at.desc(), Media.id.asc())
        ).mappings().all()
        for row in media_rows:
            preview = media_preview_by_playlist.setdefault(row["playlist_id"], [])
            if len(preview) >= 5:
                continue
            preview.append(
                {
                    "id": str(row["media_id"]),
                    "provider": row["provider"],
                    "url": row["url"],
                    "name": row["name"],
                    "avatar_asset_id": row["avatar_asset_id"],
                }
            )
    asset_ids = {
        asset_id
        for playlist in playlists
        for asset_id in (playlist.avatar_asset_id, playlist.background_asset_id)
        if asset_id is not None
    }
    asset_ids.update(
        item["avatar_asset_id"]
        for preview in media_preview_by_playlist.values()
        for item in preview
        if item["avatar_asset_id"] is not None
    )
    assets_by_id = (
        {
            asset.id: asset
            for asset in session.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars().all()
        }
        if asset_ids
        else {}
    )
    for preview in media_preview_by_playlist.values():
        for item in preview:
            avatar_asset_id = item.pop("avatar_asset_id")
            item["avatar_asset"] = build_asset_ref(
                assets_by_id.get(avatar_asset_id),
                include_presigned=False,
            )
    output: list[dict[str, Any]] = []
    for playlist in playlists:
        snapshot = current_snapshot(session, playlist.id)
        cursor = session.get(DomainObservationCursor, playlist.id)
        change_count = _current_update_count(session, snapshot, cursor.observed_at if cursor else None)
        preview_points: list[dict[str, Any]] = []
        if snapshot is not None and int(snapshot.canonical_count or 0) > 0:
            stride = max(1, int(snapshot.canonical_count or 0) // 96)
            preview_point_indices = list(range(0, int(snapshot.canonical_count or 0), stride))[:96]
            preview_rows = session.execute(
                select(
                    EventMapCanonical.x,
                    EventMapCanonical.y,
                    EventMapCanonical.event_type_code,
                )
                .where(
                    EventMapCanonical.snapshot_id == snapshot.id,
                    EventMapCanonical.point_index.in_(preview_point_indices),
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
                # 目录头像始终走统一内容代理，避免仅为列表渲染逐资产创建 S3 预签名客户端。
                "avatar_asset": build_asset_ref(
                    assets_by_id.get(playlist.avatar_asset_id),
                    include_presigned=False,
                ),
                "background_asset": build_asset_ref(
                    assets_by_id.get(playlist.background_asset_id),
                    include_presigned=False,
                ),
                "observation_enabled": bool(playlist.observation_enabled),
                "brief_granularity": playlist.brief_granularity or "day",
                "media_count": media_count_by_playlist.get(playlist.id, 0),
                "media_preview": media_preview_by_playlist.get(playlist.id, []),
                "video_count": video_count_by_playlist.get(playlist.id, 0),
                "snapshot": snapshot_summary(snapshot),
                "preview_points": preview_points,
                "unobserved_change_count": change_count,
                "last_observed_at": _iso(cursor.observed_at) if cursor else None,
                "web_url": _web_url(playlist.id),
            }
        )
    return output


def domain_bootstrap_directory(session: Session) -> list[dict[str, Any]]:
    """返回首屏选择观测域所需的最小目录，不读取媒体预览、变化统计或星点。"""

    rows = session.execute(
        select(
            Playlist.id.label("playlist_id"),
            Playlist.name.label("name"),
            Playlist.description.label("description"),
            Playlist.observation_enabled.label("observation_enabled"),
            Playlist.brief_granularity.label("brief_granularity"),
            EventMapSnapshot.id.label("snapshot_id"),
            EventMapSnapshot.status.label("snapshot_status"),
            EventMapSnapshot.finished_at.label("snapshot_finished_at"),
        )
        .outerjoin(EventMapState, EventMapState.playlist_id == Playlist.id)
        .outerjoin(EventMapSnapshot, EventMapSnapshot.id == EventMapState.current_snapshot_id)
        .order_by(Playlist.updated_at.desc(), Playlist.id.asc())
    ).mappings().all()
    return [
        {
            "id": str(row["playlist_id"]),
            "name": row["name"],
            "description": row["description"],
            "observation_enabled": bool(row["observation_enabled"]),
            "brief_granularity": row["brief_granularity"] or "day",
            "snapshot": (
                {
                    "id": str(row["snapshot_id"]),
                    "status": "ready",
                    "observed_at": _iso(row["snapshot_finished_at"]),
                }
                if row["snapshot_id"] is not None and row["snapshot_status"] == "ready"
                else None
            ),
            "web_url": _web_url(row["playlist_id"]),
        }
        for row in rows
    ]


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
        "updated_at": _iso(cursor.updated_at),
    }


def domain_observation(session: Session, playlist_id: uuid.UUID) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if playlist is None:
        raise LookupError("domain not found")
    if session.get_bind().dialect.name == "postgresql":
        # 在线统计不承担首次 LLVM/JIT 初始化与编译耗时；设置随当前事务结束恢复。
        session.execute(text("SET LOCAL jit = off"))
    snapshot = current_snapshot(session, playlist_id)
    cursor = session.get(DomainObservationCursor, playlist_id)
    observed_after = cursor.observed_at if cursor else None
    unobserved = _current_update_count(session, snapshot, observed_after)
    media_ids = select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)
    domain_videos = (
        select(Video.id.label("video_id"), Video.status)
        .where(Video.media_id.in_(media_ids))
        .cte("domain_videos")
        .prefix_with("MATERIALIZED", dialect="postgresql")
    )
    source_counts = select(
        domain_videos.c.status.label("video_status"),
        func.count(domain_videos.c.video_id).label("status_video_count"),
    ).group_by(domain_videos.c.status).subquery()
    extraction_spec = event_extraction_spec(session)
    # 分母和抽取分类复用同一个域内去重集合，不扫描无关域的转写。
    transcript_video_ids = (
        select(func.distinct(Asset.video_id).label("video_id"))
        .join(domain_videos, domain_videos.c.video_id == Asset.video_id)
        .where(
            Asset.type == "transcript",
            Asset.format == "txt",
        )
        .cte("domain_transcript_videos")
        .prefix_with("MATERIALIZED", dialect="postgresql")
    )
    is_current_spec = and_(
        VideoEventExtractionRun.prompt_version == extraction_spec.prompt_version,
        VideoEventExtractionRun.extraction_model == extraction_spec.model,
    )
    extraction_flags = (
        select(
            VideoEventExtractionRun.video_id.label("video_id"),
            func.max(
                case(
                    (
                        and_(is_current_spec, VideoEventExtractionRun.status == "succeeded"),
                        1,
                    ),
                    else_=0,
                )
            ).label("current_succeeded"),
            func.max(
                case(
                    (
                        and_(
                            is_current_spec,
                            VideoEventExtractionRun.status == "succeeded",
                            VideoEventExtractionRun.event_count > 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("current_with_events"),
            func.max(
                case(
                    (
                        and_(is_current_spec, VideoEventExtractionRun.status == "failed"),
                        1,
                    ),
                    else_=0,
                )
            ).label("current_failed"),
            func.max(
                case(
                    (
                        and_(
                            VideoEventExtractionRun.status == "succeeded",
                            or_(
                                VideoEventExtractionRun.prompt_version.is_(None),
                                VideoEventExtractionRun.prompt_version != extraction_spec.prompt_version,
                                VideoEventExtractionRun.extraction_model.is_(None),
                                VideoEventExtractionRun.extraction_model != extraction_spec.model,
                            ),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("legacy_succeeded"),
        )
        .join(transcript_video_ids, transcript_video_ids.c.video_id == VideoEventExtractionRun.video_id)
        .group_by(VideoEventExtractionRun.video_id)
        .subquery()
    )
    extraction_counts = (
        select(
            select(func.count()).select_from(transcript_video_ids).scalar_subquery().label("transcript_videos"),
            func.coalesce(func.sum(extraction_flags.c.current_succeeded), 0).label("current_extracted"),
            func.coalesce(func.sum(extraction_flags.c.current_with_events), 0).label("current_with_events"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                extraction_flags.c.current_succeeded == 0,
                                extraction_flags.c.current_failed == 1,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("current_failed"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                extraction_flags.c.current_succeeded == 0,
                                extraction_flags.c.current_failed == 0,
                                extraction_flags.c.legacy_succeeded == 1,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("legacy_only"),
        )
        .select_from(extraction_flags)
        .subquery()
    )
    domain_event_ids = (
        select(MarketEvent.id.label("event_id"))
        .join(domain_videos, domain_videos.c.video_id == MarketEvent.source_video_id)
        .cte("domain_events")
        .prefix_with("MATERIALIZED", dialect="postgresql")
    )
    verified_event_ids = (
        select(MarketEventEvidence.event_id)
        .join(domain_event_ids, domain_event_ids.c.event_id == MarketEventEvidence.event_id)
        .where(MarketEventEvidence.evidence_json["verified"].as_boolean().is_(True))
        .distinct()
        .cte("verified_events")
        .prefix_with("MATERIALIZED", dialect="postgresql")
    )
    event_counts = (
        select(
            func.count(domain_event_ids.c.event_id).label("total_events"),
            func.count(verified_event_ids.c.event_id).label("verified_events"),
        )
        .select_from(domain_event_ids)
        .outerjoin(
            verified_event_ids,
            verified_event_ids.c.event_id == domain_event_ids.c.event_id,
        )
        .subquery()
    )
    # 聚合结果各一行，状态分组最多几行；所有覆盖统计共享同一次域内视频扫描。
    rows = session.execute(
        select(extraction_counts, event_counts, source_counts)
        .select_from(extraction_counts)
        .join(event_counts, true())
        .outerjoin(source_counts, true())
    ).mappings().all()
    counts = rows[0]
    status_reasons = {
        str(row["video_status"] or "unknown"): int(row["status_video_count"])
        for row in rows if row["status_video_count"] is not None
    }
    total_videos = sum(status_reasons.values())
    transcript_videos = int(counts["transcript_videos"])
    current_extracted = int(counts["current_extracted"])
    current_with_events = int(counts["current_with_events"])
    current_failed = int(counts["current_failed"])
    legacy_only = int(counts["legacy_only"])
    current_zero_events = max(0, current_extracted - current_with_events)
    no_successful_result = max(0, transcript_videos - current_extracted - current_failed - legacy_only)
    missing_transcript = max(0, total_videos - transcript_videos)
    total_events = int(counts["total_events"])
    verified_events = int(counts["verified_events"])
    skipped = dict(snapshot.skipped_reason_counts or {}) if snapshot else {"snapshot_not_ready": total_events}
    coverage = {
        "source_processing": {
            "numerator": transcript_videos,
            "denominator": total_videos,
            "failed_or_skipped": status_reasons,
        },
        "event_extraction": {
            "numerator": current_extracted,
            "denominator": transcript_videos,
            "failed_or_skipped": {
                "current_spec_failed": current_failed,
                "legacy_spec_only": legacy_only,
                "no_successful_result": no_successful_result,
                "missing_transcript": missing_transcript,
            },
            "details": {
                "basis": "prompt_version_and_model",
                "prompt_version": extraction_spec.prompt_version,
                "model": extraction_spec.model,
                "with_events": current_with_events,
                "zero_events": current_zero_events,
                "legacy_spec_only": legacy_only,
                "current_spec_failed": current_failed,
                "no_successful_result": no_successful_result,
                "missing_transcript": missing_transcript,
            },
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
            "observation_enabled": bool(playlist.observation_enabled),
            "brief_granularity": playlist.brief_granularity or "day",
            "brief_prompt": (playlist.brief_prompt or "").strip() or None,
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
    }
    for key, value in values.items():
        if key in allowed:
            setattr(cursor, key, value)
    if cursor.observed_at is None and cursor.snapshot_id:
        snapshot = session.get(EventMapSnapshot, cursor.snapshot_id)
        cursor.observed_at = snapshot.finished_at if snapshot and snapshot.finished_at else utcnow()
    session.flush()
    return cursor






def _current_update_count(session: Session, snapshot: EventMapSnapshot | None, observed_after: datetime | None) -> int:
    if snapshot is None:
        return 0
    canonical = select(func.count()).select_from(EventMapCanonical).join(
        EventMapCanonicalIdentity, EventMapCanonicalIdentity.id == EventMapCanonical.canonical_id,
    ).where(EventMapCanonical.snapshot_id == snapshot.id)
    story = select(func.count()).select_from(EventMapStory).join(
        EventMapStoryIdentity, EventMapStoryIdentity.id == EventMapStory.story_identity_id,
    ).where(EventMapStory.snapshot_id == snapshot.id)
    if observed_after is not None:
        canonical = canonical.where(EventMapCanonicalIdentity.updated_at > observed_after)
        story = story.where(EventMapStoryIdentity.last_material_changed_at > observed_after)
    return int(session.scalar(canonical) or 0) + int(session.scalar(story) or 0)


def observation_feed(session: Session, playlist_id: uuid.UUID, *, limit: int = 30) -> dict[str, Any]:
    if session.get(Playlist, playlist_id) is None:
        raise LookupError("domain not found")
    snapshot = current_snapshot(session, playlist_id)
    cursor = session.get(DomainObservationCursor, playlist_id)
    observed_after = cursor.observed_at if cursor else None
    event_after = cursor.event_time_end if cursor else None
    result: dict[str, Any] = {"newly_occurred": [], "newly_mapped": [], "story_updates": [], "needs_review": []}
    result["definitions"] = {
        "newly_occurred": "当前事件中发生时间晚于观察窗口终点的内容",
        "newly_mapped": "当前事件中自上次观察后收录或更新的内容",
        "story_updates": "当前故事中自上次观察后有实质更新的内容",
        "needs_review": "当前带不确定性标记的事件",
    }
    if snapshot is None:
        return result
    def item(row: EventMapCanonical, observed_at: Any) -> dict[str, Any]:
        return {"id": str(row.canonical_id), "snapshot_id": str(snapshot.id), "object_type": "canonical", "object_id": str(row.canonical_id),
                "title": row.title, "occurred_at": _iso(row.event_time_start), "observed_at": _iso(observed_at),
                "web_url": _web_url(playlist_id, object_type="canonical", object_id=row.canonical_id)}
    if event_after is not None:
        result["newly_occurred"] = [item(row, snapshot.finished_at) for row in session.execute(
            select(EventMapCanonical).where(EventMapCanonical.snapshot_id == snapshot.id,
                EventMapCanonical.event_time_start > event_after)
            .order_by(EventMapCanonical.event_time_start.desc(), EventMapCanonical.canonical_id.asc()).limit(limit)
        ).scalars()]
    mapped = select(EventMapCanonical, EventMapCanonicalIdentity.updated_at).join(
        EventMapCanonicalIdentity, EventMapCanonicalIdentity.id == EventMapCanonical.canonical_id,
    ).where(EventMapCanonical.snapshot_id == snapshot.id)
    stories = select(EventMapStory, EventMapStoryIdentity).join(
        EventMapStoryIdentity, EventMapStoryIdentity.id == EventMapStory.story_identity_id,
    ).where(EventMapStory.snapshot_id == snapshot.id)
    if observed_after is not None:
        mapped = mapped.where(EventMapCanonicalIdentity.updated_at > observed_after)
        stories = stories.where(EventMapStoryIdentity.last_material_changed_at > observed_after)
    result["newly_mapped"] = [item(row, observed_at) for row, observed_at in session.execute(
        mapped.order_by(EventMapCanonicalIdentity.updated_at.desc(), EventMapCanonical.canonical_id.asc()).limit(limit)
    )]
    result["story_updates"] = [
        {"id": str(identity.id), "snapshot_id": str(snapshot.id), "object_type": "story", "object_id": str(identity.id),
         "title": identity.stable_title, "occurred_at": _iso(row.event_time_end),
         "observed_at": _iso(identity.last_material_changed_at),
         "web_url": f"/stories?domain_id={playlist_id}&story_id={identity.id}"}
        for row, identity in session.execute(stories.order_by(
            EventMapStoryIdentity.last_material_changed_at.desc(), EventMapStoryIdentity.id.asc()).limit(limit))
    ]
    result["needs_review"] = [dict(item(row, snapshot.finished_at), reasons=row.uncertainty_flags or [])
        for row in session.execute(select(EventMapCanonical).where(
            EventMapCanonical.snapshot_id == snapshot.id, EventMapCanonical.has_uncertainty.is_(True),
        ).order_by(EventMapCanonical.event_time_start.desc(), EventMapCanonical.canonical_id.asc()).limit(limit)).scalars()]
    return result


def canonical_detail(
    session: Session, playlist_id: uuid.UUID, canonical_id: uuid.UUID,
    *, snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    snapshot = observation_snapshot(session, playlist_id, snapshot_id)
    if snapshot is None:
        raise LookupError("current domain projection is unavailable")
    row = session.get(EventMapCanonical, (snapshot.id, canonical_id))
    if row is None:
        raise LookupError("canonical not found in current domain projection")
    return {
        **_readable_story_node(row),
        "snapshot_id": str(snapshot.id),
        "representative_revision_id": str(row.representative_revision_id),
        "web_url": _web_url(playlist_id, object_type="canonical", object_id=canonical_id),
    }


def canonical_directory(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    event_time_start: datetime | None = None,
    event_time_end: datetime | None = None,
    event_type: str | None = None,
    normalized_key: str | None = None,
    entity_type: str | None = None,
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
    if normalized_key:
        entity_scope = select(EventMapEntityIndex.canonical_id).where(
            EventMapEntityIndex.snapshot_id == snapshot.id,
            EventMapEntityIndex.normalized_key == normalized_key,
        )
        if entity_type:
            entity_scope = entity_scope.where(EventMapEntityIndex.entity_type == entity_type)
        statement = statement.where(EventMapCanonical.canonical_id.in_(entity_scope))
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


def _event_record_playback_position(revision: EventMapRecordRevision) -> float | None:
    for raw_item in revision.evidence_json or []:
        item = dict(raw_item)
        provenance = item.get("evidence_json") if isinstance(item.get("evidence_json"), dict) else item
        value = provenance.get("start_seconds")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _latest_video_assets(
    session: Session,
    video_ids: set[uuid.UUID],
) -> dict[tuple[uuid.UUID, str], Asset]:
    if not video_ids:
        return {}
    rows = session.execute(
        select(Asset)
        .where(Asset.video_id.in_(video_ids), Asset.type.in_(["thumbnail", "video"]))
        .order_by(Asset.video_id.asc(), Asset.type.asc(), Asset.created_at.desc(), Asset.id.asc())
    ).scalars().all()
    assets: dict[tuple[uuid.UUID, str], Asset] = {}
    for asset in rows:
        if asset.video_id is not None:
            assets.setdefault((asset.video_id, asset.type), asset)
    return assets


def event_highlights(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    event_date_start: date | None = None,
    event_date_end: date | None = None,
    scope: Literal["event_date", "24h"] = "event_date",
    window_start: date | None = None,
    window_end: date | None = None,
    snapshot_id: uuid.UUID | None = None,
    event_type_code: int | None = None,
    normalized_key: str | None = None,
    entity_type: str | None = None,
    topic_id: uuid.UUID | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """按事件日期或过去 24 小时发布的视频选择 canonical 与可播放来源。"""

    if scope == "event_date":
        if event_date_start is None or event_date_end is None:
            raise ValueError("event date range is required")
        if event_date_start > event_date_end:
            raise ValueError("invalid event date range")
        if (event_date_end - event_date_start).days >= 7:
            raise ValueError("event date range must not exceed 7 days")
    if window_start is not None and window_end is not None and window_start > window_end:
        raise ValueError("invalid event map window")
    snapshot = observation_snapshot(session, playlist_id, snapshot_id)

    epoch_ordinal = date(1970, 1, 1).toordinal()
    published_before = utcnow() if scope == "24h" else None
    published_since = published_before - timedelta(hours=24) if published_before is not None else None
    video_time_clauses = (
        [Video.published_at >= published_since, Video.published_at <= published_before]
        if published_since is not None and published_before is not None else []
    )

    def response(
        *,
        resolved_snapshot_id: uuid.UUID | None,
        matched_event_total: int = 0,
        total: int = 0,
        excluded_imprecise_total: int = 0,
        point_indices: list[int] | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result_items = items or []
        selected_video_ids = {
            item["primary_video"]["video_id"]
            for item in result_items
            if item.get("primary_video", {}).get("video_id")
        }
        return {
            "snapshot_id": str(resolved_snapshot_id) if resolved_snapshot_id is not None else None,
            "scope": scope,
            "event_date_start": _iso(event_date_start) if scope == "event_date" else None,
            "event_date_end": _iso(event_date_end) if scope == "event_date" else None,
            "published_since": _iso(published_since),
            "published_before": _iso(published_before),
            "window_start": window_start.isoformat() if window_start is not None else None,
            "window_end": window_end.isoformat() if window_end is not None else None,
            "matched_event_total": matched_event_total,
            "total": total,
            "shown_total": len(result_items),
            "playable_event_total": total,
            "video_total": len(selected_video_ids),
            "max_cards_per_media": _EVENT_HIGHLIGHT_MAX_CARDS_PER_MEDIA,
            "excluded_imprecise_total": excluded_imprecise_total,
            "point_indices": point_indices or [],
            "items": result_items,
        }

    if snapshot is None:
        return response(resolved_snapshot_id=snapshot.id if snapshot is not None else None)

    scope_clauses: list[Any] = [EventMapCanonical.snapshot_id == snapshot.id]
    if scope == "24h":
        recent_canonicals = (
            select(EventMapCanonicalMember.canonical_id)
            .join(EventMapRecordRevision, EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id)
            .join(Video, Video.id == EventMapRecordRevision.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(
                EventMapCanonicalMember.snapshot_id == snapshot.id,
                PlaylistMedia.playlist_id == playlist_id,
                *video_time_clauses,
            )
        )
        scope_clauses.append(EventMapCanonical.canonical_id.in_(recent_canonicals))
    elif event_date_start is not None and event_date_end is not None:
        scope_clauses.extend([
            EventMapCanonical.event_start_day >= event_date_start.toordinal() - epoch_ordinal,
            EventMapCanonical.event_start_day <= event_date_end.toordinal() - epoch_ordinal,
        ])
    if window_start is not None:
        scope_clauses.append(
            EventMapCanonical.event_end_day >= window_start.toordinal() - epoch_ordinal
        )
    if window_end is not None:
        scope_clauses.append(
            EventMapCanonical.event_start_day <= window_end.toordinal() - epoch_ordinal
        )
    if event_type_code is not None:
        scope_clauses.append(EventMapCanonical.event_type_code == int(event_type_code))
    if normalized_key:
        entity_scope = select(EventMapEntityIndex.canonical_id).where(
            EventMapEntityIndex.snapshot_id == snapshot.id,
            EventMapEntityIndex.normalized_key == normalized_key,
        )
        if entity_type:
            entity_scope = entity_scope.where(EventMapEntityIndex.entity_type == entity_type)
        scope_clauses.append(EventMapCanonical.canonical_id.in_(entity_scope))
    if topic_id is not None:
        topic_scope = select(EventMapTopicMember.canonical_id).where(
            EventMapTopicMember.snapshot_id == snapshot.id,
            EventMapTopicMember.topic_id == topic_id,
        )
        scope_clauses.append(EventMapCanonical.canonical_id.in_(topic_scope))

    excluded_imprecise_total = 0
    if scope == "event_date":
        excluded_imprecise_total = int(
            session.execute(
                select(func.count())
                .select_from(EventMapCanonical)
                .where(
                    *scope_clauses,
                    EventMapCanonical.time_precision.notin_(["day", "second"]),
                )
            ).scalar_one()
            or 0
        )
        scope_clauses.append(EventMapCanonical.time_precision.in_(["day", "second"]))

    video_count = func.count(func.distinct(EventMapRecordRevision.source_video_id))
    source_count = func.count(func.distinct(Video.media_id))
    precision_rank = case(
        (EventMapCanonical.time_precision == "second", 0),
        (EventMapCanonical.time_precision == "day", 1),
        (EventMapCanonical.time_precision == "month", 2),
        (EventMapCanonical.time_precision == "year", 3),
        else_=4,
    )
    ranked_statement = (
        select(
            EventMapCanonical,
            video_count.label("video_count"),
            source_count.label("source_count"),
        )
        .outerjoin(
            EventMapCanonicalMember,
            and_(
                EventMapCanonicalMember.snapshot_id == EventMapCanonical.snapshot_id,
                EventMapCanonicalMember.canonical_id == EventMapCanonical.canonical_id,
            ),
        )
        .outerjoin(
            EventMapRecordRevision,
            EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id,
        )
        .outerjoin(Video, Video.id == EventMapRecordRevision.source_video_id)
        .where(*scope_clauses)
        .group_by(EventMapCanonical)
    )
    ranked_rows = session.execute(
        ranked_statement.order_by(
            source_count.desc(),
            video_count.desc(),
            EventMapCanonical.member_count.desc(),
            EventMapCanonical.has_uncertainty.asc(),
            EventMapCanonical.time_disagreement_count.asc(),
            precision_rank.asc(),
            EventMapCanonical.event_time_start.desc(),
            EventMapCanonical.point_index.asc(),
        )
    ).all()
    canonicals = [canonical for canonical, _video_count, _source_count in ranked_rows]
    ranking_by_canonical = {
        canonical.canonical_id: {
            "canonical_rank": rank,
            "source_count": int(row_source_count or 0),
            "video_count": int(row_video_count or 0),
        }
        for rank, (canonical, row_video_count, row_source_count) in enumerate(ranked_rows, start=1)
    }
    canonical_ids = [canonical.canonical_id for canonical in canonicals]
    if not canonical_ids:
        return response(
            resolved_snapshot_id=snapshot.id,
            excluded_imprecise_total=excluded_imprecise_total,
        )

    record_rows = session.execute(
        select(EventMapCanonicalMember, EventMapRecordRevision, Video, Media)
        .join(
            EventMapRecordRevision,
            EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id,
        )
        .join(Video, Video.id == EventMapRecordRevision.source_video_id)
        .join(Media, Media.id == Video.media_id)
        .join(
            PlaylistMedia,
            and_(
                PlaylistMedia.media_id == Video.media_id,
                PlaylistMedia.playlist_id == playlist_id,
            ),
        )
        .where(
            EventMapCanonicalMember.snapshot_id == snapshot.id,
            EventMapCanonicalMember.canonical_id.in_(canonical_ids),
            *video_time_clauses,
            select(Asset.id)
            .where(Asset.video_id == Video.id, Asset.type == "video")
            .exists(),
        )
        .order_by(
            EventMapCanonicalMember.canonical_id.asc(),
            EventMapCanonicalMember.is_representative.desc(),
            EventMapRecordRevision.id.asc(),
        )
    ).all()
    canonical_by_id = {canonical.canonical_id: canonical for canonical in canonicals}
    candidates_by_canonical: dict[uuid.UUID, list[dict[str, Any]]] = {}
    seen_canonical_video: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for member, revision, video, media in record_rows:
        key = (member.canonical_id, video.id)
        if key in seen_canonical_video:
            continue
        seen_canonical_video.add(key)
        canonical = canonical_by_id[member.canonical_id]
        candidates_by_canonical.setdefault(member.canonical_id, []).append(
            {
                "member": member,
                "revision": revision,
                "video": video,
                "media": media,
                "is_representative": bool(
                    member.is_representative
                    or revision.id == canonical.representative_revision_id
                ),
            }
        )

    playable_event_total = sum(
        1 for canonical_id in canonical_ids if candidates_by_canonical.get(canonical_id)
    )
    card_limit = max(1, min(10, int(limit)))
    selected_events: list[tuple[EventMapCanonical, dict[str, Any]]] = []
    media_card_counts: dict[uuid.UUID, int] = {}
    for canonical in canonicals:
        candidates = candidates_by_canonical.get(canonical.canonical_id, [])
        if not candidates:
            continue
        eligible_candidates = [
            candidate
            for candidate in candidates
            if media_card_counts.get(candidate["media"].id, 0)
            < _EVENT_HIGHLIGHT_MAX_CARDS_PER_MEDIA
        ]
        if not eligible_candidates:
            continue
        # 事件证据排序在此前已固定。每个事件只从自身真实关联视频中选择；
        # 优先使用当前占位更少的媒体，同一媒体达到两张后不再进入卡片序列。
        chosen = min(
            eligible_candidates,
            key=lambda candidate: (
                media_card_counts.get(candidate["media"].id, 0),
                not candidate["is_representative"],
                str(candidate["video"].id),
            ),
        )
        selected_events.append((canonical, chosen))
        chosen_media_id = chosen["media"].id
        media_card_counts[chosen_media_id] = media_card_counts.get(chosen_media_id, 0) + 1
        if len(selected_events) >= card_limit:
            break

    asset_by_video_type = _latest_video_assets(
        session,
        {candidate["video"].id for _canonical, candidate in selected_events},
    )
    media_avatar_asset_ids = {
        candidate["media"].avatar_asset_id
        for _canonical, candidate in selected_events
        if candidate["media"].avatar_asset_id is not None
    }
    media_avatar_assets_by_id = (
        {
            asset.id: asset
            for asset in session.execute(
                select(Asset).where(Asset.id.in_(media_avatar_asset_ids))
            ).scalars().all()
        }
        if media_avatar_asset_ids
        else {}
    )
    timeline_by_video_id = {}
    for _canonical, candidate in selected_events:
        video = candidate["video"]
        if video.id not in timeline_by_video_id:
            timeline_by_video_id[video.id] = resolve_video_timeline(session, video)
    selected_canonical_ids = [canonical.canonical_id for canonical, _candidate in selected_events]
    topic_rows = session.execute(
        select(EventMapTopicMember.canonical_id, EventMapTopic)
        .join(
            EventMapTopic,
            and_(
                EventMapTopic.snapshot_id == EventMapTopicMember.snapshot_id,
                EventMapTopic.topic_id == EventMapTopicMember.topic_id,
            ),
        )
        .where(
            EventMapTopicMember.snapshot_id == snapshot.id,
            EventMapTopicMember.canonical_id.in_(selected_canonical_ids),
            EventMapTopicMember.level == 0,
        )
    ).all() if selected_canonical_ids else []
    topic_by_canonical = {canonical_id: topic for canonical_id, topic in topic_rows}

    items: list[dict[str, Any]] = []
    for representative_rank, (canonical, candidate) in enumerate(selected_events, start=1):
        primary_canonical_id = canonical.canonical_id
        primary_revision = candidate["revision"]
        primary_video = candidate["video"]
        primary_media = candidate["media"]
        primary_timeline = timeline_by_video_id[primary_video.id]
        thumbnail_asset = build_asset_ref(asset_by_video_type.get((primary_video.id, "thumbnail")))
        media_avatar_asset = build_asset_ref(
            media_avatar_assets_by_id.get(primary_media.avatar_asset_id),
            include_presigned=False,
        )
        topic = topic_by_canonical.get(primary_canonical_id)
        ranking = ranking_by_canonical[primary_canonical_id]
        items.append(
            {
                "canonical_id": str(primary_canonical_id),
                "canonical_ids": [str(primary_canonical_id)],
                "canonical_count": 1,
                "point_index": int(canonical.point_index),
                "point_indices": [int(canonical.point_index)],
                "title": canonical.title,
                "summary": canonical.summary,
                "event_time_start": _iso(canonical.event_time_start),
                "event_time_end": _iso(canonical.event_time_end),
                "time_precision": canonical.time_precision,
                "member_count": int(canonical.member_count or 0),
                "representative_rank": representative_rank,
                "ranking_factors": {
                    "canonical_rank": ranking["canonical_rank"],
                    "source_count": ranking["source_count"],
                    "video_count": ranking["video_count"],
                    "member_count": int(canonical.member_count or 0),
                    "has_uncertainty": bool(canonical.has_uncertainty),
                    "time_disagreement_count": int(canonical.time_disagreement_count or 0),
                    "time_precision": canonical.time_precision,
                },
                "topic": (
                    {"topic_id": str(topic.topic_id), "label": topic.label}
                    if topic is not None
                    else None
                ),
                "video_count": ranking["video_count"],
                "primary_video": {
                    "video_id": str(primary_video.id),
                    "title": primary_video.title,
                    "media_id": str(primary_media.id),
                    "media_name": primary_media.name,
                    "media_avatar_asset": (
                        media_avatar_asset.model_dump() if media_avatar_asset else None
                    ),
                    "thumbnail_url": primary_video.thumbnail_url,
                    "thumbnail_asset": thumbnail_asset.model_dump() if thumbnail_asset else None,
                    "player_url": f"/video?video_id={primary_video.id}",
                    "duration_sec": primary_video.duration_sec,
                    "published_at": _iso(primary_video.published_at),
                    "content_published_at": _iso(primary_timeline.content_published_at),
                    "timeline_at": _iso(primary_timeline.timeline_at),
                    "source_date": (
                        primary_timeline.timeline_at.date().isoformat()
                        if primary_timeline.timeline_at is not None
                        else None
                    ),
                    "time_source": primary_timeline.time_source,
                    "time_status": primary_timeline.time_status,
                    "time_confidence": primary_timeline.time_confidence,
                    "playback_position_seconds": _event_record_playback_position(primary_revision),
                },
            }
        )
    return response(
        resolved_snapshot_id=snapshot.id,
        matched_event_total=len(canonicals),
        total=playable_event_total,
        excluded_imprecise_total=excluded_imprecise_total,
        point_indices=[int(canonical.point_index) for canonical in canonicals],
        items=items,
    )


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


def story_directory(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    scope: str = "all",
    query: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """读取故事更新目录，而不是把当前快照中的故事当成一批技术对象展示。"""

    valid_scopes = {"all", "attention", "followed", "established", "emerging"}
    if scope not in valid_scopes:
        raise ValueError("invalid story scope")

    snapshot = current_snapshot(session, playlist_id)
    if snapshot is None:
        return {
            "snapshot_id": None,
            "items": [],
            "total": 0,
            "scope": scope,
            "q": str(query or "").strip(),
            "limit": limit,
            "offset": 0,
            "has_more": False,
            "baseline_suggestion": False,
        }
    page_offset = max(0, int(offset or 0))
    page_limit = max(1, int(limit)) if limit is not None else None
    search_text = str(query or "").strip()
    cursor = session.get(DomainObservationCursor, playlist_id)
    has_read_baseline = session.execute(
        select(StoryReadState.story_identity_id)
        .join(EventMapStoryIdentity, EventMapStoryIdentity.id == StoryReadState.story_identity_id)
        .where(
            EventMapStoryIdentity.playlist_id == playlist_id,
            StoryReadState.last_read_at.is_not(None),
        )
        .limit(1)
    ).scalar_one_or_none() is not None
    baseline_suggestion = scope == "attention" and cursor is None and not has_read_baseline

    base_statement = (
        select(EventMapStory, EventMapStoryIdentity, StoryReadState)
        .join(EventMapStoryIdentity, EventMapStoryIdentity.id == EventMapStory.story_identity_id)
        .outerjoin(StoryReadState, StoryReadState.story_identity_id == EventMapStory.story_identity_id)
        .where(EventMapStory.snapshot_id == snapshot.id)
    )
    filters: list[Any] = []
    if scope == "followed":
        filters.append(StoryReadState.followed.is_(True))
    elif scope == "established":
        filters.append(EventMapStory.maturity == "established")
    elif scope == "emerging":
        filters.append(EventMapStory.maturity == "emerging")
    elif scope == "attention":
        if baseline_suggestion:
            # 阅读位置和关注状态都不是“已经读过”的证明。首访仍给成熟故事推荐，
            # 但用户主动关注且尚未读完的初步线索也不能因此从阅读队列消失。
            filters.append(
                or_(
                    EventMapStory.maturity == "established",
                    and_(
                        StoryReadState.followed.is_(True),
                        or_(
                            StoryReadState.last_read_at.is_(None),
                            EventMapStoryIdentity.last_material_changed_at > StoryReadState.last_read_at,
                        ),
                    ),
                )
            )
        else:
            interacted_update = and_(
                StoryReadState.story_identity_id.is_not(None),
                or_(
                    StoryReadState.last_read_at.is_(None),
                    EventMapStoryIdentity.last_material_changed_at > StoryReadState.last_read_at,
                ),
            )
            unobserved_established_update = (
                and_(
                    EventMapStory.maturity == "established",
                    StoryReadState.story_identity_id.is_(None),
                    EventMapStoryIdentity.last_material_changed_at > cursor.observed_at,
                )
                if cursor is not None and cursor.observed_at is not None
                else None
            )
            filters.append(
                or_(interacted_update, unobserved_established_update)
                if unobserved_established_update is not None
                else interacted_update
            )
    if search_text:
        pattern = f"%{search_text}%"
        latest_member = aliased(EventMapStoryMember)
        latest_canonical = aliased(EventMapCanonical)
        latest_position = (
            select(func.max(EventMapStoryMember.position))
            .where(
                EventMapStoryMember.snapshot_id == EventMapStory.snapshot_id,
                EventMapStoryMember.story_id == EventMapStory.story_id,
            )
            .correlate(EventMapStory)
            .scalar_subquery()
        )
        progress_match = (
            select(latest_member.canonical_id)
            .join(
                latest_canonical,
                and_(
                    latest_canonical.snapshot_id == latest_member.snapshot_id,
                    latest_canonical.canonical_id == latest_member.canonical_id,
                ),
            )
            .where(
                latest_member.snapshot_id == EventMapStory.snapshot_id,
                latest_member.story_id == EventMapStory.story_id,
                latest_member.position == latest_position,
                latest_canonical.title.ilike(pattern),
            )
            .exists()
        )
        filters.append(
            or_(
                EventMapStoryIdentity.stable_title.ilike(pattern),
                progress_match,
            )
        )
    if filters:
        base_statement = base_statement.where(*filters)

    count_statement = (
        select(func.count())
        .select_from(EventMapStory)
        .join(EventMapStoryIdentity, EventMapStoryIdentity.id == EventMapStory.story_identity_id)
        .outerjoin(StoryReadState, StoryReadState.story_identity_id == EventMapStory.story_identity_id)
        .where(EventMapStory.snapshot_id == snapshot.id)
    )
    if filters:
        count_statement = count_statement.where(*filters)
    total = int(session.execute(count_statement).scalar_one() or 0)
    if baseline_suggestion:
        # 首访只给一页推荐，不暴露“继续加载整个成熟故事库”的假入口。
        total = min(total, page_limit or 80)

    statement = base_statement.order_by(
        EventMapStoryIdentity.last_material_changed_at.desc(),
        EventMapStoryIdentity.id.asc(),
    )
    if baseline_suggestion and page_offset:
        statement = statement.limit(0)
    elif page_offset:
        statement = statement.offset(page_offset)
    if not (baseline_suggestion and page_offset) and page_limit is not None:
        statement = statement.limit(page_limit)
    elif not (baseline_suggestion and page_offset) and baseline_suggestion:
        statement = statement.limit(total)
    rows = session.execute(statement).all()

    story_ids = [story.story_id for story, _identity, _read_state in rows]
    identity_ids = [identity.id for _story, identity, _read_state in rows]
    progress_by_story: dict[uuid.UUID, EventMapCanonical] = {}
    edges_by_story: dict[uuid.UUID, list[EventMapStoryEdge]] = {story_id: [] for story_id in story_ids}
    if story_ids:
        progress_rows = session.execute(
            select(EventMapStoryMember, EventMapCanonical)
            .join(
                EventMapCanonical,
                and_(
                    EventMapCanonical.snapshot_id == EventMapStoryMember.snapshot_id,
                    EventMapCanonical.canonical_id == EventMapStoryMember.canonical_id,
                ),
            )
            .where(
                EventMapStoryMember.snapshot_id == snapshot.id,
                EventMapStoryMember.story_id.in_(story_ids),
            )
            .order_by(EventMapStoryMember.story_id, EventMapStoryMember.position.desc())
        ).all()
        for member, canonical in progress_rows:
            progress_by_story.setdefault(member.story_id, canonical)
        for edge in session.execute(
            select(EventMapStoryEdge).where(
                EventMapStoryEdge.snapshot_id == snapshot.id,
                EventMapStoryEdge.story_id.in_(story_ids),
            )
        ).scalars():
            edges_by_story.setdefault(edge.story_id, []).append(edge)

    output: list[dict[str, Any]] = []
    for story, identity, read_state in rows:
        identity_id = identity.id
        recommended = bool(
            baseline_suggestion
            and story.maturity == "established"
            and read_state is None
        )
        progress = progress_by_story.get(story.story_id)
        latest_progress = {
            "title": progress.title if progress else story.title,
            "summary": progress.summary if progress else story.summary,
            "occurred_at": _iso(progress.event_time_start if progress else story.event_time_end),
        }
        last_material_at = identity.last_material_changed_at
        if read_state is not None:
            unread = read_state.last_read_at is None or _datetime_after(last_material_at, read_state.last_read_at)
        elif cursor is not None and cursor.observed_at is not None:
            unread = bool(
                story.maturity == "established"
                and _datetime_after(last_material_at, cursor.observed_at)
            )
        else:
            unread = False
        story_edges = edges_by_story.get(story.story_id, [])
        support_revision_ids = {
            str(revision_id)
            for edge in story_edges
            for revision_id in (edge.evidence_revision_ids or [])
        }
        output.append(
            {
                "story_identity_id": str(identity_id),
                "story_id": str(story.story_id),
                "snapshot_id": str(story.snapshot_id),
                "stable_title": identity.stable_title,
                "latest_progress": latest_progress,
                "title": identity.stable_title,
                "summary": latest_progress["summary"],
                "story_type": story.story_type,
                "anchor_key": story.anchor_key,
                "maturity": story.maturity,
                "quality_score": story.quality_score,
                "event_time_start": _iso(story.event_time_start),
                "event_time_end": _iso(story.event_time_end),
                "canonical_count": int(story.canonical_count or 0),
                "relation_count": len(story_edges),
                "support_record_count": len(support_revision_ids),
                "last_material_snapshot_id": str(identity.last_material_snapshot_id) if identity.last_material_snapshot_id else None,
                "last_material_changed_at": _iso(last_material_at),
                "baseline_suggestion": recommended,
                "recommended": recommended,
                "followed": bool(read_state.followed) if read_state else False,
                "last_read_at": _iso(read_state.last_read_at) if read_state else None,
                "unread": unread,
                "web_url": _web_url(playlist_id, object_type="story", object_id=identity_id, snapshot_id=snapshot.id),
            }
        )
    return {
        "snapshot_id": str(snapshot.id),
        "items": output,
        "total": total,
        "scope": scope,
        "q": search_text,
        "limit": page_limit,
        "offset": page_offset,
        "has_more": False if baseline_suggestion else page_offset + len(output) < total,
        "baseline_suggestion": baseline_suggestion,
    }








def _readable_story_node(row: Any) -> dict[str, Any]:
    return {
        "canonical_id": str(row.canonical_id),
        "point_index": int(row.point_index),
        "title": row.title,
        "summary": row.summary,
        "occurred_at": _iso(row.event_time_start),
        "event_time_start": _iso(row.event_time_start),
        "event_time_end": _iso(row.event_time_end),
        "time_precision": row.time_precision,
        "event_type": row.event_type,
        "member_count": int(row.member_count or 0),
        "has_uncertainty": bool(row.has_uncertainty),
        "uncertainty_flags": row.uncertainty_flags or [],
    }


def _readable_story_edge(
    edge: dict[str, Any],
    canonical_by_id: dict[uuid.UUID, Any],
) -> dict[str, Any] | None:
    source_id = uuid.UUID(str(edge["source_canonical_id"]))
    target_id = uuid.UUID(str(edge["target_canonical_id"]))
    source = canonical_by_id.get(source_id)
    target = canonical_by_id.get(target_id)
    if source is None or target is None:
        return None
    relation_type = str(edge.get("relation_type") or "")
    evidence = dict(edge.get("evidence") or {})
    claim_bridge = dict(evidence.get("claim_bridge") or {})
    evidence_ids = sorted({str(value) for value in (edge.get("evidence_revision_ids") or [])})
    return {
        "edge_id": str(edge.get("edge_id")),
        "source_canonical_id": str(source_id),
        "target_canonical_id": str(target_id),
        "source_point_index": int(source.point_index),
        "target_point_index": int(target.point_index),
        "source_title": source.title,
        "target_title": target.title,
        "relation_type": relation_type,
        "relation_label": _STORY_RELATION_LABELS.get(relation_type, "存在关联"),
        "support_record_count": len(evidence_ids),
        "explanation": {
            "source_claim": claim_bridge.get("source_claim") or evidence.get("source_claim") or source.summary or source.title,
            "target_claim": claim_bridge.get("target_claim") or evidence.get("target_claim") or target.summary or target.title,
            "source_excerpts": list(evidence.get("source_excerpts") or []),
            "target_excerpts": list(evidence.get("target_excerpts") or []),
            "gap_days": evidence.get("gap_days"),
        },
    }




def _story_edge_payload(row: EventMapStoryEdge) -> dict[str, Any]:
    return {
        "edge_id": str(row.edge_id),
        "source_canonical_id": str(row.source_canonical_id),
        "target_canonical_id": str(row.target_canonical_id),
        "relation_type": row.relation_type,
        "direction": row.direction,
        "status": row.status,
        "evidence_revision_ids": list(row.evidence_revision_ids or []),
        "evidence": dict(row.evidence_json or {}),
    }


def story_detail(
    session: Session, playlist_id: uuid.UUID, identity_id: uuid.UUID,
    *, snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    identity = session.get(EventMapStoryIdentity, identity_id)
    if identity is None or identity.playlist_id != playlist_id:
        raise LookupError("story not found")
    snapshot = observation_snapshot(session, playlist_id, snapshot_id)
    if snapshot is None:
        raise LookupError("current domain projection is unavailable")
    story = session.execute(select(EventMapStory).where(
        EventMapStory.snapshot_id == snapshot.id,
        EventMapStory.story_identity_id == identity_id,
    )).scalar_one_or_none()
    if story is None:
        raise LookupError("story not found in current domain projection")
    nodes = session.execute(select(EventMapCanonical).join(EventMapStoryMember, and_(
        EventMapStoryMember.snapshot_id == EventMapCanonical.snapshot_id,
        EventMapStoryMember.canonical_id == EventMapCanonical.canonical_id,
    )).where(EventMapStoryMember.snapshot_id == snapshot.id,
             EventMapStoryMember.story_id == story.story_id)
        .order_by(EventMapCanonical.event_time_start.asc(), EventMapStoryMember.position.asc())
    ).scalars().all()
    canonical_by_id = {row.canonical_id: row for row in nodes}
    edges = session.execute(select(EventMapStoryEdge).where(
        EventMapStoryEdge.snapshot_id == snapshot.id,
        EventMapStoryEdge.story_id == story.story_id,
    ).order_by(EventMapStoryEdge.edge_id.asc())).scalars().all()
    trajectory = {
        "snapshot_id": str(snapshot.id),
        "nodes": [_readable_story_node(row) for row in nodes],
        "edges": [value for row in edges
                  if (value := _readable_story_edge(_story_edge_payload(row), canonical_by_id)) is not None],
    }
    support_ids = {str(value) for row in edges for value in (row.evidence_revision_ids or [])}
    read_state = session.get(StoryReadState, identity_id)
    cursor = session.get(DomainObservationCursor, playlist_id)
    active = current_snapshot(session, playlist_id)
    is_previous_projection = active is None or active.id != snapshot.id
    unread = False
    if not is_previous_projection:
        if read_state is not None:
            unread = read_state.last_read_at is None or _datetime_after(identity.last_material_changed_at, read_state.last_read_at)
        elif cursor is not None and cursor.observed_at is not None and story.maturity == "established":
            unread = _datetime_after(identity.last_material_changed_at, cursor.observed_at)
    return {
        "story_identity_id": str(identity_id),
        "stable_title": identity.stable_title,
        "status": identity.status,
        "followed": bool(read_state.followed) if read_state else False,
        "last_read_snapshot_id": str(read_state.last_read_snapshot_id) if read_state and read_state.last_read_snapshot_id else None,
        "last_read_at": _iso(read_state.last_read_at) if read_state else None,
        "last_position": read_state.last_position if read_state else None,
        "last_material_snapshot_id": str(identity.last_material_snapshot_id) if identity.last_material_snapshot_id else None,
        "last_material_changed_at": _iso(identity.last_material_changed_at),
        "is_historical": is_previous_projection,
        "can_mark_read": not is_previous_projection,
        "unread": unread,
        "selected_revision": {
            "story_id": str(story.story_id), "snapshot_id": str(snapshot.id),
            "title": story.title, "summary": story.summary,
            "maturity": story.maturity,
            "event_time_start": _iso(story.event_time_start),
            "event_time_end": _iso(story.event_time_end),
            "member_ids": [str(row.canonical_id) for row in nodes],
            "observed_at": _iso(snapshot.finished_at),
        },
        "latest_progress": trajectory["nodes"][-1] if nodes else None,
        "maturity": story.maturity,
        "event_time_start": _iso(story.event_time_start),
        "event_time_end": _iso(story.event_time_end),
        "event_count": len(nodes), "relation_count": len(edges),
        "support_record_count": len(support_ids),
        "trajectory": trajectory,
        "brief_references": object_brief_references(session, playlist_id=playlist_id, object_type="story", object_id=identity_id),
        "web_url": f"/stories?domain_id={playlist_id}&story_id={identity_id}",
    }


def story_edge_support(
    session: Session,
    playlist_id: uuid.UUID,
    identity_id: uuid.UUID,
    edge_id: uuid.UUID,
    *,
    snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """按选中关系懒加载记录级支持；不把记录级摘录冒充为逐段关系归因。"""

    if snapshot_id is None:
        raise ValueError("snapshot_id is required")
    identity = session.get(EventMapStoryIdentity, identity_id)
    if identity is None or identity.playlist_id != playlist_id:
        raise LookupError("story not found")
    snapshot = observation_snapshot(session, playlist_id, snapshot_id)
    if snapshot is None:
        raise LookupError("domain projection is unavailable")
    story = session.execute(select(EventMapStory).where(
        EventMapStory.snapshot_id == snapshot.id,
        EventMapStory.story_identity_id == identity_id,
    )).scalar_one_or_none()
    if story is None:
        raise LookupError("story not found in domain projection")
    edge = session.get(EventMapStoryEdge, (snapshot.id, edge_id))
    if edge is None or edge.story_id != story.story_id:
        raise LookupError("story relation not found")
    raw_edge = _story_edge_payload(edge)
    canonical_ids = {edge.source_canonical_id, edge.target_canonical_id}
    canonical_by_id = {row.canonical_id: row for row in session.execute(
        select(EventMapCanonical).where(EventMapCanonical.snapshot_id == snapshot.id,
                                       EventMapCanonical.canonical_id.in_(canonical_ids))
    ).scalars()}
    readable_edge = _readable_story_edge(raw_edge, canonical_by_id)
    if readable_edge is None:
        raise LookupError("story relation events not found")
    support_revision_ids = sorted({uuid.UUID(str(value)) for value in (edge.evidence_revision_ids or [])}, key=str)

    record_rows = []
    if support_revision_ids:
        record_rows = session.execute(
            select(EventMapRecordRevision, Video, Media)
            .join(Video, Video.id == EventMapRecordRevision.source_video_id)
            .join(Media, Media.id == Video.media_id)
            .join(
                PlaylistMedia,
                and_(
                    PlaylistMedia.media_id == Media.id,
                    PlaylistMedia.playlist_id == playlist_id,
                ),
            )
            .where(EventMapRecordRevision.id.in_(support_revision_ids))
        ).all()
        if {record.id for record, _video, _media in record_rows} != set(support_revision_ids):
            raise LookupError("story relation support does not belong to domain")

    video_ids = {video.id for _record, video, _media in record_rows}
    transcript_assets = session.execute(
        select(Asset)
        .where(
            Asset.video_id.in_(video_ids),
            Asset.type == "transcript",
            Asset.format == "txt",
            Asset.variant == "plain",
        )
        .order_by(Asset.created_at.desc(), Asset.id.asc())
    ).scalars().all() if video_ids else []
    transcript_candidates_by_video: dict[uuid.UUID, list[Asset]] = {}
    for asset in transcript_assets:
        transcript_candidates_by_video.setdefault(asset.video_id, []).append(asset)
    current_transcript_by_video: dict[uuid.UUID, Asset] = {}
    for video_id, candidates in transcript_candidates_by_video.items():
        selected_asset = next(
            (
                asset
                for source_name, language in TRANSCRIPT_SOURCE_LANGUAGE_ORDER
                for asset in candidates
                if asset.source == source_name and asset.language == language
            ),
            candidates[0],
        )
        current_transcript_by_video[video_id] = selected_asset

    records: list[dict[str, Any]] = []
    for record, video, media in sorted(record_rows, key=lambda item: str(item[0].id)):
        current_transcript = current_transcript_by_video.get(video.id)
        evidence_items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(record.evidence_json or []):
            item = dict(raw_item)
            provenance = item.get("evidence_json") if isinstance(item.get("evidence_json"), dict) else item
            frozen_asset_id = item.get("transcript_asset_id")
            if frozen_asset_id is None:
                version_status = "not_applicable" if provenance.get("source_kind") != "transcript" else "unknown"
            elif current_transcript is None:
                version_status = "unavailable"
            elif str(frozen_asset_id) == str(current_transcript.id):
                version_status = "current"
            else:
                version_status = "superseded"
            playback_position = provenance.get("start_seconds")
            evidence_items.append(
                {
                    "id": f"{record.id}:{index}",
                    "text": item.get("evidence_text") or item.get("text"),
                    "source_label": provenance.get("source_label"),
                    "verified": provenance.get("verified") is True,
                    "source_version_status": version_status,
                    "playback_position_seconds": playback_position,
                    "playback_mapping": "exact_segment" if playback_position is not None else "char_range_only",
                }
            )
        statuses = {item["source_version_status"] for item in evidence_items}
        if "superseded" in statuses:
            source_version_status = "superseded"
        elif "unavailable" in statuses:
            source_version_status = "unavailable"
        elif "unknown" in statuses or not statuses:
            source_version_status = "unknown"
        elif statuses == {"not_applicable"}:
            source_version_status = "not_applicable"
        else:
            source_version_status = "current"
        playback_position = next(
            (
                item["playback_position_seconds"]
                for item in evidence_items
                if item["playback_position_seconds"] is not None
            ),
            None,
        )
        player_url = f"/video?video_id={video.id}"
        records.append(
            {
                "record_revision_id": str(record.id),
                "title": record.title or video.title,
                "summary": record.summary,
                "event_time_start": _iso(record.event_time_start),
                "event_time_end": _iso(record.event_time_end),
                "source": video.title,
                "source_title": video.title,
                "media_name": media.name,
                "source_url": video.url,
                "player_url": player_url,
                "excerpt": next((item["text"] for item in evidence_items if item["text"]), None),
                "verified": bool(evidence_items) and all(item["verified"] for item in evidence_items),
                "source_version_status": source_version_status,
                "playback_position_seconds": playback_position,
                "evidence": evidence_items,
                "source_context": {
                    "video_id": str(video.id),
                    "title": video.title,
                    "url": video.url,
                    "player_url": player_url,
                    "media_id": str(media.id),
                    "media_name": media.name,
                },
            }
        )
    return {
        "story_identity_id": str(identity_id),
        "snapshot_id": str(snapshot.id),
        "support_granularity": "record_revision",
        "edge": readable_edge,
        "records": records,
        "support_records": records,
        "audit_note": "这些记录支持两端事件及关系判定；当前未建立逐段摘录到关系的精确归因。",
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
    active_snapshot = current_snapshot(session, playlist_id)
    if mark_read:
        if active_snapshot is None:
            raise ValueError("current domain projection is unavailable")
        if snapshot_id is None:
            snapshot_id = active_snapshot.id if active_snapshot else None
        elif active_snapshot is None or snapshot_id != active_snapshot.id:
            raise ValueError("historical story revision cannot clear the current unread state")
    if snapshot_id is not None:
        revision = session.execute(
            select(EventMapStory.story_id).where(
                EventMapStory.story_identity_id == identity_id,
                EventMapStory.snapshot_id == snapshot_id,
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
        "references": [brief_reference_payload(row, brief.playlist_id, brief=brief) for row in refs],
        "web_url": f"/briefs?domain_id={brief.playlist_id}&brief_id={brief.id}",
    }


def brief_reference_payload(
    row: BriefReference,
    playlist_id: uuid.UUID,
    *,
    brief: Brief | None = None,
) -> dict[str, Any]:
    context = dict(row.context or {})
    web_url = context.get("web_url") or _web_url(
        playlist_id,
        object_type=row.object_type,
        object_id=row.object_id,
        snapshot_id=row.snapshot_id,
    )
    if row.object_type == "story":
        params = {
            "domain_id": str(playlist_id),
            "story_id": str(row.object_id),
        }
        focus_canonical_id = context.get("focus_canonical_id") or context.get("target_canonical_id")
        if focus_canonical_id:
            params["focus_canonical_id"] = str(focus_canonical_id)
        web_url = f"/stories?{urlencode(params)}"
    return {
        "id": str(row.id),
        "brief_id": str(row.brief_id),
        "granularity": brief.granularity if brief is not None else None,
        "period_start": brief.period_start.isoformat() if brief is not None else None,
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
        "web_url": web_url,
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
            select(BriefReference, Brief)
            .join(Brief, Brief.id == BriefReference.brief_id)
            .where(
                Brief.playlist_id == playlist_id,
                BriefReference.object_type == "canonical",
                BriefReference.object_id.in_(canonical_ids),
                BriefReference.snapshot_id == snapshot.id,
            )
            .order_by(Brief.period_start.desc(), BriefReference.position.asc())
        ).all()
        return [brief_reference_payload(row, playlist_id, brief=brief) for row, brief in rows]
    statement = (
        select(BriefReference, Brief)
        .join(Brief, Brief.id == BriefReference.brief_id)
        .where(
            Brief.playlist_id == playlist_id,
            BriefReference.object_type == object_type,
            BriefReference.object_id == object_id,
        )
        .order_by(Brief.period_start.desc(), BriefReference.position.asc())
    )
    if snapshot_id is not None:
        statement = statement.where(BriefReference.snapshot_id == snapshot_id)
    rows = session.execute(statement).all()
    return [brief_reference_payload(row, playlist_id, brief=brief) for row, brief in rows]


def _story_evidence_references(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    revision_ids: list[uuid.UUID],
    snapshot_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    if not revision_ids or snapshot_id is None:
        return []
    selected_revision_ids = {str(value) for value in revision_ids}
    rows = session.execute(
        select(
            EventMapStory.story_identity_id,
            EventMapStory.title,
            EventMapStoryEdge.edge_id,
            EventMapStoryEdge.source_canonical_id,
            EventMapStoryEdge.target_canonical_id,
            EventMapStoryEdge.relation_type,
            EventMapStoryEdge.evidence_revision_ids,
        )
        .join(
            EventMapStoryEdge,
            and_(
                EventMapStoryEdge.snapshot_id == EventMapStory.snapshot_id,
                EventMapStoryEdge.story_id == EventMapStory.story_id,
            ),
        )
        .where(
            EventMapStory.snapshot_id == snapshot_id,
            or_(
                *[
                    EventMapStoryEdge.evidence_revision_ids.contains([revision_id])
                    for revision_id in sorted(selected_revision_ids)
                ]
            ),
        )
        .order_by(EventMapStory.story_identity_id, EventMapStoryEdge.edge_id)
    ).all()
    output: list[dict[str, Any]] = []
    for row in rows:
        evidence_revision_id = min(set(row.evidence_revision_ids) & selected_revision_ids)
        output.append(
            {
                "story_identity_id": str(row.story_identity_id),
                "story_title": row.title,
                "edge_id": str(row.edge_id),
                "source_canonical_id": str(row.source_canonical_id),
                "target_canonical_id": str(row.target_canonical_id),
                "relation_type": row.relation_type,
                "evidence_revision_id": evidence_revision_id,
                "snapshot_id": str(snapshot_id),
                "web_url": _web_url(
                    playlist_id,
                    object_type="story",
                    object_id=row.story_identity_id,
                    snapshot_id=snapshot_id,
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
    snapshot = current_snapshot(session, playlist_id)
    canonical_ids = session.execute(
        select(EventMapCanonicalMember.canonical_id)
        .where(
            EventMapCanonicalMember.snapshot_id == snapshot.id,
            EventMapCanonicalMember.record_revision_id == revision_id,
        )
        .distinct()
    ).scalars().all() if snapshot is not None else []
    brief_refs = session.execute(
        select(BriefReference, Brief)
        .join(Brief, Brief.id == BriefReference.brief_id)
        .where(
            Brief.playlist_id == playlist_id,
            BriefReference.evidence_revision_id == revision_id,
        )
    ).all()
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
            snapshot_id=snapshot.id if snapshot is not None else None,
        ),
        "brief_references": [
            brief_reference_payload(row, playlist_id, brief=brief)
            for row, brief in brief_refs
        ],
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
        select(BriefReference, Brief)
        .join(Brief, Brief.id == BriefReference.brief_id)
        .where(
            Brief.playlist_id == playlist_id,
            BriefReference.evidence_revision_id.in_(revision_ids),
        )
    ).all() if revision_ids else []
    snapshot = current_snapshot(session, playlist_id)
    canonicals = session.execute(
        select(EventMapCanonical.canonical_id, EventMapCanonical.title)
        .join(
            EventMapCanonicalMember,
            and_(
                EventMapCanonicalMember.snapshot_id == EventMapCanonical.snapshot_id,
                EventMapCanonicalMember.canonical_id == EventMapCanonical.canonical_id,
            ),
        )
        .where(
            EventMapCanonical.snapshot_id == snapshot.id,
            EventMapCanonicalMember.record_revision_id.in_(revision_ids),
        )
        .distinct()
        .order_by(EventMapCanonical.canonical_id)
    ).all() if revision_ids and snapshot is not None else []
    return {
        "video_id": str(video_id),
        "canonical_references": [
            {
                "canonical_id": str(canonical_id),
                "title": title,
                "web_url": _web_url(playlist_id, object_type="canonical", object_id=canonical_id),
            }
            for canonical_id, title in canonicals
        ],
        "story_references": _story_evidence_references(
            session,
            playlist_id=playlist_id,
            revision_ids=revision_ids,
            snapshot_id=snapshot.id if snapshot is not None else None,
        ),
        "brief_references": [
            brief_reference_payload(row, playlist_id, brief=brief)
            for row, brief in brief_refs
        ],
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
