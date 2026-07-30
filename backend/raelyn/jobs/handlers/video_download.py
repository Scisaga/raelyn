from __future__ import annotations

import re
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session
from sqlalchemy import select

from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.jobs.log import job_log
from raelyn.jobs.progress import set_job_progress
from raelyn.jobs.registry import registry
from raelyn.jobs.worker_activity import touch_current_worker_activity
from raelyn.models import Job, Video
from raelyn.models import Media
from raelyn.models import Asset
from raelyn.services.assets import ensure_asset
from raelyn.services.pg_lock import advisory_lock_any
from raelyn.services.provider_pause import ProviderPauseRequestError
from raelyn.services.video_meta import parse_published_at
from raelyn.services.event_analysis import schedule_playlists_event_map_dirty_for_video
from raelyn.services.workdir import job_workdir
from raelyn.services.ytdlp import (
    _BILIBILI_CHINESE_SUBTITLE_LANGS,
    _BILIBILI_DEFAULT_SUBTITLE_LANGS,
    _BILIBILI_ENGLISH_SUBTITLE_LANGS,
    _CHINESE_SUBTITLE_LANGS,
    _DEFAULT_SUBTITLE_LANGS,
    _ENGLISH_SUBTITLE_LANGS,
    YTDLP_RETRY_WITHOUT_COOKIES_PARAM,
    YtdlpCookiesInvalidError,
    load_info_json,
    ytdlp_available_subtitle_languages,
    ytdlp_download,
    ytdlp_download_subtitles,
)
from raelyn.timeutil import utcnow

from .common import (
    _normalize_bilibili_video_url,
    _pause_all_jobs_for_cookies,
    _pause_provider_jobs,
    _provider_guard_names,
)


def _infer_language_from_filename(name: str) -> str | None:
    match = re.match(r"^[^.]+\.([a-zA-Z-]+)\.(vtt|srt|ass)$", name)
    if not match:
        return None
    return match.group(1)


_CJK_RE = re.compile(r"[\u3400-\u9FFF]")
_SUBTITLE_FILE_SUFFIXES = {".vtt", ".srt", ".ass"}
_VIDEO_FILE_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv", ".avi", ".ts"}
_JOB_PROGRESS_TOTAL = 10000
_JOB_PROGRESS_FINISHING = 9900
_DOWNLOAD_LEASE_EXTENSION_SECONDS = 3600


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _target_subtitle_langs(video: Video, media: Media | None, value: object = None) -> tuple[str, list[str]]:
    provider = str(getattr(video, "provider", "") or "").strip().lower()
    requested = str(value or "").strip().lower().replace("_", "-")
    if requested in {"zh", "chinese", "cn", "zh-cn", "zh-hans", "zh-hant", "zh-tw", "zh-hk"}:
        if provider == "bilibili":
            return "zh", list(_BILIBILI_CHINESE_SUBTITLE_LANGS)
        return "zh", list(_CHINESE_SUBTITLE_LANGS)
    if requested in {"en", "english"}:
        if provider == "bilibili":
            return "en", list(_BILIBILI_ENGLISH_SUBTITLE_LANGS)
        return "en", list(_ENGLISH_SUBTITLE_LANGS)
    if requested in {"all", "*", "any"}:
        if provider == "bilibili":
            return "all", list(_BILIBILI_DEFAULT_SUBTITLE_LANGS)
        return "all", list(_DEFAULT_SUBTITLE_LANGS)

    text = " ".join(
        str(item or "")
        for item in (
            getattr(video, "title", None),
            getattr(video, "description", None),
            getattr(media, "name", None),
            getattr(media, "description", None),
        )
    )
    if provider == "bilibili" or _contains_cjk(text):
        if provider == "bilibili":
            return "zh", list(_BILIBILI_CHINESE_SUBTITLE_LANGS)
        return "zh", list(_CHINESE_SUBTITLE_LANGS)
    return "en", list(_ENGLISH_SUBTITLE_LANGS)


def _subtitle_files(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.is_file() and path.suffix.lower() in _SUBTITLE_FILE_SUFFIXES]


