from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String, cast, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.models import Asset, Job, Media, Video
from raelyn.services.pg_lock import advisory_lock_any, try_xact_lock
from raelyn.services.provider_pause import ProviderPauseRequestError
from raelyn.services.profile_fetch import fetch_media_profile
from raelyn.services.provider import build_media_videos_url
from raelyn.services.transcripts import TRANSCRIPT_VARIANTS
from raelyn.services.video_actions import schedule_video_download
from raelyn.services.video_meta import parse_published_at
from raelyn.services.event_analysis import schedule_playlists_event_regime_dirty_for_video
from raelyn.services.ytdlp import YtdlpCookiesInvalidError, ytdlp_extract_info
from raelyn.services.ytdlp_errors import is_provider_media_unavailable_error
from raelyn.timeutil import utcnow

from .briefs import _enqueue_brief_for_video_playlists
from .common import (
    _cache_media_avatar,
    _entry_timestamp,
    _is_members_only_entry,
    _looks_like_bilibili_face_url,
    _normalize_bilibili_video_url,
    _pause_all_jobs_for_cookies,
    _pause_provider_jobs,
    _pick_latest_entries,
    _provider_guard_names,
    _ytdlp_members_only_download_enabled,
)

_AUTO_DISCOVERED_DOWNLOAD_PRIORITY = 7
_DOWNLOAD_JOB_TYPES = ("video.download", "video.download.youtube", "video.download.bilibili")
_AUTO_DISABLED_SOURCE_UNAVAILABLE_REASON = "source_unavailable"
_RAW_INFO_KEEP_KEYS = [
    "id",
    "title",
    "original_title",
    "description",
    "uploader",
    "channel",
    "upload_date",
    "timestamp",
    "release_timestamp",
    "release_date",
    "duration",
    "webpage_url",
]


def _auto_disable_media_source_unavailable(session: Session, *, job: Job, media: Media, err: Exception) -> dict:
    now = utcnow()
    message = f"{media.provider} 媒体源不可用，已自动停用监控：{err}"
    media.monitor_enabled = False
    media.last_video_sync_at = now
    sync_cursor = dict(media.sync_cursor or {})
    sync_cursor["auto_disabled"] = {
        "reason": _AUTO_DISABLED_SOURCE_UNAVAILABLE_REASON,
        "message": message,
        "at": now.isoformat(),
        "job_id": str(job.id),
    }
    media.sync_cursor = sync_cursor
    job_log(session, job, message, level="warn")
    return {"disabled": True, "reason": _AUTO_DISABLED_SOURCE_UNAVAILABLE_REASON}


def _job_video_id_expr():
    return Job.params["video_id"].astext


def _enqueue_existing_discovered_downloads(
    session: Session,
    *,
    media: Media,
    allow_members_only_download: bool,
    download_priority: int,
) -> int:
    eligible_statuses = ["discovered"]
    if allow_members_only_download:
        eligible_statuses.append("members_only")

    video_ids = (
        session.execute(
            select(Video.id)
            .where(
                Video.media_id == media.id,
                Video.status.in_(eligible_statuses),
                ~exists(select(1).where(Asset.video_id == Video.id, Asset.type == "video")),
                ~exists(
                    select(1).select_from(Job).where(
                        Job.type.in_(_DOWNLOAD_JOB_TYPES),
                        Job.status.in_(("pending", "running")),
                        _job_video_id_expr() == cast(Video.id, String),
                    )
                ),
            )
            .order_by(Video.published_at.desc().nullslast(), Video.created_at.desc(), Video.id.desc())
        )
        .scalars()
        .all()
    )
    enqueued = 0
    for video_id in video_ids:
        schedule_video_download(session, video_id, priority=download_priority)
        enqueued += 1
    return enqueued


def _job_download_priority(job: Job) -> int:
    try:
        return int(job.params.get("download_priority", _AUTO_DISCOVERED_DOWNLOAD_PRIORITY))
    except Exception:
        return _AUTO_DISCOVERED_DOWNLOAD_PRIORITY


