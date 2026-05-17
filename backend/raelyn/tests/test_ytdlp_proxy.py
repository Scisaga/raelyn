from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_dlp import YoutubeDL

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.services.ytdlp import (
    _GENERIC_MP4_FORMAT,
    _GENERIC_MP4_720_FORMAT,
    _YOUTUBE_HLS_FIRST_FORMAT,
    _apply_common_ytdlp_opts,
    _build_dash_mp4_format,
    _download_format_attempts,
    _is_http_403_forbidden_error,
)


class YtdlpProxyTests(unittest.TestCase):
    def test_bilibili_explicitly_disables_proxy(self) -> None:
        original = settings.ytdlp_proxy
        try:
            settings.ytdlp_proxy = "http://127.0.0.1:7890"
            opts: dict[str, object] = {}
            _apply_common_ytdlp_opts(
                opts,
                url="https://www.bilibili.com/video/BV1xx411c7mD",
                provider="bilibili",
            )
            self.assertEqual(opts.get("proxy"), "")
            self.assertEqual(
                opts.get("http_headers"),
                {
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.bilibili.com/",
                    "Origin": "https://www.bilibili.com",
                },
            )
            self.assertEqual(YoutubeDL({"proxy": opts["proxy"]}).proxies, {"all": "__noproxy__"})
        finally:
            settings.ytdlp_proxy = original

    def test_youtube_ytdlp_keeps_configured_proxy(self) -> None:
        original = settings.ytdlp_proxy
        try:
            settings.ytdlp_proxy = "http://127.0.0.1:7890"
            opts: dict[str, object] = {}
            _apply_common_ytdlp_opts(
                opts,
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                provider="youtube",
            )
            self.assertEqual(opts.get("proxy"), "http://127.0.0.1:7890")
        finally:
            settings.ytdlp_proxy = original

    def test_youtube_injects_configured_impersonate_target(self) -> None:
        original = settings.ytdlp_youtube_impersonate
        try:
            settings.ytdlp_youtube_impersonate = " chrome "
            opts: dict[str, object] = {}
            _apply_common_ytdlp_opts(
                opts,
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                provider="youtube",
            )
            self.assertEqual(str(opts.get("impersonate")), "chrome")
        finally:
            settings.ytdlp_youtube_impersonate = original

    def test_bilibili_does_not_inject_impersonate_target(self) -> None:
        original = settings.ytdlp_youtube_impersonate
        try:
            settings.ytdlp_youtube_impersonate = "chrome"
            opts: dict[str, object] = {}
            _apply_common_ytdlp_opts(
                opts,
                url="https://www.bilibili.com/video/BV1xx411c7mD",
                provider="bilibili",
            )
            self.assertNotIn("impersonate", opts)
        finally:
            settings.ytdlp_youtube_impersonate = original

    def test_youtube_without_bgutil_pot_config_does_not_inject_provider(self) -> None:
        original = settings.ytdlp_pot_bgutil_base_url
        try:
            settings.ytdlp_pot_bgutil_base_url = ""
            opts: dict[str, object] = {}
            with patch("raelyn.services.ytdlp._load_ytdlp_youtube_lang", return_value=""):
                _apply_common_ytdlp_opts(
                    opts,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    provider="youtube",
                )
            extractor_args = opts.get("extractor_args") or {}
            self.assertNotIn("youtubepot-bgutilhttp", extractor_args)
        finally:
            settings.ytdlp_pot_bgutil_base_url = original

    def test_youtube_injects_bgutil_pot_provider_and_keeps_lang(self) -> None:
        original = settings.ytdlp_pot_bgutil_base_url
        try:
            settings.ytdlp_pot_bgutil_base_url = " http://127.0.0.1:4416 "
            opts: dict[str, object] = {}
            with patch("raelyn.services.ytdlp._load_ytdlp_youtube_lang", return_value="en-US"):
                _apply_common_ytdlp_opts(
                    opts,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    provider="youtube",
                )
            self.assertEqual(
                opts.get("extractor_args"),
                {
                    "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
                    "youtube": {"lang": ["en-US"]},
                },
            )
        finally:
            settings.ytdlp_pot_bgutil_base_url = original

    def test_bilibili_does_not_inject_bgutil_pot_provider(self) -> None:
        original = settings.ytdlp_pot_bgutil_base_url
        try:
            settings.ytdlp_pot_bgutil_base_url = "http://127.0.0.1:4416"
            opts: dict[str, object] = {}
            _apply_common_ytdlp_opts(
                opts,
                url="https://www.bilibili.com/video/BV1xx411c7mD",
                provider="bilibili",
            )
            extractor_args = opts.get("extractor_args") or {}
            self.assertNotIn("youtubepot-bgutilhttp", extractor_args)
        finally:
            settings.ytdlp_pot_bgutil_base_url = original

    def test_youtube_sync_keeps_configured_proxy(self) -> None:
        original = settings.ytdlp_proxy
        try:
            settings.ytdlp_proxy = "http://127.0.0.1:7890"
            opts: dict[str, object] = {}
            _apply_common_ytdlp_opts(
                opts,
                url="https://www.youtube.com/@example/videos",
                provider="youtube",
            )
            self.assertEqual(opts.get("proxy"), "http://127.0.0.1:7890")
        finally:
            settings.ytdlp_proxy = original

    def test_other_provider_disables_proxy(self) -> None:
        original = settings.ytdlp_proxy
        try:
            settings.ytdlp_proxy = "http://127.0.0.1:7890"
            opts: dict[str, object] = {}
            _apply_common_ytdlp_opts(
                opts,
                url="https://example.com/video",
                provider="other",
            )
            self.assertEqual(opts.get("proxy"), "")
            self.assertEqual(YoutubeDL({"proxy": opts["proxy"]}).proxies, {"all": "__noproxy__"})
        finally:
            settings.ytdlp_proxy = original

    def test_youtube_legacy_dash_first_format_prefers_hls_mp4(self) -> None:
        attempts = _download_format_attempts(cookie_provider="youtube", configured_format=_GENERIC_MP4_FORMAT)

        self.assertEqual(attempts[0], ("youtube_hls_mp4", _YOUTUBE_HLS_FIRST_FORMAT, "mp4"))
        self.assertIn(("youtube_dash_mp4", _build_dash_mp4_format(1080), "mp4"), attempts)

    def test_youtube_legacy_720_format_keeps_height_cap_for_hls_fallback(self) -> None:
        attempts = _download_format_attempts(cookie_provider="youtube", configured_format=_GENERIC_MP4_720_FORMAT)

        self.assertEqual(
            attempts[0],
            (
                "youtube_hls_mp4",
                (
                    "best[ext=mp4][height<=720]/best[height<=720]"
                    "/bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=720]+bestaudio/best"
                ),
                "mp4",
            ),
        )
        self.assertIn(("youtube_dash_mp4", _build_dash_mp4_format(720), "mp4"), attempts)

    def test_youtube_custom_format_keeps_user_first_and_adds_hls_fallback(self) -> None:
        attempts = _download_format_attempts(cookie_provider="youtube", configured_format="137+140")

        self.assertEqual(attempts[0], ("user", "137+140", None))
        self.assertEqual(attempts[1], ("youtube_hls_mp4", _YOUTUBE_HLS_FIRST_FORMAT, "mp4"))

    def test_bilibili_keeps_configured_format_first(self) -> None:
        attempts = _download_format_attempts(cookie_provider="bilibili", configured_format="137+140")

        self.assertEqual(attempts[0], ("user", "137+140", None))
        self.assertNotIn(("youtube_hls_mp4", _YOUTUBE_HLS_FIRST_FORMAT, "mp4"), attempts)

    def test_detect_http_403_forbidden_format_failure(self) -> None:
        self.assertTrue(_is_http_403_forbidden_error(RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")))
        self.assertFalse(_is_http_403_forbidden_error(RuntimeError("HTTP Error 404: Not Found")))


if __name__ == "__main__":
    unittest.main()
