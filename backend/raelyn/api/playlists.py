from __future__ import annotations

import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import Date, cast, func, select

from raelyn.api.orm import OrmModel
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import Media, Playlist, PlaylistMedia, Video
from raelyn.services.brief_schedule import schedule_brief_refresh_for_media_change
from raelyn.services.periods import day_bounds_utc, local_date, normalize_granularity, period_bounds_utc, period_start
from raelyn.services.s3 import s3_presign_get, s3_upload_file


router = APIRouter(tags=["playlists"])

_PLAYLIST_IMG_MAX_BYTES = 2 * 1024 * 1024


class PlaylistCreate(BaseModel):
    name: str
    description: str | None = None
    media_ids: list[uuid.UUID] = []


class PlaylistUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    brief_granularity: str | None = None  # day | week | month
    brief_prompt: str | None = None  # null/empty => use default


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
    brief_granularity: str = "day"
    media_count: int | None = None
    media_preview: list[PlaylistMediaOut] = Field(default_factory=list)
    video_count: int | None = None
    latest_video_at: Any | None = None
    earliest_date: date | None = None
    latest_date: date | None = None
    created_at: Any
    updated_at: Any


class PlaylistDetailOut(PlaylistOut):
    brief_prompt: str | None = None
    media: list[PlaylistMediaOut] = []


class PlaylistMediaAdd(BaseModel):
    media_id: uuid.UUID


class PlaylistMediaReplace(BaseModel):
    media_ids: list[uuid.UUID]

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
    out.brief_granularity = (getattr(p, "brief_granularity", None) or "day").strip() or "day"

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
        out.earliest_date = local_date(min_ts)
        out.latest_date = local_date(max_ts)
    else:
        out.video_count = 0
        out.latest_video_at = None
        out.earliest_date = None
        out.latest_date = None
    return out


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

        schedule_brief_refresh_for_media_change(
            session,
            playlist_id=playlist.id,
            changed_media_ids=media_ids,
            change_type="media_added",
        )
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
            out.brief_granularity = (getattr(p, "brief_granularity", None) or "day").strip() or "day"
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
            out.earliest_date = local_date(min_ts)
            out.latest_date = local_date(max_ts)
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
        out.brief_prompt = (getattr(playlist, "brief_prompt", None) or "").strip() or None
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
        if payload.brief_granularity is not None:
            g = payload.brief_granularity.strip().lower()
            if g not in {"day", "week", "month"}:
                raise HTTPException(status_code=400, detail="invalid brief_granularity (day/week/month)")
            playlist.brief_granularity = g
        if "brief_prompt" in payload.model_fields_set:
            p = (payload.brief_prompt or "").strip()
            playlist.brief_prompt = p or None
        session.flush()
        return _playlist_out(session, playlist)


@router.delete("/playlists/{playlist_id}/background", response_model=PlaylistOut)
def clear_playlist_background(playlist_id: uuid.UUID) -> PlaylistOut:
    with session_scope() as session:
        playlist = session.get(Playlist, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="playlist not found")
        playlist.background_s3_key = None
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
            schedule_brief_refresh_for_media_change(
                session,
                playlist_id=playlist_id,
                changed_media_ids=[payload.media_id],
                change_type="media_added",
            )
    return {"ok": True}


@router.delete("/playlists/{playlist_id}/media/{media_id}")
def remove_playlist_media(playlist_id: uuid.UUID, media_id: uuid.UUID) -> dict:
    with session_scope() as session:
        existing = session.get(PlaylistMedia, {"playlist_id": playlist_id, "media_id": media_id})
        if existing:
            session.delete(existing)
            schedule_brief_refresh_for_media_change(
                session,
                playlist_id=playlist_id,
                changed_media_ids=[media_id],
                change_type="media_removed",
            )
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
        changed_ids = list(existing_set.symmetric_difference(desired_set))

        for pm in existing:
            if pm.media_id not in desired_set:
                session.delete(pm)
        for mid in ids:
            if mid not in existing_set:
                session.add(PlaylistMedia(playlist_id=playlist_id, media_id=mid))

        if changed_ids:
            schedule_brief_refresh_for_media_change(
                session,
                playlist_id=playlist_id,
                changed_media_ids=changed_ids,
                change_type="media_replaced",
            )
    return {"ok": True}


