from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import raelyn.jobs.handlers  # noqa: F401
from raelyn.jobs.registry import registry


class JobRegistryImportTests(unittest.TestCase):
    def test_import_registers_all_expected_job_types(self) -> None:
        expected = {
            "media.delete",
            "media.sync_profile",
            "media.sync_videos",
            "video.enrich_metadata.youtube",
            "video.download",
            "video.download.youtube",
            "video.download.bilibili",
            "video.backfill_subtitles",
            "video.backfill_subtitles.youtube",
            "video.backfill_subtitles.bilibili",
            "video.extract_audio",
            "video.normalize_subtitle",
            "video.asr_transcribe",
            "video.extract_events",
            "video.polish_transcript",
            "event.embed",
            "playlist.backfill_events",
            "playlist.backfill_events_range",
            "playlist.mark_event_map_dirty",
            "playlist.build_event_map_snapshot",
            "playlist.prune_event_map_snapshots",
            "brief.generate_period",
            "brief.generate_daily",
        }
        for job_type in expected:
            self.assertIsNotNone(registry.get(job_type), job_type)


if __name__ == "__main__":
    unittest.main()
