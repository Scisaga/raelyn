from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from collections.abc import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import AppConfig
from raelyn.services.provider_cookies import (
    cookie_config_name,
    cookie_provider_for_target,
    cookie_provider_label,
    load_provider_cookie_text,
    looks_like_netscape_cookie_file,
    normalize_cookie_provider,
)
from raelyn.services.provider_pause import (
    BILIBILI_PROVIDER_PAUSE_REASON,
    ProviderPauseRequestError,
    bilibili_provider_pause_message,
)
from raelyn.services.ytdlp_errors import is_ffmpeg_segfault, parse_upcoming_live_delay_seconds


class YtdlpCookiesInvalidError(RuntimeError):
    def __init__(self, reason: str, message: str, *, provider: str | None = None) -> None:
        self.provider = normalize_cookie_provider(provider)
        self.reason = str(reason or "").strip() or "ytdlp_cookies_invalid"
        super().__init__(str(message or "").strip() or "yt-dlp cookies invalid")


YTDLP_RETRY_WITHOUT_COOKIES_PARAM = "_download_without_cookies"


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


def _normalize_msg(msg: str) -> str:
    m = str(msg or "").strip().lower()
    # Normalize curly quotes to avoid missing patterns.
    return m.replace("’", "'").replace("“", '"').replace("”", '"')


def _cookie_invalid_reason_from_message(msg: str) -> str | None:
    m = _normalize_msg(msg)
    if not m:
        return None
    if "does not look like a netscape format cookies file" in m:
        return "ytdlp_cookies_format_invalid"
    if "provided youtube account cookies are no longer valid" in m:
        return "ytdlp_cookies_expired"
    if "cookies are no longer valid" in m:
        return "ytdlp_cookies_expired"
    return None


def _raise_if_cookie_invalid_messages(msgs: list[str], *, provider: str | None = None) -> None:
    p = normalize_cookie_provider(provider)
    cfg_name = cookie_config_name(p)
    label = cookie_provider_label(p)
    for s in msgs:
        reason = _cookie_invalid_reason_from_message(s)
        if not reason:
            continue
        if reason == "ytdlp_cookies_format_invalid":
            raise YtdlpCookiesInvalidError(
                reason,
                f"{cfg_name} 无效：不是 Netscape cookies.txt 格式。请在 UI -> 设置 更新 {label} cookies.txt（tab 分隔）。",
                provider=p,
            )
        if reason == "ytdlp_cookies_expired":
            raise YtdlpCookiesInvalidError(
                reason,
                f"{cfg_name} 已失效：{label} 登录态 cookies 过期。请在 UI -> 设置 更新 {label} cookies.txt。",
                provider=p,
            )
        raise YtdlpCookiesInvalidError(reason, f"{cfg_name} 无效，请在 UI -> 设置 更新 {label} cookies.txt。", provider=p)


def _raise_if_provider_pause_messages(msgs: list[str]) -> None:
    combined = "\n".join([str(msg or "") for msg in msgs if str(msg or "").strip()]).strip()
    if not combined:
        return
    err = RuntimeError(combined)
    if _is_bilibili_risk_control_error(err) or _is_bilibili_precondition_failed_error(err):
        raise ProviderPauseRequestError(
            provider="bilibili",
            reason=BILIBILI_PROVIDER_PAUSE_REASON,
            message=bilibili_provider_pause_message(),
        )


def _js_runtimes() -> dict[str, dict[str, str | None]] | None:
    # Prefer project-local node (bin/node) so behavior is consistent across hosts/containers.
    root = Path(__file__).resolve().parents[3]
    local_node = root / "bin" / "node"
    node = str(local_node) if local_node.exists() else shutil.which("node")
    if node:
        return {"node": {"path": node}}
    return None


