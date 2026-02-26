from __future__ import annotations

import tempfile
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dateutil import tz
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from videosync.api.orm import OrmModel
from videosync.config import settings
from videosync.db import session_scope
from videosync.jobs.enqueue import enqueue_job
from videosync.models import Media, Playlist, PlaylistMedia, Video
from videosync.services.s3 import s3_presign_get, s3_upload_file


router = APIRouter(tags=["playlists"])

_PLAYLIST_IMG_MAX_BYTES = 2 * 1024 * 1024


class PlaylistCreate(BaseModel):
    name: str
    description: str | None = None
    media_ids: list[uuid.UUID] = []


class PlaylistUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class PlaylistMediaOut(BaseModel):
    id: uuid.UUID
    provider: str
    url: str
    name: str | None = None
    avatar_url: str | None = None


class PlaylistOut(OrmModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    avatar_url: str | None = None
    background_url: str | None = None
    media_count: int | None = None
    media_preview: list[PlaylistMediaOut] = Field(default_factory=list)
    video_count: int | None = None
    latest_video_at: Any | None = None
    earliest_date: date | None = None
    latest_date: date | None = None
    created_at: Any
    updated_at: Any


class PlaylistDetailOut(PlaylistOut):
    media: list[PlaylistMediaOut] = []


class PlaylistMediaAdd(BaseModel):
    media_id: uuid.UUID


class PlaylistMediaReplace(BaseModel):
    media_ids: list[uuid.UUID]


def _local_date(ts: Any | None) -> date | None:
    if not ts:
        return None
    try:
        tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
        return ts.astimezone(tzinfo).date()
    except Exception:
        return None


def _media_avatar_url(m: Any) -> str | None:
    avatar_url = getattr(m, "avatar_url", None)
    key = (getattr(m, "avatar_s3_key", None) or "").strip()
    if key:
        try:
            return s3_presign_get(settings.s3_bucket, key)
        except Exception:
            return avatar_url
    return avatar_url


def _playlist_out(session, p: Playlist, *, preview: list[PlaylistMediaOut] | None = None) -> PlaylistOut:
    out = PlaylistOut.model_validate(p)

    avatar_key = (getattr(p, "avatar_s3_key", None) or "").strip()
    if avatar_key:
        try:
            out.avatar_url = s3_presign_get(settings.s3_bucket, avatar_key)
        except Exception:
            pass

    bg_key = (getattr(p, "background_s3_key", None) or "").strip()
    if bg_key:
        try:
            out.background_url = s3_presign_get(settings.s3_bucket, bg_key)
        except Exception:
            pass

    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == p.id)).scalars().all()
    out.media_count = len(media_ids)
    if preview is not None:
        out.media_preview = preview
    else:
        # Default: best-effort preview for single playlist.
        rows = (
            session.execute(
                select(Media)
                .join(PlaylistMedia, PlaylistMedia.media_id == Media.id)
                .where(PlaylistMedia.playlist_id == p.id)
                .order_by(PlaylistMedia.added_at.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )
        out.media_preview = [
            PlaylistMediaOut(
                id=m.id,
                provider=m.provider,
                url=m.url,
                name=m.name,
                avatar_url=_media_avatar_url(m),
            )
            for m in rows
        ]
    if media_ids:
        out.video_count = int(session.execute(select(func.count()).select_from(Video).where(Video.media_id.in_(list(media_ids)))).scalar_one() or 0)
        min_ts, max_ts = session.execute(
            select(func.min(Video.published_at), func.max(Video.published_at)).where(Video.media_id.in_(list(media_ids)))
        ).one()
        if not min_ts and not max_ts:
            min_ts, max_ts = session.execute(
                select(func.min(Video.created_at), func.max(Video.created_at)).where(Video.media_id.in_(list(media_ids)))
            ).one()
        out.latest_video_at = max_ts
        out.earliest_date = _local_date(min_ts)
        out.latest_date = _local_date(max_ts)
    else:
        out.video_count = 0
        out.latest_video_at = None
        out.earliest_date = None
        out.latest_date = None
    return out


def _day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
    day_start = datetime.combine(d, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
    return day_start, day_start + timedelta(days=1)


def _enqueue_brief_full_range(session, *, playlist_id: uuid.UUID, priority: int = 3) -> int:
    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
    if not media_ids:
        return 0

    min_ts = session.execute(
        select(func.min(Video.published_at)).where(Video.media_id.in_(list(media_ids)), Video.published_at.is_not(None))
    ).scalar_one_or_none()
    if not min_ts:
        min_ts = session.execute(select(func.min(Video.created_at)).where(Video.media_id.in_(list(media_ids)))).scalar_one_or_none()
    start_date = _local_date(min_ts)
    if not start_date:
        return 0

    tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
    today = datetime.now(tzinfo).date()
    if today < start_date:
        return 0

    n = 0
    d = start_date
    while d <= today:
        enqueue_job(
            session,
            type_="brief.generate_daily",
            params={"playlist_id": str(playlist_id), "date": d.isoformat()},
            priority=priority,
        )
        n += 1
        d = d + timedelta(days=1)
    return n


@router.post("/playlists", response_model=PlaylistOut)
def create_playlist(payload: PlaylistCreate) -> PlaylistOut:
    with session_scope() as session:
        playlist = Playlist(name=payload.name, description=payload.description)
        session.add(playlist)
        session.flush()
        media_ids = list(dict.fromkeys(payload.media_ids or []))
        if media_ids:
            items = session.execute(select(Media.id).where(Media.id.in_(list(media_ids)))).scalars().all()
            found = {uuid.UUID(str(x)) for x in items}
            missing = [str(x) for x in media_ids if x not in found]
            if missing:
                raise HTTPException(status_code=404, detail=f"media not found: {', '.join(missing)}")
            for mid in media_ids:
                session.add(PlaylistMedia(playlist_id=playlist.id, media_id=mid))

        # Full-range briefs (earliest -> today) as requested; can be slow but runs async in worker.
        _enqueue_brief_full_range(session, playlist_id=playlist.id, priority=1)
        session.refresh(playlist)
        return _playlist_out(session, playlist)


@router.get("/playlists", response_model=list[PlaylistOut])
def list_playlists(limit: int = 100, offset: int = 0) -> list[PlaylistOut]:
    with session_scope() as session:
        stmt = select(Playlist).order_by(Playlist.updated_at.desc()).limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        playlist_ids = [p.id for p in items]
        preview_map: dict[uuid.UUID, list[PlaylistMediaOut]] = {pid: [] for pid in playlist_ids}
        media_count_map: dict[uuid.UUID, int] = {}
        video_stat_map: dict[uuid.UUID, dict[str, Any]] = {}

        if playlist_ids:
            # media counts
            for pid, cnt in session.execute(
                select(PlaylistMedia.playlist_id, func.count(PlaylistMedia.media_id))
                .where(PlaylistMedia.playlist_id.in_(playlist_ids))
                .group_by(PlaylistMedia.playlist_id)
            ).all():
                media_count_map[pid] = int(cnt or 0)

            # video counts + time range (coalesce published_at->created_at)
            co_ts = func.coalesce(Video.published_at, Video.created_at)
            for pid, vcnt, min_ts, max_ts in session.execute(
                select(
                    PlaylistMedia.playlist_id,
                    func.count(Video.id),
                    func.min(co_ts),
                    func.max(co_ts),
                )
                .join(Video, Video.media_id == PlaylistMedia.media_id)
                .where(PlaylistMedia.playlist_id.in_(playlist_ids))
                .group_by(PlaylistMedia.playlist_id)
            ).all():
                video_stat_map[pid] = {"video_count": int(vcnt or 0), "min_ts": min_ts, "max_ts": max_ts}

            # media preview (first 5 by added_at) - db-agnostic, no window funcs
            rows = (
                session.execute(
                    select(
                        PlaylistMedia.playlist_id.label("playlist_id"),
                        Media.id.label("media_id"),
                        Media.provider.label("provider"),
                        Media.url.label("url"),
                        Media.name.label("name"),
                        Media.avatar_url.label("avatar_url"),
                        Media.avatar_s3_key.label("avatar_s3_key"),
                    )
                    .join(Media, Media.id == PlaylistMedia.media_id)
                    .where(PlaylistMedia.playlist_id.in_(playlist_ids))
                    .order_by(PlaylistMedia.playlist_id.asc(), PlaylistMedia.added_at.desc())
                )
                .mappings()
                .all()
            )
            for r in rows:
                pid = r["playlist_id"]
                cur = preview_map.get(pid)
                if cur is None:
                    cur = []
                    preview_map[pid] = cur
                if len(cur) >= 5:
                    continue
                avatar_url = r["avatar_url"]
                key = (r["avatar_s3_key"] or "").strip()
                if key:
                    try:
                        avatar_url = s3_presign_get(settings.s3_bucket, key)
                    except Exception:
                        avatar_url = r["avatar_url"]
                cur.append(
                    PlaylistMediaOut(
                        id=r["media_id"],
                        provider=r["provider"],
                        url=r["url"],
                        name=r["name"],
                        avatar_url=avatar_url,
                    )
                )

        out_items: list[PlaylistOut] = []
        for p in items:
            out = PlaylistOut.model_validate(p)
            avatar_key = (getattr(p, "avatar_s3_key", None) or "").strip()
            if avatar_key:
                try:
                    out.avatar_url = s3_presign_get(settings.s3_bucket, avatar_key)
                except Exception:
                    pass
            bg_key = (getattr(p, "background_s3_key", None) or "").strip()
            if bg_key:
                try:
                    out.background_url = s3_presign_get(settings.s3_bucket, bg_key)
                except Exception:
                    pass

            out.media_preview = preview_map.get(p.id) or []
            out.media_count = int(media_count_map.get(p.id) or 0)

            stats = video_stat_map.get(p.id) or {}
            max_ts = stats.get("max_ts")
            min_ts = stats.get("min_ts")
            out.video_count = int(stats.get("video_count") or 0)
            out.latest_video_at = max_ts
            out.earliest_date = _local_date(min_ts)
            out.latest_date = _local_date(max_ts)
            out_items.append(out)
        return out_items


@router.get("/playlists/{playlist_id}", response_model=PlaylistOut)
def get_playlist(playlist_id: uuid.UUID) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        return _playlist_out(session, playlist)


@router.get("/playlists/{playlist_id}/detail", response_model=PlaylistDetailOut)
def get_playlist_detail(playlist_id: uuid.UUID) -> PlaylistDetailOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")

        out = PlaylistDetailOut.model_validate(_playlist_out(session, playlist).model_dump())
        rows = (
            session.execute(
                select(Media)
                .join(PlaylistMedia, PlaylistMedia.media_id == Media.id)
                .where(PlaylistMedia.playlist_id == playlist_id)
                .order_by(PlaylistMedia.added_at.desc())
            )
            .scalars()
            .all()
        )
        media_out: list[PlaylistMediaOut] = []
        for m in rows:
            media_out.append(
                PlaylistMediaOut(
                    id=m.id,
                    provider=m.provider,
                    url=m.url,
                    name=m.name,
                    avatar_url=_media_avatar_url(m),
                )
            )
        out.media = media_out
        return out


@router.patch("/playlists/{playlist_id}", response_model=PlaylistOut)
def update_playlist(playlist_id: uuid.UUID, payload: PlaylistUpdate) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        if payload.name is not None:
            playlist.name = payload.name
        if payload.description is not None:
            playlist.description = payload.description
        session.flush()
        return _playlist_out(session, playlist)


@router.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: uuid.UUID) -> dict:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        session.delete(playlist)
    return {"ok": True}


@router.post("/playlists/{playlist_id}/media")
def add_playlist_media(playlist_id: uuid.UUID, payload: PlaylistMediaAdd) -> dict:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        media = session.get(Media, payload.media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")

        existing = session.get(PlaylistMedia, {"playlist_id": playlist_id, "media_id": payload.media_id})
        if not existing:
            session.add(PlaylistMedia(playlist_id=playlist_id, media_id=payload.media_id))
            _enqueue_brief_full_range(session, playlist_id=playlist_id, priority=1)
    return {"ok": True}


@router.delete("/playlists/{playlist_id}/media/{media_id}")
def remove_playlist_media(playlist_id: uuid.UUID, media_id: uuid.UUID) -> dict:
    with session_scope() as session:
        existing = session.get(PlaylistMedia, {"playlist_id": playlist_id, "media_id": media_id})
        if existing:
            session.delete(existing)
            _enqueue_brief_full_range(session, playlist_id=playlist_id, priority=1)
    return {"ok": True}


@router.put("/playlists/{playlist_id}/media")
def replace_playlist_media(playlist_id: uuid.UUID, payload: PlaylistMediaReplace) -> dict:
    ids = list(dict.fromkeys(payload.media_ids or []))
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        if ids:
            found = set(session.execute(select(Media.id).where(Media.id.in_(list(ids)))).scalars().all())
            missing = [str(x) for x in ids if x not in found]
            if missing:
                raise HTTPException(status_code=404, detail=f"media not found: {', '.join(missing)}")

        existing = session.execute(select(PlaylistMedia).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        existing_set = {pm.media_id for pm in existing}
        desired_set = set(ids)

        for pm in existing:
            if pm.media_id not in desired_set:
                session.delete(pm)
        for mid in ids:
            if mid not in existing_set:
                session.add(PlaylistMedia(playlist_id=playlist_id, media_id=mid))

        _enqueue_brief_full_range(session, playlist_id=playlist_id, priority=1)
    return {"ok": True}


def _save_playlist_image(*, file: UploadFile, key_base: str) -> str:
    ct = (file.content_type or "").lower().strip()
    if ct not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="unsupported image type (jpg/png/webp only)")

    suffix = ".jpg" if ct == "image/jpeg" else ".png" if ct == "image/png" else ".webp"
    key = f"{key_base}{suffix}"
    with tempfile.NamedTemporaryFile(prefix="videosync-playlist-", suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        size = 0
        while True:
            chunk = file.file.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _PLAYLIST_IMG_MAX_BYTES:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(status_code=400, detail="image too large (max 2MB)")
            tmp.write(chunk)

    try:
        s3_upload_file(local_path=tmp_path, bucket=settings.s3_bucket, key=key, content_type=ct)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return key


@router.post("/playlists/{playlist_id}/avatar", response_model=PlaylistOut)
def upload_playlist_avatar(playlist_id: uuid.UUID, file: UploadFile = File(...)) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        stored = _save_playlist_image(file=file, key_base=f"playlist/{playlist_id}/avatar")
        playlist.avatar_s3_key = stored
        session.flush()
        return _playlist_out(session, playlist)


@router.post("/playlists/{playlist_id}/background", response_model=PlaylistOut)
def upload_playlist_background(playlist_id: uuid.UUID, file: UploadFile = File(...)) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        stored = _save_playlist_image(file=file, key_base=f"playlist/{playlist_id}/background")
        playlist.background_s3_key = stored
        session.flush()
        return _playlist_out(session, playlist)


class PlaylistVideoOut(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    url: str
    title: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    published_at: Any | None = None
    duration_sec: int | None = None
    status: str
    error_message: str | None = None
    media_name: str | None = None
    media_avatar_url: str | None = None


@router.get("/playlists/{playlist_id}/videos_by_date", response_model=list[PlaylistVideoOut])
def list_playlist_videos_by_date(playlist_id: uuid.UUID, date: date) -> list[PlaylistVideoOut]:
    with session_scope() as session:
        media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        if not media_ids:
            return []
        start, end = _day_bounds_utc(date)
        rows = (
            session.execute(
                select(Video, Media)
                .join(Media, Media.id == Video.media_id)
                .where(Video.media_id.in_(list(media_ids)), Video.published_at.is_not(None), Video.published_at >= start, Video.published_at < end)
                .order_by(Video.published_at.desc(), Video.created_at.desc())
            )
            .all()
        )

        out: list[PlaylistVideoOut] = []
        for v, m in rows:
            desc = v.description
            if (not desc) and v.raw_info and isinstance(v.raw_info, dict) and v.raw_info.get("description"):
                try:
                    desc = str(v.raw_info.get("description") or "")
                except Exception:
                    desc = None
            if desc:
                desc = desc.strip().replace("\n", " ")
                if len(desc) > 140:
                    desc = desc[:140].rstrip() + "…"
            avatar_url = m.avatar_url
            key = (getattr(m, "avatar_s3_key", None) or "").strip()
            if key:
                try:
                    avatar_url = s3_presign_get(settings.s3_bucket, key)
                except Exception:
                    pass
            out.append(
                PlaylistVideoOut(
                    id=v.id,
                    media_id=v.media_id,
                    url=v.url,
                    title=v.title,
                    description=desc,
                    thumbnail_url=v.thumbnail_url,
                    published_at=v.published_at,
                    duration_sec=v.duration_sec,
                    status=v.status,
                    error_message=v.error_message,
                    media_name=m.name,
                    media_avatar_url=avatar_url,
                )
            )
        return out
