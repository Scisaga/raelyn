from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import quote, unquote, urlparse


def detect_provider(url: str) -> str | None:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "bilibili.com" in u:
        return "bilibili"
    return None


@dataclass(frozen=True)
class MediaIdentity:
    provider: str
    provider_media_id: str


# Note: Some handles can appear URL-encoded (e.g. /@%E4%B9%9D...). We unquote the path before matching,
# and accept unicode word characters plus common handle symbols.
_YOUTUBE_HANDLE_RE = re.compile(r"^/@(?P<handle>[\w._-]{1,100})(?:/|$)")
_YOUTUBE_CHANNEL_RE = re.compile(r"^/channel/(?P<cid>UC[A-Za-z0-9_-]{10,})(?:/|$)")
_YOUTUBE_USER_RE = re.compile(r"^/user/(?P<user>[A-Za-z0-9._-]{1,100})(?:/|$)")
_YOUTUBE_C_RE = re.compile(r"^/c/(?P<cname>[A-Za-z0-9._-]{1,100})(?:/|$)")
_YOUTUBE_VANITY_RE = re.compile(r"^/(?P<name>[\w._-]{1,100})(?:/|$)")

_YOUTUBE_RESERVED_TOPLEVEL = {
    "watch",
    "playlist",
    "results",
    "feed",
    "shorts",
    "channel",
    "c",
    "user",
    "live",
    "videos",
    "trending",
    "music",
    "gaming",
    "news",
    "sports",
    "movies",
    "kids",
    "premium",
    "account",
    "signin",
    "logout",
}

_BILIBILI_SPACE_RE = re.compile(r"^/(?P<mid>\d{1,20})(?:/|$)")


def _normalize_url(url: str) -> str:
    # Ensure urlparse can see the netloc.
    u = url.strip()
    if not u:
        return u
    if "://" not in u:
        return "https://" + u
    return u


def _extract_media_id_from_url(*, provider: str, url: str) -> str | None:
    u = _normalize_url(url)
    p = urlparse(u)
    host = (p.netloc or "").lower()
    raw_path = p.path or ""
    # Decode percent-encoded path segments (e.g. /@%E4%B9%9D...).
    try:
        path = unquote(raw_path)
    except Exception:
        path = raw_path

    if provider == "youtube":
        # Typical channel URLs:
        # - https://www.youtube.com/@handle
        # - https://www.youtube.com/channel/UCxxxx
        # - https://www.youtube.com/user/username
        # - https://www.youtube.com/c/customname
        # - https://www.youtube.com/customname   (legacy vanity URL, usually redirects to @handle)
        if "youtube.com" not in host and "youtu.be" not in host:
            return None
        m = _YOUTUBE_HANDLE_RE.match(path)
        if m:
            handle = (m.group("handle") or "").strip()
            if not handle or any(ch.isspace() for ch in handle):
                return None
            return f"@{handle}"
        m = _YOUTUBE_CHANNEL_RE.match(path)
        if m:
            return m.group("cid")
        m = _YOUTUBE_USER_RE.match(path)
        if m:
            return f"user:{m.group('user')}"
        m = _YOUTUBE_C_RE.match(path)
        if m:
            return f"c:{m.group('cname')}"
        m = _YOUTUBE_VANITY_RE.match(path)
        if m:
            name = (m.group("name") or "").strip()
            if not name:
                return None
            # Avoid accidentally accepting non-channel URLs like /watch, /playlist, etc.
            if name.lower() in _YOUTUBE_RESERVED_TOPLEVEL:
                return None
            return f"path:{name}"
        return None

    if provider == "bilibili":
        # Typical UP URLs:
        # - https://space.bilibili.com/123456
        if "space.bilibili.com" in host:
            m = _BILIBILI_SPACE_RE.match(path)
            if m:
                return m.group("mid")
        return None

    return None


def extract_media_identity(*, provider: str, url: str) -> MediaIdentity:
    provider_media_id = _extract_media_id_from_url(provider=provider, url=url)
    if provider_media_id:
        return MediaIdentity(provider=provider, provider_media_id=provider_media_id)

    raise ValueError(
        "仅支持添加频道/UP 主主页 URL（例如 YouTube /@handle、/channel/UC...、/user/...、/c/...、/name 或 bilibili space 链接）"
    )


def build_media_videos_url(*, provider: str, provider_media_id: str) -> str | None:
    """
    Build a canonical "videos list" URL for a given provider media id.

    Why: Some providers' home pages are not a true "uploads feed" for yt-dlp.
    For example, YouTube channel home pages may only expose "Videos/Shorts" tabs
    in extract-flat mode. Using the explicit videos tab makes sync stable.
    """
    pid = (provider_media_id or "").strip()
    if not pid:
        return None

    if provider == "youtube":
        # provider_media_id formats:
        # - "@handle"
        # - "UCxxxx" (channel id)
        # - "user:username"
        # - "c:customname"
        # - "path:customname" (legacy vanity URL)
        if pid.startswith("@"):
            handle = pid.removeprefix("@")
            safe_handle = quote(handle, safe="._-")
            return f"https://www.youtube.com/@{safe_handle}/videos"
        if pid.startswith("UC"):
            return f"https://www.youtube.com/channel/{pid}/videos"
        if pid.startswith("user:"):
            return f"https://www.youtube.com/user/{pid.removeprefix('user:')}/videos"
        if pid.startswith("c:"):
            return f"https://www.youtube.com/c/{pid.removeprefix('c:')}/videos"
        if pid.startswith("path:"):
            name = pid.removeprefix("path:")
            safe_name = quote(name, safe="._-")
            return f"https://www.youtube.com/{safe_name}/videos"
        return None

    if provider == "bilibili":
        # UP 主视频页
        if pid.isdigit():
            return f"https://space.bilibili.com/{pid}/video"
        return None

    return None
