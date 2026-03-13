from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.services.ytdlp import _is_youtube_js_challenge_failed_messages, _remote_components


class YtdlpEjsDetectionTests(unittest.TestCase):
    def test_remote_components_default(self) -> None:
        self.assertEqual(_remote_components(), ["ejs:github"])

    def test_remote_components_parse_commas_and_spaces(self) -> None:
        original = settings.ytdlp_remote_components
        try:
            settings.ytdlp_remote_components = " ejs:github, ejs:npm   ejs:github "
            self.assertEqual(_remote_components(), ["ejs:github", "ejs:npm"])
        finally:
            settings.ytdlp_remote_components = original

    def test_detect_n_challenge(self) -> None:
        msgs = [
            "[youtube] -YmrN9XEI-4: n challenge solving failed: Some formats may be missing.",
        ]
        self.assertTrue(_is_youtube_js_challenge_failed_messages(msgs))

    def test_detect_only_images_available(self) -> None:
        msgs = [
            "Only images are available for download. use --list-formats to see them",
        ]
        self.assertTrue(_is_youtube_js_challenge_failed_messages(msgs))

    def test_detect_js_challenge_provider_no_solutions(self) -> None:
        msgs = [
            '[youtube] [jsc] JS Challenge Provider "node" returned an invalid response: error=\'no solutions\'',
        ]
        self.assertTrue(_is_youtube_js_challenge_failed_messages(msgs))

    def test_non_match(self) -> None:
        msgs = ["Requested format is not available"]
        self.assertFalse(_is_youtube_js_challenge_failed_messages(msgs))


if __name__ == "__main__":
    unittest.main()
