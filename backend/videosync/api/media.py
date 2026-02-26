from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from videosync.api.orm import OrmModel
from videosync.config import settings
from videosync.db import session_scope
from videosync.jobs.enqueue import enqueue_job
from videosync.models import Media, Video
from videosync.services.provider import detect_provider, extract_media_identity
from videosync.services.s3 import s3_clear_bucket, s3_presign_get


router = APIRouter(tags=["media"])


class MediaCreate(BaseModel):
    provider: str | None = None
    url: str


class MediaOut(OrmModel):
    id: uuid.UUID
    provider: str
    provider_media_id: str
    url: str
    monitor_enabled: bool
    name: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    last_profile_sync_at: Any | None = None
    last_video_sync_at: Any | None = None


class MediaUpdate(BaseModel):
    monitor_enabled: bool | None = None


def _media_out(m: Media, *, local_video_count: int | None = None) -> MediaOut:
    out = MediaOut.model_validate(m)
    if local_video_count is not None:
        out.video_count = int(local_video_count)
    key = (getattr(m, "avatar_s3_key", None) or "").strip()
    if key:
        try:
            out.avatar_url = s3_presign_get(settings.s3_bucket, key)
        except Exception:
            pass
    return out


@router.post("/media", response_model=MediaOut)
def create_media(payload: MediaCreate) -> MediaOut:
    provider = payload.provider or detect_provider(payload.url)
    if not provider:
        raise HTTPException(status_code=400, detail="无法从 URL 识别 provider，请显式传 provider")

    try:
        identity = extract_media_identity(provider=provider, url=payload.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法解析媒体：{e}") from e
    with session_scope() as session:
        existing = session.execute(
            select(Media).where(Media.provider == provider, Media.provider_media_id == identity.provider_media_id)
        ).scalar_one_or_none()
        if existing:
            existing.url = payload.url
            media = existing
        else:
            media = Media(provider=provider, provider_media_id=identity.provider_media_id, url=payload.url)
            session.add(media)
        session.flush()

        enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media.id)}, priority=10)
        enqueue_job(session, type_="media.sync_videos", params={"media_id": str(media.id)}, priority=5)

        session.refresh(media)
        return _media_out(media, local_video_count=0)


@router.get("/media", response_model=list[MediaOut])
def list_media(provider: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> list[MediaOut]:
    with session_scope() as session:
        video_counts = select(Video.media_id.label("media_id"), func.count(Video.id).label("local_video_count")).group_by(
            Video.media_id
        )
        counts_sq = video_counts.subquery()

        stmt = select(Media, counts_sq.c.local_video_count).outerjoin(counts_sq, counts_sq.c.media_id == Media.id)
        if provider:
            stmt = stmt.where(Media.provider == provider)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((Media.name.ilike(like)) | (Media.description.ilike(like)))
        stmt = stmt.order_by(Media.updated_at.desc()).limit(limit).offset(offset)
        rows = session.execute(stmt).all()
        return [_media_out(m, local_video_count=int(c or 0)) for m, c in rows]


@router.get("/media/{media_id}", response_model=MediaOut)
def get_media(media_id: uuid.UUID) -> MediaOut:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        c = session.execute(select(func.count()).select_from(Video).where(Video.media_id == media.id)).scalar_one()
        return _media_out(media, local_video_count=int(c or 0))


@router.patch("/media/{media_id}", response_model=MediaOut)
def update_media(media_id: uuid.UUID, payload: MediaUpdate) -> MediaOut:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        if payload.monitor_enabled is not None:
            media.monitor_enabled = payload.monitor_enabled
            session.flush()
        c = session.execute(select(func.count()).select_from(Video).where(Video.media_id == media.id)).scalar_one()
        return _media_out(media, local_video_count=int(c or 0))


@router.delete("/media/{media_id}")
def delete_media(media_id: uuid.UUID) -> dict:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        bucket = settings.s3_bucket
        # Best-effort delete all S3 objects that belong to this media:
        # - video assets: <provider>/<media_id>/...
        # - cached avatar: media/<media_id>/...
        prefixes = [f"{media.provider}/{media.id}/", f"media/{media.id}/"]
        for prefix in prefixes:
            res = s3_clear_bucket(bucket=bucket, prefix=prefix)
            if not res.get("ok"):
                raise HTTPException(status_code=502, detail=f"s3 delete failed ({prefix}): {res.get('error')}")
        session.delete(media)
    return {"ok": True}


@router.post("/media/{media_id}/sync")
def sync_media(media_id: uuid.UUID, scope: str = "recent") -> dict:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media.id)}, priority=10)

        scope_key = str(scope or "").strip().lower()
        if scope_key in {"recent", "latest"}:
            max_entries = 50
        elif scope_key in {"all", "full"}:
            max_entries = 0
        else:
            raise HTTPException(status_code=400, detail=f"invalid scope: {scope!r} (expected: recent|all)")

        enqueue_job(
            session,
            type_="media.sync_videos",
            params={"media_id": str(media.id), "force": True, "max_entries": max_entries},
            priority=5,
        )
    return {"ok": True}
