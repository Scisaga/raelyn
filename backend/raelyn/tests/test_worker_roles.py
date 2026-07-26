from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Job
from raelyn.services.provider_pause import job_provider
from raelyn.services.worker_roles import WORKER_ROLE_TYPES, job_type_worker_role, provider_for_job


class WorkerRoleTests(unittest.TestCase):
    def test_ai_embedding_analysis_roles_are_split(self) -> None:
        self.assertEqual(WORKER_ROLE_TYPES["download_youtube"], ["video.download.youtube", "video.backfill_subtitles.youtube"])
        self.assertEqual(WORKER_ROLE_TYPES["download_bilibili"], ["video.download.bilibili", "video.backfill_subtitles.bilibili"])
        self.assertIn("video.enrich_metadata.youtube", WORKER_ROLE_TYPES["sync"])
        self.assertEqual(WORKER_ROLE_TYPES["embedding"], ["event.embed"])
        self.assertEqual(
            WORKER_ROLE_TYPES["analysis"],
            [
                "playlist.mark_event_map_dirty",
                "playlist.build_event_map_snapshot",
                "playlist.prune_event_map_snapshots",
            ],
        )
        self.assertEqual(
            WORKER_ROLE_TYPES["ai"],
            [
                "video.extract_events",
                "video.extract_events_batch",
                "playlist.backfill_events",
                "playlist.backfill_events_range",
                "video.polish_transcript",
                "brief.generate_daily",
                "brief.generate_period",
            ],
        )

    def test_job_type_worker_role_maps_new_job_types(self) -> None:
        self.assertEqual(job_type_worker_role("event.embed"), "embedding")
        self.assertEqual(job_type_worker_role("playlist.mark_event_map_dirty"), "analysis")
        self.assertEqual(job_type_worker_role("playlist.build_event_map_snapshot"), "analysis")
        self.assertEqual(job_type_worker_role("playlist.prune_event_map_snapshots"), "analysis")
        self.assertEqual(job_type_worker_role("video.extract_events"), "ai")
        self.assertEqual(job_type_worker_role("video.extract_events_batch"), "ai")
        self.assertEqual(job_type_worker_role("playlist.backfill_events"), "ai")
        self.assertEqual(job_type_worker_role("playlist.backfill_events_range"), "ai")
        self.assertEqual(job_type_worker_role("brief.generate_daily"), "ai")
        self.assertEqual(job_type_worker_role("video.enrich_metadata.youtube"), "sync")
        self.assertEqual(job_type_worker_role("video.backfill_subtitles.youtube"), "download_youtube")
        self.assertEqual(job_type_worker_role("video.backfill_subtitles.bilibili"), "download_bilibili")

    def test_provider_helpers_map_subtitle_backfill_provider_job_types(self) -> None:
        youtube_job = Job(type="video.backfill_subtitles.youtube", params={"video_id": "unused"})
        metadata_job = Job(type="video.enrich_metadata.youtube", params={"video_id": "unused"})
        bilibili_job = Job(type="video.backfill_subtitles.bilibili", params={"video_id": "unused"})

        self.assertEqual(provider_for_job(None, youtube_job), "youtube")
        self.assertEqual(provider_for_job(None, metadata_job), "youtube")
        self.assertEqual(provider_for_job(None, bilibili_job), "bilibili")
        self.assertEqual(job_provider(None, youtube_job), "youtube")
        self.assertEqual(job_provider(None, metadata_job), "youtube")
        self.assertEqual(job_provider(None, bilibili_job), "bilibili")


if __name__ == "__main__":
    unittest.main()
