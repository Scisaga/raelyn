from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.ytdlp_errors import is_ffmpeg_segfault, parse_upcoming_live_delay_seconds


class YtdlpRetryAndLiveTests(unittest.TestCase):
    def test_parse_upcoming_live_delay_seconds_cn_minutes(self) -> None:
        s = "ERROR: [youtube] aG_5tUwbn8g: 33分钟后直播!"
        self.assertEqual(parse_upcoming_live_delay_seconds(s), 33 * 60)

    def test_parse_upcoming_live_delay_seconds_cn_hours(self) -> None:
        s = "ERROR: [youtube] xxx: 2小时后直播!"
        self.assertEqual(parse_upcoming_live_delay_seconds(s), 2 * 3600)

    def test_parse_upcoming_live_delay_seconds_en_minutes(self) -> None:
        s = "ERROR: Premieres in 28 minutes"
        self.assertEqual(parse_upcoming_live_delay_seconds(s), 28 * 60)

    def test_parse_upcoming_live_delay_seconds_hms(self) -> None:
        s = "This live event will begin in 0:33:00"
        self.assertEqual(parse_upcoming_live_delay_seconds(s), 33 * 60)

    def test_is_ffmpeg_segfault(self) -> None:
        self.assertTrue(is_ffmpeg_segfault("ERROR: ffmpeg exited with code -11"))
        self.assertTrue(is_ffmpeg_segfault("SIGSEGV"))
        self.assertTrue(is_ffmpeg_segfault("Segmentation fault"))
        self.assertFalse(is_ffmpeg_segfault("ffmpeg exited with code 1"))


if __name__ == "__main__":
    unittest.main()
