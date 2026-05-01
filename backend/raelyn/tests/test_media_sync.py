from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers.media_sync import media_sync_videos
from raelyn.models import Job, Media, Video


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


class MediaSyncVideoOrderingTests(unittest.TestCase):
    def test_media_sync_enqueues_auto_downloads_newest_first_with_priority_7(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://www.youtube.com/@channel-1/videos",
            monitor_enabled=True,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "max_entries": 0},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None

        added_videos: list[Video] = []

        def _add(obj) -> None:
            if isinstance(obj, Video):
                added_videos.append(obj)

        def _flush() -> None:
            if added_videos and added_videos[-1].id is None:
                added_videos[-1].id = uuid.uuid4()

        session.add.side_effect = _add
        session.flush.side_effect = _flush
        session.execute.side_effect = [_scalar_one_or_none(None) for _ in range(8)]

        info = {
            "entries": [
                {"id": "oldvideo001", "webpage_url": "https://www.youtube.com/watch?v=oldvideo001", "timestamp": 100},
                {"id": "newvideo001", "webpage_url": "https://www.youtube.com/watch?v=newvideo001", "timestamp": 400},
                {"id": "midvideo001", "webpage_url": "https://www.youtube.com/watch?v=midvideo001", "timestamp": 250},
                {"id": "nextvideo01", "webpage_url": "https://www.youtube.com/watch?v=nextvideo01", "timestamp": 350},
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")):
            with patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=info):
                with patch("raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", True):
                    with patch("raelyn.jobs.handlers.media_sync.enqueue_job") as enqueue_job:
                        result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 4})
        self.assertEqual(
            [video.provider_video_id for video in added_videos],
            ["newvideo001", "nextvideo01", "midvideo001", "oldvideo001"],
        )
        self.assertEqual([call.kwargs["priority"] for call in enqueue_job.call_args_list], [7, 7, 7, 7])

    def test_media_sync_respects_download_priority_override(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-2",
            url="https://www.youtube.com/@channel-2/videos",
            monitor_enabled=True,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "max_entries": 0, "download_priority": 8},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None

        added_videos: list[Video] = []

        def _add(obj) -> None:
            if isinstance(obj, Video):
                added_videos.append(obj)

        def _flush() -> None:
            if added_videos and added_videos[-1].id is None:
                added_videos[-1].id = uuid.uuid4()

        session.add.side_effect = _add
        session.flush.side_effect = _flush
        session.execute.side_effect = [_scalar_one_or_none(None) for _ in range(4)]

        info = {
            "entries": [
                {"id": "newvideo002", "webpage_url": "https://www.youtube.com/watch?v=newvideo002", "timestamp": 500},
                {"id": "oldvideo002", "webpage_url": "https://www.youtube.com/watch?v=oldvideo002", "timestamp": 100},
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")):
            with patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=info):
                with patch("raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", True):
                    with patch("raelyn.jobs.handlers.media_sync.enqueue_job") as enqueue_job:
                        result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 2})
        self.assertEqual([call.kwargs["priority"] for call in enqueue_job.call_args_list], [8, 8])

    def test_media_sync_all_enqueues_existing_discovered_downloads(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="bilibili",
            provider_media_id="channel-3",
            url="https://space.bilibili.com/123/videos",
            monitor_enabled=True,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "max_entries": 0, "enqueue_existing_downloads": True, "download_priority": 5},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None
        existing_video_ids = [uuid.uuid4(), uuid.uuid4()]

        def _execute(stmt):
            compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
            self.assertIn("asset.type =", compiled)
            self.assertIn("job.type in", compiled)
            self.assertIn("video.status in", compiled)
            return Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=existing_video_ids))))

        session.execute.side_effect = _execute

        with patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("bilibili:sync")):
            with patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value={"entries": []}):
                with patch("raelyn.jobs.handlers.media_sync.schedule_video_download") as schedule_video_download:
                    result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 0})
        self.assertEqual(
            [call.args[1] for call in schedule_video_download.call_args_list],
            existing_video_ids,
        )
        self.assertTrue(all(call.kwargs["priority"] == 5 for call in schedule_video_download.call_args_list))

    def test_media_sync_auto_disables_missing_youtube_channel(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="@missing",
            url="https://www.youtube.com/@missing/videos",
            monitor_enabled=True,
            sync_cursor={"previous": "kept"},
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id)},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None
        err = RuntimeError("ERROR: [youtube:tab] @missing: Unable to download API page: HTTP Error 404: Not Found")

        with patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")):
            with patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", side_effect=err):
                with patch("raelyn.jobs.handlers.media_sync.job_log") as job_log:
                    result = media_sync_videos(session, job)

        self.assertEqual(result, {"disabled": True, "reason": "source_unavailable"})
        self.assertFalse(media.monitor_enabled)
        self.assertIsNotNone(media.last_video_sync_at)
        self.assertEqual(media.sync_cursor["previous"], "kept")
        self.assertEqual(media.sync_cursor["auto_disabled"]["reason"], "source_unavailable")
        self.assertIn("媒体源不可用", media.sync_cursor["auto_disabled"]["message"])
        job_log.assert_called()


if __name__ == "__main__":
    unittest.main()
