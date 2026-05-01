from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
