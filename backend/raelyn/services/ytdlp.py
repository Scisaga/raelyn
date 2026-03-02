from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from collections.abc import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import AppConfig


class _YtdlpCaptureLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:  # noqa: D401
        return

    def warning(self, msg: str) -> None:
        m = str(msg or "").strip()
        if m:
            self.warnings.append(m)

    def error(self, msg: str) -> None:
        m = str(msg or "").strip()
        if m:
            self.errors.append(m)


def _js_runtimes() -> dict[str, dict[str, str | None]] | None:
    # Prefer project-local node (bin/node) so behavior is consistent across hosts/containers.
    root = Path(__file__).resolve().parents[3]
    local_node = root / "bin" / "node"
    node = str(local_node) if local_node.exists() else shutil.which("node")
    if node:
        return {"node": {"path": node}}
    return None


def _load_ytdlp_cookies_text() -> str:
    # Persisted via /api/config (AppConfig key: "ytdlp_cookies").
    try:
        with session_scope() as session:
            item = session.get(AppConfig, "ytdlp_cookies")
            value = item.value if item else None
    except Exception:
        return ""

    if not isinstance(value, dict):
        return ""
    text = value.get("text")
    if not isinstance(text, str):
        return ""
    return text


def _load_ytdlp_format_text() -> str:
    # Persisted via /api/config (AppConfig key: "ytdlp_format").
    try:
        with session_scope() as session:
            item = session.get(AppConfig, "ytdlp_format")
            value = item.value if item else None
    except Exception:
        return ""

    if not isinstance(value, dict):
        return ""
    text = value.get("text")
    if not isinstance(text, str):
        return ""
    return text


def _load_ytdlp_subtitles_enabled() -> bool:
    # Persisted via /api/config (AppConfig key: "ytdlp_subtitles").
    # Default: disabled (to reduce risk-control / bot checks).
    try:
        with session_scope() as session:
            item = session.get(AppConfig, "ytdlp_subtitles")
            value = item.value if item else None
    except Exception:
        return False

    if not isinstance(value, dict):
        return False
    enabled = value.get("enabled")
    return bool(enabled) if isinstance(enabled, bool) else False


def _ensure_ytdlp_cookies_file(text: str) -> Path | None:
    if not (text or "").strip():
        return None

    root = Path(__file__).resolve().parents[3]
    out_dir = root / "tmp"
    out_dir.mkdir(parents=True, exist_ok=True)

    target = out_dir / "ytdlp_cookies.txt"
    tmp = out_dir / ".ytdlp_cookies.txt.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    try:
        os.chmod(target, 0o600)
    except Exception:
        # Best-effort; ignore on platforms/filesystems that don't support chmod.
        pass
    return target


def _is_youtube_bot_check_error(err: Exception) -> bool:
    msg = str(err or "").lower()
    msg = msg.replace("’", "'")
    return "sign in to confirm you're not a bot" in msg or "confirm you're not a bot" in msg


def _is_bilibili_risk_control_error(err: Exception) -> bool:
    # Typical yt-dlp error:
    #   ERROR: [BilibiliSpaceVideo] 123456: Request is rejected by server (352)
    msg = str(err or "").lower()
    return ("bilibili" in msg) and ("(352)" in msg or " 352" in msg) and ("rejected by server" in msg or "request is rejected" in msg)


def _is_bilibili_precondition_failed_error(err: Exception) -> bool:
    # Typical yt-dlp error:
    #   ERROR: [BiliBili] BVxxxx: Unable to download webpage: HTTP Error 412: Precondition Failed
    msg = str(err or "").lower()
    return ("bilibili" in msg) and ("http error 412" in msg or "precondition failed" in msg)


def _is_requested_format_unavailable(err: Exception) -> bool:
    msg = str(err or "").lower()
    return "requested format is not available" in msg or "requested format not available" in msg


def _youtube_bot_check_hint() -> str:
    return (
        "YouTube 拒绝访问（需要登录/人机验证）。解决方法：在 UI 的 Settings 页面配置 "
        "YTDLP_COOKIES（Netscape cookies.txt 格式，登录 YouTube 后从浏览器导出并粘贴保存），"
        "或通过 API `PUT /api/config/ytdlp_cookies` 写入。若已配置仍失败，通常是 IP 被风控，"
        "需要更换网络或配置代理（YTDLP_PROXY）。"
    )


