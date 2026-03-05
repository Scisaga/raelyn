from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dateutil import tz
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.jobs.log import job_log
from raelyn.jobs.progress import set_job_progress
from raelyn.jobs.registry import registry
from raelyn.models import AppConfig, Asset, Brief, Job, Media, Playlist, PlaylistMedia, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.brief_prompt import (
    DEFAULT_BRIEF_PROMPT_TEMPLATE,
    brief_period_bounds_utc,
    brief_period_end_inclusive,
    brief_period_start,
    build_brief_blocks,
    compose_brief_prompt,
    load_plain_transcript_asset,
)
from raelyn.services.ffmpeg import extract_audio_to_m4a
from raelyn.services.http_client import httpx_client
from raelyn.services.llm import llm_enabled, llm_generate_markdown
from raelyn.services.pg_lock import advisory_lock_any
from raelyn.services.profile_fetch import fetch_media_profile
from raelyn.services.provider import build_media_videos_url
from raelyn.services.s3 import s3_download_file, s3_upload_file
from raelyn.services.asr import asr_enabled, asr_transcribe
from raelyn.services.subtitles import normalize_subtitle
from raelyn.services.video_meta import parse_published_at
from raelyn.services.workdir import job_workdir
from raelyn.services.ytdlp import YtdlpCookiesInvalidError, load_info_json, ytdlp_download, ytdlp_extract_info
from raelyn.services.system_pause import set_paused
from raelyn.timeutil import utcnow


def _pause_all_jobs_for_cookies(session: Session, *, job: Job, err: YtdlpCookiesInvalidError) -> None:
    reason = getattr(err, "reason", "") or "ytdlp_cookies_invalid"
    msg = str(err) or "YTDLP_COOKIES invalid"
    pause_msg = f"已暂停全部任务：{msg}（请在 UI -> 设置 更新 YTDLP_COOKIES）"
    set_paused(session, reason=str(reason), message=pause_msg)
    job_log(session, job, pause_msg, level="error")


def _ytdlp_members_only_download_enabled(session: Session) -> bool:
    # Persisted via /api/config (AppConfig key: "ytdlp_members_only").
    try:
        item = session.get(AppConfig, "ytdlp_members_only")
        value = item.value if item else None
    except Exception:
        return False
    if not isinstance(value, dict):
        return False
    enabled = value.get("enabled")
    return bool(enabled) if isinstance(enabled, bool) else False


def _provider_guard_name(provider: str, kind: str) -> str:
    return f"{provider}:{kind}"


def _clamp_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = int(default)
    return max(int(lo), min(int(hi), int(n)))


def _provider_guard_names(provider: str, kind: str) -> list[str]:
    """
    Provider-level concurrency guard.
    - sync: controlled by {youtube,bilibili}_sync_concurrency
    - download: controlled by {youtube,bilibili}_download_concurrency
    """
    p = str(provider or "").strip().lower()
    k = str(kind or "").strip().lower()
    base = _provider_guard_name(p or provider, k or kind)

    if k == "sync":
        if p == "youtube":
            n = _clamp_int(settings.youtube_sync_concurrency, default=1, lo=1, hi=10)
        elif p == "bilibili":
            n = _clamp_int(settings.bilibili_sync_concurrency, default=1, lo=1, hi=10)
        else:
            n = 1
    elif k == "download":
        if p == "youtube":
            n = _clamp_int(settings.youtube_download_concurrency, default=1, lo=1, hi=10)
        elif p == "bilibili":
            n = _clamp_int(settings.bilibili_download_concurrency, default=1, lo=1, hi=10)
        else:
            n = 1
    else:
        n = 1

    if n <= 1:
        return [base]
    return [f"{base}:{i}" for i in range(n)]


_BILIBILI_BV_PART_RE = re.compile(r"^[A-Za-z0-9]{10}$")


def _normalize_bilibili_video_url(url_or_id: str) -> str:
    s = str(url_or_id or "").strip()
    if not s:
        return s

    if s.startswith("http://") or s.startswith("https://"):
        try:
            p = urlparse(s)
            host = (p.netloc or "").lower()
            if "bilibili.com" in host and p.path.startswith("/video/"):
                seg = (p.path.split("/video/", 1)[1].split("/", 1)[0] or "").strip()
                if seg and _BILIBILI_BV_PART_RE.match(seg):
                    return f"https://www.bilibili.com/video/BV{seg}"
                if seg and seg.isdigit():
                    return f"https://www.bilibili.com/video/av{seg}"
        except Exception:
            pass
        return s

    low = s.lower()
    if low.startswith("bv") or low.startswith("av"):
        return f"https://www.bilibili.com/video/{s}"
    if s.isdigit():
        return f"https://www.bilibili.com/video/av{s}"
    if _BILIBILI_BV_PART_RE.match(s):
        return f"https://www.bilibili.com/video/BV{s}"
    return f"https://www.bilibili.com/video/{s}"


def _pick_latest_entries(info: dict[str, Any], max_entries: int) -> list[dict[str, Any]]:
    entries = info.get("entries") or []
    if not isinstance(entries, list):
        return []
    items = [e for e in entries if isinstance(e, dict)]

    def _ts(e: dict[str, Any]) -> int:
        for k in ("timestamp", "release_timestamp"):
            v = e.get(k)
            try:
                if v is None:
                    continue
                return int(v)
            except Exception:
                continue

        # yt-dlp often provides upload_date like "YYYYMMDD"
        upload_date = e.get("upload_date") or e.get("release_date")
        if isinstance(upload_date, str) and len(upload_date) == 8 and upload_date.isdigit():
            try:
                dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=tz.tzutc())
                return int(dt.timestamp())
            except Exception:
                pass

        return 0

    import heapq

    return heapq.nlargest(max_entries, items, key=_ts)


