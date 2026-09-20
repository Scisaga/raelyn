from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError
from yt_dlp.networking.impersonate import ImpersonateTarget

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import AppConfig
from raelyn.services.browser_identity import BROWSER_USER_AGENT
from raelyn.services.ffmpeg import MediaPacketValidationError, require_media_packets
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


class YtdlpTransientDownloadError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = str(reason or "").strip() or "youtube_transient_download"
        super().__init__(str(message or "").strip() or "YouTube transient download failure")


YTDLP_RETRY_WITHOUT_COOKIES_PARAM = "_download_without_cookies"

_GENERIC_MP4_FORMAT = (
    "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
    "/best[ext=mp4][height<=1080]"
    "/bestvideo[height<=1080]+bestaudio"
    "/best[height<=1080]"
    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
    "/best[ext=mp4]"
    "/best"
)
_YOUTUBE_HLS_FIRST_FORMAT = (
    "best[ext=mp4][height<=1080]"
    "/best[height<=1080]"
    "/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
    "/bestvideo[height<=1080]+bestaudio"
    "/best"
)
_GENERIC_MP4_720_FORMAT = (
    "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
    "/best[ext=mp4][height<=720]"
    "/bestvideo[height<=720]+bestaudio"
    "/best[height<=720]"
    "/best"
)
_LEGACY_YOUTUBE_DASH_FIRST_FORMATS = {
    _GENERIC_MP4_FORMAT,
    _GENERIC_MP4_720_FORMAT,
    (
        "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
        "/best[ext=mp4][height<=1080]"
        "/bestvideo[height<=1080]+bestaudio"
        "/best[height<=1080]"
        "/best"
    ),
}
_CHINESE_SUBTITLE_LANGS = ["zh-Hant", "zh-Hans", "zh-CN", "zh-TW", "zh-HK", "zh"]
_ENGLISH_SUBTITLE_LANGS = ["en"]
_DEFAULT_SUBTITLE_LANGS = [*_CHINESE_SUBTITLE_LANGS, *_ENGLISH_SUBTITLE_LANGS]
_BILIBILI_CHINESE_SUBTITLE_LANGS = ["ai-zh", *_CHINESE_SUBTITLE_LANGS]
_BILIBILI_ENGLISH_SUBTITLE_LANGS = ["ai-en", *_ENGLISH_SUBTITLE_LANGS]
_BILIBILI_DEFAULT_SUBTITLE_LANGS = [*_BILIBILI_CHINESE_SUBTITLE_LANGS, *_BILIBILI_ENGLISH_SUBTITLE_LANGS]
_YOUTUBE_MEDIA_URL_RETRIES = 1
_YOUTUBE_MEDIA_URL_RESOLVE_ATTEMPTS = 2
_YTDLP_VIDEO_FILE_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv", ".avi", ".ts"}


class _YtdlpCaptureLogger:
    def __init__(self, *, activity_hook: Callable[[], object] | None = None) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self._activity_hook = activity_hook

    def _touch_activity(self) -> None:
        if self._activity_hook:
            self._activity_hook()

    def debug(self, msg: str) -> None:  # noqa: D401
        self._touch_activity()

    def warning(self, msg: str) -> None:
        self._touch_activity()
        m = str(msg or "").strip()
        if m:
            self.warnings.append(m)

    def error(self, msg: str) -> None:
        self._touch_activity()
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


def _youtube_authcheck_diagnostics(msgs: list[str]) -> str:
    # 只保留固定类别和数字错误码；yt-dlp 原始日志可能含代理凭据和签名 URL。
    combined = _normalize_msg("\n".join(msgs))
    details = [f"HTTP {code}" for code in dict.fromkeys(re.findall(r"http(?: error)?[ :]+([45]\d\d)\b", combined))]
    details.extend(f"curl {code}" for code in dict.fromkeys(re.findall(r"curl:\s*\((\d+)\)", combined)))
    for patterns, label in (
        (("timed out", "timeout"), "网络超时"),
        (("connection reset", "connection closed", "connection refused"), "连接中断或被拒绝"),
        (("could not resolve", "name resolution"), "DNS 解析失败"),
        (("certificate verify failed", "ssl certificate problem"), "TLS 证书校验失败"),
        (("unable to download webpage",), "频道网页下载失败"),
        (("unable to extract initial data", "unable to extract yt initial data"), "频道页初始数据缺失"),
    ):
        if any(pattern in combined for pattern in patterns):
            details.append(label)
    return "、".join(details) or "未捕获明确底层原因"


