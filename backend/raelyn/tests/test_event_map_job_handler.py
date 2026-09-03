from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import MagicMock, patch


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers import event_analysis as event_map_jobs
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import EventMapState, Job, Playlist


class EventMapBuildJobTests(unittest.TestCase):
    def _job(self, playlist_id: uuid.UUID) -> Job:
        return Job(
            id=uuid.uuid4(),
            type="playlist.build_event_map_snapshot",
            status="running",
            params={
                "playlist_id": str(playlist_id),
                "trigger": "dirty",
                "requested_generation": 4,
            },
        )

    def test_current_generation_skips_build_and_clears_active_owner(self) -> None:
        playlist_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        job = self._job(playlist_id)
        state = EventMapState(
            playlist_id=playlist_id,
            active_job_id=job.id,
            dirty_generation=4,
            built_generation=4,
        )
        session = MagicMock()
        session.get.return_value = playlist

        with patch.object(event_map_jobs, "_locked_event_map_state", return_value=state):
            with patch.object(event_map_jobs, "build_event_map_snapshot") as build:
                result = event_map_jobs.playlist_build_event_map_snapshot(session, job)

        self.assertEqual(result["outcome"], "skipped_generation_current")
        self.assertEqual(result["requested_generation"], 4)
        self.assertIsNone(state.active_job_id)
        build.assert_not_called()

    def test_disabled_observation_skips_queued_build_and_clears_active_owner(self) -> None:
        playlist_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p", observation_enabled=False)
        job = self._job(playlist_id)
        state = EventMapState(
            playlist_id=playlist_id,
            active_job_id=job.id,
            dirty_generation=5,
            built_generation=4,
        )
        session = MagicMock()
        session.get.return_value = playlist

        with patch.object(event_map_jobs, "_locked_event_map_state", return_value=state):
            with patch.object(event_map_jobs, "build_event_map_snapshot") as build:
                result = event_map_jobs.playlist_build_event_map_snapshot(session, job)

        self.assertEqual(result["outcome"], "observation_disabled")
        self.assertIsNone(state.active_job_id)
        build.assert_not_called()

    def test_quiet_period_is_superseded_when_another_build_is_pending(self) -> None:
        now = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
        playlist_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        job = self._job(playlist_id)
        pending_job_id = uuid.uuid4()
        state = EventMapState(
            playlist_id=playlist_id,
            active_job_id=job.id,
            dirty_generation=5,
            built_generation=4,
            first_dirty_at=now - timedelta(seconds=30),
            last_dirty_at=now,
        )
        session = MagicMock()
        session.get.return_value = playlist

        with patch.object(event_map_jobs, "_locked_event_map_state", return_value=state):
            with patch.object(event_map_jobs, "utcnow", return_value=now):
                with patch.object(
                    event_map_jobs,
                    "_pending_event_map_build_id",
                    return_value=pending_job_id,
                ) as pending:
                    with patch.object(event_map_jobs, "build_event_map_snapshot") as build:
                        result = event_map_jobs.playlist_build_event_map_snapshot(session, job)

        self.assertEqual(result["outcome"], "superseded_by_pending_build")
        self.assertEqual(result["pending_job_id"], str(pending_job_id))
        self.assertEqual(state.active_job_id, pending_job_id)
        pending.assert_called_once_with(
            session,
            playlist_id=playlist_id,
            exclude_job_id=job.id,
        )
        build.assert_not_called()

    def test_quiet_period_reschedules_when_no_other_build_is_pending(self) -> None:
        now = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
        playlist_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        job = self._job(playlist_id)
        state = EventMapState(
            playlist_id=playlist_id,
            active_job_id=job.id,
            dirty_generation=5,
            built_generation=4,
            first_dirty_at=now,
            last_dirty_at=now,
        )
        session = MagicMock()
        session.get.return_value = playlist

        with patch.object(event_map_jobs, "_locked_event_map_state", return_value=state):
            with patch.object(event_map_jobs, "utcnow", return_value=now):
                with patch.object(event_map_jobs, "_pending_event_map_build_id", return_value=None):
                    with patch.object(event_map_jobs, "build_event_map_snapshot") as build:
                        with self.assertRaises(JobReschedule) as raised:
                            event_map_jobs.playlist_build_event_map_snapshot(session, job)

        self.assertEqual(raised.exception.delay_seconds, 120)
        self.assertEqual(raised.exception.reason, "event_map_waiting_for_quiet_period")
        self.assertEqual(state.active_job_id, job.id)
        build.assert_not_called()

    def test_active_dirty_job_supersedes_build_without_enqueuing_another_build(self) -> None:
        now = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
        playlist_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        job = self._job(playlist_id)
        dirty_job_id = uuid.uuid4()
        state = EventMapState(
            playlist_id=playlist_id,
            active_job_id=job.id,
            dirty_generation=5,
            built_generation=4,
            first_dirty_at=now - timedelta(minutes=20),
            last_dirty_at=now - timedelta(minutes=20),
        )
        session = MagicMock()
        session.get.return_value = playlist

        with patch.object(event_map_jobs, "_locked_event_map_state", return_value=state):
            with patch.object(
                event_map_jobs,
                "build_event_map_snapshot",
                return_value={
                    "ok": False,
                    "outcome": "superseded_by_active_dirty",
                    "active_dirty_job_id": str(dirty_job_id),
                },
            ):
                with patch.object(event_map_jobs, "_enqueue_dirty_event_map_build") as enqueue:
                    result = event_map_jobs.playlist_build_event_map_snapshot(session, job)

        self.assertEqual(result["outcome"], "superseded_by_active_dirty")
        self.assertIsNone(state.active_job_id)
        enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
