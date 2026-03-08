from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from raelyn.api.orm import OrmModel
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Asset, Job, Media, Video
from raelyn.services.downloads import content_disposition_attachment
from raelyn.services.provider import detect_provider, extract_media_identity
from raelyn.services.s3 import s3_clear_bucket, s3_presign_get


router = APIRouter(tags=["media"])
_DOWNLOAD_JOB_TYPES = ("video.download", "video.download.youtube", "video.download.bilibili")


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


class MediaImportIn(BaseModel):
    text: str


class CleanupVideoOut(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    provider: str
    provider_video_id: str
    status: str
    title: str | None = None
    media_name: str | None = None
    created_at: Any | None = None


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


def _pending_download_job_ids_for_media(session, *, media_id: uuid.UUID) -> list[uuid.UUID]:
    video_ids = {
        str(v)
        for v in session.execute(select(Video.id).where(Video.media_id == media_id)).scalars().all()
        if v
    }
    if not video_ids:
        return []

    pending_jobs = (
        session.execute(
            select(Job).where(
                Job.status == "pending",
                Job.type.in_(_DOWNLOAD_JOB_TYPES),
            )
        )
        .scalars()
        .all()
    )

    job_ids: list[uuid.UUID] = []
    for job in pending_jobs:
        params = job.params if isinstance(job.params, dict) else {}
        video_id = str(params.get("video_id") or "").strip()
        if not video_id or video_id not in video_ids:
            continue
        job_ids.append(job.id)
    return job_ids


def _list_cleanup_videos(session) -> list[tuple[Video, Media]]:
    rows = (
        session.execute(
            select(Video, Media)
            .join(Media, Media.id == Video.media_id)
            .where(Media.monitor_enabled.is_(False))
            .where(Video.status == "discovered")
            .order_by(Video.created_at.desc(), Video.id.desc())
        )
        .all()
    )
    if not rows:
        return []

    video_ids = [video.id for video, _media in rows if video and getattr(video, "id", None)]
    if not video_ids:
        return []

    asset_video_ids = {
        video_id
        for video_id in session.execute(select(Asset.video_id).where(Asset.type == "video", Asset.video_id.in_(video_ids)))
        .scalars()
        .all()
        if video_id
    }
    active_download_video_ids = {
        video_id
        for job in session.execute(
            select(Job).where(Job.status.in_(["pending", "running"]), Job.type.in_(_DOWNLOAD_JOB_TYPES))
        )
        .scalars()
        .all()
        for video_id in [str((job.params if isinstance(job.params, dict) else {}).get("video_id") or "").strip()]
        if video_id
    }

    return [
        (video, media)
        for video, media in rows
        if video.id not in asset_video_ids and str(video.id) not in active_download_video_ids
    ]


def _cleanup_video_out(video: Video, media: Media) -> CleanupVideoOut:
    return CleanupVideoOut(
        id=video.id,
        media_id=video.media_id,
        provider=video.provider,
        provider_video_id=video.provider_video_id,
        status=video.status,
        title=video.title,
        media_name=media.name,
        created_at=video.created_at,
    )


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
            media = Media(provider=provider, provider_media_id=identity.provider_media_id, url=payload.url, monitor_enabled=False)
            session.add(media)
        session.flush()

        enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media.id)}, priority=10)

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
        stmt = stmt.order_by(Media.created_at.desc(), Media.id.desc()).limit(limit).offset(offset)
        rows = session.execute(stmt).all()
        return [_media_out(m, local_video_count=int(c or 0)) for m, c in rows]


@router.get("/media/export")
def export_media() -> PlainTextResponse:
    with session_scope() as session:
        urls = (
            session.execute(select(Media.url).order_by(Media.updated_at.desc()))
            .scalars()
            .all()
        )
    lines = [str(u).strip() for u in urls if str(u or "").strip()]
    body = "\n".join(lines)
    if body:
        body += "\n"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"media-export-{ts}.txt"
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition_attachment(filename)},
    )


