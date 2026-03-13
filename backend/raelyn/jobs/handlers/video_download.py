from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.jobs.log import job_log
from raelyn.jobs.progress import set_job_progress
from raelyn.jobs.registry import registry
from raelyn.models import Job, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.pg_lock import advisory_lock_any
from raelyn.services.provider_pause import ProviderPauseRequestError
from raelyn.services.video_meta import parse_published_at
from raelyn.services.workdir import job_workdir
from raelyn.services.ytdlp import YtdlpCookiesInvalidError, load_info_json, ytdlp_download

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


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


@registry.register("video.download")
def video_download(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
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

        video.status = "downloading"
        with job_workdir(job.id) as wd:
            info: dict[str, Any] | None = None
            subtitle_download_failed = False
            set_job_progress(job_id=job.id, current=0, total=10000)
            last_progress_at = 0.0

            def _hook(data: dict[str, Any]) -> None:
                nonlocal last_progress_at
                if (data.get("status") or "") != "downloading":
                    return
                now = time.monotonic()
                if now - last_progress_at < 0.5:
                    return
                last_progress_at = now

                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and total:
                    pct = int(float(downloaded) * 10000.0 / float(total))
                    set_job_progress(job_id=job.id, current=max(0, min(10000, pct)), total=10000)
                    return

                percent_str = str(data.get("_percent_str") or "").strip().rstrip("%")
                try:
                    pct = int(float(percent_str) * 100)
                except Exception:
                    return
                set_job_progress(job_id=job.id, current=max(0, min(10000, pct)), total=10000)

            try:
                download_target = video.url or video.provider_video_id
                if video.provider == "bilibili":
                    download_target = _normalize_bilibili_video_url(download_target)
                info = ytdlp_download(url=download_target, provider=video.provider, out_dir=wd, progress_hook=_hook)
                set_job_progress(job_id=job.id, current=10000, total=10000)
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

            def _pick_video_file(paths: list[Path]) -> Path | None:
                mp4s = [path for path in paths if path.suffix.lower() == ".mp4"]
                if mp4s:
                    return sorted(mp4s, key=lambda item: item.stat().st_size, reverse=True)[0]
                for path in sorted(paths, key=lambda item: item.stat().st_size, reverse=True):
                    if path.suffix.lower() in {".json", ".jpg", ".webp", ".png", ".vtt", ".srt", ".ass"}:
                        continue
                    if path.suffix.lower() in {".m4a", ".opus", ".aac", ".mp3", ".wav"}:
                        continue
                    if path.name.endswith(".info.json"):
                        continue
                    return path
                return None

            candidates = [path for path in wd.glob("*") if path.is_file()]
            video_file = _pick_video_file(candidates)

            if (not video_file) and subtitle_download_failed:
                download_target = video.url or video.provider_video_id
                if video.provider == "bilibili":
                    download_target = _normalize_bilibili_video_url(download_target)
                info = ytdlp_download(
                    url=download_target,
                    provider=video.provider,
                    out_dir=wd,
                    write_subtitles=False,
                    write_auto_subtitles=False,
                    progress_hook=_hook,
                )
                set_job_progress(job_id=job.id, current=10000, total=10000)
                candidates = [path for path in wd.glob("*") if path.is_file()]
                video_file = _pick_video_file(candidates)

            if video_file:
                set_job_progress(job_id=job.id, current=10000, total=10000)

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
                        video.title = new_title

            if not video_file:
                raise RuntimeError("yt-dlp 未产出视频文件")

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
            sub_files = [path for path in wd.glob("*") if path.suffix.lower() in {".vtt", ".srt", ".ass"}]
            for sub_file in sub_files:
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
                )
                sub_count += 1

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
