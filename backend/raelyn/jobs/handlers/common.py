from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dateutil import tz
import httpx
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.log import job_log
from raelyn.models import AppConfig, Asset, Job, Media
from raelyn.services.assets import replace_standalone_asset
from raelyn.services.provider_cookies import cookie_config_name, cookie_provider_label, normalize_cookie_provider
from raelyn.services.http_client import httpx_client
from raelyn.services.provider_pause import ProviderPauseRequestError, job_provider, set_provider_paused
from raelyn.services.system_pause import set_paused
from raelyn.services.workdir import job_workdir
from raelyn.services.ytdlp import YtdlpCookiesInvalidError


def _pause_all_jobs_for_cookies(session: Session, *, job: Job, err: YtdlpCookiesInvalidError) -> None:
    provider = normalize_cookie_provider(getattr(err, "provider", None)) or normalize_cookie_provider(job_provider(session, job))
    reason = getattr(err, "reason", "") or "ytdlp_cookies_invalid"
    msg = str(err) or "cookies invalid"
    if provider:
        pause_msg = (
            f"{cookie_provider_label(provider)}任务已暂停：{msg}"
            f"（请在 UI -> 设置 更新 {cookie_config_name(provider)}）"
        )
        pause = set_provider_paused(session, provider=provider, reason=str(reason), message=pause_msg)
        job_log(session, job, pause.get("message") or pause_msg, level="error")
        return
    pause_msg = f"已暂停全部任务：{msg}（请在 UI -> 设置 更新 Cookies）"
    set_paused(session, reason=str(reason), message=pause_msg)
    job_log(session, job, pause_msg, level="error")


def _pause_provider_jobs(session: Session, *, job: Job, err: ProviderPauseRequestError) -> None:
    provider = getattr(err, "provider", "") or "provider"
    reason = getattr(err, "reason", "") or "provider_pause_requested"
    msg = str(err) or f"{provider} paused"
    pause = set_provider_paused(session, provider=provider, reason=str(reason), message=msg)
    job_log(session, job, pause.get("message") or msg, level="error")


def _ytdlp_members_only_download_enabled(session: Session) -> bool:
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
            parsed = urlparse(s)
            host = (parsed.netloc or "").lower()
            if "bilibili.com" in host and parsed.path.startswith("/video/"):
                seg = (parsed.path.split("/video/", 1)[1].split("/", 1)[0] or "").strip()
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
    items = [entry for entry in entries if isinstance(entry, dict)]

    def _ts(entry: dict[str, Any]) -> int:
        for key in ("timestamp", "release_timestamp"):
            value = entry.get(key)
            try:
                if value is None:
                    continue
                return int(value)
            except Exception:
                continue

        upload_date = entry.get("upload_date") or entry.get("release_date")
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
    content = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/avif": "avif",
    }
    if content in mapping:
        return mapping[content]

    try:
        ext = Path(urlparse(url).path or "").suffix.lower().lstrip(".")
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
        if media.provider == "bilibili" or "bilibili.com" in url.lower():
            use_proxy = ""
        with httpx_client(proxy=use_proxy, timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError(f"avatar http {response.status_code}")
            data = response.content or b""
            if not data:
                raise RuntimeError("empty avatar")
            if len(data) > max_bytes:
                raise RuntimeError("avatar too large")
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower() or None

        ext = _guess_image_ext(content_type=content_type, url=url)
        with job_workdir(job.id) as wd:
            local_path = wd / f"media-avatar.{ext}"
            local_path.write_bytes(data)
            s3_key = f"media/{media.id}/avatar.{ext}"
            asset = replace_standalone_asset(
                session,
                asset_id=media.avatar_asset_id,
                type_="image",
                format_=ext,
                source="media",
                variant="avatar",
                local_path=local_path,
                s3_key=s3_key,
                metadata={"media_id": str(media.id), "kind": "avatar"},
                content_type=content_type,
            )
            media.avatar_asset_id = asset.id
            media.avatar_s3_key = s3_key
            return True
    except Exception as e:
        job_log(session, job, f"avatar cache failed: {e}", level="warn")
        return False


def _looks_like_bilibili_face_url(url: str | None) -> bool:
    value = str(url or "").strip()
    if not value:
        return False
    try:
        parsed = urlparse(value)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
    except Exception:
        return False
    return ("hdslb.com" in host) and ("/bfs/face/" in path)


def _best_language_subtitle(assets: list[Asset]) -> Asset | None:
    zh = [asset for asset in assets if asset.type == "subtitle" and (asset.language or "").lower().startswith("zh")]
    if zh:
        return zh[0]
    subs = [asset for asset in assets if asset.type == "subtitle"]
    return subs[0] if subs else None


def _is_members_only_entry(provider: str, entry: dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(provider or "").lower() != "youtube":
        return False

    availability = entry.get("availability")
    if availability is None:
        availability = entry.get("_availability")
    value = str(availability or "").strip().lower()
    if not value:
        return False

    if value in {"subscriber_only", "premium_only"}:
        return True
    return ("members" in value) or ("member" in value) or ("subscriber" in value) or ("premium" in value)
