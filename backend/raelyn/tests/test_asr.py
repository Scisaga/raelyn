from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.asr import resolve_asr_timeout_seconds


class AsrServiceTests(unittest.TestCase):
    def test_resolve_asr_timeout_keeps_base_timeout_for_short_or_unknown_media(self) -> None:
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=600, media_duration_seconds=None), 600)
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=600, media_duration_seconds=300), 600)

    def test_resolve_asr_timeout_grows_for_long_media(self) -> None:
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=600, media_duration_seconds=15705), 1429)

    def test_resolve_asr_timeout_preserves_explicitly_larger_base_timeout(self) -> None:
        self.assertEqual(resolve_asr_timeout_seconds(base_timeout_seconds=1800, media_duration_seconds=15705), 1800)


if __name__ == "__main__":
    unittest.main()
