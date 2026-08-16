from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import String, and_, cast, delete, func, or_, select

from raelyn.api.asset_refs import AssetRef, build_asset_ref
from raelyn.api.orm import OrmModel
from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Asset, Job, Media, Video
from raelyn.services.downloads import content_disposition_attachment
from raelyn.services.media_actions import schedule_all_media_sync, schedule_media_sync
from raelyn.services.media_deletion import (
    MEDIA_DELETE_JOB_TYPE,
    MEDIA_DELETE_PRIORITY,
    active_media_delete_job,
    active_media_delete_job_map,
)
from raelyn.services.media_sources import resolve_media_url


router = APIRouter(tags=["media"])
_DOWNLOAD_JOB_TYPES = ("video.download", "video.download.youtube", "video.download.bilibili")
_CLEANUP_SCAN_BATCH_SIZE = 500
_CLEANUP_DELETE_BATCH_SIZE = 500


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
    avatar_asset: AssetRef | None = None
    description: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    last_profile_sync_at: Any | None = None
    last_video_sync_at: Any | None = None
    disabled_reason: str | None = None
    disabled_message: str | None = None
    disabled_at: str | None = None
    deleting: bool = False
    deletion_job_id: uuid.UUID | None = None


class MediaOptionOut(OrmModel):
    id: uuid.UUID
    provider: str
    provider_media_id: str
    url: str
    name: str | None = None
    monitor_enabled: bool


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


class MediaDeleteSubmitOut(BaseModel):
    ok: bool = True
    status: str = "accepted"
    media_id: uuid.UUID
    job_id: uuid.UUID
    job_type: str = MEDIA_DELETE_JOB_TYPE
    reused: bool = False


def _media_out(
    session,
    m: Media,
    *,
    local_video_count: int | None = None,
    deleting_job_id: uuid.UUID | None = None,
    include_presigned_assets: bool = True,
) -> MediaOut:
    out = MediaOut.model_validate(m)
    auto_disabled = (m.sync_cursor or {}).get("auto_disabled") if isinstance(m.sync_cursor, dict) else None
    if isinstance(auto_disabled, dict) and not bool(m.monitor_enabled):
        reason = auto_disabled.get("reason")
        message = auto_disabled.get("message")
        disabled_at = auto_disabled.get("at")
        out.disabled_reason = str(reason) if isinstance(reason, str) and reason else None
        out.disabled_message = str(message) if isinstance(message, str) and message else None
        out.disabled_at = str(disabled_at) if isinstance(disabled_at, str) and disabled_at else None
    if local_video_count is not None:
        out.video_count = int(local_video_count)
    out.avatar_asset = (
        build_asset_ref(session.get(Asset, m.avatar_asset_id), include_presigned=include_presigned_assets)
        if getattr(m, "avatar_asset_id", None)
        else None
    )
    out.deleting = deleting_job_id is not None
    out.deletion_job_id = deleting_job_id
    return out


def _job_video_id_expr():
    return Job.params["video_id"].astext


def _pending_download_job_ids_for_media(session, *, media_id: uuid.UUID) -> list[uuid.UUID]:
    return (
        session.execute(
            select(Job.id)
            .select_from(Job)
            .join(Video, _job_video_id_expr() == cast(Video.id, String))
            .where(
                Video.media_id == media_id,
                Job.status == "pending",
                Job.type.in_(_DOWNLOAD_JOB_TYPES),
            )
        )
        .scalars()
        .all()
    )

def _cleanup_scan_rows_stmt(
    *,
    limit: int,
    before_created_at: Any | None = None,
    before_id: uuid.UUID | None = None,
):
    stmt = (
        select(Video, Media)
        .join(Media, Media.id == Video.media_id)
        .where(Media.monitor_enabled.is_(False), Video.status == "discovered")
        .order_by(Video.created_at.desc(), Video.id.desc())
    )
    if before_created_at is not None and before_id is not None:
        stmt = stmt.where(
            or_(
                Video.created_at < before_created_at,
                and_(Video.created_at == before_created_at, Video.id < before_id),
            )
        )
    return stmt.limit(limit)


def _filter_cleanup_candidates(session, rows: list[tuple[Video, Media]]) -> list[tuple[Video, Media]]:
    if not rows:
        return []

    video_ids = [video.id for video, _media in rows]
    video_id_texts = [str(video_id) for video_id in video_ids]

    asset_video_ids = {
        video_id
        for video_id in session.execute(
            select(Asset.video_id).where(Asset.type == "video", Asset.video_id.in_(video_ids))
        )
        .scalars()
        .all()
        if video_id
    }
    active_job_video_ids = set(
        session.execute(
            select(_job_video_id_expr()).where(
                Job.status.in_(["pending", "running"]),
                Job.type.in_(_DOWNLOAD_JOB_TYPES),
                _job_video_id_expr().in_(video_id_texts),
            )
        )
        .scalars()
        .all()
    )

    return [
        (video, media)
        for video, media in rows
        if video.id not in asset_video_ids and str(video.id) not in active_job_video_ids
    ]


def _scan_cleanup_candidates(
    session,
    *,
    limit: int,
    stop_after_found: int | None = None,
    batch_size: int = _CLEANUP_SCAN_BATCH_SIZE,
) -> tuple[list[tuple[Video, Media]], bool]:
    found: list[tuple[Video, Media]] = []
    before_created_at = None
    before_id = None
    target = max(1, int(stop_after_found or limit))

    while True:
        rows = session.execute(
            _cleanup_scan_rows_stmt(limit=batch_size, before_created_at=before_created_at, before_id=before_id)
        ).all()
        if not rows:
            return found[:limit], False

        candidates = _filter_cleanup_candidates(session, rows)
        found.extend(candidates)
        if len(found) >= target:
            return found[:limit], True

        last_video, _last_media = rows[-1]
        before_created_at = last_video.created_at
        before_id = last_video.id


