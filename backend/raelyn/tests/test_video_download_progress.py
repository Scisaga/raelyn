from __future__ import annotations

import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers.video_download import (
    _download_lease_expires_at,
    _job_progress_from_ytdlp_hook,
    _pick_downloaded_video_file,
)
from raelyn.timeutil import utcnow


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

    def test_download_lease_extension_keeps_long_download_running(self) -> None:
        lease_expires_at = _download_lease_expires_at()
        self.assertGreater((lease_expires_at - utcnow()).total_seconds(), 3500)

    def test_pick_downloaded_video_file_ignores_ytdlp_sidecars(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "video.ytdl").write_text('{"downloader": {"current_fragment": {"index": 0}}}', encoding="utf-8")
            (root / "video.info.json").write_text("{}", encoding="utf-8")
            (root / "video.webp").write_bytes(b"thumbnail")
            (root / "video.vtt").write_text("WEBVTT", encoding="utf-8")

            picked = _pick_downloaded_video_file([path for path in root.iterdir()])

        self.assertIsNone(picked)

    def test_pick_downloaded_video_file_prefers_mp4(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            webm = root / "video.webm"
            mp4 = root / "video.mp4"
            webm.write_bytes(b"w" * 100)
            mp4.write_bytes(b"m" * 10)

            picked = _pick_downloaded_video_file([webm, mp4])

        self.assertEqual(picked, mp4)


if __name__ == "__main__":
    unittest.main()
