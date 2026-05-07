from __future__ import annotations

import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs import claim
from raelyn.models import AppConfig, Job, Media, Video
from raelyn import worker
from raelyn.services.asr import AsrBackendDefer
from raelyn.services.ytdlp import YTDLP_RETRY_WITHOUT_COOKIES_PARAM, YtdlpCookiesInvalidError


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class WorkerRetryMergeTests(unittest.TestCase):
    def test_asr_capacity_defer_skips_claim_for_asr_only_worker(self) -> None:
        defer = AsrBackendDefer(reason="asr backend is busy", delay_seconds=30)

        with patch("raelyn.worker.inspect_asr_backend_defer", return_value=defer):
            skip_types, sleep_seconds = worker._claim_skip_types_for_external_capacity(["video.asr_transcribe"])

        self.assertEqual(skip_types, {"video.asr_transcribe"})
        self.assertEqual(sleep_seconds, 5.0)

    def test_asr_capacity_defer_only_skips_asr_for_all_worker(self) -> None:
        defer = AsrBackendDefer(reason="asr backend is busy", delay_seconds=30)

        with patch("raelyn.worker.inspect_asr_backend_defer", return_value=defer):
            skip_types, sleep_seconds = worker._claim_skip_types_for_external_capacity(None)

        self.assertEqual(skip_types, {"video.asr_transcribe"})
        self.assertEqual(sleep_seconds, 1.0)

    def test_merge_retry_into_existing_pending_job(self) -> None:
        dedupe_key = f"brief:{uuid.uuid4()}:2026-03-20"
        current_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            priority=1007,
            dedupe_key=dedupe_key,
            scheduled_for=datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc),
            attempt=1,
            error_message="boom",
            error_stack="trace",
            worker_id="worker-1",
            progress_current=1,
            progress_total=2,
        )
        pending_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="pending",
            priority=2,
            dedupe_key=dedupe_key,
            scheduled_for=datetime(2026, 3, 20, 2, 0, tzinfo=timezone.utc),
        )
        retry_at = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)
        now = datetime(2026, 3, 20, 0, 22, 12, tzinfo=timezone.utc)

        session = Mock()
        session.execute.return_value = _scalar_one_or_none(pending_job)

        with patch("raelyn.worker.utcnow", return_value=now):
            with patch("raelyn.worker.job_log") as job_log:
                merged = worker._merge_retry_into_existing_pending_job(
                    session,
                    job=current_job,
                    retry_at=retry_at,
                    attempt=1,
                    backoff_seconds=10,
                )

        self.assertTrue(merged)
        self.assertEqual(pending_job.scheduled_for, retry_at)
        self.assertEqual(pending_job.priority, 1007)
        self.assertIsNone(pending_job.error_message)
        self.assertIsNone(pending_job.error_stack)

        self.assertEqual(current_job.status, "failed")
        self.assertEqual(current_job.finished_at, now)
        self.assertIsNone(current_job.lease_expires_at)
        self.assertIsNone(current_job.worker_id)
        self.assertIsNone(current_job.progress_current)
        self.assertIsNone(current_job.progress_total)

        self.assertEqual(job_log.call_count, 2)

    def test_merge_retry_returns_false_without_existing_pending_job(self) -> None:
        current_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            priority=7,
            dedupe_key=f"brief:{uuid.uuid4()}:2026-03-20",
            scheduled_for=datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc),
        )
        session = Mock()
        session.execute.return_value = _scalar_one_or_none(None)

        with patch("raelyn.worker.job_log") as job_log:
            merged = worker._merge_retry_into_existing_pending_job(
                session,
                job=current_job,
                retry_at=datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc),
                attempt=1,
                backoff_seconds=10,
            )

        self.assertFalse(merged)
        self.assertEqual(current_job.status, "running")
        self.assertEqual(job_log.call_count, 0)

    def test_finalize_terminal_failure_does_not_schedule_retry(self) -> None:
        job = Job(
            id=uuid.uuid4(),
            type="playlist.build_analysis_snapshot",
            status="running",
            attempt=0,
            max_attempts=5,
            worker_id="worker-1",
            lease_expires_at=datetime(2026, 3, 20, 0, 22, tzinfo=timezone.utc),
            progress_current=1,
            progress_total=2,
        )
        now = datetime(2026, 3, 20, 0, 23, tzinfo=timezone.utc)
        session = Mock()

        with patch("raelyn.worker.utcnow", return_value=now):
            with patch("raelyn.worker.job_log") as job_log:
                worker._finalize_terminal_failure(session, job=job, reason="analysis aborted: available memory too low")

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempt, 1)
        self.assertEqual(job.error_message, "analysis aborted: available memory too low")
        self.assertIsNone(job.worker_id)
        self.assertIsNone(job.lease_expires_at)
        self.assertEqual(job.finished_at, now)
        job_log.assert_called_once()


