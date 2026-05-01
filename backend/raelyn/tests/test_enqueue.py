from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.enqueue import _normalize_dedupe_key_and_params


class EnqueueDedupeTests(unittest.TestCase):
    def test_media_sync_videos_dedupe_key_uses_media_id(self) -> None:
        media_id = uuid.uuid4()

        dedupe_key, params = _normalize_dedupe_key_and_params(
            "media.sync_videos",
            {"media_id": str(media_id), "source": "scheduler"},
        )

        self.assertEqual(dedupe_key, f"media.sync_videos:{media_id}")
        self.assertEqual(params, {"media_id": str(media_id), "source": "scheduler"})

    def test_media_sync_profile_dedupe_key_uses_media_id(self) -> None:
        media_id = uuid.uuid4()

        dedupe_key, params = _normalize_dedupe_key_and_params(
            "media.sync_profile",
            {"media_id": str(media_id)},
        )

        self.assertEqual(dedupe_key, f"media.sync_profile:{media_id}")
        self.assertEqual(params, {"media_id": str(media_id)})

    def test_media_sync_without_valid_media_id_is_not_deduped(self) -> None:
        dedupe_key, params = _normalize_dedupe_key_and_params("media.sync_videos", {"media_id": "bad-id"})

        self.assertIsNone(dedupe_key)
        self.assertEqual(params, {"media_id": "bad-id"})


if __name__ == "__main__":
    unittest.main()