def _remote_components() -> list[str] | None:
    raw = str(settings.ytdlp_remote_components or "").strip()
    if not raw:
        return None
    items: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[\s,]+", raw):
        item = str(part or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items or None


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


def _load_ytdlp_youtube_lang() -> str:
    # Persisted via /api/config (AppConfig key: "ytdlp_youtube_lang").
    # Default: "zh-CN" (yt-dlp's YouTube extractors accept a limited, case-sensitive set of language codes).
    try:
        with session_scope() as session:
            item = session.get(AppConfig, "ytdlp_youtube_lang")
            value = item.value if item else None
    except Exception:
        return "zh-CN"

    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return "zh-CN"
    if value.get("enabled") is False:
        return ""
    lang = value.get("lang")
    return str(lang).strip() if isinstance(lang, str) else "zh-CN"


def _normalize_youtube_lang(lang: str) -> str:
    s = str(lang or "").strip()
    if not s:
        return ""
    lower = s.lower().replace("_", "-")

    # yt-dlp's YouTube extractors validate language codes (case-sensitive).
    # Map common Chinese variants to the closest supported codes.
    if lower in {"zh", "zh-hans", "zh-cn", "zh-sg"}:
        return "zh-CN"
    if lower in {"zh-hant", "zh-tw"}:
        return "zh-TW"
    if lower in {"zh-hk", "zh-mo"}:
        return "zh-HK"

    # Canonicalize to a typical BCP-47 casing so "en-gb" works as "en-GB", etc.
    parts = [p for p in lower.split("-") if p]
    if not parts:
        return ""
    out: list[str] = [parts[0]]
    for p in parts[1:]:
        if len(p) == 2 and p.isalpha():
            out.append(p.upper())
        elif len(p) == 4 and p.isalpha():
            out.append(p.title())
        else:
            out.append(p)
    return "-".join(out)


def _ensure_ytdlp_cookies_file(text: str, *, provider: str | None = None) -> Path | None:
    if not (text or "").strip():
        return None
    # Fail fast for obviously invalid formats to avoid confusing yt-dlp errors.
    p = normalize_cookie_provider(provider)
    if not looks_like_netscape_cookie_file(text):
        raise YtdlpCookiesInvalidError(
            "ytdlp_cookies_format_invalid",
            (
                f"{cookie_config_name(p)} 无效：不是 Netscape cookies.txt 格式。"
                f"请在 UI -> 设置 更新 {cookie_provider_label(p)} cookies.txt（tab 分隔）。"
            ),
            provider=p,
        )

    root = Path(__file__).resolve().parents[3]
    out_dir = root / "tmp"
    out_dir.mkdir(parents=True, exist_ok=True)

    suffix = p or "shared"
    target = out_dir / f"ytdlp_cookies_{suffix}.txt"
    tmp = out_dir / f".ytdlp_cookies_{suffix}.txt.tmp"
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


def _is_youtube_js_challenge_failed_messages(msgs: list[str]) -> bool:
    for s in msgs:
        m = _normalize_msg(s)
        if not m:
            continue
        if "n challenge solving failed" in m:
            return True
        if "only images are available for download" in m:
            return True
        if "challenge solver script distribution" in m:
            return True
        if ("js challenge provider" in m) and ("invalid response" in m or "no solutions" in m):
            return True
        if "github.com/yt-dlp/yt-dlp/wiki/ejs" in m or "/wiki/ejs" in m:
            return True
    return False


def _youtube_js_challenge_hint() -> str:
    # This error frequently happens when:
    # - yt-dlp is outdated relative to YouTube's latest "n" JS challenge
    # - yt-dlp-ejs is missing/outdated
    # - node runtime isn't available
    node_path = None
    try:
        js = _js_runtimes() or {}
        node_path = (js.get("node") or {}).get("path")  # type: ignore[assignment]
    except Exception:
        node_path = None

    node_desc = f"node={node_path}" if isinstance(node_path, str) and node_path else "node=not_found"
    remote_components = _remote_components() or []
    remote_desc = ",".join(remote_components) if remote_components else "not_set"
    return (
        "YouTube JS challenge 解析失败（EJS / n challenge），导致视频/音频格式不可用（可能只剩缩略图等图片格式）。\n"
        f"环境信息：{node_desc}; remote_components={remote_desc}\n"
        "解决建议：\n"
        "1) 升级 Python 依赖：`.venv/bin/python -m pip install -U yt-dlp[default]`；\n"
        "2) 或直接运行：`./scripts/dev/install-ytdlp-ejs.sh`（会升级 yt-dlp-ejs 并做基础自检）；\n"
        "3) 确认已启用 `YTDLP_REMOTE_COMPONENTS=ejs:github`，并且 `node` 可用；\n"
        "4) 若仍失败：配置 YouTube cookies（UI -> 设置 -> YTDLP_COOKIES_YOUTUBE）或代理（YTDLP_PROXY），并重试。"
    )


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
        "YTDLP_COOKIES_YOUTUBE（Netscape cookies.txt 格式，登录 YouTube 后从浏览器导出并粘贴保存），"
        "或通过 API `PUT /api/config/ytdlp_cookies_youtube` 写入。若已配置仍失败，通常是 IP 被风控，"
        "需要更换网络或配置代理（YTDLP_PROXY）。"
    )


