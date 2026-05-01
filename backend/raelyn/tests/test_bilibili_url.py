from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers.common import _normalize_bilibili_video_url


class BilibiliUrlNormalizeTests(unittest.TestCase):
    def test_preserves_query_when_normalizing_short_bv_video_url(self) -> None:
        url = "https://www.bilibili.com/video/1mt41117C6/?p=8&spm_id_from=333.788"

        normalized = _normalize_bilibili_video_url(url)

        self.assertEqual(
            normalized,
            "https://www.bilibili.com/video/BV1mt41117C6?p=8&spm_id_from=333.788",
        )

    def test_preserves_query_when_normalizing_av_video_url(self) -> None:
        url = "https://m.bilibili.com/video/123456/?p=1"

        normalized = _normalize_bilibili_video_url(url)

        self.assertEqual(normalized, "https://www.bilibili.com/video/av123456?p=1")

    def test_preserves_query_for_normal_bv_video_url(self) -> None:
        url = "https://m.bilibili.com/video/BV1mt41117C6/?p=2"

        normalized = _normalize_bilibili_video_url(url)

        self.assertEqual(normalized, "https://www.bilibili.com/video/BV1mt41117C6?p=2")

    def test_plain_bv_id_still_expands_to_video_url(self) -> None:
        normalized = _normalize_bilibili_video_url("BV1mt41117C6")

        self.assertEqual(normalized, "https://www.bilibili.com/video/BV1mt41117C6")


if __name__ == "__main__":
    unittest.main()