def _raise_if_youtube_bot_check_messages(
    msgs: list[str],
    *,
    provider: str | None = None,
    using_cookies: bool = True,
) -> None:
    p = normalize_cookie_provider(provider)
    if p and p != "youtube":
        return
    combined = "\n".join([str(msg or "") for msg in msgs if str(msg or "").strip()]).strip()
    if not combined:
        return
    if _is_youtube_tab_authcheck_error(combined):
        raise ProviderPauseRequestError(
            provider="youtube",
            reason="youtube_auth_check",
            message=(
                "YouTube同步任务已暂停：YouTube 频道/播放列表鉴权检查失败，但这不一定是 cookies 失效。"
                "若不是私有内容，请优先检查 cookies 导出会话、YTDLP_PROXY 出口、bgutil PO Token Provider 和同步频率。"
                f"底层诊断：{_youtube_authcheck_diagnostics(msgs)}。"
                f"环境信息：pot_provider={_youtube_pot_provider_desc()}，impersonate={_youtube_impersonate_desc()}。"
            ),
        )
    if _is_youtube_bot_check_error(RuntimeError(combined)):
        cookie_part = (
            "YouTube 无 cookies 下载仍触发人机验证。"
            if not using_cookies
            else "YouTube 在使用 cookies 时仍触发人机验证，但这不一定是 YTDLP_COOKIES_YOUTUBE 失效。"
        )
        raise ProviderPauseRequestError(
            provider="youtube",
            reason="youtube_bot_check",
            message=(
                f"YouTube下载任务已暂停：{cookie_part}"
                "更常见原因是出口 IP、请求频率、浏览器导出会话与运行出口不一致，或 PO Token 生成/使用失败。"
                "请检查 YTDLP_PROXY、bgutil PO Token Provider、cookies 导出方式和下载/同步并发。"
                f"环境信息：pot_provider={_youtube_pot_provider_desc()}，impersonate={_youtube_impersonate_desc()}。"
            ),
        )


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


def _bgutil_pot_base_url() -> str:
    return str(settings.ytdlp_pot_bgutil_base_url or "").strip()


def _youtube_pot_provider_desc() -> str:
    base_url = _bgutil_pot_base_url()
    return f"bgutil_http={base_url}" if base_url else "bgutil_http=not_set"


def _youtube_impersonate_target() -> str:
    return str(settings.ytdlp_youtube_impersonate or "").strip()


def _youtube_impersonate_desc() -> str:
    target = _youtube_impersonate_target()
    return target or "not_set"


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


def _normalize_format_selector(text: str) -> str:
    return "".join(str(text or "").split())


def _is_legacy_youtube_dash_first_format(text: str) -> bool:
    normalized = _normalize_format_selector(text)
    return bool(normalized) and normalized in {_normalize_format_selector(item) for item in _LEGACY_YOUTUBE_DASH_FIRST_FORMATS}


def _format_height_cap(format_selector: str) -> int:
    values = [int(item) for item in re.findall(r"height\s*<=\s*(\d+)", str(format_selector or ""))]
    return max(values) if values else 1080


def _build_youtube_hls_first_format(height_cap: int) -> str:
    cap = max(1, int(height_cap or 1080))
    return (
        f"best[ext=mp4][height<={cap}]"
        f"/best[height<={cap}]"
        f"/bestvideo[ext=mp4][height<={cap}]+bestaudio[ext=m4a]"
        f"/bestvideo[height<={cap}]+bestaudio"
        "/best"
    )


def _build_dash_mp4_format(height_cap: int) -> str:
    cap = max(1, int(height_cap or 1080))
    return (
        f"bestvideo[ext=mp4][height<={cap}]+bestaudio[ext=m4a]"
        f"/best[ext=mp4][height<={cap}]"
        f"/bestvideo[height<={cap}]+bestaudio"
        f"/best[height<={cap}]"
        "/best"
    )


def _format_merge_output(format_selector: str) -> str | None:
    lower = str(format_selector or "").lower()
    if "ext=mp4" in lower or "[mp4" in lower or "m4a" in lower:
        return "mp4"
    return None


def _download_format_attempts(*, cookie_provider: str | None, configured_format: str) -> list[tuple[str, str, str | None]]:
    provider = normalize_cookie_provider(cookie_provider)
    configured = str(configured_format or "").strip()
    attempts: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()

    def add(label: str, selector: str, merge: str | None = None) -> None:
        normalized = _normalize_format_selector(selector)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        attempts.append((label, selector, merge))

    if provider == "youtube":
        height_cap = _format_height_cap(configured)
        youtube_hls_first_format = _build_youtube_hls_first_format(height_cap)
        youtube_dash_format = _build_dash_mp4_format(height_cap)
        if not configured or _is_legacy_youtube_dash_first_format(configured):
            add("youtube_hls_mp4", youtube_hls_first_format, "mp4")
        else:
            add("user", configured, _format_merge_output(configured))
            add("youtube_hls_mp4", youtube_hls_first_format, "mp4")
        add("youtube_dash_mp4", youtube_dash_format, "mp4")
    elif configured:
        add("user", configured, _format_merge_output(configured))
        add("prefer_mp4", _GENERIC_MP4_FORMAT, "mp4")
    else:
        add("prefer_mp4", _GENERIC_MP4_FORMAT, "mp4")

    add("fallback_best", "bv*+ba/b", None)
    add("fallback_plain_best", "b", None)
    return attempts


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
    msg = _normalize_msg(str(err or ""))
    return (
        "sign in to confirm you're not a bot" in msg
        or "confirm you're not a bot" in msg
        or "确认你不是聊天机器人" in msg
        or "确认你不是机器人" in msg
    )


