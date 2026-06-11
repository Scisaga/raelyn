from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.ffmpeg import extract_audio_to_m4a


class FfmpegAudioExtractionTests(unittest.TestCase):
    def test_extract_audio_error_includes_ffmpeg_stderr(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["ffmpeg"],
            returncode=183,
            stdout="",
            stderr="Invalid data found when processing input",
        )

        with patch("raelyn.services.ffmpeg.subprocess.run", return_value=completed):
            with self.assertRaises(RuntimeError) as raised:
                extract_audio_to_m4a(input_path=Path("/tmp/input.ytdl"), output_path=Path("/tmp/audio.m4a"))

        self.assertIn("exit=183", str(raised.exception))
        self.assertIn("Invalid data found when processing input", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
