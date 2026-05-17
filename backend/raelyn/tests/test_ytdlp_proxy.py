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
from raelyn.services.ytdlp import _apply_common_ytdlp_opts


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


if __name__ == "__main__":
    unittest.main()
