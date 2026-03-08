from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.media import (
    _delete_cleanup_videos,
    _filter_cleanup_candidates,
    _list_cleanup_videos,
    _pending_download_job_ids_for_media,
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
        rows = [(SimpleNamespace(id=uuid.uuid4(), created_at=1), SimpleNamespace(id=uuid.uuid4()))]
        session = Mock()
        session.execute.side_effect = [
            Mock(all=Mock(return_value=rows)),
            _ScalarResult([]),
            _ScalarResult([]),
            Mock(all=Mock(return_value=[])),
        ]

        result, has_more = _list_cleanup_videos(session)

        self.assertEqual(result, rows)
        self.assertFalse(has_more)

    def test_filter_cleanup_candidates_excludes_assets_and_active_jobs(self) -> None:
        keep_video_id = uuid.uuid4()
        asset_video_id = uuid.uuid4()
        job_video_id = uuid.uuid4()
        media = SimpleNamespace(id=uuid.uuid4(), name="media-a")
        rows = [
            (SimpleNamespace(id=keep_video_id), media),
            (SimpleNamespace(id=asset_video_id), media),
            (SimpleNamespace(id=job_video_id), media),
        ]

        session = Mock()
        session.execute.side_effect = [
            _ScalarResult([asset_video_id]),
            _ScalarResult([str(job_video_id)]),
        ]

        result = _filter_cleanup_candidates(session, rows)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0].id, keep_video_id)

    def test_delete_cleanup_videos_deletes_in_batches(self) -> None:
        first_video = SimpleNamespace(id=uuid.uuid4(), created_at=2)
        second_video = SimpleNamespace(id=uuid.uuid4(), created_at=1)
        media = SimpleNamespace(id=uuid.uuid4())
        delete_result = SimpleNamespace(rowcount=1)
        session = Mock()
        session.execute.side_effect = [
            Mock(all=Mock(return_value=[(first_video, media)])),
            _ScalarResult([]),
            _ScalarResult([]),
            delete_result,
            Mock(all=Mock(return_value=[(second_video, media)])),
            _ScalarResult([]),
            _ScalarResult([]),
            delete_result,
            Mock(all=Mock(return_value=[])),
        ]

        deleted = _delete_cleanup_videos(session, batch_size=1)

        self.assertEqual(deleted, 2)


if __name__ == "__main__":
    unittest.main()
