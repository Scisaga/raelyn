from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api import workers as workers_api
from raelyn.jobs.claim import claim_next_job
from raelyn.models import AppConfig, WorkerHeartbeat
from raelyn.services.worker_role_pause import (
    clear_worker_role_pause,
    get_worker_role_pause,
    set_worker_role_paused,
)


@contextmanager
def _fake_session_scope(session):
    yield session


class _FakeConfigSession:
    def __init__(self, *, config_items: dict[str, AppConfig] | None = None, worker_rows: list[WorkerHeartbeat] | None = None) -> None:
        self.config_items = dict(config_items or {})
        self.worker_rows = list(worker_rows or [])
        self.added: list[object] = []

    def get(self, _model, key):
        return self.config_items.get(key)

    def add(self, obj) -> None:
        self.added.append(obj)
        if isinstance(obj, AppConfig):
            self.config_items[obj.key] = obj

    def flush(self) -> None:
        return None

    def execute(self, _stmt):
        return Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=list(self.worker_rows)))))


class WorkerRolePauseStateTests(unittest.TestCase):
    def test_set_and_clear_worker_role_pause(self) -> None:
        session = _FakeConfigSession()

        pause = set_worker_role_paused(
            session,
            role="sync",
            reason="manual_worker_role_pause",
            message="已暂停 sync worker 领取新任务；运行中任务不受影响。",
        )

        self.assertTrue(pause["paused"])
        self.assertEqual(pause["reason"], "manual_worker_role_pause")
        self.assertEqual(
            session.config_items["worker_role_pause"].value["sync"]["message"],
            "已暂停 sync worker 领取新任务；运行中任务不受影响。",
        )

        cleared = clear_worker_role_pause(session, role="sync")

        self.assertEqual(cleared, {"paused": False, "reason": None, "message": None, "set_at": None})
        self.assertEqual(session.config_items["worker_role_pause"].value, {})

    def test_set_worker_role_pause_is_idempotent_for_same_payload(self) -> None:
        item = AppConfig(
            key="worker_role_pause",
            value={
                "sync": {
                    "paused": True,
                    "reason": "manual_worker_role_pause",
                    "message": "同一条暂停消息",
                    "set_at": "2026-03-28T00:00:00+00:00",
                }
            },
        )
        item.updated_at = "unchanged"
        session = _FakeConfigSession(config_items={"worker_role_pause": item})

        pause = set_worker_role_paused(
            session,
            role="sync",
            reason="manual_worker_role_pause",
            message="同一条暂停消息",
        )

        self.assertTrue(pause["paused"])
        self.assertEqual(item.updated_at, "unchanged")
        self.assertEqual(session.added, [])

    def test_invalid_worker_role_is_rejected(self) -> None:
        session = _FakeConfigSession()

        with self.assertRaises(ValueError):
            set_worker_role_paused(session, role="all", reason="x", message="y")

        self.assertEqual(
            get_worker_role_pause(session, "all"),
            {"paused": False, "reason": None, "message": None, "set_at": None},
        )


