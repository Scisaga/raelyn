from __future__ import annotations

import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.youtube_download_circuit import (
    _claim_decision,
    _state_after_failure,
    _state_after_success,
    _state_blocks_claims,
)


class YoutubeDownloadCircuitTests(unittest.TestCase):
    def test_four_failures_from_two_jobs_open_circuit_and_allow_one_probe(self) -> None:
        now = datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc)
        first_job = uuid.UUID("00000000-0000-0000-0000-000000000001")
        second_job = uuid.UUID("00000000-0000-0000-0000-000000000002")
        state = None

        for offset, job_id in enumerate((first_job, second_job, first_job, second_job)):
            state = _state_after_failure(
                state,
                job_id=job_id,
                reason="youtube_media_transport",
                now=now + timedelta(seconds=offset),
            )

        self.assertEqual(state["state"], "open")
        retry_at = datetime.fromisoformat(state["retry_at"])
        self.assertEqual(retry_at, now + timedelta(seconds=303))
        self.assertTrue(_state_blocks_claims(state, now=retry_at - timedelta(seconds=1)))

        probe_job = uuid.UUID("00000000-0000-0000-0000-000000000003")
        allowed, half_open = _claim_decision(state, job_id=probe_job, now=retry_at)
        self.assertTrue(allowed)
        self.assertEqual(half_open["state"], "half_open")
        self.assertEqual(half_open["probe_job_id"], str(probe_job))
        self.assertTrue(_state_blocks_claims(half_open, now=retry_at + timedelta(seconds=1)))

    def test_failed_probe_uses_next_cooldown_and_success_closes_circuit(self) -> None:
        now = datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc)
        probe_job = uuid.UUID("00000000-0000-0000-0000-000000000003")
        half_open = {
            "state": "half_open",
            "observations": [],
            "cooldown_index": 0,
            "probe_job_id": str(probe_job),
            "probe_started_at": now.isoformat(),
        }

        reopened = _state_after_failure(
            half_open,
            job_id=probe_job,
            reason="youtube_no_video_formats",
            now=now,
        )

        self.assertEqual(reopened["state"], "open")
        self.assertEqual(reopened["cooldown_index"], 1)
        self.assertEqual(datetime.fromisoformat(reopened["retry_at"]), now + timedelta(minutes=15))
        self.assertEqual(_state_after_success(reopened)["state"], "closed")
        self.assertEqual(_state_after_success(reopened)["observations"], [])


if __name__ == "__main__":
    unittest.main()
