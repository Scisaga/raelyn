from __future__ import annotations

import html as html_lib
import re
from typing import Any
from urllib.parse import urljoin, urlparse
import time

import httpx

from raelyn.services.browser_identity import BROWSER_USER_AGENT
from raelyn.services.http_client import browser_http_client, httpx_client
from raelyn.services.provider_cookies import load_provider_cookie_text
from raelyn.services.provider_pause import (
    BILIBILI_PROVIDER_PAUSE_REASON,
    ProviderPauseRequestError,
    bilibili_provider_pause_message,
)


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r"([a-zA-Z_:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title\b[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
_BILIBILI_SPACE_MID_RE = re.compile(r"^/(?P<mid>\d{1,20})(?:/|$)")


def _parse_meta_tags(html: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html):
        attrs: dict[str, str] = {}
        for m in _ATTR_RE.finditer(tag):
            key = (m.group(1) or "").strip().lower()
            val = (m.group(2) or m.group(3) or m.group(4) or "").strip()
            if key:
                attrs[key] = val
        if not attrs:
            continue
        name = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        content = (attrs.get("content") or "").strip()
        if not name or not content:
            continue
        if name not in meta:
            meta[name] = html_lib.unescape(content)
    return meta


def _parse_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    if not m:
        return None
    title = html_lib.unescape(m.group("title") or "").strip()
    return title or None


