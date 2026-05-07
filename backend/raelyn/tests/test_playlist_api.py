from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.playlists import (
    _candidate_detail_out,
    _playlist_analysis_summary,
    get_playlist_analysis_candidates,
    get_playlist_analysis_signals,
    list_playlist_video_counts_by_period,
    list_playlist_videos_by_period,
)


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
    def test_analysis_summary_uses_ready_run_coverage_without_rescanning_playlist(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = SimpleNamespace(
            id=run_id,
            video_total=181002,
            video_embedded=155100,
            video_skipped=6,
            video_failed=112,
        )
        session.execute.side_effect = [
            SimpleNamespace(scalar_one=lambda: 365),
            SimpleNamespace(scalar_one=lambda: 12),
            SimpleNamespace(one=lambda: (date(2025, 5, 1), date(2026, 4, 27))),
        ]
        state = SimpleNamespace(
            analysis_dirty=False,
            last_ready_run_id=run_id,
            last_requested_at=None,
            last_built_at=None,
            last_error=None,
        )

        with patch("raelyn.api.playlists.ensure_playlist_analysis_state", return_value=state):
            with patch("raelyn.api.playlists.active_playlist_analysis_run", return_value=None):
                with patch("raelyn.api.playlists.pending_playlist_analysis_job", return_value=None):
                    with patch("raelyn.api.playlists.active_playlist_embedding_backfill_job", return_value=None):
                        with patch("raelyn.api.playlists.playlist_coverage_stats") as coverage_stats:
                            summary = _playlist_analysis_summary(session, playlist_id)

        coverage_stats.assert_not_called()
        self.assertEqual(summary.video_total, 181002)
        self.assertEqual(summary.video_embedded, 155100)
        self.assertEqual(summary.video_skipped, 6)
        self.assertEqual(summary.video_failed, 112)
        self.assertEqual(summary.period_count, 365)
        self.assertEqual(summary.signal_start_date, date(2025, 5, 1))
        self.assertEqual(summary.signal_end_date, date(2026, 4, 27))

    def test_analysis_candidate_detail_excludes_training_windows(self) -> None:
        candidate_id = uuid.uuid4()
        candidate = SimpleNamespace(
            id=candidate_id,
            candidate_date=date(2026, 4, 20),
            effective_trade_date=date(2026, 4, 21),
            peak_date=date(2026, 4, 20),
            event_start=date(2026, 4, 20),
            event_end=date(2026, 4, 20),
            event_type="burst",
            status="confirmed",
            score=3.2,
            confidence=0.8,
            uncertainty=0.25,
            drift_score=0.12,
            dispersion_score=0.25,
            drift_rolling_z=3.2,
            summary="事件摘要",
            top_terms=["tariff"],
            evidence_video_ids=["v1"],
            evidence_json={
                "preview": "事件摘要",
                "videos": [],
                "detection": {
                    "method": "open_topic_burst_v1",
                    "breakpoint_date": "2026-04-20",
                    "granularity": "event",
                    "video_count": 9,
                    "media_count": 4,
                    "active_days": 3,
                    "cohesion": 0.91,
                    "representative_title": "事件摘要",
                },
            },
            available_at=datetime(2026, 4, 20, 1, 0, tzinfo=timezone.utc),
            train_start=date(2022, 4, 21),
            valid_start=date(2025, 4, 21),
            test_start=date(2026, 4, 21),
        )

        payload = _candidate_detail_out(candidate).model_dump()

        self.assertEqual(payload["event_id"], candidate_id)
        self.assertEqual(payload["event_date"], date(2026, 4, 20))
        self.assertEqual(payload["breakpoint_date"], date(2026, 4, 20))
        self.assertEqual(payload["detection_method"], "open_topic_burst_v1")
        self.assertEqual(payload["detection_granularity"], "event")
        self.assertNotIn("train_start", payload)
        self.assertNotIn("valid_start", payload)
        self.assertNotIn("test_start", payload)

    def test_analysis_candidate_detail_exposes_detection_metadata(self) -> None:
        candidate_id = uuid.uuid4()
        candidate = SimpleNamespace(
            id=candidate_id,
            candidate_date=date(2026, 5, 4),
            effective_trade_date=date(2026, 5, 5),
            peak_date=date(2026, 5, 4),
            event_start=date(2026, 4, 6),
            event_end=date(2026, 5, 31),
            event_type="regime",
            status="draft",
            score=2.75,
            confidence=0.68,
            uncertainty=0.1,
            drift_score=0.42,
            dispersion_score=0.1,
            drift_rolling_z=2.75,
            summary="候选断点",
            top_terms=[],
            evidence_video_ids=[],
            evidence_json={
                "preview": "候选断点",
                "videos": [],
                "detection": {
                    "method": "two_window_centroid_drift_v1",
                    "breakpoint_date": "2026-05-04",
                    "granularity": "week",
                    "boundary_score": 0.42,
                    "boundary_z": 2.75,
                    "before_start": "2026-04-06",
                    "before_end": "2026-05-03",
                    "after_start": "2026-05-04",
                    "after_end": "2026-05-31",
                    "supporting_granularities": ["month", "week"],
                },
            },
            available_at=datetime(2026, 5, 4, 1, 0, tzinfo=timezone.utc),
        )

        payload = _candidate_detail_out(candidate).model_dump()

        self.assertEqual(payload["breakpoint_date"], date(2026, 5, 4))
        self.assertEqual(payload["detection_method"], "two_window_centroid_drift_v1")
        self.assertEqual(payload["detection_granularity"], "week")
        self.assertEqual(payload["boundary_score"], 0.42)
        self.assertEqual(payload["boundary_z"], 2.75)
        self.assertEqual(payload["before_start"], date(2026, 4, 6))
        self.assertEqual(payload["after_end"], date(2026, 5, 31))
        self.assertEqual(payload["supporting_granularities"], ["month", "week"])

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

    def test_analysis_signals_accepts_date_range_filters(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_last_ready_run_id", return_value=run_id):
                result = get_playlist_analysis_signals(
                    playlist_id=playlist_id,
                    granularity="day",
                    since=date(2026, 1, 1),
                    until=date(2026, 1, 31),
                )

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("playlist_analysis_signal.granularity = 'day'", compiled)
        self.assertIn("playlist_analysis_signal.period_date >= '2026-01-01'", compiled)
        self.assertIn("playlist_analysis_signal.period_date <= '2026-01-31'", compiled)

    def test_analysis_candidates_orders_events_descending(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_last_ready_run_id", return_value=run_id):
                result = get_playlist_analysis_candidates(playlist_id=playlist_id)

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("playlist_analysis_candidate.candidate_date desc", compiled)

    def test_analysis_signals_rejects_invalid_date_range(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_last_ready_run_id", return_value=uuid.uuid4()):
                with self.assertRaises(HTTPException) as cm:
                    get_playlist_analysis_signals(
                        playlist_id=playlist_id,
                        since=date(2026, 2, 1),
                        until=date(2026, 1, 1),
                    )

        self.assertEqual(cm.exception.status_code, 400)
        session.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
