from __future__ import annotations

import sys
import unittest
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers.briefs import _brief_generate_period_impl
from raelyn.jobs.handlers.media_delete import media_delete
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import Asset, Job, Media, Playlist


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class _FakeSession:
    def __init__(self, *, media=None, avatar_asset=None) -> None:
        self.media = media
        self.avatar_asset = avatar_asset
        self.deleted: list[object] = []
        self.added: list[object] = []
        self.flushed = 0

    def get(self, model, key):
        if model is Media and self.media and key == self.media.id:
            return self.media
        if model is Asset and self.avatar_asset and key == self.avatar_asset.id:
            return self.avatar_asset
        return None

    def delete(self, obj) -> None:
        self.deleted.append(obj)

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushed += 1


class MediaDeleteJobTests(unittest.TestCase):
    def test_media_delete_reschedules_while_related_running_jobs_exist(self) -> None:
        media_id = uuid.uuid4()
        media = Media(id=media_id, provider="youtube", provider_media_id="demo", url="https://example.com/@demo", monitor_enabled=True)
        delete_job = Job(id=uuid.uuid4(), type="media.delete", status="running", priority=20, params={"media_id": str(media_id)})
        related_job = Job(
            id=uuid.uuid4(),
            type="video.download.youtube",
            status="running",
            priority=8,
            params={"video_id": str(uuid.uuid4())},
        )
        snapshot = SimpleNamespace(affected_periods=[], cleanup_buckets=set(), prefixes=[])
        session = _FakeSession(media=media)

        with patch("raelyn.jobs.handlers.media_delete.collect_media_delete_snapshot", return_value=snapshot):
            with patch("raelyn.jobs.handlers.media_delete.list_related_jobs", return_value=[related_job]):
                with patch("raelyn.jobs.handlers.media_delete.request_job_cancel", return_value="requested") as request_cancel:
                    with patch("raelyn.jobs.handlers.media_delete.job_log") as job_log:
                        with self.assertRaises(JobReschedule) as ctx:
                            media_delete(session, delete_job)

        self.assertEqual(ctx.exception.reason, "waiting_related_jobs_to_stop")
        self.assertFalse(session.deleted)
        self.assertEqual(session.flushed, 0)
        self.assertFalse(media.monitor_enabled)
        request_cancel.assert_called_once_with(session, related_job, reason="media_delete")
        job_log.assert_called_once()

    def test_media_delete_deletes_media_and_refreshes_affected_briefs(self) -> None:
        media_id = uuid.uuid4()
        avatar_asset_id = uuid.uuid4()
        media = Media(
            id=media_id,
            provider="youtube",
            provider_media_id="demo",
            url="https://example.com/@demo",
            monitor_enabled=True,
            avatar_asset_id=avatar_asset_id,
            avatar_s3_key="media/demo/avatar.png",
        )
        avatar_asset = Asset(
            id=avatar_asset_id,
            video_id=None,
            type="media_avatar",
            format="png",
            source="upload",
            s3_bucket="bucket-a",
            s3_key="media/demo/avatar.png",
        )
        delete_job = Job(id=uuid.uuid4(), type="media.delete", status="running", priority=20, params={"media_id": str(media_id)})
        pending_job = Job(
            id=uuid.uuid4(),
            type="video.polish_transcript",
            status="pending",
            priority=5,
            params={"video_id": str(uuid.uuid4())},
        )
        snapshot = SimpleNamespace(
            affected_periods=[SimpleNamespace(playlist_id=uuid.uuid4(), granularity="day", period_start=date(2026, 3, 19))],
            cleanup_buckets={"bucket-a"},
            prefixes=["youtube/demo/"],
        )
        session = _FakeSession(media=media, avatar_asset=avatar_asset)

        with patch("raelyn.jobs.handlers.media_delete.collect_media_delete_snapshot", return_value=snapshot):
            with patch("raelyn.jobs.handlers.media_delete.list_related_jobs", return_value=[pending_job]):
                with patch("raelyn.jobs.handlers.media_delete.refresh_affected_briefs_after_media_delete", return_value={"refreshed": 1, "emptied": 1}):
                    with patch("raelyn.jobs.handlers.media_delete.s3_clear_bucket", return_value={"ok": True, "deleted": 2}) as s3_clear:
                        with patch("raelyn.jobs.handlers.media_delete.job_log") as job_log:
                            result = media_delete(session, delete_job)

        self.assertEqual(result["media_id"], str(media_id))
        self.assertEqual(result["deleted_pending_jobs"], 1)
        self.assertEqual(result["requested_cancel_jobs"], 0)
        self.assertEqual(result["brief_refreshed"], 1)
        self.assertEqual(result["brief_emptied"], 1)
        self.assertEqual(result["s3_deleted"], 2)
        self.assertEqual(result["s3_errors"], [])
        self.assertIn(pending_job, session.deleted)
        self.assertIn(media, session.deleted)
        self.assertIn(avatar_asset, session.deleted)
        self.assertFalse(media.monitor_enabled)
        self.assertIsNone(media.avatar_asset_id)
        self.assertIsNone(media.avatar_s3_key)
        self.assertEqual(session.flushed, 1)
        s3_clear.assert_called_once_with(bucket="bucket-a", prefix="youtube/demo/")
        job_log.assert_called_once()

    def test_brief_generate_period_marks_empty_when_period_has_no_videos(self) -> None:
        playlist_id = uuid.uuid4()
        job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            priority=5,
            params={"playlist_id": str(playlist_id), "granularity": "day", "period_start": "2026-03-19"},
        )
        playlist = Playlist(id=playlist_id, name="示例列表", brief_granularity="day")
        session = Mock()
        session.get.return_value = playlist
        session.execute.side_effect = [
            _scalars_all([uuid.uuid4()]),
            _scalars_all([]),
            _scalar_one_or_none(None),
        ]

        with patch("raelyn.jobs.handlers.briefs.ensure_video_published_at_backfilled"):
            with patch("raelyn.jobs.handlers.briefs.mark_brief_empty") as mark_empty:
                with patch("raelyn.jobs.handlers.briefs.job_log") as job_log:
                    result = _brief_generate_period_impl(
                        session,
                        job,
                        playlist_id=playlist_id,
                        granularity="day",
                        period_start=date(2026, 3, 19),
                    )

        self.assertEqual(result, {"empty": True, "reason": "no videos"})
        mark_empty.assert_called_once_with(
            session,
            playlist_id=playlist_id,
            granularity="day",
            period_start=date(2026, 3, 19),
        )
        job_log.assert_called_once()

    def test_brief_generate_period_fails_when_period_has_playback_but_no_transcript(self) -> None:
        playlist_id = uuid.uuid4()
        job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            priority=5,
            params={"playlist_id": str(playlist_id), "granularity": "day", "period_start": "2026-03-19"},
        )
        playlist = Playlist(id=playlist_id, name="示例列表", brief_granularity="day")
        session = Mock()
        session.get.return_value = playlist
        session.execute.side_effect = [
            _scalars_all([uuid.uuid4()]),
            _scalars_all([]),
            _scalar_one_or_none(uuid.uuid4()),
            _scalar_one_or_none(None),
        ]

        with patch("raelyn.jobs.handlers.briefs.ensure_video_published_at_backfilled"):
            result = _brief_generate_period_impl(
                session,
                job,
                playlist_id=playlist_id,
                granularity="day",
                period_start=date(2026, 3, 19),
            )

        self.assertEqual(result, {"failed": True, "reason": "no transcript"})
        added_brief = session.add.call_args.args[0]
        self.assertEqual(added_brief.playlist_id, playlist_id)
        self.assertEqual(added_brief.status, "failed")
        self.assertEqual(added_brief.error_message, "本周期无可用文本（字幕/文字稿缺失）")
        self.assertEqual(added_brief.markdown_asset_id, None)
        session.flush.assert_called_once()


if __name__ == "__main__":
    unittest.main()
