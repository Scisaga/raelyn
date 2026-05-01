from __future__ import annotations

import re


def _normalize_msg(msg: str) -> str:
    m = str(msg or "").strip().lower()
    return m.replace("’", "'").replace("“", '"').replace("”", '"')


def is_ffmpeg_segfault(msg: str) -> bool:
    m = _normalize_msg(msg)
    if not m:
        return False
    return (
        "ffmpeg exited with code -11" in m
        or "sigsegv" in m
        or "segmentation fault" in m
        or "signal 11" in m
    )


def is_provider_media_unavailable_error(msg: str, *, provider: str | None = None) -> bool:
    p = str(provider or "").strip().lower()
    m = _normalize_msg(msg)
    if not m:
        return False
    if p and p != "youtube":
        return False
    return (
        "youtube:tab" in m
        and "http error 404" in m
        and (
            "requested entity was not found" in m
            or "unable to download api page" in m
            or "unable to download webpage" in m
        )
    )


def parse_upcoming_live_delay_seconds(msg: str) -> int | None:
    """
    Parse "upcoming livestream/premiere" hints from yt-dlp error strings.

    Examples seen in the wild:
      - "33分钟后直播!"
      - "28 分钟後直播"
      - "Premieres in 28 minutes"
      - "This live event will begin in 0:33:00"
    """
    s = str(msg or "").strip()
    if not s:
        return None

    # Chinese (minutes/hours)
    # YouTube 会同时出现“直播”“首播”等倒计时文案。
    cn_event = r"(直播|首播|首映)"
    m = re.search(rf"(\d+)\s*(分钟|分鐘)\s*(后|後)\s*{cn_event}", s)
    if m:
        try:
            return int(m.group(1)) * 60
        except Exception:
            return None
    m = re.search(rf"(\d+)\s*(小时|小時)\s*(后|後)\s*{cn_event}", s)
    if m:
        try:
            return int(m.group(1)) * 3600
        except Exception:
            return None

    # English (minutes/hours)
    lower = s.lower()
    m = re.search(r"(?:premieres|premiere|begin|begins|start|starts)\s+in\s+(\d+)\s*minutes?", lower)
    if m:
        try:
            return int(m.group(1)) * 60
        except Exception:
            return None
    m = re.search(r"(?:premieres|premiere|begin|begins|start|starts)\s+in\s+(\d+)\s*hours?", lower)
    if m:
        try:
            return int(m.group(1)) * 3600
        except Exception:
            return None

    # Time token (HH:MM:SS) – grab the last one in the string.
    times = re.findall(r"(\d{1,2}):(\d{2}):(\d{2})", s)
    if times:
        hh, mm, ss = times[-1]
        try:
            return int(hh) * 3600 + int(mm) * 60 + int(ss)
        except Exception:
            return None
    return None
