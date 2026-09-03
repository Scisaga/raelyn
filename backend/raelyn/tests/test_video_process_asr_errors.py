from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers import video_process
from raelyn.jobs.handlers.video_process import video_asr_transcribe
from raelyn.jobs.reschedule import JobReschedule, JobTerminalFailure
from raelyn.models import Asset, Job, Video
from raelyn.services.asr import AsrBackendDefer
from raelyn.services.ffmpeg import ffmpeg_bin
from raelyn.services.inference import EffectiveAsrConfig


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, *, video: Video, audio_asset: Asset) -> None:
        self._video = video
        self._audio_asset = audio_asset
        self.events = []
        self.commit_count = 0

    def get(self, model, key):
        if model is Video and key == self._video.id:
            return self._video
        return None

    def execute(self, _stmt):
        return _ScalarResult(self._audio_asset)

    def add(self, item) -> None:
        self.events.append(item)

    def flush(self, _objects=None) -> None:
        return None

    def commit(self) -> None:
        self.commit_count += 1


def _http_status_error(status_code: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://asr.local/v1/audio/transcriptions")
    response = httpx.Response(status_code, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError("asr failed", request=request, response=response)


def _read_timeout_error() -> httpx.ReadTimeout:
    request = httpx.Request("POST", "http://asr.local/v1/audio/transcriptions")
    return httpx.ReadTimeout("timed out", request=request)


def _local_asr_config() -> EffectiveAsrConfig:
    return EffectiveAsrConfig(
        mode="local",
        provider="local",
        source="test",
        url="http://asr.local/v1/audio/transcriptions",
        model="qwen3-asr",
        timeout_seconds=600,
        prompt="",
        temperature=None,
        response_format="",
        app_key="",
        access_key="",
        resource_id="",
        configured=True,
    )


class VideoAsrTranscribeErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-asr-test-audio-") as tmp_dir:
            output_path = Path(tmp_dir) / "valid.m4a"
            completed = subprocess.run(
                [
                    ffmpeg_bin(),
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.1",
                    "-c:a",
                    "aac",
                    str(output_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if completed.returncode != 0:
                raise unittest.SkipTest("需要项目 ffmpeg 生成真实 ASR 测试音频")
            cls.valid_audio_bytes = output_path.read_bytes()

    def _write_valid_audio(self, *, bucket: str, key: str, local_path: Path) -> None:
        local_path.write_bytes(self.valid_audio_bytes)

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
            worker_id="worker-asr-1",
            execution_token=uuid.uuid4(),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        return _FakeSession(video=video, audio_asset=audio_asset), job

    def _run_success_case(
        self,
        *,
        configured_language: str,
        asr_payload: dict,
        observation_enabled: bool = True,
    ) -> tuple[dict, Mock, Mock, Mock]:
        session, job = self._job_context()

        with ExitStack() as stack:
            stack.enter_context(patch.object(video_process.settings, "asr_language", configured_language))
            stack.enter_context(patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=None))
            stack.enter_context(
                patch("raelyn.jobs.handlers.video_process.get_effective_asr_config", return_value=_local_asr_config())
            )
            stack.enter_context(
                patch("raelyn.jobs.handlers.video_process.s3_download_file", side_effect=self._write_valid_audio)
            )
            asr_transcribe = stack.enter_context(
                patch("raelyn.jobs.handlers.video_process.asr_transcribe", return_value=asr_payload)
            )
            ensure_asset = stack.enter_context(patch("raelyn.jobs.handlers.video_process.ensure_asset"))
            enqueue_job = stack.enter_context(patch("raelyn.jobs.handlers.video_process.enqueue_job"))
            stack.enter_context(patch("raelyn.jobs.handlers.video_process.llm_enabled", return_value=True))
            stack.enter_context(
                patch(
                    "raelyn.jobs.handlers.video_process.video_has_enabled_observation",
                    return_value=observation_enabled,
                )
            )
            stack.enter_context(patch("raelyn.jobs.handlers.video_process._schedule_auto_video_event_extraction"))
            stack.enter_context(patch("raelyn.jobs.handlers.video_process._enqueue_brief_for_video_playlists"))

            result = video_asr_transcribe(session, job)

        return result or {}, asr_transcribe, ensure_asset, enqueue_job

    def test_asr_language_empty_omits_language_and_saves_detected_language(self) -> None:
        result, asr_transcribe, ensure_asset, enqueue_job = self._run_success_case(
            configured_language="",
            asr_payload={"text": "hello", "segments": [], "language": "en"},
        )

        self.assertIsNone(asr_transcribe.call_args.kwargs["language"])
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["requested_language"], None)
        self.assertEqual(len(ensure_asset.call_args_list), 2)
        for call in ensure_asset.call_args_list:
            self.assertEqual(call.kwargs["language"], "en")
            self.assertIn("/transcript/en/", call.kwargs["s3_key"])
        enqueue_job.assert_not_called()

    def test_asr_language_en_passes_hint_and_saves_configured_language(self) -> None:
        result, asr_transcribe, ensure_asset, enqueue_job = self._run_success_case(
            configured_language="en",
            asr_payload={"text": "hello", "segments": []},
        )

        self.assertEqual(asr_transcribe.call_args.kwargs["language"], "en")
        self.assertEqual(result["language"], "en")
        for call in ensure_asset.call_args_list:
            self.assertEqual(call.kwargs["language"], "en")
            self.assertIn("/transcript/en/", call.kwargs["s3_key"])
        enqueue_job.assert_not_called()

    def test_asr_language_unknown_auto_detection_saves_und_path_without_language(self) -> None:
        result, asr_transcribe, ensure_asset, enqueue_job = self._run_success_case(
            configured_language="mixed",
            asr_payload={"text": "hello", "segments": []},
        )

        self.assertIsNone(asr_transcribe.call_args.kwargs["language"])
        self.assertIsNone(result["language"])
        for call in ensure_asset.call_args_list:
            self.assertIsNone(call.kwargs["language"])
            self.assertIn("/transcript/und/", call.kwargs["s3_key"])
        enqueue_job.assert_not_called()

    def test_asr_language_zh_still_allows_existing_polish_flow(self) -> None:
        result, asr_transcribe, ensure_asset, enqueue_job = self._run_success_case(
            configured_language="zh-CN",
            asr_payload={"text": "你好", "segments": []},
        )

        self.assertEqual(asr_transcribe.call_args.kwargs["language"], "zh")
        self.assertEqual(result["language"], "zh")
        for call in ensure_asset.call_args_list:
            self.assertEqual(call.kwargs["language"], "zh")
            self.assertIn("/transcript/zh/", call.kwargs["s3_key"])
        enqueue_job.assert_called_once()
        self.assertEqual(enqueue_job.call_args.kwargs["params"]["language"], "zh")

    def test_disabled_observation_keeps_transcript_archive_without_polish(self) -> None:
        result, _asr_transcribe, ensure_asset, enqueue_job = self._run_success_case(
            configured_language="zh-CN",
            asr_payload={"text": "你好", "segments": []},
            observation_enabled=False,
        )

        self.assertEqual(result["language"], "zh")
        self.assertEqual(len(ensure_asset.call_args_list), 2)
        enqueue_job.assert_not_called()

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

        with patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=None):
            with patch("raelyn.jobs.handlers.video_process.get_effective_asr_config", return_value=_local_asr_config()):
                with patch(
                    "raelyn.jobs.handlers.video_process.s3_download_file",
                    side_effect=self._write_valid_audio,
                ):
                    with patch(
                        "raelyn.jobs.handlers.video_process.asr_transcribe",
                        side_effect=_http_status_error(400, "empty file"),
                    ):
                        with self.assertRaises(JobTerminalFailure) as raised:
                            video_asr_transcribe(session, job)

        self.assertIn("http 400", raised.exception.reason)
        self.assertNotIn("asr_transient_defers", job.params)

    def test_transient_asr_http_status_reschedules_with_longer_backoff(self) -> None:
        session, job = self._job_context()

        with patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=None):
            with patch("raelyn.jobs.handlers.video_process.get_effective_asr_config", return_value=_local_asr_config()):
                with patch(
                    "raelyn.jobs.handlers.video_process.s3_download_file",
                    side_effect=self._write_valid_audio,
                ):
                    with patch(
                        "raelyn.jobs.handlers.video_process.asr_transcribe",
                        side_effect=_http_status_error(503, "busy"),
                    ):
                        with self.assertRaises(JobReschedule) as raised:
                            video_asr_transcribe(session, job)

        self.assertEqual(raised.exception.delay_seconds, 60)
        self.assertIn("http 503", raised.exception.reason)
        self.assertEqual(job.params["asr_transient_defers"], 1)

    def test_local_asr_read_timeout_retries_first_two_attempts_and_stops_third(self) -> None:
        for previous_attempt, expected_exception in (
            (0, httpx.ReadTimeout),
            (1, httpx.ReadTimeout),
            (2, JobTerminalFailure),
        ):
            with self.subTest(previous_attempt=previous_attempt):
                session, job = self._job_context()
                job.attempt = previous_attempt
                with ExitStack() as stack:
                    stack.enter_context(
                        patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=None)
                    )
                    stack.enter_context(
                        patch(
                            "raelyn.jobs.handlers.video_process.get_effective_asr_config",
                            return_value=_local_asr_config(),
                        )
                    )
                    stack.enter_context(
                        patch(
                            "raelyn.jobs.handlers.video_process.s3_download_file",
                            side_effect=self._write_valid_audio,
                        )
                    )
                    stack.enter_context(
                        patch("raelyn.jobs.handlers.video_process.asr_transcribe", side_effect=_read_timeout_error())
                    )
                    with self.assertRaises(expected_exception) as raised:
                        video_asr_transcribe(session, job)

                if previous_attempt == 2:
                    self.assertIn("attempt=3/3", str(raised.exception))
                self.assertEqual(session.commit_count, 2)
                timeout_events = [event for event in session.events if event.message == "asr request read timed out"]
                self.assertEqual(len(timeout_events), 1)
                self.assertNotIn("url", timeout_events[0].data or {})

    def test_long_local_asr_extends_lease_before_request(self) -> None:
        session, job = self._job_context()
        video = session._video
        video.duration_sec = 31472
        now = datetime(2026, 7, 20, 0, 0, 0, tzinfo=timezone.utc)
        job.lease_expires_at = now + timedelta(hours=1)

        with ExitStack() as stack:
            stack.enter_context(patch("raelyn.jobs.handlers.video_process.inspect_asr_backend_defer", return_value=None))
            stack.enter_context(
                patch("raelyn.jobs.handlers.video_process.get_effective_asr_config", return_value=_local_asr_config())
            )
            stack.enter_context(patch("raelyn.jobs.handlers.video_process.utcnow", return_value=now))
            set_lease = stack.enter_context(
                patch("raelyn.jobs.handlers.video_process.set_job_lease_deadline", return_value=True)
            )
            stack.enter_context(
                patch("raelyn.jobs.handlers.video_process.s3_download_file", side_effect=self._write_valid_audio)
            )
            stack.enter_context(
                patch("raelyn.jobs.handlers.video_process.asr_transcribe", side_effect=_read_timeout_error())
            )
            with self.assertRaises(httpx.ReadTimeout):
                video_asr_transcribe(session, job)

        self.assertEqual(
            set_lease.call_args.kwargs["lease_expires_at"],
            now + timedelta(seconds=7988 + 300),
        )
        self.assertEqual(set_lease.call_args.kwargs["worker_id"], "worker-asr-1")
        self.assertEqual(set_lease.call_args.kwargs["execution_token"], job.execution_token)


if __name__ == "__main__":
    unittest.main()
