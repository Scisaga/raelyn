from __future__ import annotations

from datetime import date, datetime
import uuid
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from raelyn.api.asset_refs import build_asset_ref
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import Asset, Brief, Job, Media, Playlist, PlaylistMedia, Video
from raelyn.services.downloads import build_download_filename, content_disposition_attachment
from raelyn.services.periods import local_date, normalize_granularity, period_bounds_utc, period_end_inclusive, period_start
from raelyn.services.s3 import s3_get_bytes
from raelyn.services.transcripts import pick_transcript_asset, read_text_asset, transcript_polish_method
from raelyn.services.video_meta import parse_published_at

from .chunking import DEFAULT_TRANSCRIPT_CHUNK_SIZE, build_chunk_bounds, get_text_chunk, normalize_chunk_size
from .serialize import serialize_for_mcp


_PUBLISHED_AT_BACKFILLED = False
_PRESIGNED_EXPIRES_SECONDS = 3600
_BRIEF_BODY_MIME_TYPE = "text/markdown"


def _parse_uuid(value: str | uuid.UUID, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except Exception as exc:
        raise ValueError(f"invalid {field} uuid") from exc


def _parse_date(value: str | date, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value).strip())
    except Exception as exc:
        raise ValueError(f"invalid {field} date (expected YYYY-MM-DD)") from exc