def _guess_image_ext(*, content_type: str | None, url: str) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/avif": "avif",
    }
    if ct in mapping:
        return mapping[ct]

    try:
        path = urlparse(url).path or ""
        ext = Path(path).suffix.lower().lstrip(".")
    except Exception:
        ext = ""

    if ext == "jpeg":
        ext = "jpg"
    if ext in {"jpg", "png", "webp", "gif", "avif"}:
        return ext
    return "jpg"


def _cache_media_avatar(session: Session, *, job: Job, media: Media, avatar_url: str) -> bool:
    url = str(avatar_url or "").strip()
    if not url:
        return False

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    }
    timeout = httpx.Timeout(15.0)
    max_bytes = 10 * 1024 * 1024

    try:
        use_proxy = settings.ytdlp_proxy
        if media.provider == "bilibili" or ("bilibili.com" in url.lower()):
            use_proxy = ""
        with httpx_client(proxy=use_proxy, timeout=timeout, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            if r.status_code < 200 or r.status_code >= 300:
                raise RuntimeError(f"avatar http {r.status_code}")
            data = r.content or b""
            if not data:
                raise RuntimeError("empty avatar")
            if len(data) > max_bytes:
                raise RuntimeError("avatar too large")
            content_type = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower() or None

        ext = _guess_image_ext(content_type=content_type, url=url)
        with job_workdir(job.id) as wd:
            local_path = wd / f"media-avatar.{ext}"
            local_path.write_bytes(data)
            s3_key = f"media/{media.id}/avatar.{ext}"
            s3_upload_file(
                local_path=local_path,
                bucket=settings.s3_bucket,
                key=s3_key,
                content_type=content_type,
            )
            media.avatar_s3_key = s3_key
            return True
    except Exception as e:
        job_log(session, job, f"avatar cache failed: {e}", level="warn")
        return False


def _looks_like_bilibili_face_url(url: str | None) -> bool:
    u = str(url or "").strip()
    if not u:
        return False
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        path = p.path or ""
    except Exception:
        return False
    return ("hdslb.com" in host) and ("/bfs/face/" in path)


def _best_language_subtitle(assets: list[Asset]) -> Asset | None:
    # 优先中文，其次任意字幕
    zh = [a for a in assets if a.type == "subtitle" and (a.language or "").lower().startswith("zh")]
    if zh:
        return zh[0]
    subs = [a for a in assets if a.type == "subtitle"]
    return subs[0] if subs else None


def _is_members_only_entry(provider: str, entry: dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(provider or "").lower() != "youtube":
        return False

    availability = entry.get("availability")
    if availability is None:
        availability = entry.get("_availability")
    a = str(availability or "").strip().lower()
    if not a:
        return False

    # yt-dlp commonly uses values like "subscriber_only" / "premium_only".
    if a in {"subscriber_only", "premium_only"}:
        return True
    return ("members" in a) or ("member" in a) or ("subscriber" in a) or ("premium" in a)


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

        if media.provider == "bilibili":
            # If a previously cached avatar is not a real face URL, clear it so UI won't show the wrong icon.
            if media.avatar_url and not _looks_like_bilibili_face_url(media.avatar_url):
                media.avatar_url = None
                media.avatar_s3_key = None
                job_log(session, job, "cleared non-face bilibili avatar before sync", level="warn")

        # Prefer lightweight OpenGraph scraping to avoid yt-dlp traversing entries
        # (channel pages may contain members-only videos that cause confusing errors).
        profile = None
        try:
            profile = fetch_media_profile(provider=media.provider, url=media.url)
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
            elif media.provider == "bilibili":
                # If we failed to get a real face url but the stored avatar looks like a site icon, clear it.
                if media.avatar_url and not _looks_like_bilibili_face_url(media.avatar_url):
                    media.avatar_url = None
                    media.avatar_s3_key = None
                    job_log(session, job, "cleared non-face bilibili avatar (likely site icon)", level="warn")

        # Only skip yt-dlp when OpenGraph already provided an avatar (yt-dlp can be slow on channel pages).
        if profile and profile.get("avatar_url"):
            if media.avatar_url:
                if media.provider != "bilibili" or _looks_like_bilibili_face_url(media.avatar_url):
                    _cache_media_avatar(session, job=job, media=media, avatar_url=media.avatar_url)
            media.last_profile_sync_at = utcnow()
            job_log(session, job, "profile updated from open_graph", level="info")
            return {"ok": True, "source": profile.get("source")}

        # Fallback: yt-dlp (flat) for basic fields, but never fail the whole job
        # due to members-only content.
        try:
            info = ytdlp_extract_info(media.url, flat=True, max_entries=1)
        except YtdlpCookiesInvalidError as e:
            _pause_all_jobs_for_cookies(session, job=job, err=e)
            raise
        except Exception as e:
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
        # Sync should be format-agnostic: do a flat playlist extraction to avoid failing on entries
        # like upcoming livestreams (which can raise "requested format is not available" in non-flat mode).
        try:
            info = ytdlp_extract_info(sync_url, flat=True, max_entries=playlist_limit if raw_max is not None else process_limit)
        except YtdlpCookiesInvalidError as e:
            _pause_all_jobs_for_cookies(session, job=job, err=e)
            # Mark as attempted so the scheduler doesn't hammer providers while paused.
            media.last_video_sync_at = utcnow()
            raise
        except Exception as e:
            # If the provider blocked us (e.g. bilibili 352/412), mark this run as "attempted" so the scheduler
            # won't immediately enqueue again and keep hammering the provider.
            msg = str(e or "").lower()
            blocked = ("(352)" in msg) or ("http error 412" in msg) or ("precondition failed" in msg)
            if blocked:
                media.last_video_sync_at = utcnow()
                job_log(session, job, f"sync blocked by provider; throttle until next interval: {e}", level="warn")
            raise
        if process_limit is None:
            raw_entries = info.get("entries") or []
            items = raw_entries if isinstance(raw_entries, list) else []
            entries = [e for e in items if isinstance(e, dict)]
        else:
            entries = _pick_latest_entries(info, process_limit)
        created = 0
        enqueued_downloads = 0
        allow_members_only_download = _ytdlp_members_only_download_enabled(session)
        for e in entries:
            provider_video_id = e.get("id")
            if not provider_video_id:
                continue
            provider_video_id = str(provider_video_id)
            # Avoid accidentally treating channel pages/tabs as videos (common when given a channel homepage URL).
            if media.provider == "youtube" and len(provider_video_id) != 11:
                continue
            exists = session.execute(
                select(Video.id).where(Video.provider == media.provider, Video.provider_video_id == provider_video_id)
            ).scalar_one_or_none()
            if exists:
                continue

            url = e.get("webpage_url") or e.get("url") or ""
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

            v = Video(
                provider=media.provider,
                provider_video_id=provider_video_id,
                media_id=media.id,
                url=url,
                title=e.get("title"),
                thumbnail_url=e.get("thumbnail"),
                duration_sec=e.get("duration"),
                status="discovered",
            )
            v.published_at = parse_published_at(e)
            is_members_only = _is_members_only_entry(media.provider, e)
            if is_members_only and not allow_members_only_download:
                v.status = "members_only"
                v.error_message = "members-only video; not enqueued"
            session.add(v)
            created += 1
            session.flush()
            # Enqueue daily brief only when this video already has any transcript.
            # Most videos won't at discovery time; transcript completion (subtitle/ASR) will enqueue later.
            has_transcript = (
                session.execute(select(Asset.id).where(Asset.video_id == v.id, Asset.type == "transcript").limit(1))
                .scalar_one_or_none()
                is not None
            )
            if has_transcript:
                _enqueue_brief_for_video_playlists(session, video=v, delay_seconds=90)
            if settings.auto_download_new_videos:
                if (not is_members_only) or allow_members_only_download:
                    download_type = (
                        "video.download.youtube"
                        if media.provider == "youtube"
                        else ("video.download.bilibili" if media.provider == "bilibili" else "video.download")
                    )
                    enqueue_job(session, type_=download_type, params={"video_id": str(v.id)}, priority=5)
                    enqueued_downloads += 1

        media.last_video_sync_at = utcnow()
        job_log(
            session,
            job,
            f"sync done created={created} enqueued_downloads={enqueued_downloads} scanned_entries={len(entries)}",
            level="info",
        )
        return {"created": created}


def _infer_language_from_filename(name: str) -> str | None:
    # yt-dlp 字幕文件常见格式：<id>.<lang>.vtt / <id>.<lang>.srt
    m = re.match(r"^[^.]+\.([a-zA-Z-]+)\.(vtt|srt|ass)$", name)
    if not m:
        return None
    return m.group(1)


_CJK_RE = re.compile(r"[\u3400-\u9FFF]")


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


@registry.register("video.download")
def video_download(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    # Guardrail: refuse to download channel/playlist pages accidentally stored as "videos".
    # This can happen if a media sync used a channel homepage URL and created a placeholder row.
    if video.provider == "youtube" and (video.url or "").strip():
        try:
            parsed = urlparse(str(video.url))
            host = (parsed.netloc or "").lower()
            path = (parsed.path or "").rstrip("/")
            looks_like_tab = path.endswith("/videos") or path.endswith("/shorts") or path.endswith("/featured") or path.endswith("/streams")
            looks_like_playlist = path == "/playlist" or path == "/channel" or path.startswith("/@")
            if ("youtube.com" in host or "youtu.be" in host) and (looks_like_tab or looks_like_playlist):
                video.status = "failed"
                video.error_message = f"invalid youtube video url (channel/playlist page): {video.url}"
                return {"skipped": "invalid_video_url"}
        except Exception:
            pass

    with advisory_lock_any(session, _provider_guard_names(video.provider, "download")) as lock_name:
        if not lock_name:
            job_log(session, job, "provider download locked (all slots busy); reschedule", level="warn")
            enqueue_in(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
            return {"rescheduled": True}

        video.status = "downloading"
        with job_workdir(job.id) as wd:
            info: dict[str, Any] | None = None
            subtitle_download_failed = False
            try:
                set_job_progress(job_id=job.id, current=0, total=10000)
                last_progress_at = 0.0

                def _hook(d: dict[str, Any]) -> None:
                    nonlocal last_progress_at
                    if (d.get("status") or "") != "downloading":
                        return
                    now = time.monotonic()
                    if now - last_progress_at < 0.5:
                        return
                    last_progress_at = now

                    downloaded = d.get("downloaded_bytes") or 0
                    total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and total:
                        pct = int(float(downloaded) * 10000.0 / float(total))
                        pct = max(0, min(10000, pct))
                        set_job_progress(job_id=job.id, current=pct, total=10000)
                        return

                    percent_str = str(d.get("_percent_str") or "").strip().rstrip("%")
                    try:
                        pct = int(float(percent_str) * 100)
                    except Exception:
                        return
                    pct = max(0, min(10000, pct))
                    set_job_progress(job_id=job.id, current=pct, total=10000)

                try:
                    download_target = video.url or video.provider_video_id
                    if video.provider == "bilibili":
                        download_target = _normalize_bilibili_video_url(download_target)
                    info = ytdlp_download(url=download_target, out_dir=wd, progress_hook=_hook)
                    set_job_progress(job_id=job.id, current=10000, total=10000)
                except YtdlpCookiesInvalidError as e:
                    _pause_all_jobs_for_cookies(session, job=job, err=e)
                    raise
                except Exception as e:
                    msg = str(e)
                    lower = msg.lower()
                    members_only = ("members-only" in lower) or ("join this channel" in lower) or ("members only" in lower)
                    if members_only:
                        video.status = "members_only"
                        video.error_message = "members-only video; skipped"
                        job_log(session, job, "members-only video; skipped", level="warn")
                        return {"skipped": "members_only"}

                    # Subtitle download failures (e.g. HTTP 429) should not block video download.
                    is_subtitle_error = ("subtitle" in lower) and ("unable to download" in lower or "http error 429" in lower)
                    if is_subtitle_error:
                        subtitle_download_failed = True
                        job_log(session, job, f"subtitle download failed; will continue without subtitles: {msg}", level="warn")
                    else:
                        raise
            except Exception as e:
                raise

            def _pick_video_file(paths: list[Path]) -> Path | None:
                video_file = None
                mp4s = [p for p in paths if p.suffix.lower() == ".mp4"]
                if mp4s:
                    return sorted(mp4s, key=lambda x: x.stat().st_size, reverse=True)[0]
                for p in sorted(paths, key=lambda x: x.stat().st_size, reverse=True):
                    if p.suffix.lower() in {".json", ".jpg", ".webp", ".png", ".vtt", ".srt", ".ass"}:
                        continue
                    if p.suffix.lower() in {".m4a", ".opus", ".aac", ".mp3", ".wav"}:
                        continue
                    if p.name.endswith(".info.json"):
                        continue
                    video_file = p
                    break
                return video_file

            # 找到视频文件（排除 .info.json/.jpg/.webp/.vtt/.srt）
            candidates = [p for p in wd.glob("*") if p.is_file()]
            video_file = _pick_video_file(candidates)

            # If yt-dlp aborted due to subtitles, retry once without subtitles (or proceed if video already exists).
            if (not video_file) and subtitle_download_failed:
                download_target = video.url or video.provider_video_id
                if video.provider == "bilibili":
                    download_target = _normalize_bilibili_video_url(download_target)
                info = ytdlp_download(
                    url=download_target,
                    out_dir=wd,
                    write_subtitles=False,
                    write_auto_subtitles=False,
                    progress_hook=_hook,
                )
                set_job_progress(job_id=job.id, current=10000, total=10000)
                candidates = [p for p in wd.glob("*") if p.is_file()]
                video_file = _pick_video_file(candidates)

            if video_file:
                set_job_progress(job_id=job.id, current=10000, total=10000)

            # info.json (best-effort)
            raw_info = None
            if info and info.get("id"):
                raw_info = load_info_json(wd / f"{info.get('id')}.info.json")
            if raw_info is None:
                info_json_candidates = [p for p in candidates if p.name.endswith(".info.json")]
                if info_json_candidates:
                    raw_info = load_info_json(sorted(info_json_candidates, key=lambda x: x.stat().st_size, reverse=True)[0])
            if raw_info:
                # 避免 raw_info 过大：裁剪部分字段
                keep_keys = [
                    "id",
                    "title",
                    "original_title",
                    "description",
                    "uploader",
                    "channel",
                    "upload_date",
                    "timestamp",
                    "duration",
                    "webpage_url",
                ]
                video.raw_info = {k: raw_info.get(k) for k in keep_keys}
                if (not video.description) and raw_info.get("description"):
                    video.description = str(raw_info.get("description"))
                if not video.published_at:
                    video.published_at = parse_published_at(video.raw_info)
                if not video.duration_sec and raw_info.get("duration"):
                    try:
                        video.duration_sec = int(raw_info.get("duration"))
                    except Exception:
                        pass
                new_title = str(raw_info.get("title") or "").strip()
                if new_title:
                    if not video.title:
                        video.title = new_title
                    elif video.provider == "youtube" and _contains_cjk(new_title) and (not _contains_cjk(video.title)):
                        # When YouTube provides a localized Chinese title, prefer it for download filename/UI.
                        video.title = new_title

            if not video_file:
                raise RuntimeError("yt-dlp 未产出视频文件")

            ext = video_file.suffix.lower().lstrip(".") or "bin"
            s3_key = f"{video.provider}/{video.media_id}/{video.provider_video_id}/video/raw.{ext}"
            ensure_asset(
                session,
                video_id=video.id,
                type_="video",
                format_=ext,
                language=None,
                source="ytdlp",
                variant="raw",
                local_path=video_file,
                s3_key=s3_key,
            )

            # 缩略图（封面）上传
            thumb_file = None
            thumb_candidates = [p for p in candidates if p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"}]
            if thumb_candidates:
                thumb_file = sorted(thumb_candidates, key=lambda x: x.stat().st_size, reverse=True)[0]
            if thumb_file:
                t_ext = thumb_file.suffix.lower().lstrip(".") or "img"
                ensure_asset(
                    session,
                    video_id=video.id,
                    type_="thumbnail",
                    format_=t_ext,
                    language=None,
                    source="ytdlp",
                    variant="raw",
                    local_path=thumb_file,
                    s3_key=f"{video.provider}/{video.media_id}/{video.provider_video_id}/thumbnail/raw.{t_ext}",
                )

            # 字幕文件上传
            sub_files = [p for p in wd.glob("*") if p.suffix.lower() in {".vtt", ".srt", ".ass"}]
            sub_count = 0
            for sf in sub_files:
                lang = _infer_language_from_filename(sf.name)
                fmt = sf.suffix.lower().lstrip(".")
                s3_key = f"{video.provider}/{video.media_id}/{video.provider_video_id}/subtitle/{lang or 'und'}/raw.{fmt}"
                ensure_asset(
                    session,
                    video_id=video.id,
                    type_="subtitle",
                    format_=fmt,
                    language=lang,
                    source="ytdlp",
                    variant="raw",
                    local_path=sf,
                    s3_key=s3_key,
                )
                sub_count += 1

        # Video file exists in S3 now; audio/transcript can continue in background.
        video.status = "downloaded"
        video.error_message = None
        enqueue_job(
            session,
            type_="video.extract_audio",
            params={"video_id": str(video.id)},
            priority=job.priority,
            parent_job_id=str(job.id),
        )
        if sub_count > 0:
            enqueue_job(
                session,
                type_="video.normalize_subtitle",
                params={"video_id": str(video.id)},
                priority=job.priority,
                parent_job_id=str(job.id),
            )
        return {"subtitles": sub_count}


@registry.register("video.download.youtube")
def video_download_youtube(session: Session, job: Job) -> dict | None:
    return video_download(session, job)


@registry.register("video.download.bilibili")
def video_download_bilibili(session: Session, job: Job) -> dict | None:
    return video_download(session, job)


@registry.register("video.extract_audio")
def video_extract_audio(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    video_asset = session.execute(
        select(Asset)
        .where(Asset.video_id == video.id, Asset.type == "video")
        .order_by(Asset.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not video_asset:
        return {"skipped": "video asset not found"}

    with job_workdir(job.id) as wd:
        local_video = wd / f"input.{video_asset.format}"
        s3_download_file(bucket=video_asset.s3_bucket, key=video_asset.s3_key, local_path=local_video)
        audio_out = wd / "audio.m4a"
        extract_audio_to_m4a(input_path=local_video, output_path=audio_out)
        s3_key = f"{video.provider}/{video.media_id}/{video.provider_video_id}/audio/raw.m4a"
        ensure_asset(
            session,
            video_id=video.id,
            type_="audio",
            format_="m4a",
            language=None,
            source="ffmpeg",
            variant="raw",
            local_path=audio_out,
            s3_key=s3_key,
        )

    # 若没有中文字幕 transcript 且 ASR 可用，投递 ASR
    if asr_enabled():
        has_zh_subtitle = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "subtitle",
                Asset.language.is_not(None),
                Asset.language.ilike("zh%"),
            )
            .limit(1)
        ).scalar_one_or_none()
        has_zh_transcript = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "transcript",
                Asset.language.is_not(None),
                Asset.language.ilike("zh%"),
            )
            .limit(1)
        ).scalar_one_or_none()
        if not has_zh_subtitle and not has_zh_transcript:
            enqueue_job(
                session,
                type_="video.asr_transcribe",
                params={"video_id": str(video.id)},
                priority=job.priority,
                parent_job_id=str(job.id),
            )

    # "ready" means the video is playable and audio is available for downstream ASR/notes.
    video.status = "ready"
    return {"ok": True}


@registry.register("video.normalize_subtitle")
def video_normalize_subtitle(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    subs = session.execute(select(Asset).where(Asset.video_id == video.id, Asset.type == "subtitle")).scalars().all()
    sub = _best_language_subtitle(list(subs))
    if not sub:
        return {"skipped": "no_subtitles"}

    with job_workdir(job.id) as wd:
        local_sub = wd / f"subtitle.{sub.format}"
        s3_download_file(bucket=sub.s3_bucket, key=sub.s3_key, local_path=local_sub)
        segments_json, plain = normalize_subtitle(local_sub)

        seg_path = wd / "segments.json"
        txt_path = wd / "plain.txt"
        seg_path.write_text(segments_json, encoding="utf-8")
        txt_path.write_text(plain, encoding="utf-8")

        lang = (sub.language or "und").lower()
        base = f"{video.provider}/{video.media_id}/{video.provider_video_id}/transcript/{lang}"
        force = bool(job.params.get("force"))
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="json",
            language=lang,
            source="subtitle",
            variant="segments",
            local_path=seg_path,
            s3_key=f"{base}/segments.json",
            content_type="application/json",
            replace=force,
        )
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="txt",
            language=lang,
            source="subtitle",
            variant="plain",
            local_path=txt_path,
            s3_key=f"{base}/plain.txt",
            content_type="text/plain; charset=utf-8",
            replace=force,
        )

    _enqueue_brief_for_video_playlists(session, video=video)
    if llm_enabled():
        enqueue_job(
            session,
            type_="video.polish_transcript",
            params={
                "video_id": str(video.id),
                "language": lang,
                "source": "subtitle",
                "force": bool(job.params.get("force")),
            },
            priority=job.priority,
            parent_job_id=str(job.id),
        )

    video.status = "ready"
    return {"ok": True}


@registry.register("video.asr_transcribe")
def video_asr_transcribe(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    audio_asset = session.execute(
        select(Asset)
        .where(Asset.video_id == video.id, Asset.type == "audio")
        .order_by(Asset.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not audio_asset:
        force = bool(job.params.get("force"))
        if force:
            retries = int(job.params.get("retries") or 0)
            if retries < 10:
                params = dict(job.params)
                params["retries"] = retries + 1
                job_log(session, job, f"audio not ready; reschedule asr_transcribe (retry {retries + 1}/10)", level="warn")
                enqueue_in(session, seconds=30, type_=job.type, params=params, priority=job.priority)
                return {"rescheduled": True, "reason": "audio asset not found"}
        return {"skipped": "audio asset not found"}

    with job_workdir(job.id) as wd:
        local_audio = wd / f"audio.{audio_asset.format}"
        s3_download_file(bucket=audio_asset.s3_bucket, key=audio_asset.s3_key, local_path=local_audio)
        resp = asr_transcribe(audio_path=local_audio, language="zh")

        segments_path = wd / "segments.json"
        plain_path = wd / "plain.txt"
        segments = resp.get("segments")
        text = resp.get("text") or ""
        if segments:
            segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            segments_path.write_text("[]", encoding="utf-8")
        plain_path.write_text(str(text), encoding="utf-8")

        base = f"{video.provider}/{video.media_id}/{video.provider_video_id}/transcript/zh"
        force = bool(job.params.get("force"))
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="json",
            language="zh",
            source="qwen3-asr",
            variant="segments",
            local_path=segments_path,
            s3_key=f"{base}/qwen3-asr-segments.json",
            content_type="application/json",
            replace=force,
        )
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="txt",
            language="zh",
            source="qwen3-asr",
            variant="plain",
            local_path=plain_path,
            s3_key=f"{base}/qwen3-asr-plain.txt",
            content_type="text/plain; charset=utf-8",
            replace=force,
        )

    _enqueue_brief_for_video_playlists(session, video=video)
    if llm_enabled():
        enqueue_job(
            session,
            type_="video.polish_transcript",
            params={
                "video_id": str(video.id),
                "language": "zh",
                "source": "qwen3-asr",
                "force": bool(job.params.get("force")),
            },
            priority=job.priority,
            parent_job_id=str(job.id),
        )

    video.status = "ready"
    return {"ok": True}


@registry.register("video.polish_transcript")
def video_polish_transcript(session: Session, job: Job) -> dict | None:
    if not llm_enabled():
        return {"skipped": "llm not configured"}

    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    language = str(job.params.get("language") or "").strip().lower()
    source = str(job.params.get("source") or "").strip()
    force = bool(job.params.get("force"))

    if not language or not source:
        job_log(session, job, "polish_transcript skipped: missing language/source", level="warn")
        return {"skipped": "missing params"}

    # Locate the plain transcript that was created by subtitle normalization or ASR.
    plain = session.execute(
        select(Asset).where(
            Asset.video_id == video.id,
            Asset.type == "transcript",
            Asset.format == "txt",
            Asset.variant == "plain",
            Asset.source == source,
            Asset.language == language,
        )
    ).scalar_one_or_none()
    if not plain:
        return {"skipped": "plain transcript not found"}

    if not force:
        exists = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.variant == "polished",
                Asset.source == source,
                Asset.language == language,
            )
        ).scalar_one_or_none()
        if exists:
            return {"skipped": "already polished"}

    base = f"{video.provider}/{video.media_id}/{video.provider_video_id}/transcript/{language}"
    if source == "subtitle":
        s3_key = f"{base}/polished.txt"
    else:
        s3_key = f"{base}/{source}-polished.txt"

    try:
        with job_workdir(job.id) as wd:
            local_plain = wd / "plain.txt"
            s3_download_file(bucket=plain.s3_bucket, key=plain.s3_key, local_path=local_plain)
            text = local_plain.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                return {"skipped": "plain transcript empty"}

            polished = _polish_transcript_via_llm(text=text)
            polished = _sanitize_llm_plain_text(polished).strip()
            if not polished:
                job_log(session, job, "llm transcript polish returned empty; keep plain transcript", level="warn")
                return {"skipped": "empty polish result"}

            out_path = wd / "polished.txt"
            out_path.write_text(polished, encoding="utf-8")
            ensure_asset(
                session,
                video_id=video.id,
                type_="transcript",
                format_="txt",
                language=language,
                source=source,
                variant="polished",
                local_path=out_path,
                s3_key=s3_key,
                content_type="text/plain; charset=utf-8",
                metadata={"variant": "polished", "polish_method": "llm", "polished": True},
                replace=force,
            )
            job_log(session, job, "llm transcript polish saved", level="info", data={"language": language, "source": source})
            return {"ok": True}
    except Exception as e:
        job_log(session, job, f"llm transcript polish failed: {e}", level="warn")
        return {"skipped": "llm failed"}


@registry.register("video.generate_note")
def video_generate_note(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    if not llm_enabled():
        return {"skipped": "llm not configured"}

    bucket, key = load_plain_transcript_asset(session, video.id)
    if not bucket or not key:
        return {"skipped": "transcript not found"}

    with job_workdir(job.id) as wd:
        local_txt = wd / "transcript.txt"
        s3_download_file(bucket=bucket, key=key, local_path=local_txt)
        text = local_txt.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            return {"skipped": "empty transcript"}

        prompt = (
            "你是一个内容总结助手。请根据以下视频的文字内容，生成一份**Markdown**格式的总结。\n"
            "要求：\n"
            "- 用中文\n"
            "- 输出：一句话摘要、要点（bullet）、可能的行动建议（可选）\n"
            "- 不要编造未出现的信息；不确定的地方明确说明“文本未提及”。\n\n"
            f"视频标题：{video.title or video.provider_video_id}\n"
            f"来源：{video.url}\n\n"
            "以下是视频文本：\n\n"
            f"{text}\n"
        )

        md = llm_generate_markdown(prompt=prompt, think=True)
        out = wd / "note.md"
        out.write_text(md, encoding="utf-8")

        s3_key = f"{video.provider}/{video.media_id}/{video.provider_video_id}/note/summary.md"
        asset = ensure_asset(
            session,
            video_id=video.id,
            type_="note",
            format_="md",
            language="zh",
            source="llm",
            variant="summary",
            local_path=out,
            s3_key=s3_key,
            content_type="text/markdown; charset=utf-8",
            metadata={"video_id": str(video.id)},
        )
        return {"asset_id": str(asset.id)}




def _sanitize_llm_plain_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    # Strip "thinking" blocks if a model accidentally includes them in the content.
    s = re.sub(r"(?is)<think>.*?</think>", "", s)
    s = re.sub(r"(?is)</?think>", "", s)
    # Strip common markdown wrappers/models' formatting.
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    s = s.strip().strip("\ufeff")
    return s


def _split_text_for_llm(text: str, *, max_chars: int) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_chars:
        return [t]
    lines = t.splitlines()
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        ln = len(line) + 1
        if buf and size + ln > max_chars:
            chunks.append("\n".join(buf).strip())
            buf = []
            size = 0
        # If a single line is extremely long (no newlines), hard-split it.
        if ln > max_chars and not buf:
            start = 0
            while start < len(line):
                chunks.append(line[start : start + max_chars].strip())
                start += max_chars
            continue
        buf.append(line)
        size += ln
    if buf:
        chunks.append("\n".join(buf).strip())
    return [c for c in chunks if c]


def _build_transcript_polish_prompt(*, chunk: str, index: int, total: int) -> str:
    return (
        "你是一名中文文字稿编辑兼翻译。下面是一段从视频字幕/转写得到的原始文本，可能存在：口语化、断句混乱、错别字、同音错词、"
        "标点缺失、段落缺失，也可能原文是英文或中英混杂。\n"
        "请在不增删事实的前提下，把文本整理成更易读的中文正文；如果原文不是中文，请准确翻译为自然、通顺的简体中文。\n"
        "要求（必须遵守）：\n"
        "1) 最终输出必须是简体中文正文；即使原文是英文或其他语言，也不要保留大段外文原文。\n"
        "2) 自动分段：只在语义转折/话题切换处换段；不要逐句换段；段落宁可更长一些，避免出现大量短段。\n"
        "3) 段落之间用**单个**空行分隔；不要出现连续多个空行；不要把每一句都写成单独一行。\n"
        "4) 补充必要标点（保持原意）；\n"
        "5) 修正常见错别字/同音错词；不确定就保留原样；专有名词优先使用常见中文译名，不确定时保留原文名称；\n"
        "6) **所有阿拉伯数字（0-9）必须逐字保留**：不得新增、删除、改动任何数字字符（含小数点/负号/%）。\n"
        "   - 不要把阿拉伯数字改写成中文数字，也不要把中文数字改写成阿拉伯数字；\n"
        "   - 不要推断缺失单位/小数点/时间窗口；\n"
        "7) 不要总结、不要加标题、不要加解释、不要附带翻译说明；只输出整理/翻译后的正文纯文本。\n"
        "8) 这是整段文本的一个片段（"
        f"{index}/{total}"
        "），输出中不要提及片段编号。\n\n"
        "原始文本：\n"
        f"{chunk}\n"
    )


def _polish_transcript_via_llm(*, text: str) -> str:
    # Keep chunks reasonably small to reduce the chance of context overflow.
    chunks = _split_text_for_llm(text, max_chars=12_000)
    if not chunks:
        return ""

    outputs: list[str] = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        prompt = _build_transcript_polish_prompt(chunk=chunk, index=i, total=total)

        out = llm_generate_markdown(prompt=prompt, think=False)
        out = _sanitize_llm_plain_text(out)
        if out:
            outputs.append(out.strip())

    return "\n\n".join(outputs).strip()


def _maybe_polish_transcript(
    session: Session,
    *,
    job: Job,
    video: Video,
    language: str,
    source: str,
    plain_text: str,
    output_path: Path,
    s3_key: str,
    force: bool = False,
) -> None:
    if not llm_enabled():
        return

    txt = (plain_text or "").strip()
    if not txt:
        return

    if not force:
        # Skip if already generated.
        exists = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.language == language,
                Asset.source == source,
                Asset.variant == "polished",
            )
        ).scalar_one_or_none()
        if exists:
            return

    try:
        polished = _polish_transcript_via_llm(text=txt)
        polished = (polished or "").strip()
        metadata: dict[str, Any] = {"variant": "polished"}
        if not polished:
            job_log(session, job, "llm transcript polish returned empty; keep plain transcript", level="warn")
            return
        else:
            metadata.update({"polish_method": "llm", "polished": True})

        output_path.write_text(polished, encoding="utf-8")
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="txt",
            language=language,
            source=source,
            variant="polished",
            local_path=output_path,
            s3_key=s3_key,
            content_type="text/plain; charset=utf-8",
            metadata=metadata,
            replace=force,
        )
        job_log(session, job, "llm transcript polish saved", level="info", data={"language": language, "source": source})
    except Exception as e:
        job_log(session, job, f"llm transcript polish failed: {e}", level="warn")
        return


