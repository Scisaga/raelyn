from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn import scheduler
from raelyn.models import Media


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _FakeSession:
    def __init__(self, medias):
        self.medias = medias

    def execute(self, _stmt):
        return _FakeResult(self.medias)


@contextmanager
def _session_scope(session):
    yield session


class SchedulerSyncJitterTests(unittest.TestCase):
    def test_unsynced_media_is_due_immediately(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://www.youtube.com/@channel-1/videos",
            monitor_enabled=True,
        )
        media.last_video_sync_at = None

        self.assertTrue(scheduler._is_sync_due(media, datetime.now(timezone.utc)))

    def test_jitter_is_stable_and_within_configured_range(self) -> None:
        media_id = uuid.uuid4()
        last_sync = datetime(2026, 4, 26, 1, 0, tzinfo=timezone.utc)

        with patch.object(scheduler.settings, "sync_interval_jitter_minutes", 15):
            first = scheduler._sync_jitter_minutes(media_id, last_sync)
            second = scheduler._sync_jitter_minutes(media_id, last_sync)

        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0)
        self.assertLessEqual(first, 15)

    def test_due_time_uses_interval_plus_stable_jitter(self) -> None:
        last_sync = datetime(2026, 4, 26, 1, 0, tzinfo=timezone.utc)
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://www.youtube.com/@channel-1/videos",
            monitor_enabled=True,
        )
        media.last_video_sync_at = last_sync

        with (
            patch.object(scheduler.settings, "sync_interval_minutes", 60),
            patch.object(scheduler.settings, "sync_interval_jitter_minutes", 15),
        ):
            jitter = scheduler._sync_jitter_minutes(media.id, last_sync)
            due_at = last_sync + timedelta(minutes=60 + jitter)
            self.assertFalse(scheduler._is_sync_due(media, due_at - timedelta(seconds=1)))
            self.assertTrue(scheduler._is_sync_due(media, due_at))

    def test_recent_cooldown_timestamp_is_not_due_immediately(self) -> None:
        now = datetime(2026, 4, 26, 1, 0, tzinfo=timezone.utc)
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://www.youtube.com/@channel-1/videos",
            monitor_enabled=True,
        )
        media.last_video_sync_at = now

        with (
            patch.object(scheduler.settings, "sync_interval_minutes", 60),
            patch.object(scheduler.settings, "sync_interval_jitter_minutes", 0),
        ):
            self.assertFalse(scheduler._is_sync_due(media, now))
            self.assertTrue(scheduler._is_sync_due(media, now + timedelta(minutes=60)))

    def test_tick_enqueues_public_discovery_when_provider_pause_allows_it(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="@markets",
            url="https://www.youtube.com/@markets/videos",
            monitor_enabled=True,
        )
        media.last_video_sync_at = None
        session = _FakeSession([media])

        with (
            patch("raelyn.scheduler.session_scope", lambda: _session_scope(session)),
            patch("raelyn.scheduler.is_paused", return_value=False),
            patch("raelyn.scheduler._has_pending_sync_job", return_value=False),
            patch("raelyn.scheduler.get_provider_pause", return_value={"paused": True, "reason": "youtube_bot_check"}),
            patch.object(scheduler.settings, "sync_public_discovery_enabled", True),
            patch.object(scheduler.settings, "sync_public_discovery_max_entries", 200),
            patch.object(scheduler.settings, "sync_batch_size", 1),
            patch("raelyn.scheduler.enqueue_job") as enqueue_job,
        ):
            enqueued = scheduler.tick()

        self.assertEqual(enqueued, 1)
        enqueue_job.assert_called_once_with(
            session,
            type_="media.sync_videos",
            params={
                "media_id": str(media.id),
                "public_discovery": True,
                "max_entries": 200,
                "download_priority": 8,
            },
            priority=1,
        )

    def test_tick_skips_public_discovery_for_bilibili_risk_control_pause(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="bilibili",
            provider_media_id="123",
            url="https://space.bilibili.com/123/video",
            monitor_enabled=True,
        )
        media.last_video_sync_at = None
        session = _FakeSession([media])

        with (
            patch("raelyn.scheduler.session_scope", lambda: _session_scope(session)),
            patch("raelyn.scheduler.is_paused", return_value=False),
            patch("raelyn.scheduler._has_pending_sync_job", return_value=False),
            patch("raelyn.scheduler.get_provider_pause", return_value={"paused": True, "reason": "bilibili_risk_control"}),
            patch.object(scheduler.settings, "sync_public_discovery_enabled", True),
            patch.object(scheduler.settings, "sync_batch_size", 1),
            patch("raelyn.scheduler.enqueue_job") as enqueue_job,
        ):
            enqueued = scheduler.tick()

        self.assertEqual(enqueued, 0)
        enqueue_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