@router.post("/media/import")
def import_media(payload: MediaImportIn) -> dict:
    text = str(payload.text or "")
    max_chars = 2_000_000
    if len(text) > max_chars:
        raise HTTPException(status_code=413, detail=f"导入文本过大（>{max_chars} chars）")

    invalid: list[dict] = []
    duplicates_in_input: list[str] = []
    items: list[tuple[tuple[str, str], str, int]] = []
    seen_keys: set[tuple[str, str]] = set()

    for lineno, raw in enumerate(text.splitlines(), start=1):
        s = str(raw or "").strip()
        if not s or s.startswith("#"):
            continue

        provider = detect_provider(s)
        if not provider:
            invalid.append({"line": lineno, "url": s, "error": "无法识别 provider"})
            continue

        try:
            identity = extract_media_identity(provider=provider, url=s)
        except Exception as e:
            invalid.append({"line": lineno, "url": s, "error": f"无法解析媒体：{e}"})
            continue

        key = (identity.provider, identity.provider_media_id)
        if key in seen_keys:
            duplicates_in_input.append(s)
            continue
        seen_keys.add(key)

        items.append((key, s, lineno))
        if len(items) > 10_000:
            raise HTTPException(status_code=413, detail="导入媒体过多（>10000 条有效 URL）")

    by_provider: dict[str, set[str]] = {}
    for (provider, pid), _url, _lineno in items:
        if provider not in by_provider:
            by_provider[provider] = set()
        by_provider[provider].add(pid)

    created: list[str] = []
    existing: list[str] = []

    with session_scope() as session:
        existing_by_key: dict[tuple[str, str], Media] = {}
        for provider, ids in by_provider.items():
            if not ids:
                continue
            rows = (
                session.execute(
                    select(Media).where(Media.provider == provider, Media.provider_media_id.in_(list(ids)))
                )
                .scalars()
                .all()
            )
            for m in rows:
                existing_by_key[(m.provider, m.provider_media_id)] = m

        created_medias: list[Media] = []
        for (provider, pid), url, _lineno in items:
            m = existing_by_key.get((provider, pid))
            if m:
                m.url = url
                existing.append(url)
                continue
            m = Media(provider=provider, provider_media_id=pid, url=url, monitor_enabled=False)
            session.add(m)
            created_medias.append(m)
            created.append(url)

        session.flush()

        for m in created_medias:
            enqueue_job(session, type_="media.sync_profile", params={"media_id": str(m.id)}, priority=10)

    return {
        "ok": True,
        "created": created,
        "existing": existing,
        "invalid": invalid,
        "duplicates_in_input": duplicates_in_input,
        "total_effective": len(items),
    }


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
            was_enabled = bool(media.monitor_enabled)
            media.monitor_enabled = payload.monitor_enabled
            if was_enabled and (payload.monitor_enabled is False):
                for job_id in _pending_download_job_ids_for_media(session, media_id=media.id):
                    job = session.get(Job, job_id)
                    if job:
                        session.delete(job)
            session.flush()
        c = session.execute(select(func.count()).select_from(Video).where(Video.media_id == media.id)).scalar_one()
        return _media_out(media, local_video_count=int(c or 0))


@router.delete("/media/{media_id}")
def delete_media(media_id: uuid.UUID) -> dict:
    bucket = settings.s3_bucket
    prefixes: list[str] = []

    # Delete DB row first (transactional), then best-effort cleanup S3 after commit.
    # Reason: S3 operations can fail; we do not want partial deletes that roll back the DB.
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        prefixes = [f"{media.provider}/{media.id}/", f"media/{media.id}/"]
        session.delete(media)

    s3_errors: list[dict] = []
    s3_deleted_total = 0
    for prefix in prefixes:
        try:
            res = s3_clear_bucket(bucket=bucket, prefix=prefix)
            if not res.get("ok"):
                s3_errors.append({"prefix": prefix, "error": res.get("error")})
                continue
            s3_deleted_total += int(res.get("deleted") or 0)
        except Exception as e:
            s3_errors.append({"prefix": prefix, "error": str(e)})

    return {"ok": True, "s3_deleted": s3_deleted_total, "s3_errors": s3_errors}


@router.post("/media/sync")
def sync_all_media(scope: str = "recent") -> dict:
    with session_scope() as session:
        media_ids = session.execute(select(Media.id).where(Media.monitor_enabled.is_(True))).scalars().all()

        scope_key = str(scope or "").strip().lower()
        if scope_key in {"recent", "latest"}:
            max_entries = int(settings.sync_max_entries)
        elif scope_key in {"all", "full"}:
            max_entries = 0
        else:
            raise HTTPException(status_code=400, detail=f"invalid scope: {scope!r} (expected: recent|all)")

        for media_id in media_ids:
            enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media_id)}, priority=10)
            enqueue_job(
                session,
                type_="media.sync_videos",
                params={"media_id": str(media_id), "force": True, "max_entries": max_entries},
                priority=5,
            )

    return {"ok": True, "count": len(media_ids)}


@router.post("/media/{media_id}/sync")
def sync_media(media_id: uuid.UUID, scope: str = "recent") -> dict:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        enqueue_job(session, type_="media.sync_profile", params={"media_id": str(media.id)}, priority=10)

        scope_key = str(scope or "").strip().lower()
        if scope_key in {"recent", "latest"}:
            max_entries = int(settings.sync_max_entries)
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


@router.get("/cleanup/stale-videos")
def get_stale_videos_cleanup(limit: int = 20) -> dict:
    lim = max(1, min(int(limit or 20), 100))
    with session_scope() as session:
        candidates = _list_cleanup_videos(session)
        return {
            "count": len(candidates),
            "items": [_cleanup_video_out(video, media).model_dump(mode="json") for video, media in candidates[:lim]],
        }


@router.post("/cleanup/stale-videos")
def cleanup_stale_videos() -> dict:
    with session_scope() as session:
        candidates = _list_cleanup_videos(session)
        deleted = 0
        for video, _media in candidates:
            session.delete(video)
            deleted += 1
        return {"ok": True, "deleted": deleted}