def _is_youtube_tab_authcheck_error(msg: str) -> bool:
    m = _normalize_msg(msg)
    if not m:
        return False
    return (
        "youtube:tab" in m
        and "playlists that require authentication" in m
        and "without a successful webpage download" in m
    )


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


def _is_youtube_no_video_formats_messages(msgs: list[str]) -> bool:
    for s in msgs:
        m = _normalize_msg(s)
        if not m:
            continue
        if "no video formats found" in m:
            return True
    return False


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except Exception:
        return "unknown"


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
        f"环境信息：{node_desc}; remote_components={remote_desc}; pot_provider={_youtube_pot_provider_desc()}\n"
        "解决建议：\n"
        "1) 升级 Python 依赖：`.venv/bin/python -m pip install -U yt-dlp[default]`；\n"
        "2) 或直接运行：`./scripts/dev/install-ytdlp-ejs.sh`（会升级 yt-dlp-ejs 并做基础自检）；\n"
        "3) 确认已启用 `YTDLP_REMOTE_COMPONENTS=ejs:github`，并且 `node` 可用；\n"
        "4) 若 YouTube 同步/下载仍失败：配置 YouTube cookies（UI -> 设置 -> YTDLP_COOKIES_YOUTUBE）或代理（YTDLP_PROXY），并重试。"
    )


