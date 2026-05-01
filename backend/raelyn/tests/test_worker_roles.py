from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.worker_roles import WORKER_ROLE_TYPES, job_type_worker_role


class WorkerRoleTests(unittest.TestCase):
    def test_ai_embedding_analysis_roles_are_split(self) -> None:
        self.assertEqual(WORKER_ROLE_TYPES["embedding"], ["video.embed_transcript", "playlist.backfill_embeddings"])
        self.assertEqual(WORKER_ROLE_TYPES["analysis"], ["playlist.build_analysis_snapshot"])
        self.assertEqual(WORKER_ROLE_TYPES["ai"], ["video.polish_transcript", "brief.generate_daily", "brief.generate_period"])

    def test_job_type_worker_role_maps_new_job_types(self) -> None:
        self.assertEqual(job_type_worker_role("video.embed_transcript"), "embedding")
        self.assertEqual(job_type_worker_role("playlist.backfill_embeddings"), "embedding")
        self.assertEqual(job_type_worker_role("playlist.build_analysis_snapshot"), "analysis")
        self.assertEqual(job_type_worker_role("brief.generate_daily"), "ai")


if __name__ == "__main__":
    unittest.main()
