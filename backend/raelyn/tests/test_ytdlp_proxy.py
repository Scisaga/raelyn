from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
            self.assertEqual(YoutubeDL({"proxy": opts["proxy"]}).proxies, {"all": "__noproxy__"})
        finally:
            settings.ytdlp_proxy = original

    def test_youtube_keeps_configured_proxy(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
