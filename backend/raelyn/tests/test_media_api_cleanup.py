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

from raelyn.api.media import _list_cleanup_videos, _pending_download_job_ids_for_media


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class MediaApiCleanupTests(unittest.TestCase):
    def test_pending_download_job_ids_only_returns_matching_pending_jobs(self) -> None:
        media_id = uuid.uuid4()
        video_id = uuid.uuid4()
        other_video_id = uuid.uuid4()
        job_id = uuid.uuid4()

        session = Mock()
        session.execute.side_effect = [
            _ScalarResult([video_id]),
            _ScalarResult(
                [
                    SimpleNamespace(id=job_id, params={"video_id": str(video_id)}),
                    SimpleNamespace(id=uuid.uuid4(), params={"video_id": str(other_video_id)}),
                    SimpleNamespace(id=uuid.uuid4(), params={}),
                ]
            ),
        ]

        result = _pending_download_job_ids_for_media(session, media_id=media_id)

        self.assertEqual(result, [job_id])

    def test_list_cleanup_videos_excludes_rows_with_assets_or_active_jobs(self) -> None:
        keep_video_id = uuid.uuid4()
        with_asset_video_id = uuid.uuid4()
        with_job_video_id = uuid.uuid4()

        media = SimpleNamespace(id=uuid.uuid4(), name="media-a")
        rows = [
            (
                SimpleNamespace(
                    id=keep_video_id,
                    media_id=media.id,
                    provider="youtube",
                    provider_video_id="keep-video-1",
                    status="discovered",
                    title="keep",
                    created_at=None,
                ),
                media,
            ),
            (
                SimpleNamespace(
                    id=with_asset_video_id,
                    media_id=media.id,
                    provider="youtube",
                    provider_video_id="asset-video",
                    status="discovered",
                    title="asset",
                    created_at=None,
                ),
                media,
            ),
            (
                SimpleNamespace(
                    id=with_job_video_id,
                    media_id=media.id,
                    provider="youtube",
                    provider_video_id="job-video-1",
                    status="discovered",
                    title="job",
                    created_at=None,
                ),
                media,
            ),
        ]

        active_job = SimpleNamespace(params={"video_id": str(with_job_video_id)})
        session = Mock()
        session.execute.side_effect = [
            _RowsResult(rows),
            _ScalarResult([with_asset_video_id]),
            _ScalarResult([active_job]),
        ]

        result = _list_cleanup_videos(session)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0].id, keep_video_id)


if __name__ == "__main__":
    unittest.main()
