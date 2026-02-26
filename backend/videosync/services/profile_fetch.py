from __future__ import annotations

import html as html_lib
import re
from typing import Any
from urllib.parse import urljoin

import httpx

from videosync.config import settings
from videosync.services.http_client import httpx_client


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r"([a-zA-Z_:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title\b[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)


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
    with httpx_client(proxy=settings.ytdlp_proxy, timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=headers)
        if r.status_code < 200 or r.status_code >= 300:
            return None
        text = r.text or ""

    meta = _parse_meta_tags(text)
    title = _parse_title(text)
    if not meta and not title:
        return None

    return {"meta": meta, "title": title}


def fetch_media_profile(*, provider: str, url: str) -> dict[str, Any] | None:
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

    if avatar_url:
        avatar_url = avatar_url.strip()
        avatar_url = urljoin(url, avatar_url)

    data = {
        "name": name or None,
        "description": (description or "").strip() or None,
        "avatar_url": avatar_url or None,
        "source": "open_graph",
    }
    if not any([data["name"], data["description"], data["avatar_url"]]):
        return None
    return data