def _bilibili_risk_control_hint() -> str:
    return bilibili_provider_pause_message()


def _apply_common_ytdlp_opts(
    opts: dict[str, Any],
    *,
    url: str | None = None,
    provider: str | None = None,
    use_provider_cookies: bool = True,
) -> None:
    # Force project-local ffmpeg when available so post-processing is consistent across hosts.
    try:
        p = Path(settings.ffmpeg_bin)
        if p.exists():
            opts["ffmpeg_location"] = str(p.resolve())
    except Exception:
        pass
    if remote_components := _remote_components():
        opts["remote_components"] = remote_components

    cookie_provider = cookie_provider_for_target(url, provider)
    if use_provider_cookies:
        persisted = load_provider_cookie_text(cookie_provider)
        if (persisted or "").strip():
            cookie_path = _ensure_ytdlp_cookies_file(persisted, provider=cookie_provider)
            if cookie_path and cookie_path.exists() and cookie_path.is_file():
                opts["cookiefile"] = str(cookie_path)

    # Some providers are sensitive to UA / referer; set conservative defaults.
    # Keep it minimal to avoid interfering with providers that don't require these headers.
    u = (url or "").strip()
    lower_u = u.lower()
    # url can be a real URL (https://www.bilibili.com/...) or an id-like string (BV... / av...).
    is_bili = cookie_provider == "bilibili"
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
        # 显式禁用代理；仅移除 opts["proxy"] 还不够，yt-dlp 会继续读取进程环境里的 HTTP(S)_PROXY。
        opts["proxy"] = ""
    else:
        if settings.ytdlp_proxy.strip():
            opts["proxy"] = settings.ytdlp_proxy.strip()
            # ffmpeg cannot use SOCKS proxies for some download flows (notably HLS),
            # and yt-dlp will warn and may fail. Prefer native HLS handling in this case.
            try:
                p = str(opts.get("proxy") or "").strip().lower()
                if p.startswith("socks"):
                    opts["hls_prefer_native"] = True
            except Exception:
                pass

    is_yt = cookie_provider == "youtube"
    if is_yt:
        lang = _normalize_youtube_lang(_load_ytdlp_youtube_lang())
        if lang:
            extractor_args = dict(opts.get("extractor_args") or {})
            youtube_args = dict(extractor_args.get("youtube") or {})
            youtube_args["lang"] = [lang]
            extractor_args["youtube"] = youtube_args
            opts["extractor_args"] = extractor_args
    return


