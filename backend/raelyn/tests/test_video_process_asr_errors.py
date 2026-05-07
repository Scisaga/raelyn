from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers.video_process import video_asr_transcribe
from raelyn.jobs.reschedule import JobReschedule, JobTerminalFailure
from raelyn.models import Asset, Job, Video
from raelyn.services.asr import AsrBackendDefer


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, *, video: Video, audio_asset: Asset) -> None:
        self._video = video
        self._audio_asset = audio_asset

    def get(self, model, key):
        if model is Video and key == self._video.id:
            return self._video
        return None

    def execute(self, _stmt):
        return _ScalarResult(self._audio_asset)


def _http_status_error(status_code: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://asr.local/v1/audio/transcriptions")
    response = httpx.Response(status_code, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError("asr failed", request=request, response=response)


class VideoAsrTranscribeErrorTests(unittest.TestCase):
    def _job_context(self) -> tuple[_FakeSession, Job]:
        video_id = uuid.uuid4()
        video = Video(
            id=video_id,
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.test/watch?v=abc123",
            duration_sec=120,
        )
        audio_asset = Asset(
            video_id=video_id,
            type="audio",
            format="m4a",
            source="download",
            s3_bucket="bucket",
            s3_key="audio.m4a",
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.asr_transcribe",
            status="running",
            params={"video_id": str(video_id)},
        )
        return _FakeSession(video=video, audio_asset=audio_asset), job

    def test_capacity_guard_reschedules_before_audio_download(self) -> None:
        session, job = self._job_context()
        defer = AsrBackendDefer(reason="asr backend is busy", delay_seconds=30)

        with patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=defer):
            with patch("raelyn.jobs.handlers.video_process.s3_download_file") as s3_download_file:
                with self.assertRaises(JobReschedule) as raised:
                    video_asr_transcribe(session, job)

        s3_download_file.assert_not_called()
        self.assertEqual(raised.exception.delay_seconds, 30)
        self.assertEqual(job.params["asr_capacity_defers"], 1)

    def test_terminal_asr_http_status_does_not_retry(self) -> None:
        session, job = self._job_context()

        def write_audio(*, bucket: str, key: str, local_path: Path) -> None:
            local_path.write_bytes(b"audio")

        with patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=None):
            with patch("raelyn.jobs.handlers.video_process.s3_download_file", side_effect=write_audio):
                with patch("raelyn.jobs.handlers.video_process.asr_transcribe", side_effect=_http_status_error(400, "empty file")):
                    with self.assertRaises(JobTerminalFailure) as raised:
                        video_asr_transcribe(session, job)

        self.assertIn("http 400", raised.exception.reason)
        self.assertNotIn("asr_transient_defers", job.params)

    def test_transient_asr_http_status_reschedules_with_longer_backoff(self) -> None:
        session, job = self._job_context()

        def write_audio(*, bucket: str, key: str, local_path: Path) -> None:
            local_path.write_bytes(b"audio")

        with patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=None):
            with patch("raelyn.jobs.handlers.video_process.s3_download_file", side_effect=write_audio):
                with patch("raelyn.jobs.handlers.video_process.asr_transcribe", side_effect=_http_status_error(503, "busy")):
                    with self.assertRaises(JobReschedule) as raised:
                        video_asr_transcribe(session, job)

        self.assertEqual(raised.exception.delay_seconds, 60)
        self.assertIn("http 503", raised.exception.reason)
        self.assertEqual(job.params["asr_transient_defers"], 1)


if __name__ == "__main__":
    unittest.main()
