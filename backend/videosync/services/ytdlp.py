from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from collections.abc import Callable

from yt_dlp import YoutubeDL

from videosync.config import settings


def ytdlp_extract_info(url: str, *, flat: bool = False, max_entries: int | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist" if flat else False,
        # Prevent hanging on slow/huge pages (channel playlists can be very large).
        "socket_timeout": 15,
    }
    if flat:
        lim = int(settings.sync_max_entries) if max_entries is None else int(max_entries)
        if lim > 0:
            # Limit playlist traversal to what we actually need.
            opts["playliststart"] = 1
            opts["playlistend"] = max(1, lim)
    if settings.ytdlp_proxy.strip():
        opts["proxy"] = settings.ytdlp_proxy.strip()
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def ytdlp_download(
    *,
    url: str,
    out_dir: Path,
    write_subtitles: bool = True,
    write_auto_subtitles: bool = True,
    subtitles_langs: list[str] | None = None,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    opts: dict[str, Any] = {
        "outtmpl": {"default": outtmpl},
        "quiet": True,
        "no_warnings": True,
        # Prefer a playable mp4 when possible.
        # - YouTube usually has H.264 (mp4) + AAC (m4a) variants; this avoids vp9/webm outputs.
        # - If mp4 isn't available, yt-dlp will fall back to the best format.
        "format": (settings.ytdlp_format or "").strip()
        or "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "writesubtitles": write_subtitles,
        "writeautomaticsub": write_auto_subtitles,
        "subtitleslangs": subtitles_langs or ["zh.*", "en.*", "zh", "en"],
        "writeinfojson": True,
        "writethumbnail": True,
        "noplaylist": True,
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    if settings.ytdlp_proxy.strip():
        opts["proxy"] = settings.ytdlp_proxy.strip()
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return info


def load_info_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