def _bilibili_risk_control_hint() -> str:
    return (
        "B 站请求被风控拒绝（常见：352 / HTTP 412）。常见原因：未登录/登录态 cookies 缺失或过期（SESSDATA 等）、"
        "IP/代理出口被风控、或请求频率触发限制。\n"
        "解决建议：\n"
        "1) 在 UI -> 设置 配置 `YTDLP_COOKIES（cookies.txt）`（登录 bilibili 后导出 Netscape cookies.txt 粘贴保存）；\n"
        "2) 必要时设置 `YTDLP_PROXY`（例如住宅/国内出口）并降低并发/放慢同步频率；\n"
        "3) 若仍失败，尝试更换网络后重试。"
    )


def _apply_common_ytdlp_opts(opts: dict[str, Any], *, url: str | None = None) -> None:
    persisted = _load_ytdlp_cookies_text()
    if (persisted or "").strip():
        p = _ensure_ytdlp_cookies_file(persisted)
        if p and p.exists() and p.is_file():
            opts["cookiefile"] = str(p)

    # Some providers are sensitive to UA / referer; set conservative defaults.
    # Keep it minimal to avoid interfering with providers that don't require these headers.
    u = (url or "").strip()
    lower_u = u.lower()
    # url can be a real URL (https://www.bilibili.com/...) or an id-like string (BV... / av...).
    is_bili = ("bilibili.com" in lower_u) or lower_u.startswith("bv") or lower_u.startswith("av")
    if is_bili:
        headers = dict(opts.get("http_headers") or {})
        headers.setdefault(
            "User-Agent",
            (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        headers.setdefault("Referer", "https://www.bilibili.com/")
        opts["http_headers"] = headers
        # Explicitly bypass YTDLP_PROXY for bilibili requests.
        # Reason: some users want bilibili to go direct while other providers use a proxy.
        opts.pop("proxy", None)
    else:
        if settings.ytdlp_proxy.strip():
            opts["proxy"] = settings.ytdlp_proxy.strip()
    return


def ytdlp_extract_info(url: str, *, flat: bool = False, max_entries: int | None = None) -> dict[str, Any]:
    logger = _YtdlpCaptureLogger()
    opts: dict[str, Any] = {
        # Do not let a user's global yt-dlp config break the app (e.g. an overly strict -f selector).
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "logger": logger,
        "skip_download": True,
        # Channel/playlist pages can contain upcoming streams / unavailable videos; don't fail the whole sync.
        "ignoreerrors": True,
        "ignore_no_formats_error": True,
        "extract_flat": "in_playlist" if flat else False,
        # Prevent hanging on slow/huge pages (channel playlists can be very large).
        "socket_timeout": 15,
    }
    if not flat:
        # Even with skip_download, yt-dlp may still do format selection; keep it permissive.
        # For flat playlist extraction, avoid format selection entirely to prevent "requested format not available"
        # on entries like upcoming livestreams.
        opts["format"] = "b"
    js = _js_runtimes()
    if js:
        opts["js_runtimes"] = js
    _apply_common_ytdlp_opts(opts, url=url)
    lim = None
    if max_entries is not None:
        try:
            lim = int(max_entries)
        except Exception:
            lim = None
    elif flat:
        # Back-compat default: only apply an implicit limit for flat mode.
        # Non-flat mode should be explicit to avoid surprising truncation.
        lim = int(settings.sync_max_entries)

    if isinstance(lim, int) and lim > 0:
        # Limit playlist traversal to what we actually need.
        # This works for both flat and non-flat extraction.
        opts["playliststart"] = 1
        opts["playlistend"] = max(1, lim)
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except (DownloadError, ExtractorError) as e:
            if _is_youtube_bot_check_error(e):
                raise RuntimeError(_youtube_bot_check_hint()) from e
            if _is_bilibili_risk_control_error(e) or _is_bilibili_precondition_failed_error(e):
                raise RuntimeError(_bilibili_risk_control_hint()) from e
            # Prefer the last captured yt-dlp error line (more user-friendly than a Python traceback).
            last = logger.errors[-1] if logger.errors else str(e)
            raise RuntimeError(last) from e

        # With ignoreerrors=True, yt-dlp may return None (and report via logger.error). Treat as failure.
        if info is None:
            last = logger.errors[-1] if logger.errors else "yt-dlp extraction returned no result"
            if _is_bilibili_risk_control_error(RuntimeError(last)) or _is_bilibili_precondition_failed_error(RuntimeError(last)):
                raise RuntimeError(_bilibili_risk_control_hint())
            raise RuntimeError(last)
        return info


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

    class _YtdlpLogger:
        def debug(self, msg: str) -> None:  # noqa: D401
            return

        def warning(self, msg: str) -> None:
            m = str(msg or "")
            if not m.strip():
                return
            print(f"[ytdlp] warn: {m}")

        def error(self, msg: str) -> None:
            m = str(msg or "")
            if not m.strip():
                return
            lower = m.lower()
            # We'll print our own retry hints for this case.
            if "requested format is not available" in lower or "requested format not available" in lower:
                return
            print(f"[ytdlp] error: {m}")

    base_opts: dict[str, Any] = {
        # Do not let a user's global yt-dlp config break the app (e.g. an overly strict -f selector).
        "ignoreconfig": True,
        "outtmpl": {"default": outtmpl},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YtdlpLogger(),
        **({"js_runtimes": js} if (js := _js_runtimes()) else {}),
        # Prefer a playable mp4 when possible.
        # - YouTube usually has H.264 (mp4) + AAC (m4a) variants; this avoids vp9/webm outputs.
        # - If mp4 isn't available, yt-dlp will fall back to the best format.
        "writesubtitles": False,
        "writeautomaticsub": False,
        "subtitleslangs": subtitles_langs or ["zh.*", "en.*", "zh", "en"],
        "writeinfojson": True,
        "writethumbnail": True,
        "noplaylist": True,
    }
    subtitles_enabled = _load_ytdlp_subtitles_enabled()
    base_opts["writesubtitles"] = bool(subtitles_enabled and write_subtitles)
    base_opts["writeautomaticsub"] = bool(subtitles_enabled and write_auto_subtitles)
    if progress_hook:
        base_opts["progress_hooks"] = [progress_hook]

    default_prefer_mp4 = (
        "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
        "/best[ext=mp4][height<=1080]"
        "/bestvideo[height<=1080]+bestaudio"
        "/best[height<=1080]"
        "/best"
    )
    persisted_format = (_load_ytdlp_format_text() or "").strip()
    user_format = persisted_format or (settings.ytdlp_format or "").strip()

    # Retry strategy:
    # - First, use user selector (if provided) else prefer-mp4 selector.
    # - If yt-dlp says the requested format is not available, retry with a more permissive selector.
    #   This avoids failing entire downloads due to overly strict format constraints.
    format_attempts: list[tuple[str, str, str | None]] = []
    if user_format:
        user_force_mp4 = ("ext=mp4" in user_format.lower()) or ("[mp4" in user_format.lower()) or ("m4a" in user_format.lower())
        format_attempts.append(("user", user_format, "mp4" if user_force_mp4 else None))
        if user_format != default_prefer_mp4:
            format_attempts.append(("prefer_mp4", default_prefer_mp4, "mp4"))
    else:
        format_attempts.append(("prefer_mp4", default_prefer_mp4, "mp4"))
    format_attempts.append(("fallback_best", "bv*+ba/b", None))
    format_attempts.append(("fallback_plain_best", "b", None))

    last_error: Exception | None = None
    for idx, (label, fmt, merge) in enumerate(format_attempts, start=1):
        opts = dict(base_opts)
        _apply_common_ytdlp_opts(opts, url=url)
        opts["format"] = fmt
        if merge:
            opts["merge_output_format"] = merge
        else:
            opts.pop("merge_output_format", None)

        try:
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=True)
        except (DownloadError, ExtractorError) as e:
            last_error = e
            if _is_youtube_bot_check_error(e):
                raise RuntimeError(_youtube_bot_check_hint()) from e
            if _is_bilibili_risk_control_error(e) or _is_bilibili_precondition_failed_error(e):
                raise RuntimeError(_bilibili_risk_control_hint()) from e
            if _is_requested_format_unavailable(e) and idx < len(format_attempts):
                next_label, next_fmt, _next_merge = format_attempts[idx]
                print(
                    f"[ytdlp] requested format not available for {label}: {fmt!r}; "
                    f"retrying with {next_label}: {next_fmt!r}"
                )
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError("yt-dlp download failed without error")


def load_info_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