def _pick_downloaded_video_file(paths: list[Path]) -> Path | None:
    video_files = [path for path in paths if path.is_file() and path.suffix.lower() in _VIDEO_FILE_SUFFIXES]
    mp4s = [path for path in video_files if path.suffix.lower() == ".mp4"]
    if mp4s:
        return sorted(mp4s, key=lambda item: item.stat().st_size, reverse=True)[0]
    if video_files:
        return sorted(video_files, key=lambda item: item.stat().st_size, reverse=True)[0]
    return None


def _video_download_target(video: Video) -> str:
    target = video.url or video.provider_video_id
    if video.provider == "bilibili":
        return _normalize_bilibili_video_url(target)
    return target


def _store_subtitle_files(session: Session, *, video: Video, files: list[Path], force: bool) -> list[str]:
    languages: list[str] = []
    for sub_file in files:
        lang = _infer_language_from_filename(sub_file.name)
        fmt = sub_file.suffix.lower().lstrip(".")
        ensure_asset(
            session,
            video_id=video.id,
            type_="subtitle",
            format_=fmt,
            language=lang,
            source="ytdlp",
            variant="raw",
            local_path=sub_file,
            s3_key=f"{video.provider}/{video.media_id}/{video.provider_video_id}/subtitle/{lang or 'und'}/raw.{fmt}",
            replace=force,
        )
        if lang:
            languages.append(lang)
    return sorted(set(languages))


def _scaled_progress(value: float, *, cap: int = _JOB_PROGRESS_FINISHING) -> int:
    try:
        pct = int(round(float(value) * float(_JOB_PROGRESS_TOTAL)))
    except Exception:
        return 0
    return max(0, min(cap, pct))


def _download_lease_expires_at():
    return utcnow() + timedelta(seconds=_DOWNLOAD_LEASE_EXTENSION_SECONDS)


def _job_progress_from_ytdlp_hook(data: dict[str, Any]) -> tuple[int, int] | None:
    status = str(data.get("status") or "").strip().lower()
    if status == "finished":
        return (_JOB_PROGRESS_FINISHING, _JOB_PROGRESS_TOTAL)
    if status != "downloading":
        return None

    downloaded = data.get("downloaded_bytes")
    total = data.get("total_bytes") or data.get("total_bytes_estimate")
    if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and float(total) > 0:
        return (_scaled_progress(float(downloaded) / float(total)), _JOB_PROGRESS_TOTAL)

    fragment_index = data.get("fragment_index")
    fragment_count = data.get("fragment_count")
    if isinstance(fragment_index, (int, float)) and isinstance(fragment_count, (int, float)) and float(fragment_count) > 0:
        return (_scaled_progress(float(fragment_index) / float(fragment_count)), _JOB_PROGRESS_TOTAL)

    percent_str = str(data.get("_percent_str") or "").strip().rstrip("%")
    if percent_str:
        try:
            return (_scaled_progress(float(percent_str) / 100.0), _JOB_PROGRESS_TOTAL)
        except Exception:
            return None
    return None