def ytdlp_extract_info(
    url: str,
    *,
    provider: str | None = None,
    flat: bool = False,
    max_entries: int | None = None,
    socket_timeout: int | None = None,
) -> dict[str, Any]:
    logger = _YtdlpCaptureLogger()
    cookie_provider = cookie_provider_for_target(url, provider)
    sock = None
    if socket_timeout is not None:
        try:
            sock = int(socket_timeout)
        except Exception:
            sock = None
    if isinstance(sock, int) and sock <= 0:
        sock = None
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
        "socket_timeout": sock or 15,
    }
    if not flat:
        # Even with skip_download, yt-dlp may still do format selection; keep it permissive.
        # For flat playlist extraction, avoid format selection entirely to prevent "requested format not available"
        # on entries like upcoming livestreams.
        opts["format"] = "b"
    js = _js_runtimes()
    if js:
        opts["js_runtimes"] = js
    _apply_common_ytdlp_opts(opts, url=url, provider=cookie_provider)
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
            _raise_if_cookie_invalid_messages(logger.warnings + logger.errors + [str(e)], provider=cookie_provider)
            _raise_if_provider_pause_messages(logger.warnings + logger.errors + [str(e)])
            if _is_youtube_bot_check_error(e):
                raise RuntimeError(_youtube_bot_check_hint()) from e
            if _is_youtube_js_challenge_failed_messages(logger.warnings + logger.errors + [str(e)]):
                raise RuntimeError(_youtube_js_challenge_hint()) from e
            if _is_bilibili_risk_control_error(e) or _is_bilibili_precondition_failed_error(e):
                raise RuntimeError(_bilibili_risk_control_hint()) from e
            # Prefer the last captured yt-dlp error line (more user-friendly than a Python traceback).
            last = logger.errors[-1] if logger.errors else str(e)
            raise RuntimeError(last) from e

        # With ignoreerrors=True, yt-dlp may return None (and report via logger.error). Treat as failure.
        if info is None:
            last = logger.errors[-1] if logger.errors else "yt-dlp extraction returned no result"
            _raise_if_cookie_invalid_messages(logger.warnings + logger.errors + [last], provider=cookie_provider)
            _raise_if_provider_pause_messages(logger.warnings + logger.errors + [last])
            if _is_bilibili_risk_control_error(RuntimeError(last)) or _is_bilibili_precondition_failed_error(RuntimeError(last)):
                raise RuntimeError(_bilibili_risk_control_hint())
            raise RuntimeError(last)
        _raise_if_cookie_invalid_messages(logger.warnings + logger.errors, provider=cookie_provider)
        _raise_if_provider_pause_messages(logger.warnings + logger.errors)
        return info


