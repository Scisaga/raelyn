from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services import playlist_analysis


class PlaylistAnalysisBackfillJobTests(unittest.TestCase):
    def test_request_reuses_any_active_playlist_backfill_job(self) -> None:
        playlist_id = uuid.uuid4()
        existing_job_id = uuid.uuid4()
        session = Mock()
        active_job = SimpleNamespace(id=existing_job_id)

        with patch.object(playlist_analysis, "active_playlist_embedding_backfill_job", return_value=active_job):
            with patch.object(playlist_analysis, "enqueue_job") as enqueue_job:
                job_id, created = playlist_analysis.request_playlist_embedding_backfill(
                    session,
                    playlist_id=playlist_id,
                    force=False,
                )

        self.assertEqual(job_id, existing_job_id)
        self.assertFalse(created)
        enqueue_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
