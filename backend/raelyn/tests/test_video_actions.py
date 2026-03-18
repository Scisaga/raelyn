from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Video
from raelyn.services.video_actions import schedule_video_download, schedule_video_retranscribe


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


class VideoActionsTests(unittest.TestCase):
    def test_schedule_video_download_uses_provider_specific_job_type(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.com/watch?v=abc123",
            status="ready",
        )
        session = Mock()
        session.get.return_value = video
        job_id = uuid.uuid4()

        with patch("raelyn.services.video_actions.ensure_media_not_deleting") as ensure_media_not_deleting:
            with patch("raelyn.services.video_actions._find_active_download_job", return_value=None):
                with patch("raelyn.services.video_actions.enqueue_job", return_value=job_id) as enqueue_job:
                    result = schedule_video_download(session, video.id)

        self.assertEqual(result["job_type"], "video.download.youtube")
        self.assertEqual(result["job_id"], str(job_id))
        ensure_media_not_deleting.assert_called_once()
        enqueue_job.assert_called_once()

    def test_schedule_video_download_rejects_members_only_without_feature_flag(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.com/watch?v=abc123",
            status="members_only",
        )
        session = Mock()
        session.get.side_effect = lambda model, _key: video if getattr(model, "__name__", "") == "Video" else None

        with self.assertRaises(RuntimeError):
            schedule_video_download(session, video.id)

    def test_schedule_video_retranscribe_prefers_subtitle_normalization(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.com/watch?v=abc123",
            status="ready",
        )
        session = Mock()
        session.get.return_value = video
        session.execute.side_effect = [
            _scalar_one_or_none(uuid.uuid4()),
            _scalar_one_or_none(None),
        ]
        job_id = uuid.uuid4()

        with patch("raelyn.services.video_actions.ensure_media_not_deleting") as ensure_media_not_deleting:
            with patch("raelyn.services.video_actions.enqueue_job", return_value=job_id) as enqueue_job:
                result = schedule_video_retranscribe(session, video.id)

        self.assertEqual(result["mode"], "subtitle")
        self.assertEqual(result["job_type"], "video.normalize_subtitle")
        ensure_media_not_deleting.assert_called_once()
        enqueue_job.assert_called_once()

    def test_schedule_video_retranscribe_enqueues_extract_audio_then_asr(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.com/watch?v=abc123",
            status="ready",
        )
        session = Mock()
        session.get.return_value = video
        session.execute.side_effect = [
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
        ]
        extract_job_id = uuid.uuid4()
        asr_job_id = uuid.uuid4()

        with patch("raelyn.services.video_actions.ensure_media_not_deleting") as ensure_media_not_deleting:
            with patch("raelyn.services.video_actions.asr_enabled", return_value=True):
                with patch("raelyn.services.video_actions.enqueue_job", return_value=extract_job_id) as enqueue_job:
                    with patch("raelyn.services.video_actions.enqueue_in", return_value=asr_job_id) as enqueue_in:
                        result = schedule_video_retranscribe(session, video.id)

        self.assertEqual(result["mode"], "asr")
        self.assertEqual(result["job_ids"], [str(extract_job_id), str(asr_job_id)])
        self.assertEqual(result["job_types"], ["video.extract_audio", "video.asr_transcribe"])
        ensure_media_not_deleting.assert_called_once()
        enqueue_job.assert_called_once()
        enqueue_in.assert_called_once()

    def test_schedule_video_retranscribe_requires_asr_or_subtitle(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.com/watch?v=abc123",
            status="ready",
        )
        session = Mock()
        session.get.return_value = video
        session.execute.side_effect = [
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
        ]

        with patch("raelyn.services.video_actions.ensure_media_not_deleting"):
            with patch("raelyn.services.video_actions.asr_enabled", return_value=False):
                with self.assertRaises(ValueError):
                    schedule_video_retranscribe(session, video.id)

    def test_schedule_video_download_rejects_when_media_deleting(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.com/watch?v=abc123",
            status="ready",
        )
        session = Mock()
        session.get.return_value = video

        with patch("raelyn.services.video_actions.ensure_media_not_deleting", side_effect=RuntimeError("媒体删除中，当前操作不可用")):
            with self.assertRaises(RuntimeError):
                schedule_video_download(session, video.id)


if __name__ == "__main__":
    unittest.main()
