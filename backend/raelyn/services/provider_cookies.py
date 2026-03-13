from __future__ import annotations

from typing import Any

from raelyn.db import session_scope
from raelyn.models import AppConfig


COOKIE_CONFIG_KEYS = {
    "youtube": "ytdlp_cookies_youtube",
    "bilibili": "ytdlp_cookies_bilibili",
}

COOKIE_CONFIG_NAMES = {
    "youtube": "YTDLP_COOKIES_YOUTUBE",
    "bilibili": "YTDLP_COOKIES_BILIBILI",
}

COOKIE_PROVIDER_LABELS = {
    "youtube": "YouTube",
    "bilibili": "B站",
}


def normalize_cookie_provider(provider: str | None) -> str:
    p = str(provider or "").strip().lower()
    return p if p in COOKIE_CONFIG_KEYS else ""


def cookie_config_key(provider: str | None) -> str | None:
    p = normalize_cookie_provider(provider)
    return COOKIE_CONFIG_KEYS.get(p) if p else None


def cookie_config_name(provider: str | None) -> str:
    p = normalize_cookie_provider(provider)
    return COOKIE_CONFIG_NAMES.get(p, "YTDLP_COOKIES")


def cookie_provider_label(provider: str | None) -> str:
    p = normalize_cookie_provider(provider)
    return COOKIE_PROVIDER_LABELS.get(p, "平台")


def load_provider_cookie_text(provider: str | None) -> str:
    key = cookie_config_key(provider)
    if not key:
        return ""
    try:
        with session_scope() as session:
            item = session.get(AppConfig, key)
            value: Any = item.value if item else None
    except Exception:
        return ""

    if not isinstance(value, dict):
        return ""
    text = value.get("text")
    return text if isinstance(text, str) else ""


def looks_like_netscape_cookie_file(text: str) -> bool:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("\t") >= 6:
            return True
    return False


def cookie_provider_for_target(target: str | None, provider: str | None = None) -> str:
    p = normalize_cookie_provider(provider)
    if p:
        return p

    u = str(target or "").strip().lower()
    if not u:
        return ""
    if "bilibili.com" in u or u.startswith("bv") or u.startswith("av"):
        return "bilibili"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return ""
