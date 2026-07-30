from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.ffmpeg import (
    MediaPacketValidationError,
    extract_audio_to_m4a,
    ffmpeg_bin,
    require_media_packets,
)


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

    def test_extract_audio_produces_real_audio_packets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-ffmpeg-test-") as tmp_dir:
            source_path = Path(tmp_dir) / "source.m4a"
            output_path = Path(tmp_dir) / "audio.m4a"
            generated = subprocess.run(
                [
                    ffmpeg_bin(),
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.1",
                    "-c:a",
                    "aac",
                    str(source_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if generated.returncode != 0:
                self.skipTest("需要项目 ffmpeg 生成真实测试音频")

            extract_audio_to_m4a(input_path=source_path, output_path=output_path)
            require_media_packets(input_path=output_path, stream_types=("audio",))

    def test_real_empty_asr_sample_has_no_audio_packets(self) -> None:
        sample_path = _BACKEND_DIR.parent / "asr-error-fff22b31-QxdiHocamtk.m4a"
        if not sample_path.exists():
            self.skipTest("缺少排障样本：将空 ASR 音频复制到项目根目录后可执行")

        with self.assertRaises(MediaPacketValidationError) as raised:
            require_media_packets(input_path=sample_path, stream_types=("audio",))

        self.assertIn("音轨", str(raised.exception))

    def test_extract_audio_rejects_real_empty_asr_sample(self) -> None:
        sample_path = _BACKEND_DIR.parent / "asr-error-fff22b31-QxdiHocamtk.m4a"
        if not sample_path.exists():
            self.skipTest("缺少排障样本：将空 ASR 音频复制到项目根目录后可执行")

        with tempfile.TemporaryDirectory(prefix="raelyn-empty-audio-test-") as tmp_dir:
            with self.assertRaises(RuntimeError) as raised:
                extract_audio_to_m4a(
                    input_path=sample_path,
                    output_path=Path(tmp_dir) / "output.m4a",
                )

        self.assertIn("ffmpeg 音频提取失败", str(raised.exception))

    def test_extract_audio_rejects_real_header_only_youtube_video(self) -> None:
        candidates = [
            *Path("/tmp").glob("raelyn-ytdlp-repro.*/QxdiHocamtk.mp4"),
            *Path("/tmp").glob("raelyn-asr-source.*/QxdiHocamtk.mp4"),
        ]
        if not candidates:
            self.skipTest("缺少上游 header-only MP4 排障样本")

        with tempfile.TemporaryDirectory(prefix="raelyn-header-only-video-test-") as tmp_dir:
            with self.assertRaises(MediaPacketValidationError) as raised:
                extract_audio_to_m4a(
                    input_path=candidates[0],
                    output_path=Path(tmp_dir) / "output.m4a",
                )

        self.assertIn("输出音频无有效数据包", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
