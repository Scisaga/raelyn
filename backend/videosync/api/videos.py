from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import or_

from videosync.api.orm import OrmModel
from videosync.config import settings
from videosync.db import session_scope
from videosync.jobs.enqueue import enqueue_job
from videosync.models import Asset, Media, Video
from videosync.services.downloads import build_download_filename, content_disposition_attachment
from videosync.services.llm import llm_enabled
from videosync.services.s3 import s3_get_bytes, s3_presign_get
from videosync.services.video_meta import parse_published_at


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
    cover_url: str | None = None
    media_name: str | None = None
    media_avatar_url: str | None = None
    video_download_url: str | None = None
    published_at: Any | None = None
    duration_sec: int | None = None
    status: str
    error_message: str | None = None


def _backfill_published_at(session, *, limit: int = 5000) -> int:
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
    for v in videos:
        dt = parse_published_at(v.raw_info)
        if not dt:
            continue
        v.published_at = dt
        updated += 1
    if updated:
        session.flush()
    return updated


@router.get("/videos", response_model=list[VideoListOut])
def list_videos(
    provider: str | None = None,
    media_id: uuid.UUID | None = None,
    media_id_in: str | None = None,
    status: str | None = None,
    q: str | None = None,
    published_since: datetime | None = None,
    published_until: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[VideoListOut]:
    with session_scope() as session:
        global _PUBLISHED_AT_BACKFILLED  # noqa: PLW0603
        if (published_since or published_until) and not _PUBLISHED_AT_BACKFILLED:
            # Older rows may have `raw_info.timestamp/upload_date` but `published_at` was never populated,
            # which would cause time filters to return an empty list. Backfill once per api process.
            _backfill_published_at(session)
            _PUBLISHED_AT_BACKFILLED = True

        stmt = select(Video, Media).join(Media, Media.id == Video.media_id)
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
            stmt = stmt.where(Video.published_at.is_not(None), Video.published_at >= published_since)
        if published_until:
            stmt = stmt.where(Video.published_at.is_not(None), Video.published_at < published_until)

        stmt = (
            stmt.order_by(Video.published_at.desc().nullslast(), Video.created_at.desc(), Video.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = session.execute(stmt).all()

        out: list[VideoListOut] = []
        for v, m in rows:
            thumb = session.execute(
                select(Asset).where(Asset.video_id == v.id, Asset.type == "thumbnail").order_by(Asset.created_at.desc()).limit(1)
            ).scalar_one_or_none()
            cover_url = s3_presign_get(thumb.s3_bucket, thumb.s3_key) if thumb else v.thumbnail_url

            video_asset = session.execute(
                select(Asset).where(Asset.video_id == v.id, Asset.type == "video").order_by(Asset.created_at.desc()).limit(1)
            ).scalar_one_or_none()
            video_download_url = None
            if video_asset:
                filename = build_download_filename(
                    media_name=m.name,
                    title=v.title,
                    fallback_id=v.provider_video_id,
                    ext=video_asset.format,
                )
                video_download_url = s3_presign_get(
                    video_asset.s3_bucket,
                    video_asset.s3_key,
                    response_content_disposition=content_disposition_attachment(filename),
                )

            media_avatar_url = m.avatar_url
            key = (getattr(m, "avatar_s3_key", None) or "").strip()
            if key:
                try:
                    media_avatar_url = s3_presign_get(settings.s3_bucket, key)
                except Exception:
                    media_avatar_url = m.avatar_url
            out.append(
                VideoListOut(
                    id=v.id,
                    provider=v.provider,
                    provider_video_id=v.provider_video_id,
                    media_id=v.media_id,
                    url=v.url,
                    title=v.title,
                    thumbnail_url=v.thumbnail_url,
                    cover_url=cover_url,
                    media_name=m.name,
                    media_avatar_url=media_avatar_url,
                    video_download_url=video_download_url,
                    published_at=v.published_at,
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
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="video not found")
        enqueue_job(session, type_="video.download", params={"video_id": str(video.id)}, priority=10)
    return {"ok": True}


def _pick_plain_transcript_asset(session, video_id: uuid.UUID) -> Asset | None:
    # Prefer subtitle zh, then ASR zh, then any transcript plain.
    order = [
        ("subtitle", "zh"),
        ("qwen3-asr", "zh"),
        ("speaches", "zh"),  # legacy
    ]
    for source, lang in order:
        a = session.execute(
            select(Asset).where(
                Asset.video_id == video_id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.variant == "plain",
                Asset.source == source,
                Asset.language == lang,
            )
        ).scalar_one_or_none()
        if a:
            return a

    return session.execute(
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == "transcript", Asset.format == "txt", Asset.variant == "plain")
        .order_by(Asset.created_at.desc())
    ).scalar_one_or_none()


@router.get("/videos/{video_id}/transcript")
def get_video_transcript(video_id: uuid.UUID, max_chars: int = 200_000) -> dict:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="video not found")
        asset = _pick_plain_transcript_asset(session, video_id)
        if not asset:
            return {"ok": False, "reason": "no transcript", "text": ""}
        raw = s3_get_bytes(bucket=asset.s3_bucket, key=asset.s3_key)
        text = raw.decode("utf-8", errors="ignore")
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "\n…(truncated)…\n"
        return {
            "ok": True,
            "asset_id": str(asset.id),
            "language": asset.language,
            "source": asset.source,
            "text": text,
        }


@router.get("/videos/{video_id}/note")
def get_video_note(video_id: uuid.UUID, max_chars: int = 200_000) -> dict:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="video not found")
        asset = session.execute(
            select(Asset)
            .where(
                Asset.video_id == video_id,
                Asset.type == "note",
                Asset.format == "md",
                Asset.source.in_(["llm", "ollama"]),
                Asset.variant == "summary",
            )
            .order_by(Asset.created_at.desc())
        ).scalar_one_or_none()
        if not asset:
            return {"ok": False, "reason": "no note", "text": ""}
        raw = s3_get_bytes(bucket=asset.s3_bucket, key=asset.s3_key)
        text = raw.decode("utf-8", errors="ignore")
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "\n…(truncated)…\n"
        return {"ok": True, "asset_id": str(asset.id), "text": text}


@router.post("/videos/{video_id}/note")
def generate_video_note(video_id: uuid.UUID) -> dict:
    if not llm_enabled():
        raise HTTPException(status_code=400, detail="llm not configured")
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="video not found")
        enqueue_job(session, type_="video.generate_note", params={"video_id": str(video.id)}, priority=2)
    return {"ok": True}