def ytdlp_download(
    *,
    url: str,
    out_dir: Path,
    provider: str | None = None,
    use_provider_cookies: bool = True,
    write_subtitles: bool = True,
    write_auto_subtitles: bool = True,
    subtitles_langs: list[str] | None = None,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    cookie_provider = cookie_provider_for_target(url, provider)
    cookie_invalid_line: str | None = None
    captured_warnings: list[str] = []
    captured_errors: list[str] = []

    class _YtdlpLogger:
        def debug(self, msg: str) -> None:  # noqa: D401
            return

        def warning(self, msg: str) -> None:
            nonlocal cookie_invalid_line
            m = str(msg or "")
            if not m.strip():
                return
            captured_warnings.append(m)
            if _cookie_invalid_reason_from_message(m):
                cookie_invalid_line = cookie_invalid_line or m
            print(f"[ytdlp] warn: {m}", flush=True)

        def error(self, msg: str) -> None:
            nonlocal cookie_invalid_line
            m = str(msg or "")
            if not m.strip():
                return
            lower = m.lower()
            # We'll print our own retry hints for this case.
            if "requested format is not available" in lower or "requested format not available" in lower:
                return
            captured_errors.append(m)
            if _cookie_invalid_reason_from_message(m):
                cookie_invalid_line = cookie_invalid_line or m
            print(f"[ytdlp] error: {m}", flush=True)

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
        # Network resilience (common failure: "bytes read ... more expected").
        "continuedl": True,
        "retries": 8,
        "fragment_retries": 8,
        "socket_timeout": 30,
        # More stable for flaky networks; slower but avoids bursts.
        "concurrent_fragment_downloads": 1,
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
    tried_progressive_mp4 = False
    for idx, (label, fmt, merge) in enumerate(format_attempts, start=1):
        opts = dict(base_opts)
        _apply_common_ytdlp_opts(opts, url=url, provider=cookie_provider, use_provider_cookies=use_provider_cookies)
        opts["format"] = fmt
        if merge:
            opts["merge_output_format"] = merge
        else:
            opts.pop("merge_output_format", None)

        try:
            if "ffmpeg_location" in opts:
                print(f"[ytdlp] ffmpeg_location={opts.get('ffmpeg_location')}", flush=True)
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if cookie_invalid_line:
                    _raise_if_cookie_invalid_messages([cookie_invalid_line], provider=cookie_provider)
                    _raise_if_provider_pause_messages([cookie_invalid_line])
                return info
        except (DownloadError, ExtractorError) as e:
            last_error = e
            joined = "\n".join([cookie_invalid_line or "", *captured_warnings[-12:], *captured_errors[-12:], str(e)]).strip()
            _raise_if_cookie_invalid_messages([joined], provider=cookie_provider)
            _raise_if_provider_pause_messages([joined])
            if _is_youtube_bot_check_error(e):
                raise RuntimeError(_youtube_bot_check_hint()) from e
            if _is_youtube_js_challenge_failed_messages(captured_warnings + captured_errors + [str(e)]):
                raise RuntimeError(_youtube_js_challenge_hint()) from e
            if _is_bilibili_risk_control_error(e) or _is_bilibili_precondition_failed_error(e):
                raise RuntimeError(_bilibili_risk_control_hint()) from e

            delay = parse_upcoming_live_delay_seconds(joined)
            if isinstance(delay, int) and delay > 0:
                # Add a small buffer so we don't retry too early around the start time.
                raise JobReschedule(delay_seconds=delay + 120, reason="upcoming livestream") from e

            if _is_requested_format_unavailable(e) and idx < len(format_attempts):
                next_label, next_fmt, _next_merge = format_attempts[idx]
                print(
                    f"[ytdlp] requested format not available for {label}: {fmt!r}; retrying with {next_label}: {next_fmt!r}",
                    flush=True,
                )
                continue
            if (not tried_progressive_mp4) and is_ffmpeg_segfault(joined):
                tried_progressive_mp4 = True
                prog_opts = dict(base_opts)
                _apply_common_ytdlp_opts(
                    prog_opts,
                    url=url,
                    provider=cookie_provider,
                    use_provider_cookies=use_provider_cookies,
                )
                prog_opts["format"] = "best[ext=mp4][height<=720]/best[ext=mp4]/b"
                prog_opts.pop("merge_output_format", None)
                print("[ytdlp] ffmpeg crash detected; retrying with progressive mp4 (<=720p) to avoid merge", flush=True)
                try:
                    with YoutubeDL(prog_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if cookie_invalid_line:
                            _raise_if_cookie_invalid_messages([cookie_invalid_line], provider=cookie_provider)
                            _raise_if_provider_pause_messages([cookie_invalid_line])
                        return info
                except (DownloadError, ExtractorError) as e2:
                    last_error = e2
                    joined2 = "\n".join(
                        [cookie_invalid_line or "", *captured_warnings[-12:], *captured_errors[-12:], str(e2)]
                    ).strip()
                    _raise_if_cookie_invalid_messages([joined2], provider=cookie_provider)
                    _raise_if_provider_pause_messages([joined2])
                    # Fall through to raise a readable error below.
                    joined = joined2 or joined
                    e = e2
            last = captured_errors[-1] if captured_errors else str(e)
            important_warns: list[str] = []
            for w in captured_warnings[-24:]:
                lw = str(w or "").lower()
                if "ffmpeg does not support socks proxies" in lw:
                    important_warns.append(str(w))
            tail_err = "\n".join(captured_errors[-8:]).strip()
            lines = [*important_warns[-3:], *(tail_err.split("\n") if tail_err else []), str(e)]
            out: list[str] = []
            for ln in [str(x or "").rstrip() for x in lines]:
                if not ln.strip():
                    continue
                if ln in out:
                    continue
                out.append(ln)
            detail = "\n".join(out).strip() or (tail_err or last)
            raise RuntimeError(detail) from e

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
