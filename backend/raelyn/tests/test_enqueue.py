from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.enqueue import _normalize_dedupe_key_and_params, enqueue_job


class EnqueueDedupeTests(unittest.TestCase):
    def test_media_sync_videos_dedupe_key_uses_media_id(self) -> None:
        media_id = uuid.uuid4()

        dedupe_key, params = _normalize_dedupe_key_and_params(
            "media.sync_videos",
            {"media_id": str(media_id), "source": "scheduler"},
        )

        self.assertEqual(dedupe_key, f"media.sync_videos:{media_id}")
        self.assertEqual(params, {"media_id": str(media_id), "source": "scheduler"})

    def test_media_sync_profile_dedupe_key_uses_media_id(self) -> None:
        media_id = uuid.uuid4()

        dedupe_key, params = _normalize_dedupe_key_and_params(
            "media.sync_profile",
            {"media_id": str(media_id)},
        )

        self.assertEqual(dedupe_key, f"media.sync_profile:{media_id}")
        self.assertEqual(params, {"media_id": str(media_id)})

    def test_media_sync_without_valid_media_id_is_not_deduped(self) -> None:
        dedupe_key, params = _normalize_dedupe_key_and_params("media.sync_videos", {"media_id": "bad-id"})

        self.assertIsNone(dedupe_key)
        self.assertEqual(params, {"media_id": "bad-id"})

    def test_youtube_metadata_enrich_dedupe_key_uses_video_id(self) -> None:
        video_id = uuid.uuid4()

        dedupe_key, params = _normalize_dedupe_key_and_params(
            "video.enrich_metadata.youtube",
            {"video_id": str(video_id)},
        )

        self.assertEqual(dedupe_key, f"video.enrich_metadata.youtube:{video_id}")
        self.assertEqual(params, {"video_id": str(video_id)})

    def test_youtube_metadata_enrich_reuses_existing_pending_job(self) -> None:
        video_id = uuid.uuid4()
        existing_job_id = uuid.uuid4()
        session = Mock()
        session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        session.execute.side_effect = [
            Mock(),
            Mock(scalar_one_or_none=Mock(return_value=existing_job_id)),
        ]

        job_id = enqueue_job(
            session,
            type_="video.enrich_metadata.youtube",
            params={"video_id": str(video_id)},
            priority=0,
        )

        self.assertEqual(job_id, existing_job_id)
        session.begin_nested.assert_not_called()
        session.flush.assert_not_called()

    def test_video_subtitle_backfill_dedupe_key_uses_video_and_target_language(self) -> None:
        video_id = uuid.uuid4()

        dedupe_key, params = _normalize_dedupe_key_and_params(
            "video.backfill_subtitles.youtube",
            {"video_id": str(video_id), "language": "zh", "force": True},
        )

        self.assertEqual(dedupe_key, f"video_subtitle_backfill:{video_id}:zh:force")
        self.assertEqual(params["video_id"], str(video_id))
        self.assertEqual(params["target_language"], "zh")
        self.assertTrue(params["force"])
        self.assertNotIn("language", params)

    def test_enqueue_dedupe_locks_and_reuses_existing_pending_job_on_postgres(self) -> None:
        media_id = uuid.uuid4()
        existing_job_id = uuid.uuid4()
        session = Mock()
        session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        session.execute.side_effect = [
            Mock(),
            Mock(scalar_one_or_none=Mock(return_value=existing_job_id)),
        ]

        job_id = enqueue_job(
            session,
            type_="media.sync_videos",
            params={"media_id": str(media_id)},
            priority=1,
        )

        self.assertEqual(job_id, existing_job_id)
        first_stmt = str(session.execute.call_args_list[0].args[0])
        self.assertIn("pg_advisory_xact_lock", first_stmt)
        session.begin_nested.assert_not_called()
        session.flush.assert_not_called()


if __name__ == "__main__":
    unittest.main()