class WorkerRecoveryMergeTests(unittest.TestCase):
    def test_requeue_expired_running_job_merges_into_existing_pending_job(self) -> None:
        dedupe_key = f"brief:{uuid.uuid4()}:2026-03-20"
        running_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            priority=1007,
            dedupe_key=dedupe_key,
            worker_id="worker-dead",
            lease_expires_at=datetime(2026, 3, 20, 0, 22, 0, tzinfo=timezone.utc),
        )
        pending_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="pending",
            priority=2,
            dedupe_key=dedupe_key,
            scheduled_for=datetime(2026, 3, 20, 2, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 3, 20, 0, 1, tzinfo=timezone.utc),
            error_message="old",
            error_stack="old",
        )
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)

        session = Mock()
        session.execute.side_effect = [
            _scalars_all([running_job]),
            _scalar_one_or_none(pending_job),
        ]

        with patch("raelyn.jobs.claim.utcnow", return_value=now):
            count = claim.requeue_expired_running_jobs(session)

        self.assertEqual(count, 1)
        self.assertEqual(pending_job.scheduled_for, now)
        self.assertEqual(pending_job.priority, 1007)
        self.assertIsNone(pending_job.error_message)
        self.assertIsNone(pending_job.error_stack)
        self.assertIsNone(pending_job.started_at)
        self.assertIsNone(pending_job.finished_at)
        self.assertEqual(running_job.status, "failed")
        self.assertEqual(running_job.finished_at, now)
        self.assertIsNone(running_job.worker_id)
        self.assertIsNone(running_job.lease_expires_at)

    def test_requeue_orphan_running_job_merges_into_existing_pending_job(self) -> None:
        dedupe_key = f"brief:{uuid.uuid4()}:2026-03-20"
        running_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            priority=7,
            dedupe_key=dedupe_key,
            worker_id="worker-dead",
        )
        pending_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="pending",
            priority=100,
            dedupe_key=dedupe_key,
            scheduled_for=datetime(2026, 3, 20, 3, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 3, 20, 0, 3, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 20, 0, 4, tzinfo=timezone.utc),
        )
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)

        session = Mock()
        session.execute.side_effect = [
            _scalars_all([running_job]),
            _scalar_one_or_none(50),
            _scalar_one_or_none(pending_job),
        ]

        with patch("raelyn.jobs.claim.utcnow", return_value=now):
            count = claim.requeue_orphan_running_jobs(session, stale_after_seconds=60, priority_bump=1000)

        self.assertEqual(count, 1)
        self.assertEqual(pending_job.scheduled_for, now)
        self.assertEqual(pending_job.priority, 1050)
        self.assertIsNone(pending_job.started_at)
        self.assertIsNone(pending_job.finished_at)
        self.assertEqual(running_job.status, "failed")
        self.assertEqual(running_job.finished_at, now)
        self.assertIsNone(running_job.worker_id)


class WorkerDownloadFailureStateTests(unittest.TestCase):
    def test_mark_download_video_terminal_failure_updates_stuck_downloading_video(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=abc123",
            status="downloading",
            error_message=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            status="failed",
            params={"video_id": str(video.id)},
            error_message="ERROR: unable to download video data: HTTP Error 403: Forbidden",
        )
        session = Mock()
        session.get.side_effect = lambda model, key: video if model is Video and key == video.id else None

        worker._mark_download_video_terminal_failure(session, job=job)

        self.assertEqual(video.status, "failed")
        self.assertEqual(video.error_message, job.error_message)

    def test_mark_download_video_terminal_failure_ignores_non_downloading_video_status(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=abc123",
            status="ready",
            error_message="old error",
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            status="failed",
            params={"video_id": str(video.id)},
            error_message="new error",
        )
        session = Mock()
        session.get.side_effect = lambda model, key: video if model is Video and key == video.id else None

        worker._mark_download_video_terminal_failure(session, job=job)

        self.assertEqual(video.status, "ready")
        self.assertEqual(video.error_message, "new error")

    def test_update_download_retry_params_disables_cookies_for_403_retry(self) -> None:
        job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            params={"video_id": "video-1"},
            error_message="ERROR: unable to download video data: HTTP Error 403: Forbidden",
        )

        worker._update_download_retry_params(job)

        self.assertTrue(job.params[YTDLP_RETRY_WITHOUT_COOKIES_PARAM])

    def test_update_download_retry_params_clears_cookie_bypass_for_non_403(self) -> None:
        job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            params={"video_id": "video-1", YTDLP_RETRY_WITHOUT_COOKIES_PARAM: True},
            error_message="yt-dlp extraction returned no result",
        )

        worker._update_download_retry_params(job)

        self.assertNotIn(YTDLP_RETRY_WITHOUT_COOKIES_PARAM, job.params)

    def test_persist_youtube_cookie_pause_after_rollback(self) -> None:
        media_id = uuid.uuid4()
        media = Media(
            id=media_id,
            provider="youtube",
            provider_media_id="channel-1",
            url="https://www.youtube.com/@channel-1/videos",
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media_id)},
        )
        config_item = AppConfig(key="provider_pause", value={})
        session = Mock()

        def _get(model, key):
            if model is Media and key == media_id:
                return media
            if model is AppConfig and key == "provider_pause":
                return config_item
            return None

        session.get.side_effect = _get

        worker._persist_provider_pause_after_rollback(
            session,
            job=job,
            err=YtdlpCookiesInvalidError(
                "ytdlp_cookies_expired",
                "YTDLP_COOKIES_YOUTUBE 已失效：YouTube 登录态 cookies 过期。请在 UI -> 设置 更新 YouTube cookies.txt。",
                provider="youtube",
            ),
        )

        pause = config_item.value["youtube"]
        self.assertTrue(pause["paused"])
        self.assertEqual(pause["reason"], "ytdlp_cookies_expired")
        self.assertIn("YTDLP_COOKIES_YOUTUBE 已失效", pause["message"])
        self.assertIn("YTDLP_COOKIES_YOUTUBE", pause["message"])


if __name__ == "__main__":
    unittest.main()