def _list_cleanup_videos(session, *, limit: int | None = None) -> tuple[list[tuple[Video, Media]], bool]:
    lim = max(1, int(limit or 20))
    return _scan_cleanup_candidates(session, limit=lim, stop_after_found=lim + 1)


def _delete_cleanup_videos(session, *, batch_size: int = _CLEANUP_DELETE_BATCH_SIZE) -> int:
    deleted = 0
    before_created_at = None
    before_id = None

    while True:
        rows = session.execute(
            _cleanup_scan_rows_stmt(limit=batch_size, before_created_at=before_created_at, before_id=before_id)
        ).all()
        if not rows:
            return deleted

        candidates = _filter_cleanup_candidates(session, rows)
        if candidates:
            candidate_ids = [video.id for video, _media in candidates]
            deleted += int(
                session.execute(delete(Video).where(Video.id.in_(candidate_ids))).rowcount or 0
            )

        last_video, _last_media = rows[-1]
        before_created_at = last_video.created_at
        before_id = last_video.id


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
    with session_scope() as session:
        try:
            resolution = resolve_media_url(
                session,
                url=payload.url,
                provider=payload.provider,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        media = resolution.media
        return _media_out(session, media, local_video_count=0)


@router.get("/media", response_model=list[MediaOut])
def list_media(
    provider: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    presign: bool = True,
) -> list[MediaOut]:
    with session_scope() as session:
        stmt = select(Media)
        if provider:
            stmt = stmt.where(Media.provider == provider)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((Media.name.ilike(like)) | (Media.description.ilike(like)))
        stmt = stmt.order_by(Media.created_at.desc(), Media.id.desc()).limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        media_ids = [m.id for m in items]
        count_map = {}
        if media_ids:
            count_rows = session.execute(
                select(Video.media_id, func.count(Video.id)).where(Video.media_id.in_(media_ids)).group_by(Video.media_id)
            ).all()
            count_map = {media_id: int(count or 0) for media_id, count in count_rows}
        delete_job_map = active_media_delete_job_map(session, media_ids)
        return [
            _media_out(
                session,
                m,
                local_video_count=count_map.get(m.id, 0),
                deleting_job_id=delete_job_map.get(m.id),
                include_presigned_assets=presign,
            )
            for m in items
        ]


@router.get("/media/options", response_model=list[MediaOptionOut])
def list_media_options(provider: str | None = None, q: str | None = None, limit: int = 500, offset: int = 0) -> list[MediaOptionOut]:
    with session_scope() as session:
        stmt = select(Media)
        if provider:
            stmt = stmt.where(Media.provider == provider)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((Media.name.ilike(like)) | (Media.description.ilike(like)))
        stmt = stmt.order_by(Media.created_at.desc(), Media.id.desc()).limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        return [MediaOptionOut.model_validate(m) for m in items]


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
def get_media(media_id: uuid.UUID, presign: bool = True) -> MediaOut:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        c = session.execute(select(func.count()).select_from(Video).where(Video.media_id == media.id)).scalar_one()
        active_delete = active_media_delete_job(session, media.id)
        return _media_out(
            session,
            media,
            local_video_count=int(c or 0),
            deleting_job_id=active_delete.id if active_delete else None,
            include_presigned_assets=presign,
        )


@router.patch("/media/{media_id}", response_model=MediaOut)
def update_media(media_id: uuid.UUID, payload: MediaUpdate) -> MediaOut:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        active_delete = active_media_delete_job(session, media.id)
        if active_delete:
            raise HTTPException(status_code=409, detail="媒体删除中，当前操作不可用")
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
        return _media_out(session, media, local_video_count=int(c or 0))


@router.delete("/media/{media_id}", status_code=status.HTTP_202_ACCEPTED, response_model=MediaDeleteSubmitOut)
def delete_media(media_id: uuid.UUID) -> MediaDeleteSubmitOut:
    with session_scope() as session:
        media = session.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        existing = active_media_delete_job(session, media.id)
        if existing:
            return MediaDeleteSubmitOut(media_id=media.id, job_id=existing.id, reused=True)

        media.monitor_enabled = False
        job_id = enqueue_job(
            session,
            type_=MEDIA_DELETE_JOB_TYPE,
            params={"media_id": str(media.id)},
            priority=MEDIA_DELETE_PRIORITY,
        )
        return MediaDeleteSubmitOut(media_id=media.id, job_id=job_id, reused=False)


@router.post("/media/sync")
def sync_all_media(scope: str = "recent") -> dict:
    with session_scope() as session:
        try:
            return schedule_all_media_sync(session, scope=scope)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/media/{media_id}/sync")
def sync_media(media_id: uuid.UUID, scope: str = "recent") -> dict:
    with session_scope() as session:
        try:
            return schedule_media_sync(session, media_id, scope=scope)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/cleanup/stale-videos")
def get_stale_videos_cleanup(limit: int = 20) -> dict:
    lim = max(1, min(int(limit or 20), 100))
    with session_scope() as session:
        candidates, has_more = _list_cleanup_videos(session, limit=lim)
        return {
            "count": len(candidates),
            "has_more": has_more,
            "items": [_cleanup_video_out(video, media).model_dump(mode="json") for video, media in candidates],
        }


@router.post("/cleanup/stale-videos")
def cleanup_stale_videos() -> dict:
    with session_scope() as session:
        deleted = _delete_cleanup_videos(session)
        return {"ok": True, "deleted": deleted}
