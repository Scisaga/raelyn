from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs import claim
from raelyn.jobs.heartbeat import touch_worker_heartbeat
from raelyn.jobs import progress
from raelyn.models import AppConfig, Job, Media, Video, WorkerHeartbeat
from raelyn import worker
from raelyn.services.asr import AsrBackendDefer
from raelyn.services.ytdlp import YTDLP_RETRY_WITHOUT_COOKIES_PARAM, YtdlpCookiesInvalidError


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class WorkerRetryMergeTests(unittest.TestCase):
    def test_claim_assigns_a_fresh_execution_token_for_each_execution(self) -> None:
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)
        stale_token = uuid.uuid4()
        job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="pending",
            scheduled_for=now,
            execution_token=stale_token,
        )
        session = Mock()
        session.execute.return_value = _scalars_all([job])

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.job_provider", return_value=None):
                with patch("raelyn.jobs.claim.worker_role_for_job", return_value=None):
                    with patch("raelyn.jobs.claim.utcnow", return_value=now):
                        first_claim = claim.claim_next_job(session, worker_id="worker-1")
                        first_token = first_claim.execution_token

                        job.status = "pending"
                        job.worker_id = None
                        second_claim = claim.claim_next_job(session, worker_id="worker-2")
                        second_token = second_claim.execution_token

        self.assertIs(first_claim, job)
        self.assertIs(second_claim, job)
        self.assertIsInstance(first_token, uuid.UUID)
        self.assertIsInstance(second_token, uuid.UUID)
        self.assertNotEqual(first_token, stale_token)
        self.assertNotEqual(second_token, first_token)
        self.assertEqual(job.worker_id, "worker-2")

    def test_claim_skips_job_query_when_worker_role_paused(self) -> None:
        session = Mock()

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.is_worker_role_paused", return_value=True):
                claimed = claim.claim_next_job(
                    session,
                    worker_id="worker-1",
                    type_in=["video.extract_events", "playlist.backfill_events_range"],
                )

        self.assertIsNone(claimed)
        session.execute.assert_not_called()

    def test_claim_skips_job_query_when_direct_provider_paused(self) -> None:
        session = Mock()

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.is_worker_role_paused", return_value=False):
                with patch("raelyn.jobs.claim.is_provider_paused", return_value=True):
                    claimed = claim.claim_next_job(
                        session,
                        worker_id="worker-1",
                        type_in=["video.download.youtube"],
                    )

        self.assertIsNone(claimed)
        session.execute.assert_not_called()

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
            execution_token=uuid.uuid4(),
        )
        pending_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="pending",
            priority=2,
            dedupe_key=dedupe_key,
            scheduled_for=datetime(2026, 3, 20, 2, 0, tzinfo=timezone.utc),
            execution_token=uuid.uuid4(),
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
        self.assertIsNone(pending_job.execution_token)

        self.assertEqual(current_job.status, "failed")
        self.assertEqual(current_job.finished_at, now)
        self.assertIsNone(current_job.lease_expires_at)
        self.assertIsNone(current_job.worker_id)
        self.assertIsNone(current_job.execution_token)
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
            type="playlist.build_event_map_snapshot",
            status="running",
            attempt=0,
            max_attempts=5,
            worker_id="worker-1",
            lease_expires_at=datetime(2026, 3, 20, 0, 22, tzinfo=timezone.utc),
            progress_current=1,
            progress_total=2,
            execution_token=uuid.uuid4(),
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
        self.assertIsNone(job.execution_token)
        self.assertIsNone(job.lease_expires_at)
        self.assertEqual(job.finished_at, now)
        job_log.assert_called_once()


