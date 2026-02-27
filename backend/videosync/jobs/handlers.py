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

from videosync.config import settings
from videosync.jobs.enqueue import enqueue_in, enqueue_job
from videosync.jobs.log import job_log
from videosync.jobs.progress import set_job_progress
from videosync.jobs.registry import registry
from videosync.models import AppConfig, Asset, DailyBrief, Job, Media, PlaylistMedia, Video
from videosync.services.assets import ensure_asset
from videosync.services.ffmpeg import extract_audio_to_m4a
from videosync.services.http_client import httpx_client
from videosync.services.llm import llm_enabled, llm_generate_markdown
from videosync.services.pg_lock import advisory_lock
from videosync.services.profile_fetch import fetch_media_profile
from videosync.services.s3 import s3_download_file, s3_upload_file
from videosync.services.asr import asr_enabled, asr_transcribe
from videosync.services.subtitles import normalize_subtitle
from videosync.services.video_meta import parse_published_at
from videosync.services.workdir import job_workdir
from videosync.services.ytdlp import load_info_json, ytdlp_download, ytdlp_extract_info
from videosync.timeutil import utcnow


def _provider_guard_name(provider: str, kind: str) -> str:
    return f"{provider}:{kind}"


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
        with httpx_client(proxy=settings.ytdlp_proxy, timeout=timeout, follow_redirects=True, headers=headers) as client:
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


def _best_language_subtitle(assets: list[Asset]) -> Asset | None:
    # 优先中文，其次任意字幕
    zh = [a for a in assets if a.type == "subtitle" and (a.language or "").lower().startswith("zh")]
    if zh:
        return zh[0]
    subs = [a for a in assets if a.type == "subtitle"]
    return subs[0] if subs else None