def _youtube_no_video_formats_hint() -> str:
    return (
        "YouTube 未返回可播放视频格式（yt-dlp 提取到的 formats 为空）。"
        "这通常不是画质 selector 问题；更常见原因是 yt-dlp / EJS 提取器落后、"
        "YTDLP_PROXY 出口不可达或被风控、PO Token Provider 未生效、cookies 导出会话与运行出口不一致，"
        "或视频本身存在地区/年龄/会员/直播状态限制。\n"
        "环境信息："
        f"yt_dlp={_package_version('yt-dlp')}; "
        f"yt_dlp_ejs={_package_version('yt-dlp-ejs')}; "
        f"curl_cffi={_package_version('curl_cffi')}; "
        f"pot_provider={_youtube_pot_provider_desc()}; "
        f"impersonate={_youtube_impersonate_desc()}; "
        f"proxy_configured={bool(str(settings.ytdlp_proxy or '').strip())}\n"
        "处理建议：\n"
        "1) 确认 `YTDLP_PROXY` 在 worker 运行环境中可连接；\n"
        "2) 运行 `./scripts/dev/install-ytdlp-ejs.sh` 或重新安装 `backend/requirements.txt`；\n"
        "3) 用同一环境执行 `.venv/bin/python -m yt_dlp -v <YouTube URL>`，检查 player status、"
        "PO Token Providers 与是否仍返回空 formats；\n"
        "4) 若代理、PO Token 和依赖版本都正常，再按 cookies 导出会话、请求频率和视频访问限制排查。"
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


def _is_http_403_forbidden_error(err: Exception) -> bool:
    msg = _normalize_msg(str(err or ""))
    return "http error 403" in msg and "forbidden" in msg


def _is_youtube_media_transport_error(message: str) -> bool:
    normalized_message = _normalize_msg(message)
    if "unable to download video subtitles" in normalized_message:
        return False
    for line in str(message or "").splitlines():
        normalized_line = _normalize_msg(line)
        if "[download]" not in normalized_line:
            continue
        if any(
            marker in normalized_line
            for marker in (
                "connect tunnel failed",
                "connection closed abruptly",
                "connection reset by peer",
                "connection was reset",
                "http error 502",
                "response 502",
            )
        ):
            return True
    return False


def _is_youtube_original_url_reresolve_error(message: str) -> bool:
    return _is_youtube_media_transport_error(message) or _is_youtube_no_video_formats_messages([message])


def _rewrite_ytdlp_attempt_paths(value: Any, *, attempt_dir: Path, out_dir: Path) -> Any:
    attempt_text = str(attempt_dir)
    attempt_prefix = f"{attempt_text}{os.sep}"
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = _rewrite_ytdlp_attempt_paths(item, attempt_dir=attempt_dir, out_dir=out_dir)
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _rewrite_ytdlp_attempt_paths(item, attempt_dir=attempt_dir, out_dir=out_dir)
        return value
    if isinstance(value, tuple):
        return tuple(
            _rewrite_ytdlp_attempt_paths(item, attempt_dir=attempt_dir, out_dir=out_dir)
            for item in value
        )
    if not isinstance(value, str):
        return value
    if value == attempt_text:
        return str(out_dir)
    if value.startswith(attempt_prefix):
        return str(out_dir / value[len(attempt_prefix) :])
    return value


def _youtube_bot_check_hint() -> str:
    return (
        "YouTube 拒绝访问（需要登录/人机验证）。解决方法：在 UI 的 Settings 页面配置 "
        "YTDLP_COOKIES_YOUTUBE（Netscape cookies.txt 格式，登录 YouTube 后从浏览器导出并粘贴保存），"
        "或通过 API `PUT /api/config/ytdlp_cookies_youtube` 写入。若 YouTube 同步/下载已配置 cookies 仍失败，"
        "通常是 IP 被风控，需要更换网络或配置代理（YTDLP_PROXY），并启用 PO Token Provider。"
        f"环境信息：pot_provider={_youtube_pot_provider_desc()}, impersonate={_youtube_impersonate_desc()}。"
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

    # 禁止 yt-dlp 隐式读取进程环境中的 HTTP(S)_PROXY；YouTube 的 yt-dlp 同步/下载显式使用 YTDLP_PROXY。
    opts["proxy"] = ""

    is_bili = cookie_provider == "bilibili"
    if is_bili:
        # B 站对 UA / referer 较敏感，保留最小必要请求头。
        headers = dict(opts.get("http_headers") or {})
        headers.setdefault("User-Agent", BROWSER_USER_AGENT)
        headers.setdefault("Referer", "https://www.bilibili.com/")
        headers.setdefault("Origin", "https://www.bilibili.com")
        opts["http_headers"] = headers

    if cookie_provider == "youtube" and settings.ytdlp_proxy.strip():
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
        if impersonate_target := _youtube_impersonate_target():
            opts["impersonate"] = ImpersonateTarget.from_str(impersonate_target)

        extractor_args = dict(opts.get("extractor_args") or {})
        bgutil_base_url = _bgutil_pot_base_url()
        if bgutil_base_url:
            bgutil_args = dict(extractor_args.get("youtubepot-bgutilhttp") or {})
            bgutil_args["base_url"] = [bgutil_base_url]
            extractor_args["youtubepot-bgutilhttp"] = bgutil_args

        lang = _normalize_youtube_lang(_load_ytdlp_youtube_lang())
        if lang:
            youtube_args = dict(extractor_args.get("youtube") or {})
            youtube_args["lang"] = [lang]
            extractor_args["youtube"] = youtube_args
        if extractor_args:
            opts["extractor_args"] = extractor_args
    return


def ytdlp_extract_info(
    url: str,
    *,
    provider: str | None = None,
    flat: bool = False,
    max_entries: int | None = None,
    socket_timeout: int | None = None,
    use_provider_cookies: bool = True,
    youtube_metadata_only: bool = False,
    activity_hook: Callable[[], object] | None = None,
) -> dict[str, Any]:
    logger = _YtdlpCaptureLogger(activity_hook=activity_hook)
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
    _apply_common_ytdlp_opts(
        opts,
        url=url,
        provider=cookie_provider,
        use_provider_cookies=bool(use_provider_cookies),
    )
    if cookie_provider == "youtube" and youtube_metadata_only:
        # metadata 补全只需要标题、发布时间等页面字段，不需要解析可下载格式。
        # 跳过 player config / JS challenge，避免把详情补全阻塞在媒体签名解析上。
        extractor_args = dict(opts.get("extractor_args") or {})
        youtube_args = dict(extractor_args.get("youtube") or {})
        youtube_args["player_client"] = ["web_safari"]
        youtube_args["player_skip"] = ["configs", "js"]
        extractor_args["youtube"] = youtube_args
        opts["extractor_args"] = extractor_args
    using_cookies = bool(opts.get("cookiefile"))
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
            msgs = logger.warnings + logger.errors + [str(e)]
            _raise_if_cookie_invalid_messages(msgs, provider=cookie_provider)
            _raise_if_youtube_bot_check_messages(
                msgs,
                provider=cookie_provider,
                using_cookies=using_cookies,
            )
            _raise_if_provider_pause_messages(msgs)
            if _is_youtube_bot_check_error(e):
                raise RuntimeError(_youtube_bot_check_hint()) from e
            if _is_youtube_js_challenge_failed_messages(logger.warnings + logger.errors + [str(e)]):
                raise RuntimeError(_youtube_js_challenge_hint()) from e
            if cookie_provider == "youtube" and _is_youtube_no_video_formats_messages(msgs):
                raise RuntimeError(_youtube_no_video_formats_hint()) from e
            if _is_bilibili_risk_control_error(e) or _is_bilibili_precondition_failed_error(e):
                raise RuntimeError(_bilibili_risk_control_hint()) from e
            # Prefer the last captured yt-dlp error line (more user-friendly than a Python traceback).
            last = logger.errors[-1] if logger.errors else str(e)
            raise RuntimeError(last) from e

        # With ignoreerrors=True, yt-dlp may return None (and report via logger.error). Treat as failure.
        if info is None:
            last = logger.errors[-1] if logger.errors else "yt-dlp extraction returned no result"
            msgs = logger.warnings + logger.errors + [last]
            _raise_if_cookie_invalid_messages(msgs, provider=cookie_provider)
            _raise_if_youtube_bot_check_messages(
                msgs,
                provider=cookie_provider,
                using_cookies=using_cookies,
            )
            _raise_if_provider_pause_messages(msgs)
            if _is_bilibili_risk_control_error(RuntimeError(last)) or _is_bilibili_precondition_failed_error(RuntimeError(last)):
                raise RuntimeError(_bilibili_risk_control_hint())
            if cookie_provider == "youtube" and _is_youtube_no_video_formats_messages(msgs):
                raise RuntimeError(_youtube_no_video_formats_hint())
            raise RuntimeError(last)
        _raise_if_cookie_invalid_messages(logger.warnings + logger.errors, provider=cookie_provider)
        _raise_if_youtube_bot_check_messages(
            logger.warnings + logger.errors,
            provider=cookie_provider,
            using_cookies=using_cookies,
        )
        _raise_if_provider_pause_messages(logger.warnings + logger.errors)
        return info


def ytdlp_fetch_bytes(
    url: str,
    *,
    provider: str,
    max_bytes: int,
    socket_timeout: int = 15,
) -> tuple[bytes, str | None]:
    target_url = str(url or "").strip()
    if not target_url:
        raise ValueError("yt-dlp fetch URL is empty")

    limit = max(1, int(max_bytes))
    opts: dict[str, Any] = {
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": max(1, int(socket_timeout)),
        "http_headers": {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        },
    }
    _apply_common_ytdlp_opts(
        opts,
        url=target_url,
        provider=provider,
        use_provider_cookies=True,
    )

    with YoutubeDL(opts) as ydl:
        with ydl.urlopen(target_url) as response:
            status = getattr(response, "status", None)
            if isinstance(status, int) and not 200 <= status < 300:
                raise RuntimeError(f"yt-dlp fetch http {status}")
            data = response.read(limit + 1)
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower() or None

    if not data:
        raise RuntimeError("yt-dlp fetch returned empty body")
    if len(data) > limit:
        raise RuntimeError("yt-dlp fetch response too large")
    return data, content_type


def _promote_ytdlp_attempt_files(attempt_dir: Path, out_dir: Path) -> None:
    sources = sorted(attempt_dir.iterdir(), key=lambda item: item.name)
    collisions = [source.name for source in sources if (out_dir / source.name).exists()]
    if collisions:
        names = ", ".join(collisions[:10])
        raise RuntimeError(f"yt-dlp attempt output conflicts with existing files: {names}")
    for source in sources:
        shutil.move(str(source), str(out_dir / source.name))


def _validate_ytdlp_attempt_media(attempt_dir: Path, *, attempt_label: str) -> None:
    video_files = [
        path
        for path in attempt_dir.iterdir()
        if path.is_file() and path.suffix.lower() in _YTDLP_VIDEO_FILE_SUFFIXES
    ]
    mp4s = [path for path in video_files if path.suffix.lower() == ".mp4"]
    candidates = mp4s or video_files
    if not candidates:
        raise MediaPacketValidationError(f"yt-dlp 格式尝试 {attempt_label} 未产出视频文件")

    video_file = max(candidates, key=lambda item: item.stat().st_size)
    try:
        require_media_packets(input_path=video_file, stream_types=("video", "audio"))
    except MediaPacketValidationError as exc:
        raise MediaPacketValidationError(
            f"yt-dlp 格式尝试 {attempt_label} 产出无效媒体文件 "
            f"{video_file.name}（{video_file.stat().st_size} bytes）：{exc}"
        ) from exc


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
    activity_hook: Callable[[], object] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cookie_provider = cookie_provider_for_target(url, provider)
    effective_use_provider_cookies = bool(use_provider_cookies)
    cookie_invalid_line: str | None = None
    captured_warnings: list[str] = []
    captured_errors: list[str] = []

    class _YtdlpLogger:
        def _touch_activity(self) -> None:
            if activity_hook:
                activity_hook()

        def debug(self, msg: str) -> None:  # noqa: D401
            self._touch_activity()

        def warning(self, msg: str) -> None:
            nonlocal cookie_invalid_line
            self._touch_activity()
            m = str(msg or "")
            if not m.strip():
                return
            captured_warnings.append(m)
            if _cookie_invalid_reason_from_message(m):
                cookie_invalid_line = cookie_invalid_line or m
            print(f"[ytdlp] warn: {m}", flush=True)

        def error(self, msg: str) -> None:
            nonlocal cookie_invalid_line
            self._touch_activity()
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
        "subtitleslangs": subtitles_langs or _DEFAULT_SUBTITLE_LANGS,
        "writeinfojson": True,
        "writethumbnail": True,
        "noplaylist": True,
        # Network resilience (common failure: "bytes read ... more expected").
        "continuedl": True,
        "retries": _YOUTUBE_MEDIA_URL_RETRIES if cookie_provider == "youtube" else 8,
        "fragment_retries": _YOUTUBE_MEDIA_URL_RETRIES if cookie_provider == "youtube" else 8,
        "socket_timeout": 30,
        # More stable for flaky networks; slower but avoids bursts.
        "concurrent_fragment_downloads": 1,
    }
    subtitles_enabled = _load_ytdlp_subtitles_enabled()
    base_opts["writesubtitles"] = bool(subtitles_enabled and write_subtitles)
    base_opts["writeautomaticsub"] = bool(subtitles_enabled and write_auto_subtitles)
    if progress_hook:
        base_opts["progress_hooks"] = [progress_hook]

    persisted_format = (_load_ytdlp_format_text() or "").strip()
    user_format = persisted_format or (settings.ytdlp_format or "").strip()

    # Retry strategy:
    # - YouTube prefers combined/HLS MP4 first. On 2026-05-18, several Bloomberg videos returned
    #   HTTP 403 for 360p+ DASH video-only GVS URLs while HLS format 96 downloaded successfully.
    # - If a selector is unavailable、命中媒体 URL 403，或产物没有真实音视频包，则尝试下一个 selector。
    format_attempts = _download_format_attempts(cookie_provider=cookie_provider, configured_format=user_format)

    last_error: Exception | None = None
    last_attempt_warnings: list[str] = []
    last_attempt_errors: list[str] = []
    tried_progressive_mp4 = False
    tried_transport_fallback = False

    def _extract_download(
        opts: dict[str, Any],
        *,
        attempt_label: str,
        allow_original_url_reresolve: bool = True,
    ) -> dict[str, Any]:
        nonlocal cookie_invalid_line, last_attempt_warnings, last_attempt_errors
        resolve_attempts = (
            _YOUTUBE_MEDIA_URL_RESOLVE_ATTEMPTS
            if cookie_provider == "youtube" and allow_original_url_reresolve
            else 1
        )
        last_attempt_error: Exception | None = None

        for resolve_attempt in range(1, resolve_attempts + 1):
            cookie_invalid_line = None
            warning_start = len(captured_warnings)
            error_start = len(captured_errors)
            if activity_hook:
                activity_hook()

            with TemporaryDirectory(prefix=".ytdlp-resolve-", dir=out_dir) as attempt_dir_text:
                attempt_dir = Path(attempt_dir_text)
                attempt_opts = dict(opts)
                attempt_opts["outtmpl"] = {"default": str(attempt_dir / "%(id)s.%(ext)s")}
                try:
                    if "ffmpeg_location" in attempt_opts:
                        print(f"[ytdlp] ffmpeg_location={attempt_opts.get('ffmpeg_location')}", flush=True)
                    with YoutubeDL(attempt_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                    last_attempt_warnings = captured_warnings[warning_start:]
                    last_attempt_errors = captured_errors[error_start:]
                    if cookie_invalid_line:
                        _raise_if_cookie_invalid_messages([cookie_invalid_line], provider=cookie_provider)
                        _raise_if_provider_pause_messages([cookie_invalid_line])
                    if cookie_provider == "youtube":
                        _validate_ytdlp_attempt_media(attempt_dir, attempt_label=attempt_label)
                    _promote_ytdlp_attempt_files(attempt_dir, out_dir)
                    _rewrite_ytdlp_attempt_paths(info, attempt_dir=attempt_dir, out_dir=out_dir)
                    return info
                except (DownloadError, ExtractorError, MediaPacketValidationError) as e:
                    last_attempt_error = e
                    last_attempt_warnings = captured_warnings[warning_start:]
                    last_attempt_errors = captured_errors[error_start:]
                    joined = "\n".join(
                        [cookie_invalid_line or "", *last_attempt_warnings[-12:], *last_attempt_errors[-12:], str(e)]
                    ).strip()
                    _raise_if_cookie_invalid_messages([joined], provider=cookie_provider)
                    _raise_if_youtube_bot_check_messages(
                        [joined],
                        provider=cookie_provider,
                        using_cookies=effective_use_provider_cookies,
                    )
                    _raise_if_provider_pause_messages([joined])
                    if (
                        cookie_provider == "youtube"
                        and resolve_attempt < resolve_attempts
                        and _is_youtube_original_url_reresolve_error(joined)
                    ):
                        reason = "formats 为空" if _is_youtube_no_video_formats_messages([joined]) else "媒体传输失败"
                        print(
                            f"[ytdlp] {reason}（{attempt_label}）；"
                            f"re-resolving original video URL ({resolve_attempt + 1}/{resolve_attempts})",
                            flush=True,
                        )
                        continue
                    raise

        if last_attempt_error:
            raise last_attempt_error
        raise RuntimeError("yt-dlp download attempt failed without error")

    for idx, (label, fmt, merge) in enumerate(format_attempts, start=1):
        opts = dict(base_opts)
        _apply_common_ytdlp_opts(
            opts,
            url=url,
            provider=cookie_provider,
            use_provider_cookies=effective_use_provider_cookies,
        )
        opts["format"] = fmt
        if merge:
            opts["merge_output_format"] = merge
        else:
            opts.pop("merge_output_format", None)

        try:
            return _extract_download(opts, attempt_label=label)
        except (DownloadError, ExtractorError, MediaPacketValidationError) as e:
            last_error = e
            joined = "\n".join(
                [cookie_invalid_line or "", *last_attempt_warnings[-12:], *last_attempt_errors[-12:], str(e)]
            ).strip()
            _raise_if_cookie_invalid_messages([joined], provider=cookie_provider)
            _raise_if_youtube_bot_check_messages(
                [joined],
                provider=cookie_provider,
                using_cookies=effective_use_provider_cookies,
            )
            _raise_if_provider_pause_messages([joined])
            if _is_youtube_bot_check_error(e):
                raise RuntimeError(_youtube_bot_check_hint()) from e
            if _is_youtube_js_challenge_failed_messages(last_attempt_warnings + last_attempt_errors + [str(e)]):
                raise RuntimeError(_youtube_js_challenge_hint()) from e
            if cookie_provider == "youtube" and _is_youtube_no_video_formats_messages([joined]):
                raise YtdlpTransientDownloadError(
                    "youtube_no_video_formats",
                    f"{_youtube_no_video_formats_hint()}\n"
                    f"下载阶段已从原视频页重新解析 {_YOUTUBE_MEDIA_URL_RESOLVE_ATTEMPTS} 轮，"
                    "每轮返回的 formats 仍为空。",
                ) from e
            if _is_bilibili_risk_control_error(e) or _is_bilibili_precondition_failed_error(e):
                raise RuntimeError(_bilibili_risk_control_hint()) from e

            delay = parse_upcoming_live_delay_seconds(joined)
            if isinstance(delay, int) and delay > 0:
                # Add a small buffer so we don't retry too early around the start time.
                raise JobReschedule(delay_seconds=delay + 120, reason="upcoming livestream") from e

            transport_failure = cookie_provider == "youtube" and _is_youtube_media_transport_error(joined)
            retryable_format_failure = (
                isinstance(e, MediaPacketValidationError)
                or _is_requested_format_unavailable(e)
                or (cookie_provider == "youtube" and _is_http_403_forbidden_error(e))
                or (transport_failure and not tried_transport_fallback)
            )
            if retryable_format_failure and idx < len(format_attempts):
                next_label, next_fmt, _next_merge = format_attempts[idx]
                if isinstance(e, MediaPacketValidationError):
                    reason = str(e)
                elif _is_http_403_forbidden_error(e):
                    reason = "HTTP 403"
                elif transport_failure:
                    tried_transport_fallback = True
                    reason = (
                        f"媒体连接在从原视频页重新解析 {_YOUTUBE_MEDIA_URL_RESOLVE_ATTEMPTS} 轮后仍失败"
                    )
                else:
                    reason = "requested format not available"
                print(
                    f"[ytdlp] {reason} for {label}: {fmt!r}; retrying with {next_label}: {next_fmt!r}",
                    flush=True,
                )
                if not transport_failure:
                    continue
                try:
                    fallback_opts = dict(base_opts)
                    _apply_common_ytdlp_opts(
                        fallback_opts,
                        url=url,
                        provider=cookie_provider,
                        use_provider_cookies=effective_use_provider_cookies,
                    )
                    fallback_opts["format"] = next_fmt
                    if _next_merge:
                        fallback_opts["merge_output_format"] = _next_merge
                    else:
                        fallback_opts.pop("merge_output_format", None)
                    return _extract_download(
                        fallback_opts,
                        attempt_label=next_label,
                        allow_original_url_reresolve=False,
                    )
                except (DownloadError, ExtractorError, MediaPacketValidationError) as fallback_error:
                    last_error = fallback_error
                    joined = "\n".join(
                        [
                            cookie_invalid_line or "",
                            *last_attempt_warnings[-12:],
                            *last_attempt_errors[-12:],
                            str(fallback_error),
                        ]
                    ).strip()
                    _raise_if_cookie_invalid_messages([joined], provider=cookie_provider)
                    _raise_if_youtube_bot_check_messages(
                        [joined],
                        provider=cookie_provider,
                        using_cookies=effective_use_provider_cookies,
                    )
                    _raise_if_provider_pause_messages([joined])
                    e = fallback_error
                    if _is_youtube_no_video_formats_messages([joined]):
                        raise YtdlpTransientDownloadError(
                            "youtube_no_video_formats",
                            f"{_youtube_no_video_formats_hint()}\n"
                            "fallback selector 从原视频页解析 1 轮后，formats 仍为空。",
                        ) from fallback_error
            if (not tried_progressive_mp4) and is_ffmpeg_segfault(joined):
                tried_progressive_mp4 = True
                prog_opts = dict(base_opts)
                _apply_common_ytdlp_opts(
                    prog_opts,
                    url=url,
                    provider=cookie_provider,
                    use_provider_cookies=effective_use_provider_cookies,
                )
                prog_opts["format"] = "best[ext=mp4][height<=720]/best[ext=mp4]/b"
                prog_opts.pop("merge_output_format", None)
                print("[ytdlp] ffmpeg crash detected; retrying with progressive mp4 (<=720p) to avoid merge", flush=True)
                try:
                    return _extract_download(prog_opts, attempt_label="progressive_mp4_720")
                except (DownloadError, ExtractorError, MediaPacketValidationError) as e2:
                    last_error = e2
                    joined2 = "\n".join(
                        [cookie_invalid_line or "", *last_attempt_warnings[-12:], *last_attempt_errors[-12:], str(e2)]
                    ).strip()
                    _raise_if_cookie_invalid_messages([joined2], provider=cookie_provider)
                    _raise_if_youtube_bot_check_messages(
                        [joined2],
                        provider=cookie_provider,
                        using_cookies=effective_use_provider_cookies,
                    )
                    _raise_if_provider_pause_messages([joined2])
                    if cookie_provider == "youtube" and _is_youtube_no_video_formats_messages([joined2]):
                        raise RuntimeError(_youtube_no_video_formats_hint()) from e2
                    # Fall through to raise a readable error below.
                    joined = joined2 or joined
                    e = e2
            last = last_attempt_errors[-1] if last_attempt_errors else str(e)
            important_warns: list[str] = []
            for w in last_attempt_warnings[-24:]:
                lw = str(w or "").lower()
                if "ffmpeg does not support socks proxies" in lw:
                    important_warns.append(str(w))
            tail_err = "\n".join(last_attempt_errors[-8:]).strip()
            lines = [*important_warns[-3:], *(tail_err.split("\n") if tail_err else []), str(e)]
            out: list[str] = []
            for ln in [str(x or "").rstrip() for x in lines]:
                if not ln.strip():
                    continue
                if ln in out:
                    continue
                out.append(ln)
            detail = "\n".join(out).strip() or (tail_err or last)
            if cookie_provider == "youtube" and (
                tried_transport_fallback or _is_youtube_media_transport_error(joined)
            ):
                if tried_transport_fallback:
                    recovery_summary = (
                        "YouTube 媒体下载已尝试原 selector 和一个 fallback selector，"
                        f"原 selector 从视频页解析 {_YOUTUBE_MEDIA_URL_RESOLVE_ATTEMPTS} 轮、"
                        "fallback selector 解析 1 轮后仍连接失败。"
                    )
                else:
                    recovery_summary = (
                        f"YouTube 媒体下载已从原视频页重新解析 {_YOUTUBE_MEDIA_URL_RESOLVE_ATTEMPTS} 轮，"
                        "每轮连接仍失败。"
                    )
                detail = f"{recovery_summary}\n{detail}"
                raise YtdlpTransientDownloadError("youtube_media_transport", detail) from e
            raise RuntimeError(detail) from e

    if last_error:
        raise last_error
    raise RuntimeError("yt-dlp download failed without error")


def ytdlp_download_subtitles(
    *,
    url: str,
    out_dir: Path,
    provider: str | None = None,
    use_provider_cookies: bool = True,
    subtitles_langs: list[str] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    cookie_provider = cookie_provider_for_target(url, provider)
    logger = _YtdlpCaptureLogger()
    opts: dict[str, Any] = {
        "ignoreconfig": True,
        "outtmpl": {"default": outtmpl},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": logger,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": subtitles_langs or _DEFAULT_SUBTITLE_LANGS,
        "subtitlesformat": "vtt/srt/best",
        "writeinfojson": True,
        "noplaylist": True,
        "continuedl": True,
        "retries": 8,
        "fragment_retries": 8,
        "socket_timeout": 30,
    }
    if js := _js_runtimes():
        opts["js_runtimes"] = js
    _apply_common_ytdlp_opts(
        opts,
        url=url,
        provider=cookie_provider,
        use_provider_cookies=use_provider_cookies,
    )
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except (DownloadError, ExtractorError) as e:
        msgs = logger.warnings + logger.errors + [str(e)]
        _raise_if_cookie_invalid_messages(msgs, provider=cookie_provider)
        _raise_if_youtube_bot_check_messages(
            msgs,
            provider=cookie_provider,
            using_cookies=bool(use_provider_cookies),
        )
        _raise_if_provider_pause_messages(msgs)
        if _is_youtube_bot_check_error(e):
            raise RuntimeError(_youtube_bot_check_hint()) from e
        if _is_youtube_js_challenge_failed_messages(msgs):
            raise RuntimeError(_youtube_js_challenge_hint()) from e
        if cookie_provider == "youtube" and _is_youtube_no_video_formats_messages(msgs):
            raise RuntimeError(_youtube_no_video_formats_hint()) from e
        if _is_bilibili_risk_control_error(e) or _is_bilibili_precondition_failed_error(e):
            raise RuntimeError(_bilibili_risk_control_hint()) from e
        last = logger.errors[-1] if logger.errors else str(e)
        raise RuntimeError(last) from e

    if info is None:
        last = logger.errors[-1] if logger.errors else "yt-dlp subtitle extraction returned no result"
        msgs = logger.warnings + logger.errors + [last]
        _raise_if_cookie_invalid_messages(msgs, provider=cookie_provider)
        _raise_if_youtube_bot_check_messages(msgs, provider=cookie_provider)
        _raise_if_provider_pause_messages(msgs)
        if cookie_provider == "youtube" and _is_youtube_no_video_formats_messages(msgs):
            raise RuntimeError(_youtube_no_video_formats_hint())
        raise RuntimeError(last)

    _raise_if_cookie_invalid_messages(logger.warnings + logger.errors, provider=cookie_provider)
    _raise_if_youtube_bot_check_messages(logger.warnings + logger.errors, provider=cookie_provider)
    _raise_if_provider_pause_messages(logger.warnings + logger.errors)
    return info


def ytdlp_available_subtitle_languages(info: dict[str, Any] | None) -> dict[str, list[str]]:
    data = info if isinstance(info, dict) else {}

    def keys(value: Any) -> list[str]:
        if not isinstance(value, dict):
            return []
        return sorted(str(k) for k in value if str(k or "").strip())

    return {
        "manual": keys(data.get("subtitles")),
        "automatic": keys(data.get("automatic_captions")),
    }


def load_info_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