class ClaimNextJobWorkerRolePauseTests(unittest.TestCase):
    def test_claim_next_job_skips_paused_worker_role(self) -> None:
        now = datetime.now(timezone.utc)
        sync_job = SimpleNamespace(
            type="media.sync_videos",
            status="pending",
            worker_id=None,
            error_message="old",
            error_stack="old",
            progress_current=1,
            progress_total=2,
            started_at=None,
            lease_expires_at=None,
            priority=10,
            scheduled_for=now,
            created_at=now,
            id="sync-job",
            params={"media_id": "media-1"},
        )
        asr_job = SimpleNamespace(
            type="video.asr_transcribe",
            status="pending",
            worker_id=None,
            error_message="old",
            error_stack="old",
            progress_current=3,
            progress_total=4,
            started_at=None,
            lease_expires_at=None,
            priority=9,
            scheduled_for=now,
            created_at=now,
            id="asr-job",
            params={"video_id": "video-1"},
        )
        scalar_result = Mock()
        scalar_result.all.return_value = [sync_job, asr_job]
        session = Mock()
        session.execute.return_value = Mock(scalars=Mock(return_value=scalar_result))

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.job_provider", return_value=None):
                with patch("raelyn.jobs.claim.is_provider_paused", return_value=False):
                    with patch("raelyn.jobs.claim.is_worker_role_paused", side_effect=lambda _session, role: role == "sync"):
                        claimed = claim_next_job(session, worker_id="worker-1", lease_seconds=60)

        self.assertIs(claimed, asr_job)
        self.assertEqual(asr_job.status, "running")
        self.assertEqual(asr_job.worker_id, "worker-1")
        self.assertIsNone(asr_job.error_message)
        self.assertIsNone(asr_job.error_stack)
        self.assertIsNone(asr_job.progress_current)
        self.assertIsNone(asr_job.progress_total)

    def test_claim_next_job_all_worker_still_respects_paused_role(self) -> None:
        now = datetime.now(timezone.utc)
        yt_job = SimpleNamespace(
            type="video.download.youtube",
            status="pending",
            worker_id=None,
            error_message="old",
            error_stack="old",
            progress_current=1,
            progress_total=2,
            started_at=None,
            lease_expires_at=None,
            priority=10,
            scheduled_for=now,
            created_at=now,
            id="yt-job",
            params={"video_id": "video-1"},
        )
        scalar_result = Mock()
        scalar_result.all.return_value = [yt_job]
        session = Mock()
        session.execute.return_value = Mock(scalars=Mock(return_value=scalar_result))

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.job_provider", return_value="youtube"):
                with patch("raelyn.jobs.claim.is_provider_paused", return_value=False):
                    with patch("raelyn.jobs.claim.is_worker_role_paused", return_value=True):
                        claimed = claim_next_job(session, worker_id="worker-all", lease_seconds=60, type_in=None)

        self.assertIsNone(claimed)

    def test_claim_next_job_skips_capacity_deferred_type(self) -> None:
        now = datetime.now(timezone.utc)
        asr_job = SimpleNamespace(
            type="video.asr_transcribe",
            status="pending",
            worker_id=None,
            error_message="old",
            error_stack="old",
            progress_current=1,
            progress_total=2,
            started_at=None,
            lease_expires_at=None,
            priority=10,
            scheduled_for=now,
            created_at=now,
            id="asr-job",
            params={"video_id": "video-1"},
        )
        sync_job = SimpleNamespace(
            type="media.sync_videos",
            status="pending",
            worker_id=None,
            error_message="old",
            error_stack="old",
            progress_current=3,
            progress_total=4,
            started_at=None,
            lease_expires_at=None,
            priority=9,
            scheduled_for=now,
            created_at=now,
            id="sync-job",
            params={"media_id": "media-1"},
        )
        scalar_result = Mock()
        scalar_result.all.return_value = [asr_job, sync_job]
        session = Mock()
        session.execute.return_value = Mock(scalars=Mock(return_value=scalar_result))

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.job_provider", return_value=None):
                with patch("raelyn.jobs.claim.is_provider_paused", return_value=False):
                    with patch("raelyn.jobs.claim.is_worker_role_paused", return_value=False):
                        claimed = claim_next_job(
                            session,
                            worker_id="worker-1",
                            lease_seconds=60,
                            skip_type_in={"video.asr_transcribe"},
                        )

        self.assertIs(claimed, sync_job)
        self.assertEqual(asr_job.status, "pending")
        self.assertEqual(sync_job.status, "running")


class WorkersApiTests(unittest.TestCase):
    def test_list_workers_includes_known_roles_and_pause_fields(self) -> None:
        session = _FakeConfigSession(
            config_items={
                "worker_role_pause": AppConfig(
                    key="worker_role_pause",
                    value={
                        "sync": {
                            "paused": True,
                            "reason": "manual_worker_role_pause",
                            "message": "已暂停 sync worker 领取新任务；运行中任务不受影响。",
                            "set_at": "2026-03-28T00:00:00+00:00",
                        }
                    },
                )
            }
        )

        with patch("raelyn.api.workers.session_scope", lambda: _fake_session_scope(session)):
            payload = workers_api.list_workers()

        by_role = {item["role"]: item for item in payload["roles"]}
        self.assertIn("sync", by_role)
        self.assertIn("all", by_role)
        self.assertTrue(by_role["sync"]["paused"])
        self.assertEqual(by_role["sync"]["pause_reason"], "manual_worker_role_pause")
        self.assertTrue(by_role["sync"]["controllable"])
        self.assertFalse(by_role["all"]["paused"])
        self.assertFalse(by_role["all"]["controllable"])

    def test_pause_and_resume_worker_role_api(self) -> None:
        session = _FakeConfigSession()

        with patch("raelyn.api.workers.session_scope", lambda: _fake_session_scope(session)):
            paused = workers_api.pause_worker_role(
                "sync",
                workers_api.WorkerRolePauseRequest(
                    reason="manual_worker_role_pause",
                    message="已暂停 sync worker 领取新任务；运行中任务不受影响。",
                ),
            )
            resumed = workers_api.resume_worker_role("sync")

        self.assertEqual(paused["role"], "sync")
        self.assertTrue(paused["pause"]["paused"])
        self.assertEqual(resumed, {"ok": True, "role": "sync", "pause": {"paused": False, "reason": None, "message": None, "set_at": None}})

    def test_pause_worker_role_api_rejects_all_and_unknown(self) -> None:
        with self.assertRaises(HTTPException) as all_ctx:
            workers_api.pause_worker_role("all", workers_api.WorkerRolePauseRequest())

        with self.assertRaises(HTTPException) as unknown_ctx:
            workers_api.resume_worker_role("mystery")

        self.assertEqual(all_ctx.exception.status_code, 400)
        self.assertEqual(unknown_ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
