from __future__ import annotations

import unittest
import uuid

from raelyn.tools import repair_empty_audio_assets


class RepairEmptyAudioAssetsTests(unittest.TestCase):
    def test_apply_requires_explicit_yes_flag(self) -> None:
        parser = repair_empty_audio_assets._build_argument_parser()

        dry_run = parser.parse_args([])
        execute = parser.parse_args(["--limit", "100", "--priority", "30", "--yes"])

        self.assertFalse(dry_run.yes)
        self.assertIsNone(dry_run.limit)
        self.assertTrue(execute.yes)
        self.assertEqual(execute.limit, 100)
        self.assertEqual(execute.priority, 30)

    def test_uuid_parser_rejects_malformed_job_params(self) -> None:
        video_id = uuid.uuid4()

        self.assertEqual(repair_empty_audio_assets._uuid(str(video_id)), video_id)
        self.assertIsNone(repair_empty_audio_assets._uuid("not-a-uuid"))
        self.assertIsNone(repair_empty_audio_assets._uuid(None))


if __name__ == "__main__":
    unittest.main()