@registry.register("video.download")
def video_download(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    force = bool(job.params.get("force"))
    claimed_worker_id = str(job.worker_id or "").strip()
    claimed_execution_token = job.execution_token
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    if video.provider == "youtube" and (video.url or "").strip():
        try:
            parsed = urlparse(str(video.url))
            host = (parsed.netloc or "").lower()
            path = (parsed.path or "").rstrip("/")
            looks_like_tab = (
                path.endswith("/videos")
                or path.endswith("/shorts")
                or path.endswith("/featured")
                or path.endswith("/streams")
            )
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

        use_provider_cookies = not bool((job.params or {}).get(YTDLP_RETRY_WITHOUT_COOKIES_PARAM))
        video.status = "downloading"
        with job_workdir(job.id) as wd:
            info: dict[str, Any] | None = None
            subtitle_download_failed = False
            if claimed_execution_token is None or not set_job_progress(
                job_id=job.id,
                worker_id=claimed_worker_id,
                execution_token=claimed_execution_token,
                current=0,
                total=_JOB_PROGRESS_TOTAL,
                lease_expires_at=_download_lease_expires_at(),
            ):
                raise RuntimeError("download job ownership changed before download started")
            last_progress_at = 0.0

            def _hook(data: dict[str, Any]) -> None:
                nonlocal last_progress_at
                progress = _job_progress_from_ytdlp_hook(data)
                if progress is None:
                    return
                status = str(data.get("status") or "").strip().lower()
                now = time.monotonic()
                if status == "downloading" and now - last_progress_at < 0.5:
                    return
                last_progress_at = now
                current, total = progress
                if not set_job_progress(
                    job_id=job.id,
                    worker_id=claimed_worker_id,
                    execution_token=claimed_execution_token,
                    current=current,
                    total=total,
                    lease_expires_at=_download_lease_expires_at(),
                ):
                    raise RuntimeError("download job ownership changed during download")

            try:
                download_target = _video_download_target(video)
                info = ytdlp_download(
                    url=download_target,
                    provider=video.provider,
                    out_dir=wd,
                    use_provider_cookies=use_provider_cookies,
                    progress_hook=_hook,
                    activity_hook=touch_current_worker_activity,
                )
            except YtdlpCookiesInvalidError as e:
                _pause_all_jobs_for_cookies(session, job=job, err=e)
                raise
            except ProviderPauseRequestError as e:
                _pause_provider_jobs(session, job=job, err=e)
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

                is_subtitle_error = ("subtitle" in lower) and ("unable to download" in lower or "http error 429" in lower)
                if is_subtitle_error:
                    subtitle_download_failed = True
                    job_log(session, job, f"subtitle download failed; will continue without subtitles: {msg}", level="warn")
                else:
                    raise

            candidates = [path for path in wd.glob("*") if path.is_file()]
            video_file = _pick_downloaded_video_file(candidates)

            if (not video_file) and subtitle_download_failed:
                download_target = _video_download_target(video)
                info = ytdlp_download(
                    url=download_target,
                    provider=video.provider,
                    out_dir=wd,
                    use_provider_cookies=use_provider_cookies,
                    write_subtitles=False,
                    write_auto_subtitles=False,
                    progress_hook=_hook,
                    activity_hook=touch_current_worker_activity,
                )
                candidates = [path for path in wd.glob("*") if path.is_file()]
                video_file = _pick_downloaded_video_file(candidates)

            raw_info = None
            if info and info.get("id"):
                raw_info = load_info_json(wd / f"{info.get('id')}.info.json")
            if raw_info is None:
                info_json_candidates = [path for path in candidates if path.name.endswith(".info.json")]
                if info_json_candidates:
                    raw_info = load_info_json(sorted(info_json_candidates, key=lambda item: item.stat().st_size, reverse=True)[0])
            if raw_info:
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
                video.raw_info = {key: raw_info.get(key) for key in keep_keys}
                if (not video.description) and raw_info.get("description"):
                    video.description = str(raw_info.get("description"))
                if not video.published_at:
                    published_at = parse_published_at(video.raw_info)
                    if published_at and video.published_at != published_at:
                        video.published_at = published_at
                        schedule_playlists_event_map_dirty_for_video(
                            session,
                            video_id=video.id,
                            reason="video_published_at_changed",
                            source_job_id=job.id,
                            priority=job.priority,
                            require_event_map_input=True,
                        )
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
                        video.title = new_title

            if not video_file:
                names = ", ".join(sorted(path.name for path in candidates)[:20]) or "空"
                raise RuntimeError(f"yt-dlp 未产出可播放视频文件；目录产物: {names}")

            ext = video_file.suffix.lower().lstrip(".") or "bin"
            ensure_asset(
                session,
                video_id=video.id,
                type_="video",
                format_=ext,
                language=None,
                source="ytdlp",
                variant="raw",
                local_path=video_file,
                s3_key=f"{video.provider}/{video.media_id}/{video.provider_video_id}/video/raw.{ext}",
                replace=force,
            )

            thumb_candidates = [path for path in candidates if path.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"}]
            if thumb_candidates:
                thumb_file = sorted(thumb_candidates, key=lambda item: item.stat().st_size, reverse=True)[0]
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

            sub_count = 0
            sub_files = _subtitle_files([path for path in wd.glob("*")])
            subtitle_languages = _store_subtitle_files(session, video=video, files=sub_files, force=False)
            sub_count = len(sub_files)

        video.status = "downloaded"
        video.error_message = None
        enqueue_job(
            session,
            type_="video.extract_audio",
            params={"video_id": str(video.id), "force": force},
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
        return {"subtitles": sub_count, "subtitle_languages": subtitle_languages}


def _video_backfill_subtitles(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    media = session.get(Media, video.media_id) if video.media_id else None
    target_language, subtitle_langs = _target_subtitle_langs(
        video,
        media,
        job.params.get("language") or job.params.get("target_language"),
    )
    force = bool(job.params.get("force"))

    existing_subtitle = None
    if not force and subtitle_langs:
        existing_subtitle = session.execute(
            select(Asset.id)
            .where(
                Asset.video_id == video.id,
                Asset.type == "subtitle",
                Asset.source == "ytdlp",
                Asset.language.in_(subtitle_langs),
            )
            .limit(1)
        ).scalar_one_or_none()
    if existing_subtitle:
        normalize_job_id = enqueue_job(
            session,
            type_="video.normalize_subtitle",
            params={"video_id": str(video.id), "force": False},
            priority=job.priority,
            parent_job_id=str(job.id),
        )
        return {
            "ok": True,
            "skipped": "target subtitle already exists",
            "target_language": target_language,
            "target_subtitle_languages": subtitle_langs,
            "normalize_job_id": str(normalize_job_id),
        }

    with advisory_lock_any(session, _provider_guard_names(video.provider, "download")) as lock_name:
        if not lock_name:
            job_log(session, job, "provider subtitle backfill locked (all slots busy); reschedule", level="warn")
            enqueue_in(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
            return {"rescheduled": True}

        use_provider_cookies = not bool((job.params or {}).get(YTDLP_RETRY_WITHOUT_COOKIES_PARAM))
        try:
            with job_workdir(job.id) as wd:
                info = ytdlp_download_subtitles(
                    url=_video_download_target(video),
                    provider=video.provider,
                    out_dir=wd,
                    use_provider_cookies=use_provider_cookies,
                    subtitles_langs=subtitle_langs,
                )
                sub_files = _subtitle_files([path for path in wd.glob("*")])
                subtitle_languages = _store_subtitle_files(session, video=video, files=sub_files, force=force)
        except YtdlpCookiesInvalidError as e:
            _pause_all_jobs_for_cookies(session, job=job, err=e)
            raise
        except ProviderPauseRequestError as e:
            _pause_provider_jobs(session, job=job, err=e)
            raise

    available = ytdlp_available_subtitle_languages(info)
    if not subtitle_languages:
        return {
            "ok": True,
            "subtitles": 0,
            "target_language": target_language,
            "target_subtitle_languages": subtitle_langs,
            "available_subtitle_languages": available,
        }

    normalize_job_id = enqueue_job(
        session,
        type_="video.normalize_subtitle",
        params={"video_id": str(video.id), "force": force},
        priority=job.priority,
        parent_job_id=str(job.id),
    )
    return {
        "ok": True,
        "subtitles": len(subtitle_languages),
        "subtitle_languages": subtitle_languages,
        "target_language": target_language,
        "target_subtitle_languages": subtitle_langs,
        "available_subtitle_languages": available,
        "normalize_job_id": str(normalize_job_id),
    }


@registry.register("video.download.youtube")
def video_download_youtube(session: Session, job: Job) -> dict | None:
    return video_download(session, job)


@registry.register("video.download.bilibili")
def video_download_bilibili(session: Session, job: Job) -> dict | None:
    return video_download(session, job)


@registry.register("video.backfill_subtitles")
def video_backfill_subtitles(session: Session, job: Job) -> dict | None:
    return _video_backfill_subtitles(session, job)


@registry.register("video.backfill_subtitles.youtube")
def video_backfill_subtitles_youtube(session: Session, job: Job) -> dict | None:
    return _video_backfill_subtitles(session, job)


@registry.register("video.backfill_subtitles.bilibili")
def video_backfill_subtitles_bilibili(session: Session, job: Job) -> dict | None:
    return _video_backfill_subtitles(session, job)