def _parse_datetime(value: str | datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception as exc:
        raise ValueError(f"invalid {field} datetime (expected ISO8601)") from exc


def _clamp_limit(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError("limit must be an integer") from exc
    if parsed <= 0:
        raise ValueError("limit must be >= 1")
    return min(parsed, maximum)


def _clamp_offset(value: int | None) -> int:
    if value is None:
        return 0
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError("offset must be an integer") from exc
    if parsed < 0:
        raise ValueError("offset must be >= 0")
    return parsed


def _media_avatar_asset(session: Session, media: Any) -> dict[str, Any] | None:
    asset_id = getattr(media, "avatar_asset_id", None)
    if not asset_id:
        return None
    asset = session.get(Asset, asset_id)
    return serialize_for_mcp(build_asset_ref(asset, expires_seconds=_PRESIGNED_EXPIRES_SECONDS).model_dump()) if asset else None


def _video_description(video: Video) -> str | None:
    desc = video.description
    if (not desc) and isinstance(video.raw_info, dict) and video.raw_info.get("description"):
        try:
            desc = str(video.raw_info.get("description") or "")
        except Exception:
            desc = None
    return desc


def _media_payload(session: Session, media: Media, *, local_video_count: int | None = None) -> dict[str, Any]:
    return {
        "id": str(media.id),
        "provider": media.provider,
        "provider_media_id": media.provider_media_id,
        "url": media.url,
        "monitor_enabled": bool(media.monitor_enabled),
        "name": media.name,
        "avatar_asset": _media_avatar_asset(session, media),
        "description": media.description,
        "subscriber_count": media.subscriber_count,
        "video_count": int(local_video_count if local_video_count is not None else (media.video_count or 0)),
        "last_profile_sync_at": media.last_profile_sync_at,
        "last_video_sync_at": media.last_video_sync_at,
        "created_at": media.created_at,
        "updated_at": media.updated_at,
    }


def _asset_payload(asset: Asset, *, media: Media | None = None, video: Video | None = None) -> dict[str, Any]:
    filename = None
    download_url = None
    ref = None
    if media is not None and video is not None:
        filename = build_download_filename(
            media_name=media.name,
            title=video.title,
            fallback_id=video.provider_video_id,
            ext=asset.format,
        )
        try:
            ref = build_asset_ref(
                asset,
                filename=filename,
                response_content_disposition=content_disposition_attachment(filename),
                expires_seconds=_PRESIGNED_EXPIRES_SECONDS,
            )
            download_url = ref.download_presigned_url
        except Exception:
            download_url = None
            ref = build_asset_ref(asset, expires_seconds=_PRESIGNED_EXPIRES_SECONDS)
    else:
        ref = build_asset_ref(asset, expires_seconds=_PRESIGNED_EXPIRES_SECONDS)
    return {
        "id": str(asset.id),
        "video_id": str(asset.video_id) if asset.video_id else None,
        "type": asset.type,
        "format": asset.format,
        "language": asset.language,
        "source": asset.source,
        "variant": asset.variant,
        "size_bytes": asset.size_bytes,
        "checksum_sha256": asset.checksum_sha256,
        "meta": serialize_for_mcp(asset.meta),
        "presigned_url": ref.presigned_url if ref else None,
        "download_presigned_url": download_url,
        "filename": filename,
        "expires_in_seconds": _PRESIGNED_EXPIRES_SECONDS,
        "temporary_url": True,
    }


def _video_payload(session: Session, video: Video, media: Media | None) -> dict[str, Any]:
    thumb = session.execute(
        select(Asset).where(Asset.video_id == video.id, Asset.type == "thumbnail").order_by(Asset.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    video_asset = session.execute(
        select(Asset).where(Asset.video_id == video.id, Asset.type == "video").order_by(Asset.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    video_asset_ref = None
    if video_asset and media is not None:
        filename = build_download_filename(
            media_name=media.name,
            title=video.title,
            fallback_id=video.provider_video_id,
            ext=video_asset.format,
        )
        video_asset_ref = serialize_for_mcp(
            build_asset_ref(
                video_asset,
                filename=filename,
                response_content_disposition=content_disposition_attachment(filename),
                expires_seconds=_PRESIGNED_EXPIRES_SECONDS,
            ).model_dump()
        )
    return {
        "id": str(video.id),
        "provider": video.provider,
        "provider_video_id": video.provider_video_id,
        "media_id": str(video.media_id),
        "media_name": media.name if media else None,
        "media_avatar_asset": _media_avatar_asset(session, media) if media else None,
        "url": video.url,
        "title": video.title,
        "description": _video_description(video),
        "thumbnail_url": video.thumbnail_url,
        "cover_asset": serialize_for_mcp(build_asset_ref(thumb, expires_seconds=_PRESIGNED_EXPIRES_SECONDS).model_dump()) if thumb else None,
        "published_at": video.published_at,
        "duration_sec": video.duration_sec,
        "status": video.status,
        "error_message": video.error_message,
        "view_count": video.view_count,
        "like_count": video.like_count,
        "comment_count": video.comment_count,
        "tags": video.tags or [],
        "video_asset": video_asset_ref,
        "created_at": video.created_at,
        "updated_at": video.updated_at,
    }


def _playlist_summary_payload(session: Session, playlist: Playlist, *, preview: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist.id)).scalars().all()
    video_count = 0
    latest_video_at = None
    earliest_date_value = None
    latest_date_value = None
    if media_ids:
        video_count = int(session.execute(select(func.count()).select_from(Video).where(Video.media_id.in_(list(media_ids)))).scalar_one() or 0)
        min_ts, max_ts = session.execute(
            select(func.min(Video.published_at), func.max(Video.published_at)).where(Video.media_id.in_(list(media_ids)))
        ).one()
        if not min_ts and not max_ts:
            min_ts, max_ts = session.execute(
                select(func.min(Video.created_at), func.max(Video.created_at)).where(Video.media_id.in_(list(media_ids)))
            ).one()
        latest_video_at = max_ts
        earliest_date_value = local_date(min_ts)
        latest_date_value = local_date(max_ts)
    if preview is None:
        rows = (
            session.execute(
                select(Media)
                .join(PlaylistMedia, PlaylistMedia.media_id == Media.id)
                .where(PlaylistMedia.playlist_id == playlist.id)
                .order_by(PlaylistMedia.added_at.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )
        preview = [
            {
                "id": str(media.id),
                "provider": media.provider,
                "url": media.url,
                "name": media.name,
                "avatar_asset": _media_avatar_asset(session, media),
            }
            for media in rows
        ]
    return {
        "id": str(playlist.id),
        "name": playlist.name,
        "description": playlist.description,
        "avatar_asset": serialize_for_mcp(build_asset_ref(session.get(Asset, getattr(playlist, "avatar_asset_id", None))).model_dump())
        if getattr(playlist, "avatar_asset_id", None)
        else None,
        "background_asset": serialize_for_mcp(build_asset_ref(session.get(Asset, getattr(playlist, "background_asset_id", None))).model_dump())
        if getattr(playlist, "background_asset_id", None)
        else None,
        "brief_granularity": (getattr(playlist, "brief_granularity", None) or "day").strip() or "day",
        "media_count": len(media_ids),
        "media_preview": preview,
        "video_count": video_count,
        "latest_video_at": latest_video_at,
        "earliest_date": earliest_date_value,
        "latest_date": latest_date_value,
        "created_at": playlist.created_at,
        "updated_at": playlist.updated_at,
    }


def _build_transcript_chunk_payload(video_id: uuid.UUID, asset: Asset, *, chunk_index: int, chunk_size: int) -> dict[str, Any]:
    full_text, _ = read_text_asset(asset)
    base = {
        "video_id": str(video_id),
        "asset_id": str(asset.id),
        "language": asset.language,
        "source": asset.source,
        "variant": asset.variant,
        "polish_method": transcript_polish_method(asset),
        "created_at": getattr(asset, "created_at", None),
        "updated_at": getattr(asset, "updated_at", None) or getattr(asset, "created_at", None),
    }
    try:
        chunk = get_text_chunk(full_text, chunk_index=chunk_index, chunk_size=chunk_size)
    except IndexError:
        bounds = build_chunk_bounds(full_text, chunk_size)
        return {
            "ok": False,
            "status": "not_found",
            "reason": "chunk_out_of_range",
            "next_uri": None,
            **base,
            "total_chars": len(full_text),
            "chunk_index": chunk_index,
            "chunk_count": len(bounds),
            "has_more": False,
            "next_chunk_index": None,
            "text": "",
        }

    next_uri = None
    if chunk["next_chunk_index"] is not None:
        next_uri = f"raelyn://video/{video_id}/transcript/chunks/{chunk['next_chunk_index']}"
    return {
        "ok": True,
        "status": "ready",
        "reason": None,
        "next_uri": next_uri,
        **base,
        **chunk,
    }


def _video_transcript_payload(session: Session, video_id: uuid.UUID, *, chunk_index: int = 0, chunk_size: int = DEFAULT_TRANSCRIPT_CHUNK_SIZE) -> dict[str, Any]:
    video = session.get(Video, video_id)
    if not video:
        raise LookupError("video not found")

    asset = pick_transcript_asset(session, video_id)
    if not asset:
        return {
            "ok": False,
            "status": "not_ready",
            "reason": "no transcript",
            "video_id": str(video_id),
            "asset_id": None,
            "language": None,
            "source": None,
            "variant": None,
            "polish_method": None,
            "total_chars": 0,
            "chunk_index": chunk_index,
            "chunk_count": 0,
            "has_more": False,
            "next_chunk_index": None,
            "next_uri": None,
            "text": "",
        }
    return _build_transcript_chunk_payload(video_id, asset, chunk_index=chunk_index, chunk_size=normalize_chunk_size(chunk_size))


def _brief_body_uri_for_brief(brief_id: uuid.UUID) -> str:
    return f"raelyn://brief/{brief_id}/body"


def _brief_body_uri_for_playlist_date(playlist_id: uuid.UUID, target_date: date) -> str:
    return f"raelyn://playlist/{playlist_id}/briefs/by-date/{target_date.isoformat()}/body"


def _read_brief_body(asset: Asset | None) -> tuple[bool, str]:
    if not asset:
        return False, ""
    try:
        body = s3_get_bytes(bucket=asset.s3_bucket, key=asset.s3_key).decode("utf-8", errors="ignore")
    except Exception:
        return False, ""
    return True, body


def _brief_asset_ref(asset: Asset | None) -> dict[str, Any] | None:
    if not asset:
        return None
    return serialize_for_mcp(build_asset_ref(asset, expires_seconds=_PRESIGNED_EXPIRES_SECONDS).model_dump())


def resolve_playlist_brief_target(session: Session, playlist_id: uuid.UUID, target_date: date) -> tuple[Playlist, str, date, date]:
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        raise LookupError("playlist not found")
    granularity = normalize_granularity(getattr(playlist, "brief_granularity", None) or "day")
    period_start_value = period_start(target_date, granularity)
    period_end_value = period_end_inclusive(period_start_value, granularity)
    return playlist, granularity, period_start_value, period_end_value


def _find_brief(session: Session, *, playlist_id: uuid.UUID, granularity: str, period_start_value: date) -> Brief | None:
    return session.execute(
        select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == granularity, Brief.period_start == period_start_value)
    ).scalar_one_or_none()


def _playlist_latest_date(session: Session, playlist_id: uuid.UUID) -> date | None:
    max_published_at = session.execute(
        select(func.max(Video.published_at))
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .where(PlaylistMedia.playlist_id == playlist_id)
    ).scalar_one_or_none()
    if max_published_at:
        return local_date(max_published_at)
    max_created_at = session.execute(
        select(func.max(Video.created_at))
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .where(PlaylistMedia.playlist_id == playlist_id)
    ).scalar_one_or_none()
    return local_date(max_created_at)


def resolve_latest_brief_target(session: Session, playlist: Playlist) -> tuple[str, date, date, date] | None:
    granularity = normalize_granularity(getattr(playlist, "brief_granularity", None) or "day")
    latest_date_value = _playlist_latest_date(session, playlist.id)
    if latest_date_value is None:
        return None
    period_start_value = period_start(latest_date_value, granularity)
    period_end_value = period_end_inclusive(period_start_value, granularity)
    return granularity, latest_date_value, period_start_value, period_end_value


def build_brief_payload(
    session: Session,
    brief: Brief | None,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    period_start_value: date | None,
    period_end_value: date | None,
    include_body: bool,
    body_resource_uri: str | None,
) -> dict[str, Any]:
    if not brief:
        return {
            "ok": False,
            "status": "not_ready",
            "brief_id": None,
            "playlist_id": str(playlist_id),
            "granularity": granularity,
            "period_start": period_start_value,
            "period_end": period_end_value,
            "markdown_asset": None,
            "body_readable": False,
            "body_resource_uri": None,
            "body_mime_type": _BRIEF_BODY_MIME_TYPE,
            "body_markdown": "",
            "error_message": None,
            "created_at": None,
            "updated_at": None,
        }

    asset = session.get(Asset, brief.markdown_asset_id) if brief.markdown_asset_id else None
    asset_ref = _brief_asset_ref(asset)
    body_readable = False
    body_markdown = ""
    if brief.status == "ready" and asset is not None:
        body_readable, body_text = _read_brief_body(asset)
        if include_body and body_readable:
            body_markdown = body_text

    payload = {
        "ok": brief.status == "ready",
        "status": brief.status,
        "brief_id": str(brief.id),
        "playlist_id": str(playlist_id),
        "granularity": granularity,
        "period_start": period_start_value,
        "period_end": period_end_value,
        "markdown_asset": asset_ref,
        "body_readable": body_readable,
        "body_resource_uri": body_resource_uri if body_readable else None,
        "body_mime_type": _BRIEF_BODY_MIME_TYPE,
        "body_markdown": body_markdown,
        "error_message": brief.error_message,
        "created_at": brief.created_at,
        "updated_at": brief.updated_at,
    }
    if asset_ref:
        payload["expires_in_seconds"] = _PRESIGNED_EXPIRES_SECONDS
        payload["temporary_url"] = True
    return payload


def _read_brief_body_by_id(session: Session, brief_id: uuid.UUID) -> str:
    brief = session.get(Brief, brief_id)
    if not brief:
        raise LookupError("brief not found")
    asset = session.get(Asset, brief.markdown_asset_id) if brief.markdown_asset_id else None
    body_readable, body_markdown = _read_brief_body(asset)
    if not body_readable:
        raise RuntimeError("brief body not readable")
    return body_markdown


def _read_playlist_brief_body_by_date(session: Session, playlist_id: uuid.UUID, target_date: date) -> str:
    _playlist, granularity, period_start_value, _period_end_value = resolve_playlist_brief_target(session, playlist_id, target_date)
    brief = _find_brief(session, playlist_id=playlist_id, granularity=granularity, period_start_value=period_start_value)
    if not brief:
        raise RuntimeError("brief body not readable")
    asset = session.get(Asset, brief.markdown_asset_id) if brief.markdown_asset_id else None
    body_readable, body_markdown = _read_brief_body(asset)
    if not body_readable:
        raise RuntimeError("brief body not readable")
    return body_markdown


def read_brief_body(brief_id: str | uuid.UUID) -> str:
    brief_uuid = _parse_uuid(brief_id, "brief_id")
    with session_scope() as session:
        return _read_brief_body_by_id(session, brief_uuid)


def read_playlist_brief_body(playlist_id: str | uuid.UUID, *, date: str | date) -> str:
    playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
    target_date = _parse_date(date, "date")
    with session_scope() as session:
        return _read_playlist_brief_body_by_date(session, playlist_uuid, target_date)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _media_name_map(session: Session, jobs: list[Job]) -> dict[str, str]:
    media_ids: list[uuid.UUID] = []
    for job in jobs:
        try:
            media_id = (job.params or {}).get("media_id")
            if media_id:
                media_ids.append(uuid.UUID(str(media_id)))
        except Exception:
            continue
    if not media_ids:
        return {}

    rows = session.execute(select(Media.id, Media.name, Media.provider_media_id).where(Media.id.in_(list(dict.fromkeys(media_ids))))).all()
    return {str(mid): (name or provider_media_id or str(mid)) for mid, name, provider_media_id in rows}


def _job_payload(job: Job, *, media_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "type": job.type,
        "status": job.status,
        "priority": job.priority,
        "params": serialize_for_mcp(job.params),
        "result": serialize_for_mcp(job.result),
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "error_message": job.error_message,
        "error_stack": job.error_stack,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "scheduled_for": job.scheduled_for,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "worker_id": job.worker_id,
        "parent_job_id": str(job.parent_job_id) if job.parent_job_id else None,
        "media_name": media_name,
    }


def _backfill_published_at(session: Session, *, limit: int = 5000) -> int:
    stmt = (
        select(Video)
        .where(
            Video.published_at.is_(None),
            Video.raw_info.is_not(None),
            or_(
                Video.raw_info["timestamp"].astext.is_not(None),
                Video.raw_info["release_timestamp"].astext.is_not(None),
                Video.raw_info["upload_date"].astext.is_not(None),
                Video.raw_info["release_date"].astext.is_not(None),
            ),
        )
        .order_by(Video.created_at.desc())
        .limit(limit)
    )
    videos = session.execute(stmt).scalars().all()
    updated = 0
    for video in videos:
        published_at = parse_published_at(video.raw_info)
        if not published_at:
            continue
        video.published_at = published_at
        updated += 1
    if updated:
        session.flush()
    return updated


def list_media(*, provider: str | None = None, q: str | None = None, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    with session_scope() as session:
        limit_value = _clamp_limit(limit, default=20, maximum=100)
        offset_value = _clamp_offset(offset)
        video_counts = select(Video.media_id.label("media_id"), func.count(Video.id).label("local_video_count")).group_by(Video.media_id)
        counts_sq = video_counts.subquery()
        stmt = select(Media, counts_sq.c.local_video_count).outerjoin(counts_sq, counts_sq.c.media_id == Media.id)
        if provider:
            stmt = stmt.where(Media.provider == provider)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((Media.name.ilike(like)) | (Media.description.ilike(like)))
        stmt = stmt.order_by(Media.created_at.desc(), Media.id.desc()).limit(limit_value).offset(offset_value)
        rows = session.execute(stmt).all()
        return serialize_for_mcp([_media_payload(session, media, local_video_count=int(count or 0)) for media, count in rows])


def get_media(media_id: str | uuid.UUID) -> dict[str, Any]:
    media_uuid = _parse_uuid(media_id, "media_id")
    with session_scope() as session:
        media = session.get(Media, media_uuid)
        if not media:
            raise LookupError("media not found")
        count = session.execute(select(func.count()).select_from(Video).where(Video.media_id == media.id)).scalar_one()
        return serialize_for_mcp(_media_payload(session, media, local_video_count=int(count or 0)))


def list_videos(
    *,
    provider: str | None = None,
    media_id: str | uuid.UUID | None = None,
    playlist_id: str | uuid.UUID | None = None,
    status: str | None = None,
    q: str | None = None,
    published_since: str | datetime | None = None,
    published_until: str | datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        global _PUBLISHED_AT_BACKFILLED  # noqa: PLW0603
        limit_value = _clamp_limit(limit, default=20, maximum=100)
        offset_value = _clamp_offset(offset)
        published_since_value = _parse_datetime(published_since, "published_since")
        published_until_value = _parse_datetime(published_until, "published_until")
        if (published_since_value or published_until_value) and not _PUBLISHED_AT_BACKFILLED:
            _backfill_published_at(session)
            _PUBLISHED_AT_BACKFILLED = True

        stmt = select(Video, Media).join(Media, Media.id == Video.media_id)
        if provider:
            stmt = stmt.where(Video.provider == provider)
        if media_id is not None:
            stmt = stmt.where(Video.media_id == _parse_uuid(media_id, "media_id"))
        if playlist_id is not None:
            playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
            playlist = session.get(Playlist, playlist_uuid)
            if not playlist:
                raise LookupError("playlist not found")
            stmt = stmt.join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id).where(PlaylistMedia.playlist_id == playlist_uuid)
        if status:
            stmt = stmt.where(Video.status == status)
        if q:
            stmt = stmt.where(Video.title.ilike(f"%{q}%"))
        if published_since_value:
            stmt = stmt.where(Video.published_at.is_not(None), Video.published_at >= published_since_value)
        if published_until_value:
            stmt = stmt.where(Video.published_at.is_not(None), Video.published_at < published_until_value)

        rows = session.execute(
            stmt.order_by(Video.published_at.desc().nullslast(), Video.created_at.desc(), Video.id.desc()).limit(limit_value).offset(offset_value)
        ).all()
        return serialize_for_mcp([_video_payload(session, video, media) for video, media in rows])


def get_video(video_id: str | uuid.UUID) -> dict[str, Any]:
    video_uuid = _parse_uuid(video_id, "video_id")
    with session_scope() as session:
        row = session.execute(select(Video, Media).join(Media, Media.id == Video.media_id).where(Video.id == video_uuid)).first()
        if not row:
            raise LookupError("video not found")
        video, media = row
        return serialize_for_mcp(_video_payload(session, video, media))


def get_video_transcript(video_id: str | uuid.UUID, *, chunk_index: int = 0, chunk_size: int = DEFAULT_TRANSCRIPT_CHUNK_SIZE) -> dict[str, Any]:
    video_uuid = _parse_uuid(video_id, "video_id")
    with session_scope() as session:
        return serialize_for_mcp(_video_transcript_payload(session, video_uuid, chunk_index=chunk_index, chunk_size=chunk_size))


def list_video_assets(
    video_id: str | uuid.UUID,
    *,
    type: str | None = None,
    language: str | None = None,
    variant: str | None = None,
) -> list[dict[str, Any]]:
    video_uuid = _parse_uuid(video_id, "video_id")
    with session_scope() as session:
        row = session.execute(select(Video, Media).join(Media, Media.id == Video.media_id).where(Video.id == video_uuid)).first()
        if not row:
            raise LookupError("video not found")
        video, media = row
        stmt = select(Asset).where(Asset.video_id == video_uuid)
        if type:
            stmt = stmt.where(Asset.type == type)
        if language:
            stmt = stmt.where(Asset.language == language)
        if variant:
            stmt = stmt.where(Asset.variant == variant)
        assets = session.execute(stmt.order_by(Asset.created_at.asc())).scalars().all()
        return serialize_for_mcp([_asset_payload(asset, media=media, video=video) for asset in assets])


def list_playlists(*, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    with session_scope() as session:
        limit_value = _clamp_limit(limit, default=20, maximum=100)
        offset_value = _clamp_offset(offset)
        items = session.execute(select(Playlist).order_by(Playlist.updated_at.desc()).limit(limit_value).offset(offset_value)).scalars().all()
        playlist_ids = [playlist.id for playlist in items]
        preview_map: dict[uuid.UUID, list[dict[str, Any]]] = {pid: [] for pid in playlist_ids}
        if playlist_ids:
            rows = (
                session.execute(
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
                    .order_by(PlaylistMedia.playlist_id.asc(), PlaylistMedia.added_at.desc())
                )
                .mappings()
                .all()
            )
            for row in rows:
                current = preview_map.setdefault(row["playlist_id"], [])
                if len(current) >= 5:
                    continue
                current.append(
                    {
                        "id": str(row["media_id"]),
                        "provider": row["provider"],
                        "url": row["url"],
                        "name": row["name"],
                        "avatar_asset": serialize_for_mcp(
                            build_asset_ref(session.get(Asset, row["avatar_asset_id"]), expires_seconds=_PRESIGNED_EXPIRES_SECONDS).model_dump()
                        )
                        if row["avatar_asset_id"]
                        else None,
                    }
                )
        return serialize_for_mcp([_playlist_summary_payload(session, playlist, preview=preview_map.get(playlist.id) or []) for playlist in items])


def get_playlist(playlist_id: str | uuid.UUID) -> dict[str, Any]:
    playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_uuid)
        if not playlist:
            raise LookupError("playlist not found")
        payload = _playlist_summary_payload(session, playlist)
        rows = (
            session.execute(
                select(Media)
                .join(PlaylistMedia, PlaylistMedia.media_id == Media.id)
                .where(PlaylistMedia.playlist_id == playlist_uuid)
                .order_by(PlaylistMedia.added_at.desc())
            )
            .scalars()
            .all()
        )
        payload["brief_prompt"] = (getattr(playlist, "brief_prompt", None) or "").strip() or None
        payload["media"] = [
            {
                "id": str(media.id),
                "provider": media.provider,
                "url": media.url,
                "name": media.name,
                "avatar_asset": _media_avatar_asset(session, media),
            }
            for media in rows
        ]
        return serialize_for_mcp(payload)


def _playlist_videos(
    session: Session,
    playlist_uuid: uuid.UUID,
    *,
    granularity: str,
    period_start_value: date,
    limit: int,
) -> list[dict[str, Any]]:
    g = normalize_granularity(granularity)
    start_utc, end_utc = period_bounds_utc(period_start_value, g)
    rows = (
        session.execute(
            select(Video, Media)
            .join(Media, Media.id == Video.media_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(
                PlaylistMedia.playlist_id == playlist_uuid,
                Video.published_at.is_not(None),
                Video.published_at >= start_utc,
                Video.published_at < end_utc,
            )
            .order_by(Video.published_at.asc(), Video.created_at.asc(), Video.id.asc())
            .limit(limit)
        )
        .all()
    )
    return [_video_payload(session, video, media) for video, media in rows]


def get_playlist_videos(
    playlist_id: str | uuid.UUID,
    *,
    date: str | date,
    limit: int = 50,
) -> list[dict[str, Any]]:
    playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
    target_date = _parse_date(date, "date")
    limit_value = _clamp_limit(limit, default=50, maximum=200)
    with session_scope() as session:
        _playlist, granularity, period_start_value, _period_end_value = resolve_playlist_brief_target(session, playlist_uuid, target_date)
        return serialize_for_mcp(
            _playlist_videos(
                session,
                playlist_uuid,
                granularity=granularity,
                period_start_value=period_start_value,
                limit=limit_value,
            )
        )


def list_briefs(
    *,
    playlist_id: str | uuid.UUID | None = None,
    granularity: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_body: bool = False,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        limit_value = _clamp_limit(limit, default=20, maximum=100)
        offset_value = _clamp_offset(offset)
        stmt = select(Brief)
        if playlist_id is not None:
            playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
            if not session.get(Playlist, playlist_uuid):
                raise LookupError("playlist not found")
            stmt = stmt.where(Brief.playlist_id == playlist_uuid)
        if granularity:
            stmt = stmt.where(Brief.granularity == normalize_granularity(granularity))
        briefs = session.execute(stmt.order_by(Brief.period_start.desc()).limit(limit_value).offset(offset_value)).scalars().all()
        items = []
        for brief in briefs:
            items.append(
                build_brief_payload(
                    session,
                    brief,
                    playlist_id=brief.playlist_id,
                    granularity=brief.granularity,
                    period_start_value=brief.period_start,
                    period_end_value=period_end_inclusive(brief.period_start, brief.granularity),
                    include_body=include_body,
                    body_resource_uri=_brief_body_uri_for_brief(brief.id),
                )
            )
        return serialize_for_mcp(items)


def get_brief(brief_id: str | uuid.UUID, *, include_body: bool = True) -> dict[str, Any]:
    brief_uuid = _parse_uuid(brief_id, "brief_id")
    with session_scope() as session:
        brief = session.get(Brief, brief_uuid)
        if not brief:
            raise LookupError("brief not found")
        return serialize_for_mcp(
            build_brief_payload(
                session,
                brief,
                playlist_id=brief.playlist_id,
                granularity=brief.granularity,
                period_start_value=brief.period_start,
                period_end_value=period_end_inclusive(brief.period_start, brief.granularity),
                include_body=include_body,
                body_resource_uri=_brief_body_uri_for_brief(brief.id),
            )
        )


def get_playlist_brief(playlist_id: str | uuid.UUID, *, date: str | date, include_body: bool = True) -> dict[str, Any]:
    playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
    target_date = _parse_date(date, "date")
    with session_scope() as session:
        _playlist, granularity, period_start_value, period_end_value = resolve_playlist_brief_target(session, playlist_uuid, target_date)
        brief = _find_brief(session, playlist_id=playlist_uuid, granularity=granularity, period_start_value=period_start_value)
        return serialize_for_mcp(
            build_brief_payload(
                session,
                brief,
                playlist_id=playlist_uuid,
                granularity=granularity,
                period_start_value=period_start_value,
                period_end_value=period_end_value,
                include_body=include_body,
                body_resource_uri=_brief_body_uri_for_playlist_date(playlist_uuid, target_date),
            )
        )


def get_playlist_latest_brief(playlist_id: str | uuid.UUID, *, include_body: bool = False) -> dict[str, Any]:
    playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_uuid)
        if not playlist:
            raise LookupError("playlist not found")
        target = resolve_latest_brief_target(session, playlist)
        if target is None:
            granularity = normalize_granularity(getattr(playlist, "brief_granularity", None) or "day")
            return serialize_for_mcp(
                build_brief_payload(
                    session,
                    None,
                    playlist_id=playlist_uuid,
                    granularity=granularity,
                    period_start_value=None,
                    period_end_value=None,
                    include_body=include_body,
                    body_resource_uri=None,
                )
            )

        granularity, _latest_date_value, period_start_value, period_end_value = target
        brief = _find_brief(session, playlist_id=playlist_uuid, granularity=granularity, period_start_value=period_start_value)
        body_resource_uri = _brief_body_uri_for_brief(brief.id) if brief else None
        return serialize_for_mcp(
            build_brief_payload(
                session,
                brief,
                playlist_id=playlist_uuid,
                granularity=granularity,
                period_start_value=period_start_value,
                period_end_value=period_end_value,
                include_body=include_body,
                body_resource_uri=body_resource_uri,
            )
        )


def list_latest_briefs(*, limit: int = 20, offset: int = 0, include_body: bool = False) -> list[dict[str, Any]]:
    with session_scope() as session:
        limit_value = _clamp_limit(limit, default=20, maximum=100)
        offset_value = _clamp_offset(offset)
        playlists = session.execute(
            select(Playlist).order_by(Playlist.updated_at.desc()).limit(limit_value).offset(offset_value)
        ).scalars().all()
        items: list[dict[str, Any]] = []
        for playlist in playlists:
            target = resolve_latest_brief_target(session, playlist)
            if target is None:
                granularity = normalize_granularity(getattr(playlist, "brief_granularity", None) or "day")
                items.append(
                    build_brief_payload(
                        session,
                        None,
                        playlist_id=playlist.id,
                        granularity=granularity,
                        period_start_value=None,
                        period_end_value=None,
                        include_body=include_body,
                        body_resource_uri=None,
                    )
                )
                continue

            granularity, _latest_date_value, period_start_value, period_end_value = target
            brief = _find_brief(session, playlist_id=playlist.id, granularity=granularity, period_start_value=period_start_value)
            body_resource_uri = _brief_body_uri_for_brief(brief.id) if brief else None
            items.append(
                build_brief_payload(
                    session,
                    brief,
                    playlist_id=playlist.id,
                    granularity=granularity,
                    period_start_value=period_start_value,
                    period_end_value=period_end_value,
                    include_body=include_body,
                    body_resource_uri=body_resource_uri,
                )
            )
        return serialize_for_mcp(items)


def list_jobs(
    *,
    status: str | None = None,
    status_in: str | None = None,
    type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        limit_value = _clamp_limit(limit, default=20, maximum=100)
        offset_value = _clamp_offset(offset)
        stmt = select(Job)
        statuses = _parse_csv(status_in)
        if status:
            statuses.append(status)
        if statuses:
            stmt = stmt.where(Job.status.in_(list(dict.fromkeys(statuses))))
        if type:
            stmt = stmt.where(Job.type == type)
        status_set = set(statuses) if statuses else set()
        active = {"pending", "running"}
        done = {"succeeded", "failed", "canceled"}
        if statuses and status_set.issubset(active):
            stmt = stmt.order_by(
                case((Job.status == "running", 0), else_=1),
                Job.started_at.desc().nullslast(),
                Job.scheduled_for.asc(),
                Job.created_at.asc(),
            )
        elif statuses and status_set.issubset(done):
            stmt = stmt.order_by(Job.finished_at.desc().nullslast(), Job.created_at.desc())
        else:
            stmt = stmt.order_by(Job.created_at.desc())
        jobs = session.execute(stmt.limit(limit_value).offset(offset_value)).scalars().all()
        media_names = _media_name_map(session, jobs)
        return serialize_for_mcp([_job_payload(job, media_name=media_names.get(str((job.params or {}).get("media_id") or ""))) for job in jobs])


def get_job(job_id: str | uuid.UUID) -> dict[str, Any]:
    job_uuid = _parse_uuid(job_id, "job_id")
    with session_scope() as session:
        job = session.get(Job, job_uuid)
        if not job:
            raise LookupError("job not found")
        media_names = _media_name_map(session, [job])
        media_name = media_names.get(str((job.params or {}).get("media_id") or ""))
        return serialize_for_mcp(_job_payload(job, media_name=media_name))


def get_video_context(video_id: str | uuid.UUID) -> dict[str, Any]:
    video_uuid = _parse_uuid(video_id, "video_id")
    with session_scope() as session:
        row = session.execute(select(Video, Media).join(Media, Media.id == Video.media_id).where(Video.id == video_uuid)).first()
        if not row:
            raise LookupError("video not found")
        video, media = row
        transcript = _video_transcript_payload(session, video.id, chunk_index=0, chunk_size=DEFAULT_TRANSCRIPT_CHUNK_SIZE)
        assets = session.execute(select(Asset).where(Asset.video_id == video.id).order_by(Asset.created_at.asc())).scalars().all()
        return serialize_for_mcp(
            {
                "video": _video_payload(session, video, media),
                "media": _media_payload(session, media),
                "assets": [_asset_payload(asset, media=media, video=video) for asset in assets],
                "transcript": transcript,
            }
        )


def get_playlist_summary(
    playlist_id: str | uuid.UUID,
    *,
    date: str | date,
    include_transcript: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    playlist_uuid = _parse_uuid(playlist_id, "playlist_id")
    target_date = _parse_date(date, "date")
    limit_value = _clamp_limit(limit, default=50, maximum=200)
    with session_scope() as session:
        playlist, granularity, period_start_value, period_end_value = resolve_playlist_brief_target(session, playlist_uuid, target_date)
        videos = _playlist_videos(
            session,
            playlist_uuid,
            granularity=granularity,
            period_start_value=period_start_value,
            limit=limit_value,
        )
        transcript_ready_count = 0
        for item in videos:
            video_uuid = _parse_uuid(item["id"], "video_id")
            transcript_asset = pick_transcript_asset(session, video_uuid)
            transcript_status = "ready" if transcript_asset else "not_ready"
            item["transcript_status"] = transcript_status
            if transcript_asset:
                transcript_ready_count += 1
            if include_transcript and transcript_asset:
                item["transcript"] = _build_transcript_chunk_payload(video_uuid, transcript_asset, chunk_index=0, chunk_size=4_000)
        brief = build_brief_payload(
            session,
            _find_brief(session, playlist_id=playlist_uuid, granularity=granularity, period_start_value=period_start_value),
            playlist_id=playlist_uuid,
            granularity=granularity,
            period_start_value=period_start_value,
            period_end_value=period_end_value,
            include_body=True,
            body_resource_uri=_brief_body_uri_for_playlist_date(playlist_uuid, target_date),
        )
        return serialize_for_mcp(
            {
                "playlist": _playlist_summary_payload(session, playlist),
                "granularity": granularity,
                "date": target_date,
                "period_start": period_start_value,
                "period_end": period_end_value,
                "video_count": len(videos),
                "transcript_ready_count": transcript_ready_count,
                "include_transcript": include_transcript,
                "videos": videos,
                "brief": brief,
            }
        )
