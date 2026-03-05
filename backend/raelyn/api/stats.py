from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import Asset, Brief, Job, Media, Playlist, PlaylistMedia, Video
from raelyn.services.s3 import s3_get_bytes, s3_presign_get

try:
    from dateutil import tz
except Exception:  # pragma: no cover
    tz = None


router = APIRouter(tags=["stats"])

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def _local_date(ts: datetime | None) -> date | None:
    if not ts:
        return None
    try:
        if tz is None:
            return ts.date()
        tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
        return ts.astimezone(tzinfo).date()
    except Exception:
        return None


def _month_add_one(d: date) -> date:
    y = int(d.year)
    m = int(d.month)
    if m >= 12:
        return date(y + 1, 1, 1)
    return date(y, m + 1, 1)


def _period_start(d: date, granularity: str) -> date:
    g = (granularity or "day").strip().lower()
    if g == "day":
        return d
    if g == "week":
        return d - timedelta(days=d.weekday())  # Monday
    if g == "month":
        return date(d.year, d.month, 1)
    return d


def _period_bounds_utc(period_start: date, granularity: str) -> tuple[datetime, datetime]:
    if tz is None:
        start = datetime.combine(period_start, datetime.min.time())
        g = (granularity or "day").strip().lower()
        if g == "day":
            return start, start + timedelta(days=1)
        if g == "week":
            return start, start + timedelta(days=7)
        if g == "month":
            end_date = _month_add_one(period_start)
            end = datetime.combine(end_date, datetime.min.time())
            return start, end
        return start, start + timedelta(days=1)

    tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
    start = datetime.combine(period_start, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
    g = (granularity or "day").strip().lower()
    if g == "day":
        return start, start + timedelta(days=1)
    if g == "week":
        return start, start + timedelta(days=7)
    if g == "month":
        end_date = _month_add_one(period_start)
        end = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
        return start, end
    return start, start + timedelta(days=1)


def _period_end_inclusive(period_start: date, granularity: str) -> date:
    g = (granularity or "day").strip().lower()
    if g == "day":
        return period_start
    if g == "week":
        return period_start + timedelta(days=6)
    if g == "month":
        return _month_add_one(period_start) - timedelta(days=1)
    return period_start


def _strip_markdown_line(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    # Remove common leading markers.
    while t and t[0] in {"#", "-", "*", ">", "•"}:
        t = t[1:].lstrip()
    # Images / links / inline code.
    t = _MD_IMG_RE.sub("", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = t.replace("`", "")
    # Trim leftover emphasis markers.
    t = t.replace("**", "").replace("__", "").replace("*", "").replace("_", "")
    return t.strip()


def _snippet_20(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= 20:
        return t
    return t[:19] + "…"


def _media_avatar_url(m: Any) -> str | None:
    avatar_url = getattr(m, "avatar_url", None)
    key = (getattr(m, "avatar_s3_key", None) or "").strip()
    if key:
        try:
            return s3_presign_get(settings.s3_bucket, key)
        except Exception:
            return avatar_url
    return avatar_url


def _brief_snippet_from_asset(asset: Asset | None) -> str:
    if not asset:
        return ""
    try:
        data = s3_get_bytes(bucket=asset.s3_bucket, key=asset.s3_key, max_bytes=2048)
        md = (data or b"").decode("utf-8", errors="ignore")
    except Exception:
        return ""

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        line = _strip_markdown_line(raw)
        if line:
            return _snippet_20(line)
    return ""


@router.get("/stats")
def stats() -> dict:
    with session_scope() as session:
        media_count = session.execute(select(func.count()).select_from(Media)).scalar_one()
        video_count = session.execute(select(func.count()).select_from(Video)).scalar_one()
        pending_jobs = session.execute(select(func.count()).select_from(Job).where(Job.status == "pending")).scalar_one()
        failed_jobs = session.execute(select(func.count()).select_from(Job).where(Job.status == "failed")).scalar_one()

        # Recent items for the overview page.
        recent_media_rows = session.execute(select(Media).order_by(Media.created_at.desc()).limit(5)).scalars().all()
        recent_media_ids = [m.id for m in recent_media_rows if m and getattr(m, "id", None)]
        local_video_count_by_media_id: dict[str, int] = {}
        if recent_media_ids:
            rows = session.execute(
                select(Video.media_id, func.count(Video.id).label("n"))
                .where(Video.media_id.in_(recent_media_ids))
                .group_by(Video.media_id)
            ).all()
            for mid, n in rows:
                if mid:
                    local_video_count_by_media_id[str(mid)] = int(n or 0)
        recent_media: list[dict] = []
        for m in recent_media_rows:
            avatar_url = (getattr(m, "avatar_url", None) or "").strip() or None
            key = (getattr(m, "avatar_s3_key", None) or "").strip()
            if key:
                try:
                    avatar_url = s3_presign_get(settings.s3_bucket, key)
                except Exception:
                    avatar_url = avatar_url

            recent_media.append(
                {
                    "id": str(m.id),
                    "provider": m.provider,
                    "provider_media_id": m.provider_media_id,
                    "url": m.url,
                    "name": m.name,
                    "avatar_url": avatar_url,
                    "monitor_enabled": bool(m.monitor_enabled),
                    "video_count": int(local_video_count_by_media_id.get(str(m.id), 0)),
                    "created_at": m.created_at,
                }
            )

        recent_video_rows = session.execute(
            select(Video, Media)
            .join(Media, Media.id == Video.media_id)
            .order_by(Video.published_at.desc().nullslast(), Video.created_at.desc(), Video.id.desc())
            .limit(7)
        ).all()
        recent_videos = [
            {
                "id": str(v.id),
                "url": v.url,
                "title": v.title,
                "published_at": v.published_at,
                "status": v.status,
                "media_id": str(v.media_id),
                "media_name": m.name,
            }
            for v, m in recent_video_rows
        ]

        # S3 size: "tracked" by the Asset table (fast, no S3 listing).
        bucket = (settings.s3_bucket or "").strip()
        s3_tracked_size_bytes = None
        if bucket:
            s3_tracked_size_bytes = session.execute(
                select(func.sum(func.coalesce(Asset.size_bytes, 0))).where(Asset.s3_bucket == bucket)
            ).scalar_one()
            s3_tracked_size_bytes = int(s3_tracked_size_bytes or 0)

        # Service usage counts (best-effort, derived from job history).
        done_statuses = ["succeeded", "failed"]
        asr_calls = session.execute(
            select(func.count()).select_from(Job).where(Job.type == "video.asr_transcribe", Job.status.in_(done_statuses))
        ).scalar_one()
        llm_calls = session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.type.in_(["video.generate_note", "brief.generate_period", "brief.generate_daily"]),
                Job.status.in_(done_statuses),
            )
        ).scalar_one()

        # Top playlists by "latest video timestamp" (coalesce published_at -> created_at), for the overview page.
        co_ts = func.coalesce(Video.published_at, Video.created_at)
        latest_ts_subq = (
            select(PlaylistMedia.playlist_id.label("playlist_id"), func.max(co_ts).label("latest_ts"))
            .join(Video, Video.media_id == PlaylistMedia.media_id)
            .group_by(PlaylistMedia.playlist_id)
            .subquery()
        )
        top_rows = session.execute(
            select(Playlist.id, latest_ts_subq.c.latest_ts)
            .outerjoin(latest_ts_subq, latest_ts_subq.c.playlist_id == Playlist.id)
            .order_by(latest_ts_subq.c.latest_ts.desc().nullslast(), Playlist.updated_at.desc(), Playlist.id.desc())
            .limit(4)
        ).all()
        top_ids = [r[0] for r in top_rows if r and r[0]]
        top_latest_ts: dict[str, Any | None] = {str(pid): ts for pid, ts in top_rows if pid}

        playlists: list[dict] = []
        if top_ids:
            media_count_by_playlist_id: dict[str, int] = {}
            preview_map: dict[str, list[dict[str, Any]]] = {}

            rows = session.execute(
                select(PlaylistMedia.playlist_id, func.count(PlaylistMedia.media_id))
                .where(PlaylistMedia.playlist_id.in_(top_ids))
                .group_by(PlaylistMedia.playlist_id)
            ).all()
            for pid, n in rows:
                if pid:
                    media_count_by_playlist_id[str(pid)] = int(n or 0)

            preview_rows = session.execute(
                select(PlaylistMedia.playlist_id, Media)
                .join(Media, Media.id == PlaylistMedia.media_id)
                .where(PlaylistMedia.playlist_id.in_(top_ids))
                .order_by(PlaylistMedia.playlist_id, PlaylistMedia.added_at.desc())
            ).all()
            for pid, m in preview_rows:
                if not pid or not m:
                    continue
                spid = str(pid)
                items = preview_map.setdefault(spid, [])
                if len(items) >= 5:
                    continue
                items.append(
                    {
                        "id": str(m.id),
                        "provider": m.provider,
                        "url": m.url,
                        "name": m.name,
                        "avatar_url": _media_avatar_url(m),
                    }
                )

            items = session.execute(select(Playlist).where(Playlist.id.in_(top_ids))).scalars().all()
            by_id = {str(p.id): p for p in items}
            for pid in [str(x) for x in top_ids]:
                p = by_id.get(pid)
                if not p:
                    continue

                avatar_url = None
                avatar_key = (getattr(p, "avatar_s3_key", None) or "").strip()
                if avatar_key:
                    try:
                        avatar_url = s3_presign_get(settings.s3_bucket, avatar_key)
                    except Exception:
                        avatar_url = None

                latest_video_at = top_latest_ts.get(pid)
                g = (getattr(p, "brief_granularity", None) or "day").strip().lower() or "day"
                period_start = None
                latest_period_video_title = ""
                latest_brief_snippet = ""

                local_d = _local_date(latest_video_at)
                if local_d:
                    period_start = _period_start(local_d, g)
                    period_end = _period_end_inclusive(period_start, g)
                    start_utc, end_utc = _period_bounds_utc(period_start, g)

                    # Latest videos in this period.
                    vrows = session.execute(
                        select(Video.title, Video.url, Video.published_at, co_ts.label("ts"))
                        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                        .where(PlaylistMedia.playlist_id == p.id, co_ts >= start_utc, co_ts < end_utc)
                        .order_by(co_ts.desc().nullslast(), Video.id.desc())
                        .limit(3)
                    ).all()
                    latest_period_videos = []
                    for title, url, published_at, _ts in vrows:
                        t = (title or "").strip() or (url or "").strip()
                        if not t:
                            continue
                        latest_period_videos.append({"title": t, "url": (url or "").strip() or None, "published_at": published_at})
                    if latest_period_videos:
                        latest_period_video_title = latest_period_videos[0].get("title") or ""
                    else:
                        latest_period_videos = []

                    # Brief snippet (best-effort, first non-empty line of markdown).
                    brief = session.execute(
                        select(Brief).where(Brief.playlist_id == p.id, Brief.granularity == g, Brief.period_start == period_start)
                    ).scalar_one_or_none()
                    if brief:
                        st = (getattr(brief, "status", None) or "").strip().lower()
                        if st == "succeeded" and getattr(brief, "markdown_asset_id", None):
                            asset = session.get(Asset, brief.markdown_asset_id)
                            latest_brief_snippet = _brief_snippet_from_asset(asset)
                        elif st in {"pending", "running"}:
                            latest_brief_snippet = "简报生成中"
                        elif st == "failed":
                            latest_brief_snippet = "简报生成失败"

                playlists.append(
                    {
                        "id": pid,
                        "name": p.name,
                        "avatar_url": avatar_url,
                        "media_count": int(media_count_by_playlist_id.get(pid, 0)),
                        "media_preview": preview_map.get(pid) or [],
                        "latest_video_at": latest_video_at,
                        "latest_period_start": period_start.isoformat() if period_start else None,
                        "latest_period_end": period_end.isoformat() if local_d and period_start else None,
                        "latest_period_videos": latest_period_videos if local_d and period_start else [],
                        "latest_period_video_title": latest_period_video_title or None,
                        "latest_brief_snippet": _snippet_20(latest_brief_snippet) or None,
                    }
                )

        return {
            "media_count": media_count,
            "video_count": video_count,
            "pending_jobs": pending_jobs,
            "failed_jobs": failed_jobs,
            "recent_media": recent_media,
            "recent_videos": recent_videos,
            "recent_playlists": playlists,
            "s3_tracked_size_bytes": s3_tracked_size_bytes,
            "asr_calls": int(asr_calls or 0),
            "llm_calls": int(llm_calls or 0),
        }
