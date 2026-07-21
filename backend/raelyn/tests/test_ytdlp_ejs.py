from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_dlp.utils import DownloadError

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.services.ytdlp import (
    _is_youtube_js_challenge_failed_messages,
    _is_youtube_no_video_formats_messages,
    _remote_components,
    ytdlp_extract_info,
)


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

    def test_detect_no_video_formats(self) -> None:
        msgs = [
            "ERROR: [youtube] xICvY586XZ8: No video formats found; please report this issue",
        ]
        self.assertTrue(_is_youtube_no_video_formats_messages(msgs))
        self.assertFalse(_is_youtube_js_challenge_failed_messages(msgs))

    def test_detect_js_challenge_provider_no_solutions(self) -> None:
        msgs = [
            '[youtube] [jsc] JS Challenge Provider "node" returned an invalid response: error=\'no solutions\'',
        ]
        self.assertTrue(_is_youtube_js_challenge_failed_messages(msgs))

    def test_non_match(self) -> None:
        msgs = ["Requested format is not available"]
        self.assertFalse(_is_youtube_js_challenge_failed_messages(msgs))

    def test_extract_info_no_video_formats_gets_actionable_hint(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, _opts):
                return

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, *, download):
                assert download is False
                raise DownloadError("ERROR: [youtube] xICvY586XZ8: No video formats found; please report this issue")

        with (
            patch("raelyn.services.ytdlp.YoutubeDL", FakeYoutubeDL),
            patch("raelyn.services.ytdlp._load_ytdlp_youtube_lang", return_value=""),
        ):
            with self.assertRaisesRegex(RuntimeError, "YouTube 未返回可播放视频格式"):
                ytdlp_extract_info(
                    "https://www.youtube.com/watch?v=xICvY586XZ8",
                    provider="youtube",
                    use_provider_cookies=False,
                )


if __name__ == "__main__":
    unittest.main()