class WorkerRecoveryMergeTests(unittest.TestCase):
    def test_requeue_orphan_running_jobs_checks_execution_heartbeat_for_provider_jobs(self) -> None:
        captured_sql: list[str] = []
        captured_params: list[dict] = []
        session = Mock()

        def _execute(stmt):
            compiled = stmt.compile(dialect=postgresql.dialect())
            captured_sql.append(str(compiled))
            captured_params.append(dict(compiled.params))
            return _scalars_all([])

        session.execute.side_effect = _execute

        claim.requeue_orphan_running_jobs(session, stale_after_seconds=60, priority_bump=1000)

        self.assertTrue(captured_sql)
        self.assertIn("worker_heartbeat.active_at", captured_sql[0])
        self.assertIn(
            "video.download.youtube",
            set(captured_params[0].get("type_1") or []),
        )
        self.assertIn(
            "media.sync_videos",
            set(captured_params[0].get("type_1") or []),
        )

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
            execution_token=uuid.uuid4(),
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
        self.assertIsNone(running_job.execution_token)

    def test_requeue_expired_event_map_job_finalizes_only_the_old_execution_snapshot(self) -> None:
        execution_token = uuid.uuid4()
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)
        running_job = Job(
            id=uuid.uuid4(),
            type="playlist.build_event_map_snapshot",
            status="running",
            worker_id="worker-dead",
            lease_expires_at=now - timedelta(seconds=1),
            execution_token=execution_token,
            scheduled_for=now - timedelta(hours=1),
        )
        session = Mock()
        session.execute.side_effect = [
            _scalars_all([running_job]),
            Mock(rowcount=1),
        ]

        with patch("raelyn.jobs.claim.utcnow", return_value=now):
            count = claim.requeue_expired_running_jobs(session)

        self.assertEqual(count, 1)
        claim_query = session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
        self.assertIn("FOR UPDATE SKIP LOCKED", str(claim_query))
        snapshot_update = session.execute.call_args_list[1].args[0]
        compiled = snapshot_update.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.assertIn("event_map_snapshot.job_id", sql)
        self.assertIn("event_map_snapshot.execution_token", sql)
        self.assertIn("event_map_snapshot.status", sql)
        self.assertIn(running_job.id, compiled.params.values())
        self.assertIn(execution_token, compiled.params.values())
        self.assertEqual(running_job.status, "pending")
        self.assertIsNone(running_job.execution_token)

    def test_requeue_orphan_running_job_merges_into_existing_pending_job(self) -> None:
        dedupe_key = f"brief:{uuid.uuid4()}:2026-03-20"
        running_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            priority=7,
            dedupe_key=dedupe_key,
            worker_id="worker-dead",
            execution_token=uuid.uuid4(),
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
        self.assertIsNone(running_job.execution_token)

    def test_requeue_orphan_event_map_job_finalizes_only_the_old_execution_snapshot(self) -> None:
        execution_token = uuid.uuid4()
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)
        running_job = Job(
            id=uuid.uuid4(),
            type="playlist.build_event_map_snapshot",
            status="running",
            priority=7,
            worker_id="worker-dead",
            execution_token=execution_token,
            scheduled_for=now - timedelta(hours=1),
        )
        session = Mock()
        session.execute.side_effect = [
            _scalars_all([running_job]),
            _scalar_one_or_none(50),
            Mock(rowcount=1),
        ]

        with patch("raelyn.jobs.claim.utcnow", return_value=now):
            count = claim.requeue_orphan_running_jobs(
                session,
                stale_after_seconds=60,
                priority_bump=1000,
            )

        self.assertEqual(count, 1)
        snapshot_update = session.execute.call_args_list[2].args[0]
        compiled = snapshot_update.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.assertIn("event_map_snapshot.job_id", sql)
        self.assertIn("event_map_snapshot.execution_token", sql)
        self.assertIn("event_map_snapshot.status", sql)
        self.assertIn(running_job.id, compiled.params.values())
        self.assertIn(execution_token, compiled.params.values())
        self.assertEqual(running_job.status, "pending")
        self.assertIsNone(running_job.execution_token)

    def test_execution_stale_sync_job_retries_without_priority_bump(self) -> None:
        job_id = uuid.uuid4()
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)
        job = Job(
            id=job_id,
            type="media.sync_videos",
            status="running",
            priority=6006,
            attempt=0,
            max_attempts=2,
            worker_id="worker-sync",
            scheduled_for=now - timedelta(hours=1),
            started_at=now - timedelta(minutes=10),
            lease_expires_at=now + timedelta(minutes=50),
            progress_current=1,
            progress_total=2,
        )
        heartbeat = WorkerHeartbeat(
            worker_id="worker-sync",
            role="sync",
            updated_at=now - timedelta(seconds=5),
            active_at=now - timedelta(seconds=121),
            current_job_id=job_id,
        )
        session = Mock()
        session.execute.return_value = _scalars_all([job])
        session.get.side_effect = lambda model, key: heartbeat if model is WorkerHeartbeat else None

        with patch("raelyn.jobs.claim.utcnow", return_value=now):
            count = claim.requeue_orphan_running_jobs(
                session,
                stale_after_seconds=20,
                execution_stale_after_seconds=120,
                priority_bump=1000,
            )

        self.assertEqual(count, 1)
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.attempt, 1)
        self.assertEqual(job.priority, 6006)
        self.assertEqual(job.scheduled_for, now + timedelta(seconds=10))
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.finished_at)
        self.assertIsNone(job.worker_id)
        self.assertIsNone(job.lease_expires_at)
        self.assertIsNone(job.progress_current)
        self.assertIsNone(job.progress_total)
        self.assertIn("sync execution heartbeat stale", job.error_message or "")
        self.assertEqual(session.execute.call_count, 1)

    def test_execution_stale_sync_job_terminal_failure_updates_media_cooldown(self) -> None:
        media_id = uuid.uuid4()
        job_id = uuid.uuid4()
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)
        previous_sync = now - timedelta(days=1)
        job = Job(
            id=job_id,
            type="media.sync_videos",
            status="running",
            priority=6006,
            attempt=1,
            max_attempts=2,
            worker_id="worker-sync",
            params={"media_id": str(media_id)},
            scheduled_for=now - timedelta(hours=1),
            started_at=now - timedelta(minutes=10),
            lease_expires_at=now + timedelta(minutes=50),
        )
        heartbeat = WorkerHeartbeat(
            worker_id="worker-sync",
            role="sync",
            updated_at=now - timedelta(seconds=5),
            active_at=now - timedelta(seconds=121),
            current_job_id=job_id,
        )
        media = Media(
            id=media_id,
            provider="youtube",
            provider_media_id="markets",
            url="https://www.youtube.com/@markets",
            monitor_enabled=True,
        )
        media.last_video_sync_at = previous_sync
        session = Mock()
        session.execute.return_value = _scalars_all([job])

        def _get(model, key):
            if model is WorkerHeartbeat:
                return heartbeat
            if model is Media:
                return media
            return None

        session.get.side_effect = _get

        with patch("raelyn.jobs.claim.utcnow", return_value=now):
            count = claim.requeue_orphan_running_jobs(
                session,
                stale_after_seconds=20,
                execution_stale_after_seconds=120,
                priority_bump=1000,
            )

        self.assertEqual(count, 1)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempt, 2)
        self.assertEqual(job.priority, 6006)
        self.assertEqual(job.finished_at, now)
        self.assertIsNone(job.worker_id)
        self.assertIsNone(job.lease_expires_at)
        self.assertEqual(media.last_video_sync_at, now)
        self.assertIn("sync execution heartbeat stale", job.error_message or "")
        self.assertEqual(session.execute.call_count, 1)

    def test_process_stale_sync_job_still_requeues_with_priority_bump(self) -> None:
        job_id = uuid.uuid4()
        now = datetime(2026, 3, 20, 0, 22, 11, tzinfo=timezone.utc)
        job = Job(
            id=job_id,
            type="media.sync_videos",
            status="running",
            priority=7,
            attempt=0,
            max_attempts=2,
            worker_id="worker-sync",
            scheduled_for=now - timedelta(hours=1),
            started_at=now - timedelta(minutes=10),
            lease_expires_at=now + timedelta(minutes=50),
        )
        heartbeat = WorkerHeartbeat(
            worker_id="worker-sync",
            role="sync",
            updated_at=now - timedelta(seconds=61),
            active_at=now - timedelta(seconds=121),
            current_job_id=job_id,
        )
        session = Mock()
        session.execute.side_effect = [
            _scalars_all([job]),
            _scalar_one_or_none(50),
            _scalar_one_or_none(None),
        ]
        session.get.side_effect = lambda model, key: heartbeat if model is WorkerHeartbeat else None

        with patch("raelyn.jobs.claim.utcnow", return_value=now):
            count = claim.requeue_orphan_running_jobs(session, stale_after_seconds=60, priority_bump=1000)

        self.assertEqual(count, 1)
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.priority, 1050)
        self.assertEqual(job.scheduled_for, now)
        self.assertEqual(job.attempt, 0)
        self.assertIsNone(job.worker_id)
        self.assertIsNone(job.lease_expires_at)