def _compact_raw_info(info: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(info, dict):
        return None
    return {key: info.get(key) for key in _RAW_INFO_KEEP_KEYS}


def _media_sync_lock_name(media_id: uuid.UUID) -> str:
    return f"media:{media_id}:sync"


def _insert_discovered_video_if_new(
    session: Session,
    *,
    media: Media,
    provider_video_id: str,
    url: str,
    metadata_entry: dict[str, Any],
    is_members_only: bool,
    allow_members_only_download: bool,
) -> Video | None:
    video_id = uuid.uuid4()
    status = "discovered"
    error_message = None
    if is_members_only and not allow_members_only_download:
        status = "members_only"
        error_message = "members-only video; not enqueued"

    published_at = parse_published_at(metadata_entry)
    raw_info = _compact_raw_info(metadata_entry)
    values = {
        "id": video_id,
        "provider": media.provider,
        "provider_video_id": provider_video_id,
        "media_id": media.id,
        "url": url,
        "title": metadata_entry.get("title"),
        "thumbnail_url": metadata_entry.get("thumbnail"),
        "duration_sec": metadata_entry.get("duration"),
        "published_at": published_at,
        "raw_info": raw_info,
        "status": status,
        "error_message": error_message,
    }
    inserted_id = session.execute(
        pg_insert(Video)
        .values(**values)
        .on_conflict_do_nothing(constraint="video_provider_video_id_ux")
        .returning(Video.id)
    ).scalar_one_or_none()
    if not inserted_id:
        return None

    return Video(**{**values, "id": inserted_id})


def _enrich_youtube_entry_if_missing_published_at(
    *,
    session: Session,
    job: Job,
    entry: dict[str, Any],
    url: str,
) -> dict[str, Any]:
    if parse_published_at(entry) is not None:
        return entry
    try:
        info = ytdlp_extract_info(url, provider="youtube", flat=False, max_entries=1)
    except Exception as e:
        job_log(session, job, f"youtube metadata enrich failed for {url}: {e}", level="warn")
        return entry

    if not isinstance(info, dict):
        return entry
    if str(info.get("id") or "").strip() != str(entry.get("id") or "").strip():
        return entry
    enriched = dict(entry)
    for key in _RAW_INFO_KEEP_KEYS:
        value = info.get(key)
        if value is not None:
            enriched[key] = value
    if info.get("thumbnail") and not enriched.get("thumbnail"):
        enriched["thumbnail"] = info.get("thumbnail")
    return enriched


@registry.register("media.sync_profile")
def media_sync_profile(session: Session, job: Job) -> dict | None:
    media_id = uuid.UUID(job.params["media_id"])
    media = session.get(Media, media_id)
    if not media:
        return {"skipped": "media not found"}

    with advisory_lock_any(session, _provider_guard_names(media.provider, "sync")) as lock_name:
        if not lock_name:
            job_log(session, job, "provider sync locked (all slots busy); reschedule", level="warn")
            enqueue_in(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
            return {"rescheduled": True}

        if media.provider == "bilibili" and media.avatar_url and not _looks_like_bilibili_face_url(media.avatar_url):
            media.avatar_url = None
            media.avatar_s3_key = None
            job_log(session, job, "cleared non-face bilibili avatar before sync", level="warn")

        profile = None
        try:
            profile = fetch_media_profile(provider=media.provider, url=media.url)
        except ProviderPauseRequestError as e:
            _pause_provider_jobs(session, job=job, err=e)
            media.last_profile_sync_at = utcnow()
            raise
        except Exception as e:
            job_log(session, job, f"profile scrape failed: {e}", level="warn")

        if not profile:
            job_log(session, job, "profile open_graph: no data; fallback to yt-dlp", level="info")

        if profile:
            media.name = profile.get("name") or media.name
            media.description = profile.get("description") or media.description
            new_avatar = profile.get("avatar_url")
            if new_avatar:
                media.avatar_url = new_avatar
            elif media.provider == "bilibili" and media.avatar_url and not _looks_like_bilibili_face_url(media.avatar_url):
                media.avatar_url = None
                media.avatar_s3_key = None
                job_log(session, job, "cleared non-face bilibili avatar (likely site icon)", level="warn")

        if profile and profile.get("avatar_url"):
            if media.avatar_url and (media.provider != "bilibili" or _looks_like_bilibili_face_url(media.avatar_url)):
                _cache_media_avatar(session, job=job, media=media, avatar_url=media.avatar_url)
            media.last_profile_sync_at = utcnow()
            job_log(session, job, "profile updated from open_graph", level="info")
            return {"ok": True, "source": profile.get("source")}

        try:
            info = ytdlp_extract_info(media.url, provider=media.provider, flat=True, max_entries=1)
        except YtdlpCookiesInvalidError as e:
            _pause_all_jobs_for_cookies(session, job=job, err=e)
            raise
        except ProviderPauseRequestError as e:
            _pause_provider_jobs(session, job=job, err=e)
            media.last_profile_sync_at = utcnow()
            raise
        except Exception as e:
            if is_provider_media_unavailable_error(str(e), provider=media.provider):
                return _auto_disable_media_source_unavailable(session, job=job, media=media, err=e)
            msg = str(e)
            if "members-only" in msg.lower() or "members only" in msg.lower():
                job_log(session, job, "members-only content detected; skip profile sync", level="warn")
                media.last_profile_sync_at = utcnow()
                return {"ok": True, "skipped": "members_only_profile"}
            job_log(session, job, f"yt-dlp profile failed: {e}", level="warn")
            media.last_profile_sync_at = utcnow()
            return {"ok": True, "skipped": "profile_fetch_failed"}

        media.name = info.get("channel") or info.get("uploader") or info.get("title") or media.name
        media.description = info.get("description") or media.description
        media.avatar_url = info.get("channel_thumbnail") or info.get("thumbnail") or media.avatar_url
        media.subscriber_count = info.get("channel_follower_count") or info.get("subscriber_count") or media.subscriber_count
        media.video_count = info.get("playlist_count") or info.get("video_count") or media.video_count
        if media.avatar_url:
            _cache_media_avatar(session, job=job, media=media, avatar_url=media.avatar_url)
        media.last_profile_sync_at = utcnow()
        job_log(session, job, "profile updated from yt-dlp flat", level="info")
        return {"ok": True, "source": "yt_dlp_flat"}


@registry.register("media.sync_videos")
def media_sync_videos(session: Session, job: Job) -> dict | None:
    media_id = uuid.UUID(job.params["media_id"])
    media = session.get(Media, media_id)
    if not media:
        return {"skipped": "media not found"}

    force = bool(job.params.get("force"))
    if (not force) and (not media.monitor_enabled):
        return {"skipped": "monitor_disabled"}

    if not try_xact_lock(session, _media_sync_lock_name(media.id)):
        job_log(session, job, "media sync locked; reschedule", level="warn")
        enqueue_in(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
        return {"rescheduled": True}

    with advisory_lock_any(session, _provider_guard_names(media.provider, "sync")) as lock_name:
        if not lock_name:
            job_log(session, job, "provider sync locked (all slots busy); reschedule", level="warn")
            enqueue_in(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
            return {"rescheduled": True}

        raw_max = job.params.get("max_entries")
        playlist_limit: int | None = None
        process_limit: int | None = int(settings.sync_max_entries)
        if raw_max is not None:
            try:
                n = int(raw_max)
            except Exception:
                n = int(settings.sync_max_entries)
            if n <= 0:
                playlist_limit = 0
                process_limit = None
            else:
                playlist_limit = n
                process_limit = n

        sync_url = build_media_videos_url(provider=media.provider, provider_media_id=media.provider_media_id) or media.url
        job_log(
            session,
            job,
            f"sync start url={sync_url} max_entries={process_limit if raw_max is None else playlist_limit}",
            level="info",
        )
        try:
            info = ytdlp_extract_info(
                sync_url,
                provider=media.provider,
                flat=True,
                max_entries=playlist_limit if raw_max is not None else process_limit,
            )
        except YtdlpCookiesInvalidError as e:
            _pause_all_jobs_for_cookies(session, job=job, err=e)
            media.last_video_sync_at = utcnow()
            raise
        except ProviderPauseRequestError as e:
            _pause_provider_jobs(session, job=job, err=e)
            media.last_video_sync_at = utcnow()
            raise
        except Exception as e:
            if is_provider_media_unavailable_error(str(e), provider=media.provider):
                return _auto_disable_media_source_unavailable(session, job=job, media=media, err=e)
            msg = str(e or "").lower()
            blocked = ("(352)" in msg) or ("http error 412" in msg) or ("precondition failed" in msg)
            if blocked:
                media.last_video_sync_at = utcnow()
                job_log(session, job, f"sync blocked by provider; throttle until next interval: {e}", level="warn")
            raise
        if process_limit is None:
            raw_entries = info.get("entries") or []
            items = raw_entries if isinstance(raw_entries, list) else []
            entries = [entry for entry in items if isinstance(entry, dict)]
        else:
            entries = _pick_latest_entries(info, process_limit)
        entries = sorted(entries, key=_entry_timestamp, reverse=True)

        created = 0
        enqueued_downloads = 0
        download_priority = _job_download_priority(job)
        allow_members_only_download = _ytdlp_members_only_download_enabled(session)
        for entry in entries:
            provider_video_id = entry.get("id")
            if not provider_video_id:
                continue
            provider_video_id = str(provider_video_id)
            if media.provider == "youtube" and len(provider_video_id) != 11:
                continue

            url = entry.get("webpage_url") or entry.get("url") or ""
            if not url:
                if media.provider == "youtube":
                    url = f"https://www.youtube.com/watch?v={provider_video_id}"
                elif media.provider == "bilibili":
                    url = _normalize_bilibili_video_url(provider_video_id)
            elif not url.startswith("http"):
                if media.provider == "youtube":
                    url = f"https://www.youtube.com/watch?v={provider_video_id}"
                elif media.provider == "bilibili":
                    url = _normalize_bilibili_video_url(provider_video_id)
            elif media.provider == "bilibili":
                url = _normalize_bilibili_video_url(url)

            metadata_entry = entry
            if media.provider == "youtube":
                metadata_entry = _enrich_youtube_entry_if_missing_published_at(
                    session=session,
                    job=job,
                    entry=entry,
                    url=url,
                )

            is_members_only = _is_members_only_entry(media.provider, entry)
            video = _insert_discovered_video_if_new(
                session,
                media=media,
                provider_video_id=provider_video_id,
                url=url,
                metadata_entry=metadata_entry,
                is_members_only=is_members_only,
                allow_members_only_download=allow_members_only_download,
            )
            if not video:
                continue
            created += 1
            if video.published_at:
                schedule_playlists_event_regime_dirty_for_video(
                    session,
                    video_id=video.id,
                    reason="video_published_at_changed",
                    source_job_id=job.id,
                    priority=job.priority,
                )

            has_transcript = (
                session.execute(
                    select(Asset.id).where(
                        Asset.video_id == video.id,
                        Asset.type == "transcript",
                        Asset.format == "txt",
                        Asset.variant.in_(list(TRANSCRIPT_VARIANTS)),
                    ).limit(1)
                )
                .scalar_one_or_none()
                is not None
            )
            if has_transcript:
                _enqueue_brief_for_video_playlists(session, video=video, delay_seconds=90)
            if settings.auto_download_new_videos and ((not is_members_only) or allow_members_only_download):
                download_type = (
                    "video.download.youtube"
                    if media.provider == "youtube"
                    else ("video.download.bilibili" if media.provider == "bilibili" else "video.download")
                )
                enqueue_job(
                    session,
                    type_=download_type,
                    params={"video_id": str(video.id)},
                    priority=download_priority,
                )
                enqueued_downloads += 1

        existing_downloads = 0
        if bool(job.params.get("enqueue_existing_downloads")):
            existing_downloads = _enqueue_existing_discovered_downloads(
                session,
                media=media,
                allow_members_only_download=allow_members_only_download,
                download_priority=download_priority,
            )
            enqueued_downloads += existing_downloads

        media.last_video_sync_at = utcnow()
        job_log(
            session,
            job,
            (
                "sync done "
                f"created={created} "
                f"enqueued_downloads={enqueued_downloads} "
                f"existing_downloads={existing_downloads} "
                f"scanned_entries={len(entries)}"
            ),
            level="info",
        )
        return {"created": created}
