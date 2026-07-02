from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, literal, select

from raelyn.api.asset_refs import AssetRef, build_asset_ref
from raelyn.api.orm import OrmModel
from raelyn.db import session_scope
from raelyn.models import Asset, Media, Video
from raelyn.services.downloads import build_download_filename, content_disposition_attachment
from raelyn.services.transcripts import TRANSCRIPT_VARIANT_SET, build_transcript_payload
from raelyn.services.video_actions import schedule_video_download, schedule_video_retranscribe
from raelyn.services.video_meta import backfill_video_published_at
from raelyn.services.video_time import (
    content_published_at_expr,
    normalize_time_basis,
    selected_content_time_subquery,
)


router = APIRouter(tags=["videos"])

_PUBLISHED_AT_BACKFILLED = False


class VideoOut(OrmModel):
    id: uuid.UUID
    provider: str
    provider_video_id: str
    media_id: uuid.UUID
    url: str
    title: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    published_at: Any | None = None
    duration_sec: int | None = None
    status: str
    error_message: str | None = None


class VideoListOut(BaseModel):
    id: uuid.UUID
    provider: str
    provider_video_id: str
    media_id: uuid.UUID
    url: str
    title: str | None = None
    thumbnail_url: str | None = None
    cover_asset: AssetRef | None = None
    media_name: str | None = None
    media_avatar_asset: AssetRef | None = None
    video_asset: AssetRef | None = None
    published_at: Any | None = None
    content_published_at: Any | None = None
    timeline_at: Any | None = None
    time_source: str | None = None
    time_status: str | None = None
    time_confidence: float | None = None
    duration_sec: int | None = None
    status: str
    error_message: str | None = None


def _video_list_timeline_columns(time_basis: str):
    if time_basis == "platform":
        content_ts_expr = literal(None).label("content_published_at")
        timeline_ts_expr = Video.published_at.label("timeline_at")
        time_source = case(
            (Video.published_at.is_(None), None),
            else_=literal("video.published_at"),
        ).label("time_source")
        time_status = case((Video.published_at.is_(None), None), else_=literal("platform")).label("time_status")
        time_confidence = literal(None).label("time_confidence")
        return None, content_ts_expr, timeline_ts_expr, time_source, time_status, time_confidence

    selected_time = selected_content_time_subquery("video_list_content_time")
    content_ts_expr = selected_time.c.content_published_at.label("content_published_at")
    timeline_value = func.coalesce(selected_time.c.content_published_at, Video.published_at)
    timeline_ts_expr = timeline_value.label("timeline_at")
    time_source = case(
        (timeline_value.is_(None), None),
        else_=func.coalesce(selected_time.c.time_source, literal("video.published_at")),
    ).label("time_source")
    time_status = case(
        (timeline_value.is_(None), None),
        else_=func.coalesce(selected_time.c.time_status, literal("platform_fallback")),
    ).label("time_status")
    time_confidence = selected_time.c.time_confidence.label("time_confidence")
    return selected_time, content_ts_expr, timeline_ts_expr, time_source, time_status, time_confidence


def _latest_video_asset_map(session, video_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, str], Asset]:
    if not video_ids:
        return {}

    assets = (
        session.execute(
            select(Asset)
            .where(Asset.video_id.in_(video_ids), Asset.type.in_(["thumbnail", "video"]))
            .order_by(Asset.video_id.asc(), Asset.type.asc(), Asset.created_at.desc())
        )
        .scalars()
        .all()
    )
    latest: dict[tuple[uuid.UUID, str], Asset] = {}
    for asset in assets:
        if not asset.video_id:
            continue
        key = (asset.video_id, asset.type)
        if key not in latest:
            latest[key] = asset
    return latest


def _asset_by_id(session, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, Asset]:
    if not asset_ids:
        return {}
    assets = session.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars().all()
    return {asset.id: asset for asset in assets}


def _content_published_at_by_video_id(session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, Any]:
    if not video_ids:
        return {}
    content_ts_expr = content_published_at_expr().label("content_published_at")
    rows = session.execute(select(Video.id, content_ts_expr).where(Video.id.in_(video_ids))).all()
    return {video_id: content_published_at for video_id, content_published_at in rows}


