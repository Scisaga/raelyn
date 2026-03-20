from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Media
from raelyn.services.media_actions import schedule_all_media_sync, schedule_media_sync


class MediaActionsTests(unittest.TestCase):
    def test_schedule_media_sync_recent_sets_download_priority_8(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://www.youtube.com/@channel-1/videos",
            monitor_enabled=False,
        )
        session = Mock()
        session.get.return_value = media
        profile_job_id = uuid.uuid4()
        videos_job_id = uuid.uuid4()

        with patch("raelyn.services.media_actions.ensure_media_not_deleting"):
            with patch("raelyn.services.media_actions.enqueue_job", side_effect=[profile_job_id, videos_job_id]) as enqueue_job:
                result = schedule_media_sync(session, media.id, scope="recent")

        self.assertEqual(result["scope"], "recent")
        self.assertEqual(result["job_id"], str(videos_job_id))
        self.assertEqual(enqueue_job.call_count, 2)
        self.assertEqual(enqueue_job.call_args_list[1].kwargs["params"]["download_priority"], 8)

    def test_schedule_media_sync_all_does_not_override_download_priority(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://www.youtube.com/@channel-1/videos",
            monitor_enabled=False,
        )
        session = Mock()
        session.get.return_value = media

        with patch("raelyn.services.media_actions.ensure_media_not_deleting"):
            with patch("raelyn.services.media_actions.enqueue_job", side_effect=[uuid.uuid4(), uuid.uuid4()]) as enqueue_job:
                schedule_media_sync(session, media.id, scope="all")

        self.assertNotIn("download_priority", enqueue_job.call_args_list[1].kwargs["params"])
        self.assertTrue(enqueue_job.call_args_list[1].kwargs["params"]["enqueue_existing_downloads"])

    def test_schedule_all_media_sync_recent_sets_download_priority_8(self) -> None:
        media_ids = [uuid.uuid4(), uuid.uuid4()]
        session = Mock()
        session.execute.return_value = Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=media_ids))))

        with patch("raelyn.services.media_actions.active_media_delete_job_map", return_value={}):
            with patch("raelyn.services.media_actions.enqueue_job") as enqueue_job:
                result = schedule_all_media_sync(session, scope="recent")

        self.assertEqual(result["scope"], "recent")
        self.assertEqual(result["count"], 2)
        self.assertEqual(enqueue_job.call_count, 4)
        video_calls = [call for call in enqueue_job.call_args_list if call.kwargs["type_"] == "media.sync_videos"]
        self.assertEqual(len(video_calls), 2)
        self.assertTrue(all(call.kwargs["params"].get("download_priority") == 8 for call in video_calls))

    def test_schedule_all_media_sync_all_enqueues_existing_downloads(self) -> None:
        media_ids = [uuid.uuid4(), uuid.uuid4()]
        session = Mock()
        session.execute.return_value = Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=media_ids))))

        with patch("raelyn.services.media_actions.active_media_delete_job_map", return_value={}):
            with patch("raelyn.services.media_actions.enqueue_job") as enqueue_job:
                result = schedule_all_media_sync(session, scope="all")

        self.assertEqual(result["scope"], "all")
        video_calls = [call for call in enqueue_job.call_args_list if call.kwargs["type_"] == "media.sync_videos"]
        self.assertEqual(len(video_calls), 2)
        self.assertTrue(all(call.kwargs["params"].get("enqueue_existing_downloads") is True for call in video_calls))


if __name__ == "__main__":
    unittest.main()
