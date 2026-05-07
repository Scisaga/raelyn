from __future__ import annotations

import html as html_lib
import re
from typing import Any
from urllib.parse import urljoin, urlparse
import time

import httpx

from raelyn.services.http_client import httpx_client
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    timeout = httpx.Timeout(timeout_seconds)
    cookie_header = None
    if "bilibili.com" in (url or "").lower():
        cookie_header = _load_cookie_header_for_url(url)
        if cookie_header:
            headers["Cookie"] = cookie_header
    with httpx_client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=headers)
        if "bilibili.com" in (url or "").lower() and r.status_code in {412, 429}:
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


def _extract_bilibili_mid(url: str) -> str | None:
    try:
        u = (url or "").strip()
        if not u:
            return None
        p = urlparse(u)
        host = (p.netloc or "").lower()
        if "space.bilibili.com" not in host:
            return None
        m = _BILIBILI_SPACE_MID_RE.match(p.path or "")
        if not m:
            return None
        mid = m.group("mid")
        return mid if mid.isdigit() else None
    except ProviderPauseRequestError:
        raise
    except Exception:
        return None


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


def _fetch_bilibili_space_profile(*, mid: str) -> dict[str, Any] | None:
    url = f"https://api.bilibili.com/x/space/acc/info?mid={mid}&jsonp=jsonp"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://space.bilibili.com/{mid}/",
        "Accept": "application/json,text/plain,*/*",
    }
    cookie_header = _load_cookie_header_for_url(url)
    if cookie_header:
        headers["Cookie"] = cookie_header
    timeout = httpx.Timeout(10.0)
    try:
        with httpx_client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            if r.status_code in {412, 429}:
                raise ProviderPauseRequestError(
                    provider="bilibili",
                    reason=BILIBILI_PROVIDER_PAUSE_REASON,
                    message=bilibili_provider_pause_message(),
                )
            if r.status_code < 200 or r.status_code >= 300:
                return None
            payload = r.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if code in {-799, -412, 412}:
        raise ProviderPauseRequestError(
            provider="bilibili",
            reason=BILIBILI_PROVIDER_PAUSE_REASON,
            message=bilibili_provider_pause_message(),
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    name = data.get("name")
    avatar_url = data.get("face")
    description = data.get("sign")
    if isinstance(name, str):
        name = name.strip() or None
    else:
        name = None
    if isinstance(avatar_url, str):
        avatar_url = avatar_url.strip() or None
    else:
        avatar_url = None
    if isinstance(description, str):
        description = description.strip() or None
    else:
        description = None

    if not any([name, avatar_url, description]):
        return None
    return {
        "name": name,
        "description": description,
        "avatar_url": avatar_url,
        "source": "bilibili_api",
    }


def fetch_media_profile(*, provider: str, url: str) -> dict[str, Any] | None:
    if provider == "bilibili":
        mid = _extract_bilibili_mid(url)
        if mid:
            api_profile = _fetch_bilibili_space_profile(mid=mid)
            if api_profile:
                return api_profile

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
