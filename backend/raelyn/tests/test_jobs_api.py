from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api import jobs as jobs_api
from raelyn.models import Job
from raelyn.models import JobEvent
from raelyn.services.ytdlp import YTDLP_RETRY_WITHOUT_COOKIES_PARAM


@contextmanager
def _fake_session_scope(session):
    yield session


class _FakeSession:
    def __init__(self, job: Job | None) -> None:
        self.job = job
        self.added: list[object] = []

    def get(self, _model, key):
        if self.job and key == self.job.id:
            return self.job
        return None

    def add(self, obj) -> None:
        self.added.append(obj)


class _FakeRowsResult:
    def __init__(self, rows) -> None:
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _FakeCountsSession:
    def __init__(self, rows) -> None:
        self.rows = list(rows)

    def execute(self, _stmt):
        return _FakeRowsResult(self.rows)


class JobsApiTests(unittest.TestCase):
    def test_cancel_pending_job_immediately_marks_canceled(self) -> None:
        job_id = uuid.uuid4()
        job = Job(
            id=job_id,
            type="video.download.youtube",
            status="pending",
            priority=8,
            params={"video_id": "video-1"},
        )
        session = _FakeSession(job)

        with patch("raelyn.api.jobs.session_scope", lambda: _fake_session_scope(session)):
            payload = jobs_api.cancel_job(job_id)

        self.assertEqual(payload, {"ok": True, "job_id": str(job_id), "status": "canceled"})
        self.assertEqual(job.status, "canceled")
        self.assertIsNotNone(job.cancel_requested_at)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(len(session.added), 1)
        event = session.added[0]
        self.assertEqual(event.message, "canceled")
        self.assertEqual(event.data["mode"], "immediate")

    def test_cancel_running_job_sets_cancel_requested(self) -> None:
        job_id = uuid.uuid4()
        job = Job(
            id=job_id,
            type="media.delete",
            status="running",
            priority=20,
            params={"media_id": "media-1"},
        )
        session = _FakeSession(job)

        with patch("raelyn.api.jobs.session_scope", lambda: _fake_session_scope(session)):
            payload = jobs_api.cancel_job(job_id)

        self.assertEqual(payload, {"ok": True, "job_id": str(job_id), "status": "cancel_requested"})
        self.assertEqual(job.status, "running")
        self.assertIsNotNone(job.cancel_requested_at)
        self.assertEqual(len(session.added), 1)
        event = session.added[0]
        self.assertEqual(event.message, "cancel requested")
        self.assertEqual(event.data["mode"], "cooperative")

    def test_retry_job_reuses_failed_job_and_resets_runtime_state(self) -> None:
        job_id = uuid.uuid4()
        retry_at = datetime(2026, 3, 17, 8, 0, tzinfo=timezone.utc)
        job = Job(
            id=job_id,
            type="video.download.youtube",
            status="failed",
            priority=8,
            params={"video_id": "video-1", YTDLP_RETRY_WITHOUT_COOKIES_PARAM: True},
            result={"ok": False},
            progress_current=3,
            progress_total=10,
            error_message="boom",
            error_stack="traceback",
            attempt=2,
            max_attempts=2,
            started_at=datetime(2026, 3, 17, 7, 50, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 17, 7, 59, tzinfo=timezone.utc),
            lease_expires_at=datetime(2026, 3, 17, 8, 1, tzinfo=timezone.utc),
            worker_id="worker-1",
        )
        session = _FakeSession(job)

        with patch("raelyn.api.jobs.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.jobs.utcnow", return_value=retry_at):
                payload = jobs_api.retry_job(job_id)

        self.assertEqual(payload, {"ok": True, "job_id": str(job_id), "status": "pending"})
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.attempt, 0)
        self.assertEqual(job.scheduled_for, retry_at)
        self.assertNotIn(YTDLP_RETRY_WITHOUT_COOKIES_PARAM, job.params)
        self.assertIsNone(job.result)
        self.assertIsNone(job.progress_current)
        self.assertIsNone(job.progress_total)
        self.assertIsNone(job.error_message)
        self.assertIsNone(job.error_stack)
        self.assertIsNone(job.cancel_requested_at)
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.finished_at)
        self.assertIsNone(job.lease_expires_at)
        self.assertIsNone(job.worker_id)

        self.assertEqual(len(session.added), 1)
        event = session.added[0]
        self.assertIsInstance(event, JobEvent)
        self.assertEqual(event.job_id, job_id)
        self.assertEqual(event.message, "manual retry requested")
        self.assertEqual(event.data, {"previous_status": "failed", "previous_attempt": 2})

    def test_retry_job_is_noop_for_active_job(self) -> None:
        job_id = uuid.uuid4()
        job = Job(
            id=job_id,
            type="media.sync_videos",
            status="running",
            priority=0,
            params={"media_id": "media-1"},
            attempt=1,
            max_attempts=2,
        )
        session = _FakeSession(job)

        with patch("raelyn.api.jobs.session_scope", lambda: _fake_session_scope(session)):
            payload = jobs_api.retry_job(job_id)

        self.assertEqual(payload, {"ok": True, "job_id": str(job_id), "status": "running"})
        self.assertEqual(job.status, "running")
        self.assertEqual(job.attempt, 1)
        self.assertEqual(session.added, [])

    def test_job_counts_returns_grouped_counts_and_total(self) -> None:
        session = _FakeCountsSession([("pending", 2), ("running", 3)])

        with patch("raelyn.api.jobs.session_scope", lambda: _fake_session_scope(session)):
            payload = jobs_api.job_counts(status_in="pending,running", type="video.download.youtube")

        self.assertEqual(payload.model_dump(), {"counts": {"pending": 2, "running": 3}, "total": 5})


if __name__ == "__main__":
    unittest.main()
