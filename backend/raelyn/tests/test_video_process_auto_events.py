from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers import video_process


class VideoProcessAutoEventsTests(unittest.TestCase):
    def test_auto_event_extraction_delegates_to_event_service(self) -> None:
        session = Mock()
        video_id = uuid.uuid4()
        parent_job_id = str(uuid.uuid4())
        job_id = uuid.uuid4()

        with patch.object(video_process, "schedule_video_event_extraction", return_value=job_id) as schedule:
            result = video_process._schedule_auto_video_event_extraction(
                session,
                video_id=video_id,
                priority=5,
                parent_job_id=parent_job_id,
            )

        self.assertEqual(result, job_id)
        schedule.assert_called_once_with(
            session,
            video_id=video_id,
            force=False,
            priority=5,
            parent_job_id=parent_job_id,
        )


if __name__ == "__main__":
    unittest.main()
