from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.enqueue import _merge_pending_dirty_job, _normalize_dedupe_key_and_params
from raelyn.models import Job


class EnqueueDedupeTests(unittest.TestCase):
    def test_legacy_usage_backfill_dedupe_key_uses_version(self) -> None:
        dedupe_key, params = _normalize_dedupe_key_and_params(
            "system.backfill_legacy_usage",
            {"version": 1},
        )

        self.assertEqual(dedupe_key, "system_usage_legacy_backfill:v1")
        self.assertEqual(params, {"version": 1})

    def test_usage_snapshot_dedupe_key_uses_local_day(self) -> None:
        dedupe_key, params = _normalize_dedupe_key_and_params(
            "system.capture_usage_snapshot",
            {"date": "2026-09-03"},
        )

        self.assertEqual(dedupe_key, "system_usage_snapshot:2026-09-03")
        self.assertEqual(params, {"date": "2026-09-03"})

    def test_event_embedding_backfill_uses_migration_dedupe_key_and_fixed_batch(self) -> None:
        dedupe_key, params = _normalize_dedupe_key_and_params(
            "event.backfill_embeddings",
            {
                "source_model": "Qwen/Qwen3-Embedding-8B",
                "target_model": "Qwen/Qwen3-Embedding-4B",
                "embedding_dim": 1024,
                "batch_size": 128,
            },
        )

        self.assertEqual(
            dedupe_key,
            "event_embedding_backfill:Qwen/Qwen3-Embedding-8B:Qwen/Qwen3-Embedding-4B:1024",
        )
        self.assertEqual(params["batch_size"], 64)

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

    def test_pending_dirty_merge_keeps_bounded_unique_reasons_and_highest_priority(self) -> None:
        job = Job(
            id=uuid.uuid4(),
            type="playlist.mark_event_map_dirty",
            status="pending",
            priority=2,
            params={"reason": "first", "reasons": ["first", "second"]},
        )

        _merge_pending_dirty_job(
            job,
            {"reason": "third", "source_video_id": str(uuid.uuid4())},
            priority=5,
        )
        _merge_pending_dirty_job(job, {"reason": "first"}, priority=1)

        self.assertEqual(job.params["reasons"], ["first", "second", "third"])
        self.assertEqual(job.params["reason"], "first")
        self.assertEqual(job.priority, 5)


if __name__ == "__main__":
    unittest.main()
