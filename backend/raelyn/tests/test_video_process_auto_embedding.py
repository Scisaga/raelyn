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


class VideoProcessAutoEmbeddingTests(unittest.TestCase):
    def test_auto_embedding_refresh_is_disabled_by_default_config(self) -> None:
        session = Mock()
        video_id = uuid.uuid4()

        with patch.object(video_process.settings, "auto_embed_new_video_transcripts", False):
            with patch.object(video_process, "schedule_video_embedding_refresh") as schedule_video_embedding_refresh:
                result = video_process._schedule_auto_video_embedding_refresh(
                    session,
                    video_id=video_id,
                    text_checksum_value="checksum-1",
                    priority=5,
                    parent_job_id=str(uuid.uuid4()),
                )

        self.assertIsNone(result)
        schedule_video_embedding_refresh.assert_not_called()

    def test_auto_embedding_refresh_can_be_enabled_from_config(self) -> None:
        session = Mock()
        video_id = uuid.uuid4()
        parent_job_id = str(uuid.uuid4())
        embedding_job_id = uuid.uuid4()

        with patch.object(video_process.settings, "auto_embed_new_video_transcripts", True):
            with patch.object(
                video_process,
                "schedule_video_embedding_refresh",
                return_value=embedding_job_id,
            ) as schedule_video_embedding_refresh:
                result = video_process._schedule_auto_video_embedding_refresh(
                    session,
                    video_id=video_id,
                    text_checksum_value="checksum-1",
                    priority=5,
                    parent_job_id=parent_job_id,
                )

        self.assertEqual(result, embedding_job_id)
        schedule_video_embedding_refresh.assert_called_once_with(
            session,
            video_id=video_id,
            text_checksum_value="checksum-1",
            priority=5,
            parent_job_id=parent_job_id,
        )


if __name__ == "__main__":
    unittest.main()
