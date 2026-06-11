from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.tools import reset_event_extraction_v2


class EventResetToolTests(unittest.TestCase):
    def test_reset_dry_run_returns_counts_without_delete(self) -> None:
        session = Mock()
        counts = {"jobs": 3, "market_event": 2, "video_event_extraction_run": 1}

        with patch.object(reset_event_extraction_v2, "collect_event_extraction_reset_counts", return_value=counts):
            result = reset_event_extraction_v2.reset_event_extraction_v2(session, execute=False)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["counts"], counts)
        session.execute.assert_not_called()

    def test_reset_job_types_include_batch_event_pipeline(self) -> None:
        self.assertIn("video.extract_events_batch", reset_event_extraction_v2.EVENT_RESET_JOB_TYPES)
        self.assertIn("playlist.build_event_regime_snapshot", reset_event_extraction_v2.EVENT_RESET_JOB_TYPES)


if __name__ == "__main__":
    unittest.main()