@registry.register("media.sync_profile")
def media_sync_profile(session: Session, job: Job) -> dict | None:
    media_id = uuid.UUID(job.params["media_id"])
    media = session.get(Media, media_id)
    if not media:
        return {"skipped": "media not found"}

    with advisory_lock(session, _provider_guard_name(media.provider, "sync")) as ok:
        if not ok:
            job_log(session, job, "provider sync locked; reschedule", level="warn")
            enqueue_in(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
            return {"rescheduled": True}

        # Prefer lightweight OpenGraph scraping to avoid yt-dlp traversing entries
        # (channel pages may contain members-only videos that cause confusing errors).
        profile = None
        try:
            profile = fetch_media_profile(provider=media.provider, url=media.url)
        except Exception as e:
            job_log(session, job, f"profile scrape failed: {e}", level="warn")

        if profile:
            media.name = profile.get("name") or media.name
            media.description = profile.get("description") or media.description
            media.avatar_url = profile.get("avatar_url") or media.avatar_url

        # Only skip yt-dlp when OpenGraph already provided an avatar (yt-dlp can be slow on channel pages).
        if profile and profile.get("avatar_url"):
            if media.avatar_url:
                _cache_media_avatar(session, job=job, media=media, avatar_url=media.avatar_url)
            media.last_profile_sync_at = utcnow()
            return {"ok": True, "source": profile.get("source")}

        # Fallback: yt-dlp (flat) for basic fields, but never fail the whole job
        # due to members-only content.
        try:
            info = ytdlp_extract_info(media.url, flat=True, max_entries=1)
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

    with advisory_lock(session, _provider_guard_name(media.provider, "sync")) as ok:
        if not ok:
            job_log(session, job, "provider sync locked; reschedule", level="warn")
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

        info = ytdlp_extract_info(media.url, flat=True, max_entries=playlist_limit)
        if process_limit is None:
            raw_entries = info.get("entries") or []
            items = raw_entries if isinstance(raw_entries, list) else []
            entries = [e for e in items if isinstance(e, dict)]
        else:
            entries = _pick_latest_entries(info, process_limit)
        created = 0
        for e in entries:
            provider_video_id = e.get("id")
            if not provider_video_id:
                continue
            provider_video_id = str(provider_video_id)
            exists = session.execute(
                select(Video.id).where(Video.provider == media.provider, Video.provider_video_id == provider_video_id)
            ).scalar_one_or_none()
            if exists:
                continue

            url = e.get("webpage_url") or e.get("url") or ""
            if url and not url.startswith("http"):
                if media.provider == "youtube":
                    url = f"https://www.youtube.com/watch?v={provider_video_id}"
                elif media.provider == "bilibili":
                    url = f"https://www.bilibili.com/video/{provider_video_id}"

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
            session.add(v)
            created += 1
            session.flush()
            # Best-effort: if this media belongs to any playlists, enqueue the daily brief for the video's day.
            # Transcript may not be ready yet; later transcript completion will enqueue again.
            _enqueue_brief_for_video_playlists(session, video=v, delay_seconds=15 * 60)
            if settings.auto_download_new_videos:
                enqueue_job(session, type_="video.download", params={"video_id": str(v.id)}, priority=5)

        media.last_video_sync_at = utcnow()
        return {"created": created}


def _infer_language_from_filename(name: str) -> str | None:
    # yt-dlp 字幕文件常见格式：<id>.<lang>.vtt / <id>.<lang>.srt
    m = re.match(r"^[^.]+\.([a-zA-Z-]+)\.(vtt|srt|ass)$", name)
    if not m:
        return None
    return m.group(1)


@registry.register("video.download")
def video_download(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    with advisory_lock(session, _provider_guard_name(video.provider, "download")) as ok:
        if not ok:
            job_log(session, job, "provider download locked; reschedule", level="warn")
            enqueue_in(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
            return {"rescheduled": True}

        video.status = "downloading"
        with job_workdir(job.id) as wd:
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

                info = ytdlp_download(url=video.url or video.provider_video_id, out_dir=wd, progress_hook=_hook)
                set_job_progress(job_id=job.id, current=10000, total=10000)
            except Exception as e:
                msg = str(e)
                lower = msg.lower()
                members_only = ("members-only" in lower) or ("join this channel" in lower) or ("members only" in lower)
                if members_only:
                    video.status = "failed"
                    video.error_message = "members-only video; skipped"
                    job_log(session, job, "members-only video; skipped", level="warn")
                    return {"skipped": "members_only"}
                raise
            # info.json
            info_json_path = wd / f"{info.get('id')}.info.json"
            raw_info = load_info_json(info_json_path)
            if raw_info:
                # 避免 raw_info 过大：裁剪部分字段
                keep_keys = ["id", "title", "description", "uploader", "channel", "upload_date", "timestamp", "duration", "webpage_url"]
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
                if (not video.title) and raw_info.get("title"):
                    video.title = str(raw_info.get("title"))

            # 找到视频文件（排除 .info.json/.jpg/.webp/.vtt/.srt）
            candidates = [p for p in wd.glob("*") if p.is_file()]
            video_file = None
            # Prefer merged mp4 output when present.
            mp4s = [p for p in candidates if p.suffix.lower() == ".mp4"]
            if mp4s:
                video_file = sorted(mp4s, key=lambda x: x.stat().st_size, reverse=True)[0]
            for p in sorted(candidates, key=lambda x: x.stat().st_size, reverse=True):
                if video_file:
                    break
                if p.suffix.lower() in {".json", ".jpg", ".webp", ".png", ".vtt", ".srt", ".ass"}:
                    continue
                if p.suffix.lower() in {".m4a", ".opus", ".aac", ".mp3", ".wav"}:
                    continue
                if p.name.endswith(".info.json"):
                    continue
                video_file = p
                break
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


@registry.register("video.extract_audio")
def video_extract_audio(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    video_asset = session.execute(
        select(Asset).where(Asset.video_id == video.id, Asset.type == "video").order_by(Asset.created_at.desc())
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
        ).scalar_one_or_none()
        has_zh_transcript = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "transcript",
                Asset.language.is_not(None),
                Asset.language.ilike("zh%"),
            )
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
        )

    _enqueue_brief_for_video_playlists(session, video=video)

    video.status = "ready"
    return {"ok": True}


@registry.register("video.asr_transcribe")
def video_asr_transcribe(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    audio_asset = session.execute(
        select(Asset).where(Asset.video_id == video.id, Asset.type == "audio").order_by(Asset.created_at.desc())
    ).scalar_one_or_none()
    if not audio_asset:
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
        )

    _enqueue_brief_for_video_playlists(session, video=video)

    video.status = "ready"
    return {"ok": True}


@registry.register("video.generate_note")
def video_generate_note(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    if not llm_enabled():
        return {"skipped": "llm not configured"}

    bucket, key = _load_plain_transcript(session, video.id)
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

        md = llm_generate_markdown(prompt=prompt)
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


def _load_plain_transcript(session: Session, video_id: uuid.UUID) -> tuple[str | None, str | None]:
    # 优先字幕 zh，其次 ASR zh，其次任意 transcript plain
    order = [
        ("subtitle", "zh"),
        ("qwen3-asr", "zh"),
        ("speaches", "zh"),  # legacy
    ]
    for source, lang in order:
        a = session.execute(
            select(Asset).where(
                Asset.video_id == video_id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.variant == "plain",
                Asset.source == source,
                Asset.language == lang,
            )
        ).scalar_one_or_none()
        if a:
            return a.s3_bucket, a.s3_key

    a = session.execute(
        select(Asset).where(Asset.video_id == video_id, Asset.type == "transcript", Asset.format == "txt", Asset.variant == "plain")
    ).scalar_one_or_none()
    if a:
        return a.s3_bucket, a.s3_key
    return None, None


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
    n = 0
    for pid in playlist_ids:
        enqueue_in(
            session,
            seconds=delay_seconds,
            type_="brief.generate_daily",
            params={"playlist_id": str(pid), "date": d.isoformat()},
            priority=2,
        )
        n += 1
    return n


@registry.register("brief.generate_daily")
def brief_generate_daily(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(job.params["playlist_id"])
    brief_date = date.fromisoformat(job.params["date"])

    tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
    day_start = datetime.combine(brief_date, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
    day_end = day_start + timedelta(days=1)

    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
    if not media_ids:
        br = session.execute(
            select(DailyBrief).where(DailyBrief.playlist_id == playlist_id, DailyBrief.brief_date == brief_date)
        ).scalar_one_or_none()
        if not br:
            br = DailyBrief(playlist_id=playlist_id, brief_date=brief_date, status="running")
            session.add(br)
            session.flush()
        else:
            br.status = "running"
        br.status = "failed"
        br.error_message = "播放列表为空"
        return {"failed": True, "reason": "empty playlist"}

    videos = (
        session.execute(
            select(Video)
            .where(Video.media_id.in_(list(media_ids)), Video.published_at >= day_start, Video.published_at < day_end)
            .order_by(Video.published_at.asc().nullslast())
        )
        .scalars()
        .all()
    )

    if not videos:
        job_log(session, job, f"skip brief: no videos on {brief_date.isoformat()}", level="info")
        return {"skipped": True, "reason": "no videos"}

    br = session.execute(
        select(DailyBrief).where(DailyBrief.playlist_id == playlist_id, DailyBrief.brief_date == brief_date)
    ).scalar_one_or_none()
    if not br:
        br = DailyBrief(playlist_id=playlist_id, brief_date=brief_date, status="running")
        session.add(br)
        session.flush()
    else:
        br.status = "running"

    if not llm_enabled():
        br.status = "failed"
        br.error_message = "llm 未配置"
        return {"failed": True, "reason": "llm not configured"}

    with job_workdir(job.id) as wd:
        blocks: list[str] = []
        video_urls: list[str] = []
        for v in videos:
            bucket, key = _load_plain_transcript(session, v.id)
            if not bucket or not key:
                continue
            local = wd / f"{v.id}.txt"
            s3_download_file(bucket=bucket, key=key, local_path=local)
            text = local.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            video_urls.append(v.url)
            blocks.append(f"## {v.title or v.provider_video_id}\n来源：{v.url}\n\n{text}\n")

        if not blocks:
            br.status = "failed"
            br.error_message = "当日无可用文本（字幕/文字稿缺失）"
            return {"failed": True, "reason": "no transcript"}

        default_tpl = "\n".join(
            [
                "## 提示词",
                "",
                "你是一个**财经内容分析助手**。请基于下面提供的多条视频文字内容（可能含转写、字幕、摘要、片段拼接），生成一份可直接发布的**Markdown**财经简报。",
                "",
                "### 核心约束（必须遵守）",
                "",
                "1. **只使用文本中明确出现的信息**：",
                "",
                "   * 不要补充常识性“背景”来充当事实。",
                "   * 任何无法从文本直接验证的内容，一律写：**“文本未提及”** 或 **“文本表述不充分，无法确认”**。",
                "2. **每条要点必须附 1–3 个来源链接**：",
                "",
                "   * 来源链接必须来自文本中的视频链接/来源字段。",
                "   * 统一写法：句末用 `（来源：https://...，https://...）`（可用纯 URL 或 Markdown 链接）。",
                "3. 输出语言：**中文**。",
                "4. 输出必须是 **Markdown**，段落清晰，便于直接发布。",
                "5. 不需要：**主体归类**、**今日视频清单**。",
                "6. **不要输出“总标题/日期/分割线”**：",
                "",
                "   * 不要输出 H1（例如 `# ...`）或任何“总标题”。",
                "   * 不要输出“每日财经简报”字样。",
                "   * 不要输出“日期：...”或任何单独的日期行。",
                "   * 不要输出 Markdown 水平分割线（例如 `---`）。",
                "",
                "### 输出结构",
                "",
                "你必须严格按以下结构输出，并且**标题行必须用 Markdown 标题语法，单独成行**（不要写成正文里的小标题）：",
                "",
                "## 今日要点",
                "",
                "* 用 **bullet** 列出关键信息点（只写要点，不要写散文段落）。",
                "* 每条要点：",
                "",
                "  * 句式尽量短，先给“结论/信息”，再给“条件/范围/时间”。",
                "  * 必须在句末附 **1–3 个来源链接**，格式：`（来源：...）`。",
                "  * 若文本出现具体数值（涨跌幅、利率、通胀、盈利、库存、产量等），必须原样保留，并注明它属于谁/哪个时间窗口；若时间窗口不清楚，写“文本未提及”。",
                "",
                "示例格式（示例仅展示格式，不要复用示例内容）：",
                "",
                "* 美债收益率在文本中被描述为____，并被归因于____（来源：[视频标题](https://...)）",
                "* 某公司业绩/指引被提到____，但对同比/环比口径未说明（文本未提及）（来源：[视频标题](https://...)）",
                "",
                "## 影响与逻辑链",
                "",
                "* 写清楚“**因 → 果**”或“**事件 → 资产影响**”的链条。",
                "* 每条链条必须满足：链条中的每个关键节点都能在文本中找到依据；找不到就标注“文本未提及”。",
                "* 仍然要在句末附来源链接。",
                "",
                "## 风险与不确定性",
                "",
                "* 重点写：口径不一致、数据缺失、时间不明、推断过度、样本偏差、叙述互相矛盾之处。",
                "* 如不同视频说法冲突：明确写出“视频 A 说…；视频 B 说…；无法判定”（并分别给来源）。",
                "",
                "## 关注清单",
                "",
                "* 给出“接下来应继续跟踪”的观察项（不是预测结论），如：",
                "",
                "  * 关键数据发布、会议/财报、政策口径、价格/利差/汇率阈值、行业库存、地缘事件进展等。",
                "* 每一条都要说明：为什么要跟踪（依据文本哪个说法），并附来源链接。",
                "* 如果文本没有足够依据，写“文本未提及”。",
                "",
                "## 行动建议",
                "",
                "* 给“**条件触发式**”建议，形式如：",
                "",
                "  * “若文本中提到的 A 指标继续…，可考虑…；否则…（文本未提及具体阈值）”",
                "* **不允许直接给确定性买卖指令**；只能写“可考虑/可关注/需验证”。",
                "* 每条建议仍需来源链接；若建议的关键条件缺失，标注“文本未提及”。",
                "",
                "简报日期（仅供你理解上下文，不要在输出中出现）：{{date}}",
                "",
                "以下是视频文本（多条，可能包含标题与链接；若未包含链接，请在输出中把来源写为“文本未提供链接”）：",
                "{{blocks}}",
            ]
        ).strip()

        cfg_tpl: str | None = None
        try:
            cfg = session.get(AppConfig, "briefs")
            if cfg and isinstance(cfg.value, dict):
                v = cfg.value.get("daily_prompt")
                if isinstance(v, str) and v.strip():
                    cfg_tpl = v.strip()
        except Exception:
            cfg_tpl = None

        blocks_text = "\n\n\n".join(blocks)
        tpl = cfg_tpl or default_tpl
        prompt = tpl.replace("{{date}}", brief_date.isoformat()).replace("{{blocks}}", blocks_text)
        if "{{blocks}}" not in tpl:
            prompt = (prompt.rstrip() + "\n\n\n" + blocks_text).strip()

        md = llm_generate_markdown(prompt=prompt)
        md = _sanitize_brief_markdown(md)
        out = wd / "brief.md"
        out.write_text(md, encoding="utf-8")

        key = f"brief/{playlist_id}/{brief_date.isoformat()}.md"
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
            metadata={"playlist_id": str(playlist_id), "date": brief_date.isoformat(), "job_id": str(job.id), "video_urls": video_urls},
            dedupe=False,
        )
        br.status = "ready"
        br.markdown_asset_id = asset.id
        br.error_message = None
        return {"asset_id": str(asset.id)}


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
