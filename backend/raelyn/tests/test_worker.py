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
from raelyn.models import Job
from raelyn import worker


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class WorkerRetryMergeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
