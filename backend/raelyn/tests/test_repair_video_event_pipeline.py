from __future__ import annotations

import unittest
import uuid

from raelyn.models import Job
from raelyn.tools import repair_video_event_pipeline


class RepairVideoEventPipelineTests(unittest.TestCase):
    def test_apply_requires_explicit_yes_flag(self) -> None:
        parser = repair_video_event_pipeline._build_argument_parser()

        self.assertFalse(parser.parse_args([]).yes)
        self.assertTrue(parser.parse_args(["--yes"]).yes)

    def test_job_video_ids_supports_single_and_batch_payloads(self) -> None:
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        single = Job(
            type="video.extract_events",
            params={"video_id": str(first_id)},
        )
        batch = Job(
            type="video.extract_events_batch",
            params={
                "video_ids": [
                    str(first_id),
                    "not-a-uuid",
                    str(second_id),
                    str(first_id),
                ]
            },
        )

        self.assertEqual(
            repair_video_event_pipeline._job_video_ids(single),
            {first_id},
        )
        self.assertEqual(
            repair_video_event_pipeline._job_video_ids(batch),
            {first_id, second_id},
        )

    def test_job_video_ids_rejects_malformed_payloads(self) -> None:
        malformed_single = Job(type="video.download", params={"video_id": None})
        malformed_batch = Job(
            type="video.extract_events_batch",
            params={"video_ids": "not-a-list"},
        )

        self.assertEqual(repair_video_event_pipeline._job_video_ids(malformed_single), set())
        self.assertEqual(repair_video_event_pipeline._job_video_ids(malformed_batch), set())

    def test_failed_extraction_repair_never_forces_reextraction(self) -> None:
        video_id = uuid.uuid4()

        self.assertEqual(
            repair_video_event_pipeline._event_extraction_repair_params(video_id),
            {"video_id": str(video_id), "force": False},
        )


if __name__ == "__main__":
    unittest.main()
