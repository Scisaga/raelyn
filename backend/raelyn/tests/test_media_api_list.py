from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.media import list_media
from raelyn.models import Media


class _ScalarResult:
    def __init__(self, values) -> None:
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _RowsResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


@contextmanager
def _fake_session_scope(session):
    yield session


class MediaApiListTests(unittest.TestCase):
    def test_list_media_counts_only_returned_page_media(self) -> None:
        media_a = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="a",
            url="https://www.youtube.com/@a",
            monitor_enabled=True,
        )
        media_b = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="b",
            url="https://www.youtube.com/@b",
            monitor_enabled=False,
            sync_cursor={
                "auto_disabled": {
                    "reason": "source_unavailable",
                    "message": "youtube 媒体源不可用，已自动停用监控",
                    "at": "2026-05-01T01:25:12+08:00",
                }
            },
        )
        session = Mock()
        session.execute.side_effect = [
            _ScalarResult([media_a, media_b]),
            _RowsResult([(media_a.id, 2)]),
        ]

        with patch("raelyn.api.media.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.media.active_media_delete_job_map", return_value={}):
                items = list_media(limit=2, offset=0, presign=False)

        self.assertEqual([item.video_count for item in items], [2, 0])
        self.assertIsNone(items[0].disabled_reason)
        self.assertEqual(items[1].disabled_reason, "source_unavailable")
        self.assertIn("媒体源不可用", items[1].disabled_message or "")
        count_stmt = session.execute.call_args_list[1].args[0]
        compiled = str(count_stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("where video.media_id in", compiled)
        self.assertIn("group by video.media_id", compiled)


if __name__ == "__main__":
    unittest.main()
