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
    EventRegimeCandidatePatch,
    get_playlist_event_regime_candidates,
    get_playlist_event_regime_signals,
    list_playlist_video_counts_by_period,
    list_playlist_videos_by_period,
    patch_playlist_event_regime_candidate,
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
    def test_regime_candidate_detail_exposes_event_evidence_without_training_windows(self) -> None:
        candidate_id = uuid.uuid4()
        event_id = uuid.uuid4()
        candidate = SimpleNamespace(
            id=candidate_id,
            candidate_date=date(2026, 5, 4),
            effective_trade_date=date(2026, 5, 5),
            peak_date=date(2026, 5, 4),
            event_start=date(2026, 5, 4),
            event_end=date(2026, 5, 4),
            event_type="event_regime_shift",
            status="draft",
            score=2.75,
            confidence=0.68,
            uncertainty=0.1,
            drift_score=0.42,
            dispersion_score=0.1,
            drift_rolling_z=2.75,
            summary="候选断点",
            top_terms=[],
            evidence_event_ids=[str(event_id)],
            evidence_video_ids=["v1"],
            evidence_json={"preview": "候选断点", "granularity": "week"},
            available_at=datetime(2026, 5, 4, 1, 0, tzinfo=timezone.utc),
            train_start=date(2025, 1, 1),
            valid_start=date(2026, 1, 1),
            test_start=date(2026, 5, 5),
        )

        payload = _candidate_detail_out(candidate).model_dump()

        self.assertEqual(payload["id"], candidate_id)
        self.assertEqual(payload["breakpoint_date"], date(2026, 5, 4))
        self.assertEqual(payload["detection_method"], "event_embedding_regime_v1")
        self.assertEqual(payload["detection_granularity"], "week")
        self.assertEqual(payload["evidence_event_ids"], [str(event_id)])
        self.assertNotIn("train_start", payload)
        self.assertNotIn("valid_start", payload)
        self.assertNotIn("test_start", payload)

    def test_regime_signals_accepts_date_range_filters(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_last_ready_regime_run_id", return_value=run_id):
                result = get_playlist_event_regime_signals(
                    playlist_id=playlist_id,
                    granularity="day",
                    since=date(2026, 1, 1),
                    until=date(2026, 1, 31),
                )

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("event_regime_signal.granularity = 'day'", compiled)
        self.assertIn("event_regime_signal.period_date >= '2026-01-01'", compiled)
        self.assertIn("event_regime_signal.period_date <= '2026-01-31'", compiled)

    def test_regime_candidates_orders_events_descending(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_last_ready_regime_run_id", return_value=run_id):
                result = get_playlist_event_regime_candidates(playlist_id=playlist_id)

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("event_regime_candidate.candidate_date desc", compiled)

    def test_regime_candidate_patch_accepts_event_status_names(self) -> None:
        playlist_id = uuid.uuid4()
        candidate_id = uuid.uuid4()
        run_id = uuid.uuid4()
        candidate = SimpleNamespace(
            id=candidate_id,
            regime_run_id=run_id,
            candidate_date=date(2026, 5, 4),
            effective_trade_date=date(2026, 5, 5),
            peak_date=date(2026, 5, 4),
            event_start=date(2026, 5, 4),
            event_end=date(2026, 5, 4),
            event_type="event_regime_shift",
            status="draft",
            score=2.75,
            confidence=0.68,
            uncertainty=0.1,
            drift_score=0.42,
            dispersion_score=0.1,
            drift_rolling_z=2.75,
            summary="候选断点",
            top_terms=[],
            evidence_event_ids=[],
            evidence_video_ids=[],
            evidence_json={},
            available_at=None,
        )
        session = Mock()
        session.get.side_effect = [object(), candidate]

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_last_ready_regime_run_id", return_value=run_id):
                result = patch_playlist_event_regime_candidate(
                    playlist_id=playlist_id,
                    candidate_id=candidate_id,
                    payload=EventRegimeCandidatePatch(status="accepted"),
                )

        self.assertEqual(candidate.status, "accepted")
        self.assertEqual(result.status, "accepted")
        session.flush.assert_called_once_with([candidate])

    def test_regime_signals_rejects_invalid_date_range(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_last_ready_regime_run_id", return_value=uuid.uuid4()):
                with self.assertRaises(HTTPException) as cm:
                    get_playlist_event_regime_signals(
                        playlist_id=playlist_id,
                        since=date(2026, 2, 1),
                        until=date(2026, 1, 1),
                    )

        self.assertEqual(cm.exception.status_code, 400)
        session.execute.assert_not_called()

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
        self.assertIn("video_time_evidence", compiled)
        self.assertIn("make_timestamptz", compiled)
        self.assertIn("coalesce", compiled)
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
        self.assertIn("video_time_evidence", compiled)
        self.assertIn("make_timestamptz", compiled)
        self.assertIn("coalesce", compiled)
        self.assertIn("asset.type =", compiled)
        self.assertIn("exists (select 1", compiled)


if __name__ == "__main__":
    unittest.main()