def _enqueue_brief_for_video_playlists(session: Session, *, video: Video, delay_seconds: int = 90) -> int:
    if not llm_enabled():
        return 0

    ts = video.published_at or video.created_at
    if not ts:
        return 0

    tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
    try:
        d = ts.astimezone(tzinfo).date()
    except Exception:
        return 0

    playlist_ids = (
        session.execute(select(PlaylistMedia.playlist_id).where(PlaylistMedia.media_id == video.media_id).distinct())
        .scalars()
        .all()
    )
    if not playlist_ids:
        return 0

    rows = session.execute(select(Playlist.id, Playlist.brief_granularity).where(Playlist.id.in_(list(playlist_ids)))).all()
    n = 0
    for pid, granularity in rows:
        g = (granularity or "day").strip().lower()
        if g not in {"day", "week", "month"}:
            g = "day"
        pstart = brief_period_start(d, g)
        enqueue_in(
            session,
            seconds=delay_seconds,
            type_="brief.generate_period",
            params={"playlist_id": str(pid), "granularity": g, "period_start": pstart.isoformat()},
            priority=2,
        )
        n += 1
    return n


def _brief_generate_period_impl(
    session: Session,
    job: Job,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    period_start: date,
) -> dict | None:
    g = (granularity or "day").strip().lower()
    if g not in {"day", "week", "month"}:
        g = "day"
    period_start = brief_period_start(period_start, g)
    period_end = brief_period_end_inclusive(period_start, g)

    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        job_log(session, job, f"brief failed: playlist not found {playlist_id}", level="warn")
        return {"failed": True, "reason": "playlist not found"}

    start_utc, end_utc = brief_period_bounds_utc(period_start, g)

    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
    if not media_ids:
        br = session.execute(
            select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == g, Brief.period_start == period_start)
        ).scalar_one_or_none()
        if not br:
            br = Brief(playlist_id=playlist_id, granularity=g, period_start=period_start, status="running")
            session.add(br)
            session.flush()
        else:
            br.status = "running"
        br.status = "failed"
        br.error_message = "播放列表为空"
        br.markdown_asset_id = None
        return {"failed": True, "reason": "empty playlist"}

    videos = (
        session.execute(
            select(Video)
            .where(Video.media_id.in_(list(media_ids)), Video.published_at >= start_utc, Video.published_at < end_utc)
            .order_by(Video.published_at.asc().nullslast())
        )
        .scalars()
        .all()
    )
    if not videos:
        job_log(
            session,
            job,
            f"skip brief: no videos in period {g} {period_start.isoformat()}",
            level="info",
            data={"granularity": g, "period_start": period_start.isoformat()},
        )
        return {"skipped": True, "reason": "no videos"}

    br = session.execute(
        select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == g, Brief.period_start == period_start)
    ).scalar_one_or_none()
    if not br:
        br = Brief(playlist_id=playlist_id, granularity=g, period_start=period_start, status="running")
        session.add(br)
        session.flush()
    else:
        br.status = "running"

    if not llm_enabled():
        br.status = "failed"
        br.error_message = "llm 未配置"
        br.markdown_asset_id = None
        return {"failed": True, "reason": "llm not configured"}

    with job_workdir(job.id) as wd:
        blocks, video_urls = build_brief_blocks(session, videos)
        if not blocks:
            br.status = "failed"
            br.error_message = "本周期无可用文本（字幕/文字稿缺失）"
            br.markdown_asset_id = None
            return {"failed": True, "reason": "no transcript"}

        tpl = (getattr(playlist, "brief_prompt", None) or "").strip() or DEFAULT_BRIEF_PROMPT_TEMPLATE
        prompt = compose_brief_prompt(tpl, granularity=g, period_start=period_start, period_end=period_end, blocks=blocks)

        md = llm_generate_markdown(prompt=prompt, think=True)
        md = _sanitize_brief_markdown(md)
        out = wd / "brief.md"
        out.write_text(md, encoding="utf-8")

        key = f"brief/{playlist_id}/{g}/{period_start.isoformat()}.md"
        asset = ensure_asset(
            session,
            video_id=None,
            type_="brief",
            format_="md",
            language="zh",
            source="llm",
            variant=None,
            local_path=out,
            s3_key=key,
            content_type="text/markdown; charset=utf-8",
            metadata={
                "playlist_id": str(playlist_id),
                "granularity": g,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "job_id": str(job.id),
                "video_urls": video_urls,
            },
            dedupe=False,
        )
        br.status = "ready"
        br.markdown_asset_id = asset.id
        br.error_message = None
        return {"asset_id": str(asset.id)}


