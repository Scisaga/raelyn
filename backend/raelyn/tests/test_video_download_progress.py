from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers.video_download import _job_progress_from_ytdlp_hook


class VideoDownloadProgressTests(unittest.TestCase):
    def test_prefers_byte_progress_when_total_known(self) -> None:
        progress = _job_progress_from_ytdlp_hook(
            {
                "status": "downloading",
                "downloaded_bytes": 35,
                "total_bytes": 100,
            }
        )
        self.assertEqual(progress, (3500, 10000))

    def test_falls_back_to_fragment_progress(self) -> None:
        progress = _job_progress_from_ytdlp_hook(
            {
                "status": "downloading",
                "fragment_index": 7,
                "fragment_count": 10,
            }
        )
        self.assertEqual(progress, (7000, 10000))

    def test_finished_state_is_capped_below_full_completion(self) -> None:
        progress = _job_progress_from_ytdlp_hook({"status": "finished"})
        self.assertEqual(progress, (9900, 10000))


if __name__ == "__main__":
    unittest.main()
