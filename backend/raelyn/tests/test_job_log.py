from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.log import job_log
from raelyn.models import JobEvent, Job


class JobLogTests(unittest.TestCase):
    def test_job_log_flushes_only_event(self) -> None:
        session = Mock()
        job = Job(id=uuid.uuid4(), type="video.download.youtube")

        job_log(session, job, "running")

        event = session.add.call_args.args[0]
        self.assertIsInstance(event, JobEvent)
        session.flush.assert_called_once_with([event])


if __name__ == "__main__":
    unittest.main()