@registry.register("brief.generate_period")
def brief_generate_period(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(job.params["playlist_id"])
    g = str(job.params.get("granularity") or "day")
    if "period_start" in job.params and job.params.get("period_start"):
        pstart = date.fromisoformat(str(job.params["period_start"]))
    elif "date" in job.params and job.params.get("date"):
        pstart = date.fromisoformat(str(job.params["date"]))
    else:
        pstart = date.today()
    return _brief_generate_period_impl(session, job, playlist_id=playlist_id, granularity=g, period_start=pstart)


@registry.register("brief.generate_daily")
def brief_generate_daily(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(job.params["playlist_id"])
    brief_date = date.fromisoformat(job.params["date"])
    return _brief_generate_period_impl(session, job, playlist_id=playlist_id, granularity="day", period_start=brief_date)


def _sanitize_brief_markdown(md: str) -> str:
    src = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    if not src.strip():
        return md

    lines = src.split("\n")

    # Remove Markdown horizontal rules like "---".
    lines = [ln for ln in lines if not re.fullmatch(r"\s*-{3,}\s*", ln or "")]

    # Remove a standalone date line like "日期：2026-02-27".
    lines = [
        ln
        for ln in lines
        if not re.fullmatch(r"\s*日期\s*[:：]\s*\d{4}[/-]\d{1,2}[/-]\d{1,2}\s*", ln or "")
    ]

    # Remove a leading line containing "每日财经简报" near the top (usually a title).
    for i, ln in enumerate(lines[:6]):
        if not (ln or "").strip():
            continue
        if "每日财经简报" in ln:
            lines[i] = ""
        break

    while lines and not (lines[0] or "").strip():
        lines.pop(0)
    while lines and not (lines[-1] or "").strip():
        lines.pop()

    return "\n".join(lines).strip()