class WorkerHeartbeatTests(unittest.TestCase):
    def test_lock_owned_running_job_includes_execution_token_in_owner_predicate(self) -> None:
        execution_token = uuid.uuid4()
        job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            worker_id="worker-1",
            execution_token=execution_token,
        )
        session = Mock()
        session.no_autoflush = nullcontext()
        session.execute.return_value.one_or_none.return_value = (job, None)

        owned_job, cancel_requested = worker._lock_owned_running_job(
            session,
            job_id=job.id,
            worker_id="worker-1",
            execution_token=execution_token,
        )

        statement = session.execute.call_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIs(owned_job, job)
        self.assertFalse(cancel_requested)
        self.assertIn("job.execution_token", str(compiled))
        self.assertIn("FOR UPDATE", str(compiled))
        self.assertIn(execution_token, compiled.params.values())

        missing_token_session = Mock()
        missing_token_session.no_autoflush = nullcontext()
        self.assertEqual(
            worker._lock_owned_running_job(
                missing_token_session,
                job_id=job.id,
                worker_id="worker-1",
                execution_token=None,
            ),
            (None, False),
        )
        missing_token_session.execute.assert_not_called()

    def test_execution_stale_reason_exits_even_after_job_requeued(self) -> None:
        worker_id = "host:1234:abcd"
        job_id = uuid.uuid4()
        now = datetime(2026, 3, 20, 1, 2, 3, tzinfo=timezone.utc)
        hb = Mock(current_job_id=job_id, active_at=now - timedelta(seconds=121))
        job = Job(id=job_id, type="video.download.youtube", status="pending")
        session = Mock()
        session.get.side_effect = lambda model, key: hb if model.__name__ == "WorkerHeartbeat" else job

        with patch("raelyn.worker.utcnow", return_value=now):
            reason = worker._execution_stale_reason(
                session,
                worker_id=worker_id,
                stale_after_seconds=120,
            )

        self.assertIsNotNone(reason)
        self.assertIn(str(job_id), reason or "")
        self.assertIn("job_status=pending", reason or "")

    def test_execution_stale_reason_exits_for_stale_sync_activity(self) -> None:
        worker_id = "host:1234:abcd"
        job_id = uuid.uuid4()
        now = datetime(2026, 3, 20, 1, 2, 3, tzinfo=timezone.utc)
        hb = Mock(current_job_id=job_id, active_at=now - timedelta(seconds=121))
        job = Job(id=job_id, type="media.sync_videos", status="running")
        session = Mock()
        session.get.side_effect = lambda model, key: hb if model.__name__ == "WorkerHeartbeat" else job

        with patch("raelyn.worker.utcnow", return_value=now):
            reason = worker._execution_stale_reason(
                session,
                worker_id=worker_id,
                stale_after_seconds=120,
            )

        self.assertIsNotNone(reason)
        self.assertIn("media.sync_videos", reason or "")

    def test_execution_stale_reason_ignores_fresh_download_activity(self) -> None:
        worker_id = "host:1234:abcd"
        job_id = uuid.uuid4()
        now = datetime(2026, 3, 20, 1, 2, 3, tzinfo=timezone.utc)
        hb = Mock(current_job_id=job_id, active_at=now - timedelta(seconds=30))
        job = Job(id=job_id, type="video.download.youtube", status="running")
        session = Mock()
        session.get.side_effect = lambda model, key: hb if model.__name__ == "WorkerHeartbeat" else job

        with patch("raelyn.worker.utcnow", return_value=now):
            reason = worker._execution_stale_reason(
                session,
                worker_id=worker_id,
                stale_after_seconds=120,
            )

        self.assertIsNone(reason)

    def test_worker_needs_execution_watchdog_matches_provider_roles(self) -> None:
        self.assertTrue(worker._worker_needs_execution_watchdog(None))
        self.assertTrue(worker._worker_needs_execution_watchdog(["video.download.youtube"]))
        self.assertTrue(worker._worker_needs_execution_watchdog(["media.sync_videos"]))
        self.assertFalse(worker._worker_needs_execution_watchdog(["video.asr_transcribe"]))

    def test_touch_worker_heartbeat_can_update_execution_activity(self) -> None:
        worker_id = "host:1234:abcd"
        job_id = uuid.uuid4()
        hb = Mock()
        session = Mock()
        session.get.return_value = hb
        now = datetime(2026, 3, 20, 1, 2, 3, tzinfo=timezone.utc)

        with patch("raelyn.jobs.heartbeat.utcnow", return_value=now):
            touch_worker_heartbeat(
                session,
                worker_id=worker_id,
                role="download_youtube",
                active=True,
                current_job_id=job_id,
            )

        self.assertEqual(hb.updated_at, now)
        self.assertEqual(hb.active_at, now)
        self.assertEqual(hb.role, "download_youtube")
        self.assertEqual(hb.current_job_id, job_id)

    def test_set_job_progress_marks_current_worker_activity(self) -> None:
        job_id = uuid.uuid4()
        execution_token = uuid.uuid4()
        with patch("raelyn.jobs.progress.engine") as engine:
            engine.begin.return_value.__enter__.return_value.execute.return_value.rowcount = 1
            with patch("raelyn.jobs.progress.touch_current_worker_activity") as touch_activity:
                updated = progress.set_job_progress(
                    job_id=job_id,
                    worker_id="worker-1",
                    execution_token=execution_token,
                    current=1,
                    total=10,
                )

        self.assertTrue(updated)
        self.assertTrue(engine.begin.called)
        touch_activity.assert_called_once()

    def test_set_job_progress_falls_back_to_job_worker_activity(self) -> None:
        job_id = uuid.uuid4()
        execution_token = uuid.uuid4()
        with patch("raelyn.jobs.progress.engine") as engine:
            engine.begin.return_value.__enter__.return_value.execute.return_value.rowcount = 1
            with patch("raelyn.jobs.progress.touch_current_worker_activity", return_value=False):
                with patch("raelyn.jobs.progress.touch_worker_activity_for_job") as touch_for_job:
                    updated = progress.set_job_progress(
                        job_id=job_id,
                        worker_id="worker-1",
                        execution_token=execution_token,
                        current=1,
                        total=10,
                    )

        self.assertTrue(updated)
        touch_for_job.assert_called_once_with(job_id=job_id)


