from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers.media_sync import (
    _compact_raw_info,
    _compact_youtube_metadata_info,
    _insert_discovered_video_if_new,
    _youtube_profile_avatar_url,
    _youtube_metadata_enrichment_terminal_failed,
    _ytdlp_extract_info_child,
    media_sync_videos,
    youtube_metadata_enrich,
)
from raelyn.jobs.worker_activity import touch_current_worker_activity
from raelyn.models import Job, Media, Video
from raelyn.services.provider_pause import ProviderPauseRequestError
from raelyn.services.video_meta import parse_published_at


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _fake_insert_collector(added_videos: list[Video]):
    def _fake_insert(
        session,
        *,
        media: Media,
        provider_video_id: str,
        url: str,
        metadata_entry: dict,
        is_members_only: bool,
        allow_members_only_download: bool,
    ) -> Video:
        video = Video(
            id=uuid.uuid4(),
            provider=media.provider,
            provider_video_id=provider_video_id,
            media_id=media.id,
            url=url,
            title=metadata_entry.get("title"),
            thumbnail_url=metadata_entry.get("thumbnail"),
            duration_sec=metadata_entry.get("duration"),
            status="members_only" if is_members_only and not allow_members_only_download else "discovered",
        )
        if video.status == "members_only":
            video.error_message = "members-only video; not enqueued"
        video.published_at = parse_published_at(metadata_entry)
        video.raw_info = _compact_raw_info(metadata_entry)
        added_videos.append(video)
        return video

    return _fake_insert


class MediaProfileAvatarTests(unittest.TestCase):
    def test_youtube_profile_prefers_avatar_over_channel_banner(self) -> None:
        info = {
            "thumbnails": [
                {
                    "id": "banner_uncropped",
                    "url": "https://yt3.example/banner",
                    "preference": -5,
                },
                {
                    "id": "7",
                    "url": "https://yt3.example/avatar-900",
                    "width": 900,
                    "height": 900,
                },
                {
                    "id": "avatar_uncropped",
                    "url": "https://yt3.example/avatar-original",
                    "preference": 1,
                },
            ]
        }

        self.assertEqual(
            _youtube_profile_avatar_url(info),
            "https://yt3.example/avatar-original",
        )

    def test_youtube_profile_uses_square_thumbnail_when_avatar_id_is_absent(self) -> None:
        info = {
            "thumbnails": [
                {
                    "id": "banner",
                    "url": "https://yt3.example/banner",
                    "width": 2560,
                    "height": 424,
                },
                {
                    "id": "channel-image",
                    "url": "https://yt3.example/avatar",
                    "width": 800,
                    "height": 800,
                },
            ]
        }

        self.assertEqual(
            _youtube_profile_avatar_url(info),
            "https://yt3.example/avatar",
        )