def fetch_open_graph(url: str, *, timeout_seconds: float = 10.0) -> dict[str, Any] | None:
    is_bilibili = "bilibili.com" in (url or "").lower()
    headers = {} if is_bilibili else {"User-Agent": BROWSER_USER_AGENT}
    cookie_header = None
    if is_bilibili:
        cookie_header = _load_cookie_header_for_url(url)
        if cookie_header:
            headers["Cookie"] = cookie_header
        with browser_http_client(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
    else:
        timeout = httpx.Timeout(timeout_seconds)
        with httpx_client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
    if is_bilibili and r.status_code in {412, 429}:
        raise ProviderPauseRequestError(
            provider="bilibili",
            reason=BILIBILI_PROVIDER_PAUSE_REASON,
            message=bilibili_provider_pause_message(),
        )
    if r.status_code < 200 or r.status_code >= 300:
        return None
    text = r.text or ""

    meta = _parse_meta_tags(text)
    title = _parse_title(text)
    if not meta and not title:
        return None

    return {"meta": meta, "title": title}


def _parse_netscape_cookies_for_host(text: str, host: str) -> dict[str, str]:
    out: dict[str, str] = {}
    h = (host or "").strip().lower()
    if not h:
        return out
    now = int(time.time())
    for raw in (text or "").splitlines():
        line = (raw or "").strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, _path, _secure, expiry, name, value = parts[:7]
        domain = (domain or "").strip().lstrip(".").lower()
        if not domain or not (h == domain or h.endswith("." + domain)):
            continue
        try:
            exp = int(expiry)
        except Exception:
            exp = 0
        if exp and exp < now:
            continue
        if name:
            out[str(name)] = str(value or "")
    return out


def _load_cookie_header_for_url(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").split(":")[0].strip().lower()
    except Exception:
        host = ""
    if not host:
        return ""
    text = load_provider_cookie_text("bilibili")
    if not (text or "").strip():
        return ""
    cookies = _parse_netscape_cookies_for_host(text, host)
    if not cookies:
        return ""
    return "; ".join([f"{k}={v}" for k, v in cookies.items()])


def _looks_like_bilibili_face_url(url: str | None) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        path = p.path or ""
    except Exception:
        return False
    return ("hdslb.com" in host) and ("/bfs/face/" in path)


def _looks_like_bilibili_site_icon(url: str | None) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    if "favicon" in u:
        return True
    if u.endswith(".ico"):
        return True
    # Some bilibili pages expose og:image as a generic site/logo asset.
    if "logo" in u and ("hdslb" in u or "bilibili" in u):
        return True
    if "touch-icon" in u or "apple-touch-icon" in u or "android-chrome" in u:
        return True
    return False


def _extract_bilibili_mid(url: str) -> str | None:
    parsed = urlparse(str(url or "").strip())
    if "space.bilibili.com" not in (parsed.netloc or "").lower():
        return None
    match = _BILIBILI_SPACE_MID_RE.match(parsed.path or "")
    return match.group("mid") if match else None


def _parse_bilibili_card_profile(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    card = data.get("card")
    if not isinstance(card, dict):
        return None

    name = str(card.get("name") or "").strip() or None
    description = str(card.get("sign") or card.get("description") or "").strip() or None
    avatar_url = str(card.get("face") or "").strip() or None
    if avatar_url and not _looks_like_bilibili_face_url(avatar_url):
        avatar_url = None

    follower_count = data.get("follower")
    if not isinstance(follower_count, int) or isinstance(follower_count, bool) or follower_count < 0:
        follower_count = None
    video_count = data.get("archive_count")
    if not isinstance(video_count, int) or isinstance(video_count, bool) or video_count < 0:
        video_count = None

    profile = {
        "name": name,
        "description": description,
        "avatar_url": avatar_url,
        "subscriber_count": follower_count,
        "video_count": video_count,
        "source": "bilibili_card",
    }
    if not any(
        profile[key] is not None
        for key in ("name", "description", "avatar_url", "subscriber_count", "video_count")
    ):
        return None
    return profile


def _fetch_bilibili_card_profile(*, url: str, timeout_seconds: float = 10.0) -> dict[str, Any] | None:
    mid = _extract_bilibili_mid(url)
    if not mid:
        return None

    endpoint = f"https://api.bilibili.com/x/web-interface/card?mid={mid}"
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Referer": url,
        "Accept": "application/json,text/plain,*/*",
    }
    cookie_header = _load_cookie_header_for_url(endpoint)
    if cookie_header:
        headers["Cookie"] = cookie_header

    with browser_http_client(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
        response = client.get(endpoint)
    if response.status_code in {412, 429}:
        raise ProviderPauseRequestError(
            provider="bilibili",
            reason=BILIBILI_PROVIDER_PAUSE_REASON,
            message=bilibili_provider_pause_message(),
        )
    if response.status_code < 200 or response.status_code >= 300:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("code") in {-799, -412, 412}:
        raise ProviderPauseRequestError(
            provider="bilibili",
            reason=BILIBILI_PROVIDER_PAUSE_REASON,
            message=bilibili_provider_pause_message(),
        )
    return _parse_bilibili_card_profile(payload)


def fetch_media_profile(*, provider: str, url: str) -> dict[str, Any] | None:
    if provider == "bilibili":
        card_profile = _fetch_bilibili_card_profile(url=url)
        if card_profile:
            return card_profile

    og = fetch_open_graph(url)
    if not og:
        return None

    meta: dict[str, str] = og.get("meta") or {}
    title: str | None = og.get("title")

    name = meta.get("og:title") or meta.get("twitter:title") or title
    description = meta.get("og:description") or meta.get("description") or meta.get("twitter:description")
    avatar_url = meta.get("og:image") or meta.get("og:image:secure_url") or meta.get("twitter:image")

    if name:
        name = name.strip()
        if provider == "youtube":
            name = re.sub(r"\s+-\s+YouTube\s*$", "", name, flags=re.IGNORECASE).strip()
        elif provider == "bilibili":
            name = re.sub(r"\s*-\s*bilibili\s*$", "", name, flags=re.IGNORECASE).strip()
            # Typical titles:
            # - "<name>的个人空间-<name>个人主页-哔哩哔哩视频"
            name = re.sub(r"\s*的个人空间.*$", "", name).strip()
            name = re.sub(r"\s*个人空间.*$", "", name).strip()

    if avatar_url:
        avatar_url = avatar_url.strip()
        avatar_url = urljoin(url, avatar_url)
        if provider == "bilibili":
            # For bilibili, og:image often points to a generic site icon/logo; only accept real face URLs.
            if _looks_like_bilibili_site_icon(avatar_url):
                avatar_url = None
            elif not _looks_like_bilibili_face_url(avatar_url):
                avatar_url = None

    data = {
        "name": name or None,
        "description": (description or "").strip() or None,
        "avatar_url": avatar_url or None,
        "source": "open_graph",
    }
    if not any([data["name"], data["description"], data["avatar_url"]]):
        return None
    return data