def _save_playlist_image(*, file: UploadFile, key_base: str) -> str:
    ct = (file.content_type or "").lower().strip()
    if ct not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="unsupported image type (jpg/png/webp only)")

    suffix = ".jpg" if ct == "image/jpeg" else ".png" if ct == "image/png" else ".webp"
    key = f"{key_base}{suffix}"
    with tempfile.NamedTemporaryFile(prefix="raelyn-playlist-", suffix=suffix, delete=False) as tmp:
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


class PlaylistPeriodCountOut(BaseModel):
    period_start: date
    count: int


@router.get("/playlists/{playlist_id}/videos_by_date", response_model=list[PlaylistVideoOut])
def list_playlist_videos_by_date(playlist_id: uuid.UUID, date: date) -> list[PlaylistVideoOut]:
    with session_scope() as session:
        media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        if not media_ids:
            return []
        start, end = day_bounds_utc(date)
        rows = (
            session.execute(
                select(Video, Media)
                .join(Media, Media.id == Video.media_id)
                .where(Video.media_id.in_(list(media_ids)), Video.published_at.is_not(None), Video.published_at >= start, Video.published_at < end)
                .order_by(Video.published_at.asc(), Video.created_at.asc(), Video.id.asc())
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


@router.get("/playlists/{playlist_id}/video_counts_by_period", response_model=list[PlaylistPeriodCountOut])
def list_playlist_video_counts_by_period(
    playlist_id: uuid.UUID,
    granularity: str = "day",
    start: date = ...,
    end: date = ...,
) -> list[PlaylistPeriodCountOut]:
    try:
        g = normalize_granularity(granularity)
        pstart = period_start(start, g)
        pend = period_start(end, g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if pend < pstart:
        return []

    if g == "day":
        periods = (pend - pstart).days + 1
    elif g == "week":
        periods = ((pend - pstart).days // 7) + 1
    else:
        periods = (pend.year - pstart.year) * 12 + (pend.month - pstart.month) + 1
    if periods > 400:
        raise HTTPException(status_code=400, detail="range too large (max 400 periods)")

    start_utc, _ = _period_bounds_utc(pstart, g)
    _, end_utc = _period_bounds_utc(pend, g)

    tzname = (getattr(settings, "timezone", None) or "UTC").strip() or "UTC"
    unit = "day" if g == "day" else ("week" if g == "week" else "month")

    with session_scope() as session:
        local_ts = func.timezone(tzname, Video.published_at)
        bucket = func.date_trunc(unit, local_ts)
        period_start_expr = cast(bucket, Date)
        rows = (
            session.execute(
                select(
                    period_start_expr.label("period_start"),
                    func.count(Video.id).label("count"),
                )
                .select_from(Video)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(
                    PlaylistMedia.playlist_id == playlist_id,
                    Video.published_at.is_not(None),
                    Video.published_at >= start_utc,
                    Video.published_at < end_utc,
                )
                .group_by(period_start_expr)
                .order_by(period_start_expr.asc())
            )
            .all()
        )

        out: list[PlaylistPeriodCountOut] = []
        for ps, cnt in rows:
            try:
                out.append(PlaylistPeriodCountOut(period_start=ps, count=int(cnt or 0)))
            except Exception:
                continue
        return out


@router.get("/playlists/{playlist_id}/videos_by_period", response_model=list[PlaylistVideoOut])
def list_playlist_videos_by_period(
    playlist_id: uuid.UUID,
    granularity: str = "day",
    date: date = ...,
    limit: int = 200,
) -> list[PlaylistVideoOut]:
    try:
        g = normalize_granularity(granularity)
        pstart = period_start(date, g)
        start, end = period_bounds_utc(pstart, g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    n = max(1, min(int(limit or 200), 500))
    with session_scope() as session:
        media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
        if not media_ids:
            return []
        rows = (
            session.execute(
                select(Video, Media)
                .join(Media, Media.id == Video.media_id)
                .where(
                    Video.media_id.in_(list(media_ids)),
                    Video.published_at.is_not(None),
                    Video.published_at >= start,
                    Video.published_at < end,
                )
                .order_by(Video.published_at.asc(), Video.created_at.asc(), Video.id.asc())
                .limit(n)
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