class MediaSyncVideoOrderingTests(unittest.TestCase):
    def test_insert_discovered_video_uses_conflict_do_nothing(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-upsert",
            url="https://www.youtube.com/@channel-upsert/videos",
            monitor_enabled=True,
        )
        inserted_id = uuid.uuid4()
        session = Mock()
        session.execute.return_value = _scalar_one_or_none(inserted_id)

        video = _insert_discovered_video_if_new(
            session,
            media=media,
            provider_video_id="upsert00123",
            url="https://www.youtube.com/watch?v=upsert00123",
            metadata_entry={"title": "upsert title", "timestamp": 1780401635},
            is_members_only=False,
            allow_members_only_download=False,
        )

        stmt = session.execute.call_args.args[0]
        compiled = " ".join(str(stmt.compile(dialect=postgresql.dialect())).lower().split())
        self.assertIn("on conflict on constraint video_provider_video_id_ux do nothing", compiled)
        self.assertIn("returning video.id", compiled)
        self.assertIsNotNone(video)
        assert video is not None
        self.assertEqual(video.id, inserted_id)
        self.assertEqual(video.provider_video_id, "upsert00123")
        self.assertIsNotNone(video.published_at)

    def test_media_sync_reschedules_when_same_media_lock_is_busy(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-lock",
            url="https://www.youtube.com/@channel-lock/videos",
            monitor_enabled=True,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id)},
            priority=3,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=False), patch(
            "raelyn.jobs.handlers.media_sync.enqueue_in"
        ) as enqueue_in, patch("raelyn.jobs.handlers.media_sync.job_log") as job_log, patch(
            "raelyn.jobs.handlers.media_sync.ytdlp_extract_info"
        ) as ytdlp_extract_info:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"rescheduled": True})
        enqueue_in.assert_called_once_with(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
        job_log.assert_called_once()
        ytdlp_extract_info.assert_not_called()

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

        session.execute.side_effect = [_scalar_one_or_none(None) for _ in range(8)]

        info = {
            "entries": [
                {"id": "oldvideo001", "webpage_url": "https://www.youtube.com/watch?v=oldvideo001", "timestamp": 100},
                {"id": "newvideo001", "webpage_url": "https://www.youtube.com/watch?v=newvideo001", "timestamp": 400},
                {"id": "midvideo001", "webpage_url": "https://www.youtube.com/watch?v=midvideo001", "timestamp": 250},
                {"id": "nextvideo01", "webpage_url": "https://www.youtube.com/watch?v=nextvideo01", "timestamp": 350},
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync._insert_discovered_video_if_new",
            side_effect=_fake_insert_collector(added_videos),
        ), patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")), patch(
            "raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=info
        ), patch("raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", True), patch(
            "raelyn.jobs.handlers.media_sync.enqueue_job"
        ) as enqueue_job:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 4, "metadata_enrichment_enqueued": 0})
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

        session.execute.side_effect = [_scalar_one_or_none(None) for _ in range(4)]

        info = {
            "entries": [
                {"id": "newvideo002", "webpage_url": "https://www.youtube.com/watch?v=newvideo002", "timestamp": 500},
                {"id": "oldvideo002", "webpage_url": "https://www.youtube.com/watch?v=oldvideo002", "timestamp": 100},
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync._insert_discovered_video_if_new",
            side_effect=_fake_insert_collector(added_videos),
        ), patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")), patch(
            "raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=info
        ), patch("raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", True), patch(
            "raelyn.jobs.handlers.media_sync.enqueue_job"
        ) as enqueue_job:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 2, "metadata_enrichment_enqueued": 0})
        self.assertEqual([call.kwargs["priority"] for call in enqueue_job.call_args_list], [8, 8])

    def test_media_sync_enqueues_metadata_enrichment_when_flat_entry_has_no_time(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="@sunriches",
            url="https://www.youtube.com/@sunriches/videos",
            monitor_enabled=True,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "max_entries": 1},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None

        added_videos: list[Video] = []

        session.execute.side_effect = [_scalar_one_or_none(None)]

        flat_info = {
            "entries": [
                {
                    "id": "vH7rDhyn1W8",
                    "url": "https://www.youtube.com/watch?v=vH7rDhyn1W8",
                    "title": "flat title",
                }
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync._insert_discovered_video_if_new",
            side_effect=_fake_insert_collector(added_videos),
        ), patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")), patch(
            "raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=flat_info
        ), patch("raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", False), patch(
            "raelyn.jobs.handlers.media_sync._youtube_metadata_enrichment_terminal_failed",
            return_value=False,
        ), patch(
            "raelyn.jobs.handlers.media_sync.enqueue_job"
        ) as enqueue_job:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 1, "metadata_enrichment_enqueued": 1})
        self.assertEqual(len(added_videos), 1)
        video = added_videos[0]
        self.assertEqual(video.provider_video_id, "vH7rDhyn1W8")
        self.assertEqual(video.title, "flat title")
        self.assertIsNone(video.published_at)
        enqueue_job.assert_called_once_with(
            session,
            type_="video.enrich_metadata.youtube",
            params={"video_id": str(video.id)},
            priority=0,
        )

    def test_media_sync_enqueues_metadata_enrichment_for_existing_video_without_time(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="@markets",
            url="https://www.youtube.com/@markets/videos",
            monitor_enabled=True,
        )
        existing_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="vH7rDhyn1W8",
            media_id=media.id,
            url="https://www.youtube.com/watch?v=vH7rDhyn1W8",
            title="existing title",
            published_at=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "max_entries": 1},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None

        flat_info = {
            "entries": [
                {
                    "id": "vH7rDhyn1W8",
                    "url": "https://www.youtube.com/watch?v=vH7rDhyn1W8",
                    "title": "flat title",
                }
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync._insert_discovered_video_if_new",
            return_value=None,
        ), patch("raelyn.jobs.handlers.media_sync._find_video_by_provider_id", return_value=existing_video), patch(
            "raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")
        ), patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=flat_info), patch(
            "raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", False
        ), patch(
            "raelyn.jobs.handlers.media_sync._youtube_metadata_enrichment_terminal_failed",
            return_value=False,
        ), patch("raelyn.jobs.handlers.media_sync.enqueue_job") as enqueue_job:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 0, "metadata_enrichment_enqueued": 1})
        enqueue_job.assert_called_once_with(
            session,
            type_="video.enrich_metadata.youtube",
            params={"video_id": str(existing_video.id)},
            priority=0,
        )

    def test_media_sync_does_not_reenqueue_metadata_after_terminal_failure(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="@markets",
            url="https://www.youtube.com/@markets/videos",
            monitor_enabled=True,
        )
        existing_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="vH7rDhyn1W8",
            media_id=media.id,
            url="https://www.youtube.com/watch?v=vH7rDhyn1W8",
            title="existing title",
            published_at=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "max_entries": 1},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None

        flat_info = {
            "entries": [
                {
                    "id": "vH7rDhyn1W8",
                    "url": "https://www.youtube.com/watch?v=vH7rDhyn1W8",
                    "title": "flat title",
                }
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync._insert_discovered_video_if_new",
            return_value=None,
        ), patch("raelyn.jobs.handlers.media_sync._find_video_by_provider_id", return_value=existing_video), patch(
            "raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")
        ), patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=flat_info), patch(
            "raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", False
        ), patch(
            "raelyn.jobs.handlers.media_sync._youtube_metadata_enrichment_terminal_failed",
            return_value=True,
        ), patch("raelyn.jobs.handlers.media_sync.enqueue_job") as enqueue_job:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 0, "metadata_enrichment_enqueued": 0})
        enqueue_job.assert_not_called()

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

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("bilibili:sync")
        ), patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value={"entries": []}), patch(
            "raelyn.jobs.handlers.media_sync.schedule_video_download"
        ) as schedule_video_download:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 0, "metadata_enrichment_enqueued": 0})
        self.assertEqual(
            [call.args[1] for call in schedule_video_download.call_args_list],
            existing_video_ids,
        )
        self.assertTrue(all(call.kwargs["priority"] == 5 for call in schedule_video_download.call_args_list))

    def test_media_sync_public_discovery_fetches_without_cookies_and_still_enqueues_download(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="@markets",
            url="https://www.youtube.com/@markets/videos",
            monitor_enabled=True,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "public_discovery": True, "max_entries": 1, "download_priority": 8},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None
        session.execute.side_effect = [_scalar_one_or_none(None)]
        added_videos: list[Video] = []
        info = {
            "entries": [
                {
                    "id": "vH7rDhyn1W8",
                    "webpage_url": "https://www.youtube.com/watch?v=vH7rDhyn1W8",
                    "timestamp": 1780401635,
                    "title": "public title",
                }
            ]
        }

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync._insert_discovered_video_if_new",
            side_effect=_fake_insert_collector(added_videos),
        ), patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")), patch(
            "raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=info
        ) as ytdlp_extract_info, patch(
            "raelyn.jobs.handlers.media_sync.settings.auto_download_new_videos", True
        ), patch(
            "raelyn.jobs.handlers.media_sync.enqueue_job"
        ) as enqueue_job:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"created": 1, "metadata_enrichment_enqueued": 0})
        self.assertEqual(len(added_videos), 1)
        self.assertFalse(ytdlp_extract_info.call_args.kwargs["use_provider_cookies"])
        self.assertIs(ytdlp_extract_info.call_args.kwargs["activity_hook"], touch_current_worker_activity)
        enqueue_job.assert_called_once_with(
            session,
            type_="video.download.youtube",
            params={"video_id": str(added_videos[0].id)},
            priority=8,
        )

    def test_media_sync_public_discovery_provider_pause_returns_blocked_without_setting_pause(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="@markets",
            url="https://www.youtube.com/@markets/videos",
            monitor_enabled=True,
        )
        job = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            params={"media_id": str(media.id), "public_discovery": True, "max_entries": 1},
            priority=1,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: media if getattr(model, "__name__", "") == "Media" else None
        err = ProviderPauseRequestError(
            provider="youtube",
            reason="youtube_bot_check",
            message="YouTube 无 cookies 下载仍触发人机验证。",
        )

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")
        ), patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", side_effect=err), patch(
            "raelyn.jobs.handlers.media_sync._pause_provider_jobs"
        ) as pause_provider, patch(
            "raelyn.jobs.handlers.media_sync.job_log"
        ) as job_log:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"skipped": "public_discovery_blocked"})
        self.assertIsNotNone(media.last_video_sync_at)
        pause_provider.assert_not_called()
        job_log.assert_called()

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

        with patch("raelyn.jobs.handlers.media_sync.try_xact_lock", return_value=True), patch(
            "raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")
        ), patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", side_effect=err), patch(
            "raelyn.jobs.handlers.media_sync.job_log"
        ) as job_log:
            result = media_sync_videos(session, job)

        self.assertEqual(result, {"disabled": True, "reason": "source_unavailable"})
        self.assertFalse(media.monitor_enabled)
        self.assertIsNotNone(media.last_video_sync_at)
        self.assertEqual(media.sync_cursor["previous"], "kept")
        self.assertEqual(media.sync_cursor["auto_disabled"]["reason"], "source_unavailable")
        self.assertIn("媒体源不可用", media.sync_cursor["auto_disabled"]["message"])
        job_log.assert_called()


