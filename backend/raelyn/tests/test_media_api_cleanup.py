from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.media import (
    _list_cleanup_videos,
    _pending_download_job_ids_for_media,
    _stale_video_delete_stmt,
    _stale_video_rows_stmt,
)


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class MediaApiCleanupTests(unittest.TestCase):
    def test_pending_download_job_ids_uses_single_db_query(self) -> None:
        session = Mock()
        session.execute.return_value = _ScalarResult([uuid.uuid4()])

        result = _pending_download_job_ids_for_media(session, media_id=uuid.uuid4())

        self.assertEqual(len(result), 1)
        session.execute.assert_called_once()

    def test_list_cleanup_videos_uses_single_db_query(self) -> None:
        rows = [("video-a", "media-a")]
        session = Mock()
        session.execute.return_value = Mock(all=Mock(return_value=rows))

        result = _list_cleanup_videos(session)

        self.assertEqual(result, rows)
        session.execute.assert_called_once()

    def test_stale_video_rows_stmt_uses_exists_not_large_in_list(self) -> None:
        sql = str(
            _stale_video_rows_stmt(limit=20).compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": False},
            )
        )

        self.assertIn("EXISTS", sql)
        self.assertNotIn("asset.video_id IN", sql)
        self.assertNotIn("video_id_1_1", sql)
        self.assertIn("job.params", sql)

    def test_stale_video_delete_stmt_uses_subquery(self) -> None:
        sql = str(
            _stale_video_delete_stmt().compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": False},
            )
        )

        self.assertIn("DELETE FROM video", sql)
        self.assertIn("SELECT video_1.id", sql)
        self.assertIn("EXISTS", sql)


if __name__ == "__main__":
    unittest.main()
