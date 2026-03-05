from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.ytdlp import _normalize_youtube_lang


class YtdlpYoutubeLangNormalizationTests(unittest.TestCase):
    def test_normalize_empty(self) -> None:
        self.assertEqual(_normalize_youtube_lang(""), "")
        self.assertEqual(_normalize_youtube_lang("   "), "")

    def test_normalize_chinese_defaults(self) -> None:
        self.assertEqual(_normalize_youtube_lang("zh"), "zh-CN")
        self.assertEqual(_normalize_youtube_lang("ZH"), "zh-CN")
        self.assertEqual(_normalize_youtube_lang("zh-hans"), "zh-CN")
        self.assertEqual(_normalize_youtube_lang("zh_cn"), "zh-CN")
        self.assertEqual(_normalize_youtube_lang("zh-tw"), "zh-TW")
        self.assertEqual(_normalize_youtube_lang("zh_hk"), "zh-HK")

    def test_canonical_bcp47_casing(self) -> None:
        self.assertEqual(_normalize_youtube_lang("en-gb"), "en-GB")
        self.assertEqual(_normalize_youtube_lang("en-in"), "en-IN")
        self.assertEqual(_normalize_youtube_lang("es-419"), "es-419")
        self.assertEqual(_normalize_youtube_lang("sr-latn"), "sr-Latn")


if __name__ == "__main__":
    unittest.main()