class YoutubeMetadataEnrichTests(unittest.TestCase):
    def test_youtube_metadata_terminal_failure_query_uses_dedupe_and_attempt_cap(self) -> None:
        session = Mock()
        session.execute.return_value = _scalar_one_or_none(uuid.uuid4())
        video_id = uuid.uuid4()

        result = _youtube_metadata_enrichment_terminal_failed(session, video_id=video_id)

        self.assertTrue(result)
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("job.dedupe_key", compiled)
        self.assertIn("job.status", compiled)
        self.assertIn("job.attempt >= job.max_attempts", compiled)

    def test_youtube_metadata_child_returns_compact_payload(self) -> None:
        class Queue:
            item = None

            def put(self, item) -> None:
                self.item = item

        queue = Queue()
        full_info = {
            "id": "vH7rDhyn1W8",
            "title": "remote title",
            "description": "desc",
            "timestamp": 1780401635,
            "duration": 735,
            "webpage_url": "https://www.youtube.com/watch?v=vH7rDhyn1W8",
            "thumbnail": "https://img.example/thumb.jpg",
            "formats": [{"format_id": str(i), "url": "https://media.example/item"} for i in range(300)],
            "automatic_captions": {"en": [{"url": "https://caption.example/en"}]},
            "thumbnails": [{"url": "https://img.example/thumb-large.jpg"}],
        }

        with patch("raelyn.jobs.handlers.media_sync.ytdlp_extract_info", return_value=full_info):
            _ytdlp_extract_info_child(queue, url="https://www.youtube.com/watch?v=vH7rDhyn1W8")

        self.assertIsNotNone(queue.item)
        status, payload = queue.item
        self.assertEqual(status, "ok")
        self.assertEqual(payload, _compact_youtube_metadata_info(full_info))
        self.assertEqual(payload["thumbnail"], "https://img.example/thumb.jpg")
        self.assertEqual(payload["timestamp"], 1780401635)
        self.assertNotIn("formats", payload)
        self.assertNotIn("automatic_captions", payload)
        self.assertNotIn("thumbnails", payload)

    def test_youtube_metadata_enrich_updates_missing_published_at(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="vH7rDhyn1W8",
            url="https://www.youtube.com/watch?v=vH7rDhyn1W8",
            title="existing title",
            thumbnail_url=None,
            duration_sec=None,
            published_at=None,
            raw_info=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.enrich_metadata.youtube",
            params={"video_id": str(video.id)},
            priority=0,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: video if getattr(model, "__name__", "") == "Video" else None
        info = {
            "id": "vH7rDhyn1W8",
            "title": "remote title",
            "timestamp": 1780401635,
            "duration": 735,
            "thumbnail": "https://img.example/thumb.jpg",
        }

        with patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")), patch(
            "raelyn.jobs.handlers.media_sync._extract_youtube_video_metadata_with_timeout", return_value=info
        ), patch("raelyn.jobs.handlers.media_sync.schedule_playlists_event_map_dirty_for_video") as dirty:
            result = youtube_metadata_enrich(session, job)

        self.assertEqual(result, {"ok": True, "published_at_updated": True})
        self.assertEqual(video.title, "existing title")
        self.assertEqual(video.thumbnail_url, "https://img.example/thumb.jpg")
        self.assertEqual(video.duration_sec, 735)
        self.assertIsNotNone(video.published_at)
        self.assertEqual(video.raw_info["timestamp"], 1780401635)
        dirty.assert_called_once_with(
            session,
            video_id=video.id,
            reason="video_published_at_changed",
            source_job_id=job.id,
            priority=job.priority,
            require_event_map_input=True,
        )

    def test_youtube_metadata_enrich_reschedules_when_provider_lock_busy(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="vH7rDhyn1W8",
            url="https://www.youtube.com/watch?v=vH7rDhyn1W8",
            published_at=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.enrich_metadata.youtube",
            params={"video_id": str(video.id)},
            priority=0,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: video if getattr(model, "__name__", "") == "Video" else None

        with patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext(None)), patch(
            "raelyn.jobs.handlers.media_sync.enqueue_in"
        ) as enqueue_in, patch("raelyn.jobs.handlers.media_sync._extract_youtube_video_metadata_with_timeout") as extract:
            result = youtube_metadata_enrich(session, job)

        self.assertEqual(result, {"rescheduled": True})
        enqueue_in.assert_called_once_with(session, seconds=30, type_=job.type, params=job.params, priority=job.priority)
        extract.assert_not_called()

    def test_youtube_metadata_enrich_skips_when_published_at_exists(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="vH7rDhyn1W8",
            url="https://www.youtube.com/watch?v=vH7rDhyn1W8",
            published_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.enrich_metadata.youtube",
            params={"video_id": str(video.id)},
            priority=0,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: video if getattr(model, "__name__", "") == "Video" else None

        with patch("raelyn.jobs.handlers.media_sync._extract_youtube_video_metadata_with_timeout") as extract:
            result = youtube_metadata_enrich(session, job)

        self.assertEqual(result, {"skipped": "published_at already present"})
        extract.assert_not_called()

    def test_youtube_metadata_enrich_timeout_bubbles_to_job_retry(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="vH7rDhyn1W8",
            url="https://www.youtube.com/watch?v=vH7rDhyn1W8",
            published_at=None,
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.enrich_metadata.youtube",
            params={"video_id": str(video.id)},
            priority=0,
            status="pending",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: video if getattr(model, "__name__", "") == "Video" else None

        with patch("raelyn.jobs.handlers.media_sync.advisory_lock_any", return_value=nullcontext("youtube:sync")), patch(
            "raelyn.jobs.handlers.media_sync._extract_youtube_video_metadata_with_timeout",
            side_effect=TimeoutError("youtube metadata enrich timed out after 45s"),
        ):
            with self.assertRaises(TimeoutError):
                youtube_metadata_enrich(session, job)


if __name__ == "__main__":
    unittest.main()