@router.get("/videos", response_model=list[VideoListOut])
def list_videos(
    provider: str | None = None,
    media_id: uuid.UUID | None = None,
    media_id_in: str | None = None,
    status: str | None = None,
    q: str | None = None,
    published_since: datetime | None = None,
    published_until: datetime | None = None,
    time_basis: str = "content",
    limit: int = 20,
    offset: int = 0,
) -> list[VideoListOut]:
    try:
        resolved_time_basis = normalize_time_basis(time_basis)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    with session_scope() as session:
        global _PUBLISHED_AT_BACKFILLED  # noqa: PLW0603
        if (published_since or published_until) and not _PUBLISHED_AT_BACKFILLED:
            # Older rows may have `raw_info.timestamp/upload_date` but `published_at` was never populated,
            # which would cause time filters to return an empty list. Backfill once per api process.
            backfill_video_published_at(session)
            _PUBLISHED_AT_BACKFILLED = True

        selected_time, content_ts_expr, timeline_ts_expr, time_source, time_status, time_confidence = (
            _video_list_timeline_columns(resolved_time_basis)
        )

        stmt = select(Video, Media, content_ts_expr, timeline_ts_expr, time_source, time_status, time_confidence).join(
            Media, Media.id == Video.media_id
        )
        if selected_time is not None:
            stmt = stmt.outerjoin(
                selected_time,
                selected_time.c.video_id == Video.id,
            )
        if provider:
            stmt = stmt.where(Video.provider == provider)
        media_ids: list[uuid.UUID] = []
        if media_id:
            media_ids.append(media_id)
        if media_id_in:
            for part in str(media_id_in).split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    media_ids.append(uuid.UUID(part))
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=f"invalid media_id_in uuid: {part}") from e
        if media_ids:
            stmt = stmt.where(Video.media_id.in_(list(dict.fromkeys(media_ids))))
        if status:
            stmt = stmt.where(Video.status == status)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(Video.title.ilike(like))
        if published_since:
            stmt = stmt.where(timeline_ts_expr.is_not(None), timeline_ts_expr >= published_since)
        if published_until:
            stmt = stmt.where(timeline_ts_expr.is_not(None), timeline_ts_expr < published_until)

        stmt = (
            stmt.order_by(timeline_ts_expr.desc().nullslast(), Video.created_at.desc(), Video.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = session.execute(stmt).all()
        video_ids = [v.id for v, *_ in rows]
        video_asset_map = _latest_video_asset_map(session, video_ids)
        media_avatar_asset_ids = list(
            dict.fromkeys(
                m.avatar_asset_id
                for _v, m, *_rest in rows
                if getattr(m, "avatar_asset_id", None)
            )
        )
        media_avatar_assets = _asset_by_id(session, media_avatar_asset_ids)
        page_content_published_at = (
            _content_published_at_by_video_id(session, video_ids)
            if resolved_time_basis == "platform"
            else {}
        )

        out: list[VideoListOut] = []
        for v, m, content_published_at, timeline_at, time_source_value, time_status_value, time_confidence_value in rows:
            if resolved_time_basis == "platform":
                content_published_at = page_content_published_at.get(v.id)
            thumb = video_asset_map.get((v.id, "thumbnail"))
            video_asset = video_asset_map.get((v.id, "video"))
            video_asset_ref = None
            if video_asset:
                filename = build_download_filename(
                    media_name=m.name,
                    title=v.title,
                    fallback_id=v.provider_video_id,
                    ext=video_asset.format,
                )
                video_asset_ref = build_asset_ref(
                    video_asset,
                    filename=filename,
                    response_content_disposition=content_disposition_attachment(filename),
                )
            media_avatar_asset = (
                media_avatar_assets.get(m.avatar_asset_id)
                if getattr(m, "avatar_asset_id", None)
                else None
            )
            out.append(
                VideoListOut(
                    id=v.id,
                    provider=v.provider,
                    provider_video_id=v.provider_video_id,
                    media_id=v.media_id,
                    url=v.url,
                    title=v.title,
                    thumbnail_url=v.thumbnail_url,
                    cover_asset=build_asset_ref(thumb),
                    media_name=m.name,
                    media_avatar_asset=build_asset_ref(media_avatar_asset),
                    video_asset=video_asset_ref,
                    published_at=v.published_at,
                    content_published_at=content_published_at,
                    timeline_at=timeline_at,
                    time_source=time_source_value,
                    time_status=time_status_value,
                    time_confidence=time_confidence_value,
                    duration_sec=v.duration_sec,
                    status=v.status,
                    error_message=v.error_message,
                )
            )
        return out


@router.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: uuid.UUID) -> VideoOut:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="video not found")
        out = VideoOut.model_validate(video)
        if (not out.description) and video.raw_info and video.raw_info.get("description"):
            try:
                out.description = str(video.raw_info.get("description") or "")
            except Exception:
                pass
        return out


@router.post("/videos/{video_id}/download")
def download_video(video_id: uuid.UUID) -> dict:
    with session_scope() as session:
        try:
            return schedule_video_download(session, video_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/videos/{video_id}/transcript")
def get_video_transcript(
    video_id: uuid.UUID,
    max_chars: int = 200_000,
    variant: str | None = None,
    source: str | None = None,
) -> dict:
    variant_value = str(variant or "").strip().lower() or None
    if variant_value and variant_value not in TRANSCRIPT_VARIANT_SET:
        raise HTTPException(status_code=400, detail="variant must be one of: plain, polished")
    source_value = str(source or "").strip() or None
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="video not found")
        return build_transcript_payload(
            session,
            video_id,
            max_chars=max_chars,
            variant=variant_value,
            source=source_value,
        )


@router.post("/videos/{video_id}/transcript/retranscribe")
def retranscribe_video_transcript(video_id: uuid.UUID) -> dict:
    with session_scope() as session:
        try:
            return schedule_video_retranscribe(session, video_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