class WorkerDownloadFailureStateTests(unittest.TestCase):
    @staticmethod
    def _terminal_failure_session(video: Video, *, asset_id: uuid.UUID | None = None) -> Mock:
        session = Mock()
        session.execute.side_effect = [
            _scalar_one_or_none(video),
            _scalar_one_or_none(asset_id),
        ]
        return session

    def test_mark_download_video_terminal_failure_updates_rolled_back_discovered_video(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=abc123",
            status="discovered",
            error_message=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            status="failed",
            params={"video_id": str(video.id)},
            error_message="ERROR: unable to download video data: HTTP Error 403: Forbidden",
        )
        session = self._terminal_failure_session(video)

        worker._mark_download_video_terminal_failure(session, job=job)

        self.assertEqual(video.status, "failed")
        self.assertEqual(video.error_message, job.error_message)

        video_query = session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
        self.assertIn("FOR UPDATE", str(video_query))
        self.assertIn(video.id, video_query.params.values())

        asset_query = session.execute.call_args_list[1].args[0].compile(dialect=postgresql.dialect())
        self.assertIn("asset.video_id", str(asset_query))
        self.assertIn("asset.type", str(asset_query))
        self.assertIn(video.id, asset_query.params.values())
        self.assertIn("video", asset_query.params.values())

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
            error_message="new error",
        )
        session = self._terminal_failure_session(video)

        worker._mark_download_video_terminal_failure(session, job=job)

        self.assertEqual(video.status, "failed")
        self.assertEqual(video.error_message, "new error")

    def test_mark_download_video_terminal_failure_preserves_status_when_video_asset_exists(self) -> None:
        for status in ("discovered", "downloading"):
            with self.subTest(status=status):
                video = Video(
                    id=uuid.uuid4(),
                    provider="youtube",
                    provider_video_id=f"abc-{status}",
                    media_id=uuid.uuid4(),
                    url=f"https://www.youtube.com/watch?v=abc-{status}",
                    status=status,
                    error_message="old error",
                )
                job = Job(
                    id=uuid.uuid4(),
                    type="video.download.youtube",
                    status="failed",
                    params={"video_id": str(video.id)},
                    error_message="new error",
                )
                session = self._terminal_failure_session(video, asset_id=uuid.uuid4())

                worker._mark_download_video_terminal_failure(session, job=job)

                self.assertEqual(video.status, status)
                self.assertEqual(video.error_message, "new error")

    def test_mark_download_video_terminal_failure_preserves_usable_statuses(self) -> None:
        for status in ("ready", "downloaded", "members_only"):
            with self.subTest(status=status):
                video = Video(
                    id=uuid.uuid4(),
                    provider="youtube",
                    provider_video_id=f"abc-{status}",
                    media_id=uuid.uuid4(),
                    url=f"https://www.youtube.com/watch?v=abc-{status}",
                    status=status,
                    error_message="old error",
                )
                job = Job(
                    id=uuid.uuid4(),
                    type="video.download.youtube",
                    status="failed",
                    params={"video_id": str(video.id)},
                    error_message="new error",
                )
                session = self._terminal_failure_session(video)

                worker._mark_download_video_terminal_failure(session, job=job)

                self.assertEqual(video.status, status)
                self.assertEqual(video.error_message, "new error")

    def test_mark_download_video_terminal_failure_is_idempotent(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=abc123",
            status="discovered",
            error_message=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            status="failed",
            params={"video_id": str(video.id)},
            error_message="download exhausted",
        )
        session = Mock()
        session.execute.side_effect = [
            _scalar_one_or_none(video),
            _scalar_one_or_none(None),
            _scalar_one_or_none(video),
            _scalar_one_or_none(None),
        ]

        worker._mark_download_video_terminal_failure(session, job=job)
        worker._mark_download_video_terminal_failure(session, job=job)

        self.assertEqual(video.status, "failed")
        self.assertEqual(video.error_message, "download exhausted")
        self.assertEqual(session.execute.call_count, 4)

    def test_update_download_retry_params_keeps_cookies_for_403_retry(self) -> None:
        job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            params={"video_id": "video-1"},
            error_message="ERROR: unable to download video data: HTTP Error 403: Forbidden",
        )

        worker._update_download_retry_params(job)

        self.assertNotIn(YTDLP_RETRY_WITHOUT_COOKIES_PARAM, job.params)

    def test_update_download_retry_params_clears_legacy_cookie_bypass(self) -> None:
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
