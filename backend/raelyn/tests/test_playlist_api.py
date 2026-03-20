from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.playlists import list_playlist_video_counts_by_period, list_playlist_videos_by_period


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@contextmanager
def _fake_session_scope(session):
    yield session


class PlaylistApiTests(unittest.TestCase):
    def test_list_playlist_video_counts_by_period_requires_playback_admission(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        session.execute.return_value = _RowsResult(
            [
                (datetime(2026, 3, 13, 16, 30, tzinfo=timezone.utc),),
                (datetime(2026, 3, 15, 3, 26, 55, tzinfo=timezone.utc),),
                (datetime(2026, 3, 15, 12, 16, 47, tzinfo=timezone.utc),),
            ]
        )

        with patch("raelyn.api.playlists.ensure_video_published_at_backfilled"):
            with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
                result = list_playlist_video_counts_by_period(
                    playlist_id=playlist_id,
                    granularity="day",
                    start=date(2026, 3, 14),
                    end=date(2026, 3, 15),
                )

        self.assertEqual(
            [(item.period_start, item.count) for item in result],
            [(date(2026, 3, 14), 1), (date(2026, 3, 15), 2)],
        )
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("video.published_at", compiled)
        self.assertIn("asset.type =", compiled)
        self.assertIn("exists (select 1", compiled)

    def test_list_playlist_videos_by_period_requires_playback_admission(self) -> None:
        playlist_id = uuid.uuid4()
        media_id = uuid.uuid4()

        session = Mock()
        session.execute.side_effect = [
            _ScalarResult([media_id]),
            _RowsResult([]),
        ]

        with patch("raelyn.api.playlists.ensure_video_published_at_backfilled"):
            with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
                result = list_playlist_videos_by_period(
                    playlist_id=playlist_id,
                    granularity="day",
                    date=date(2026, 3, 15),
                )

        self.assertEqual(result, [])
        stmt = session.execute.call_args_list[1].args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("video.published_at is not null", compiled)
        self.assertIn("asset.type =", compiled)
        self.assertIn("exists (select 1", compiled)

    def test_playlist_period_api_backfills_published_at_before_grouping(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        session.execute.return_value = _RowsResult([])

        with patch("raelyn.api.playlists.ensure_video_published_at_backfilled") as ensure_backfilled:
            with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
                list_playlist_video_counts_by_period(
                    playlist_id=playlist_id,
                    granularity="week",
                    start=date(2026, 3, 9),
                    end=date(2026, 3, 15),
                )

        ensure_backfilled.assert_called_once_with(session)


if __name__ == "__main__":
    unittest.main()
