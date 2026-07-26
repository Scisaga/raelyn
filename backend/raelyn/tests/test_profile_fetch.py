from __future__ import annotations

import sys
import unittest
from pathlib import Path

from yt_dlp.utils import std_headers

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.browser_identity import BROWSER_USER_AGENT
from raelyn.services.http_client import browser_http_client
from raelyn.services.profile_fetch import _parse_bilibili_card_profile


class ProfileFetchHeadersTests(unittest.TestCase):
    def test_browser_user_agent_tracks_installed_ytdlp(self) -> None:
        self.assertEqual(BROWSER_USER_AGENT, str(std_headers["User-Agent"]))

    def test_browser_http_client_disables_environment_proxy(self) -> None:
        with browser_http_client() as client:
            self.assertFalse(client.trust_env)
            self.assertEqual(client.impersonate, "chrome")


class BilibiliCardProfileTests(unittest.TestCase):
    def test_card_profile_extracts_face_and_counts(self) -> None:
        profile = _parse_bilibili_card_profile(
            {
                "code": 0,
                "data": {
                    "archive_count": 674,
                    "follower": 12345,
                    "card": {
                        "name": "中国基金报",
                        "sign": "财经资讯",
                        "face": "https://i0.hdslb.com/bfs/face/example.jpg",
                    },
                },
            }
        )

        self.assertEqual(
            profile,
            {
                "name": "中国基金报",
                "description": "财经资讯",
                "avatar_url": "https://i0.hdslb.com/bfs/face/example.jpg",
                "subscriber_count": 12345,
                "video_count": 674,
                "source": "bilibili_card",
            },
        )


if __name__ == "__main__":
    unittest.main()
