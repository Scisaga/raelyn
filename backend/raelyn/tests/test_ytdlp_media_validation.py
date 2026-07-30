from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.ffmpeg import MediaPacketValidationError, ffmpeg_bin
from raelyn.services.ytdlp import _validate_ytdlp_attempt_media


class YtdlpMediaValidationTests(unittest.TestCase):
    def test_accepts_real_video_with_audio_packets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-valid-ytdlp-media-") as tmp_dir:
            output_path = Path(tmp_dir) / "valid.mp4"
            completed = subprocess.run(
                [
                    ffmpeg_bin(),
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=32x32:r=10:d=0.2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.2",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(output_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if completed.returncode != 0:
                self.skipTest("需要项目 ffmpeg 生成真实音视频测试文件")

            _validate_ytdlp_attempt_media(Path(tmp_dir), attempt_label="integration_valid")

    def test_rejects_real_header_only_youtube_video(self) -> None:
        candidates = [
            *Path("/tmp").glob("raelyn-ytdlp-repro.*/QxdiHocamtk.mp4"),
            *Path("/tmp").glob("raelyn-asr-source.*/QxdiHocamtk.mp4"),
        ]
        if not candidates:
            self.skipTest("缺少上游 header-only MP4 排障样本")

        with tempfile.TemporaryDirectory(prefix="raelyn-invalid-ytdlp-media-") as tmp_dir:
            shutil.copyfile(candidates[0], Path(tmp_dir) / "QxdiHocamtk.mp4")
            with self.assertRaises(MediaPacketValidationError) as raised:
                _validate_ytdlp_attempt_media(Path(tmp_dir), attempt_label="youtube_hls_mp4")

        self.assertIn("不包含可读取数据包", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
