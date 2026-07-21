from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.playlists import (
    _active_playlist_event_backfill_job,
    _candidate_detail_out,
    _event_regime_summary,
    _playlist_event_backfill_job_out,
    EventRegimeCandidatePatch,
    PlaylistEventsSummaryOut,
    get_playlist_events_summary,
    get_playlist_event_regime_candidates,
    get_playlist_event_regime_signals,
    list_playlist_event_entity_suggestions,
    list_playlist_events,
    list_playlist_video_counts_by_period,
    list_playlist_videos_by_period,
    patch_playlist_event_regime_candidate,
)
from raelyn.models import EventRegimeRun, EventRegimeState, Job


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

    def one(self):
        return self._rows[0]


class _ScalarOneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _BackfillProgressSession:
    def __init__(
        self,
        *,
        parent: Job,
        range_ids: list[uuid.UUID],
        range_rows: list[tuple[str, dict | None]],
        video_status_rows: list[tuple[str, int]],
    ) -> None:
        self.parent = parent
        self.range_ids = list(range_ids)
        self.range_rows = list(range_rows)
        self.video_jobs = [
            Job(
                id=uuid.uuid4(),
                type="video.extract_events_batch",
                status=status,
                params={"video_ids": [str(index) for index in range(count)]},
                result={"videos": count} if status == "succeeded" else None,
            )
            for status, count in video_status_rows
        ]
        self.execute_calls = 0

    def get(self, model, key):
        if model is Job and key == self.parent.id:
            return self.parent
        return None

    def execute(self, _stmt):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _ScalarResult(self.range_ids)
        if self.execute_calls == 2:
            return _RowsResult(self.range_rows)
        return _ScalarResult(self.video_jobs)


class _ActiveBackfillSession:
    def __init__(self, *, video_job: Job, range_job: Job) -> None:
        self.video_job = video_job
        self.range_job = range_job
        self.execute_calls = 0

    def get(self, model, key):
        if model is Job and key == self.range_job.id:
            return self.range_job
        return None

    def execute(self, _stmt):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _ScalarResult([])
        return _ScalarResult([self.video_job])


@contextmanager
def _fake_session_scope(session):
    yield session


class PlaylistApiTests(unittest.TestCase):
    def test_active_backfill_detects_running_video_child_after_range_finished(self) -> None:
        playlist_id = uuid.uuid4()
        range_id = uuid.uuid4()
        video_job = Job(
            id=uuid.uuid4(),
            type="video.extract_events",
            status="running",
            params={"video_id": str(uuid.uuid4()), "force": True},
            parent_job_id=range_id,
        )
        range_job = Job(
            id=range_id,
            type="playlist.backfill_events_range",
            status="succeeded",
            params={"playlist_id": str(playlist_id), "force": True},
        )
        session = _ActiveBackfillSession(video_job=video_job, range_job=range_job)

        self.assertIs(_active_playlist_event_backfill_job(session, playlist_id), video_job)

    def test_event_backfill_job_out_reports_force_video_progress_and_duration(self) -> None:
        parent_id = uuid.uuid4()
        range_id = uuid.uuid4()
        started_at = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        parent = Job(
            id=parent_id,
            type="playlist.backfill_events",
            status="succeeded",
            params={"playlist_id": str(uuid.uuid4()), "force": True},
            created_at=started_at,
            started_at=started_at,
        )
        range_job = Job(
            id=range_id,
            type="playlist.backfill_events_range",
            status="running",
            params={"playlist_id": str(uuid.uuid4()), "force": True},
            result={"scanned": 10, "enqueued": 8, "skipped": 2, "force": True},
            parent_job_id=parent_id,
            created_at=started_at,
            started_at=started_at,
        )
        session = _BackfillProgressSession(
            parent=parent,
            range_ids=[range_id, uuid.uuid4()],
            range_rows=[
                ("succeeded", {"scanned": 10, "enqueued": 8, "skipped": 2}),
                ("pending", None),
            ],
            video_status_rows=[("succeeded", 3), ("pending", 4), ("running", 1), ("failed", 2)],
        )

        with patch("raelyn.api.playlists.utcnow", return_value=datetime(2026, 6, 5, 1, 1, 40, tzinfo=timezone.utc)):
            payload = _playlist_event_backfill_job_out(session, range_job)

        self.assertTrue(payload.force)
        self.assertEqual(payload.scanned, 10)
        self.assertEqual(payload.enqueued, 8)
        self.assertEqual(payload.skipped, 2)
        self.assertEqual(payload.range_finished, 1)
        self.assertEqual(payload.range_pending, 1)
        self.assertEqual(payload.range_running, 0)
        self.assertEqual(payload.range_failed, 0)
        self.assertEqual(payload.range_total, 2)
        self.assertEqual(payload.video_extracted, 3)
        self.assertEqual(payload.video_pending, 4)
        self.assertEqual(payload.video_running, 1)
        self.assertEqual(payload.video_failed, 2)
        self.assertEqual(payload.video_total, 10)
        self.assertEqual(payload.elapsed_seconds, 100)
        self.assertEqual(payload.estimated_total_seconds, 367)

    def test_event_backfill_eta_counts_unscanned_months_as_future_video_work(self) -> None:
        parent_id = uuid.uuid4()
        started_at = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        parent = Job(
            id=parent_id,
            type="playlist.backfill_events",
            status="succeeded",
            params={"playlist_id": str(uuid.uuid4()), "force": True},
            created_at=started_at,
            started_at=started_at,
            progress_current=218,
            progress_total=218,
        )
        range_ids = [uuid.uuid4() for _ in range(23)]
        session = _BackfillProgressSession(
            parent=parent,
            range_ids=range_ids,
            range_rows=[("succeeded", {"scanned": 463, "enqueued": 463, "skipped": 0}) for _ in range(23)],
            video_status_rows=[("succeeded", 10108), ("pending", 537), ("running", 1)],
        )
        elapsed_seconds = 44 * 3600 + 48 * 60 + 39

        with patch(
            "raelyn.api.playlists.utcnow",
            return_value=datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
            + timedelta(seconds=elapsed_seconds),
        ):
            payload = _playlist_event_backfill_job_out(session, parent)

        self.assertEqual(payload.range_finished, 23)
        self.assertEqual(payload.range_pending, 195)
        self.assertEqual(payload.range_total, 218)
        self.assertEqual(payload.video_extracted, 10108)
        self.assertEqual(payload.video_total, 10646)
        self.assertGreater(payload.estimated_total_seconds or 0, elapsed_seconds * 5)

    def test_regime_summary_uses_live_coverage_and_month_only_signal_range(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        state = EventRegimeState(
            playlist_id=playlist_id,
            analysis_dirty=True,
            last_ready_run_id=run_id,
        )
        run = EventRegimeRun(
            id=run_id,
            playlist_id=playlist_id,
            status="ready",
            analysis_clock="day",
            embedding_model="old",
            embedding_dim=3,
            event_total=10,
            event_embedded=8,
            event_skipped=2,
            event_failed=1,
        )
        session = Mock()
        session.get.return_value = run
        session.execute.side_effect = [
            _ScalarOneResult(4),
            _RowsResult([(date(2010, 1, 1), date(2026, 6, 1))]),
        ]
        live_coverage = {
            "event_total": 161840,
            "event_embedded": 161840,
            "event_eligible": 160876,
            "event_scale_excluded": 3231,
            "event_skipped": 964,
            "event_failed": 0,
        }

        with patch("raelyn.api.playlists.ensure_event_regime_state", return_value=state):
            with patch("raelyn.api.playlists.active_event_regime_run", return_value=None):
                with patch("raelyn.api.playlists.pending_event_regime_job", return_value=None):
                    with patch("raelyn.api.playlists._active_playlist_event_backfill_job", return_value=None):
                        with patch("raelyn.api.playlists.playlist_event_regime_coverage", return_value=live_coverage):
                            payload = _event_regime_summary(session, playlist_id)

        self.assertEqual(payload.event_total, 161840)
        self.assertEqual(payload.event_embedded, 161840)
        self.assertEqual(payload.event_eligible, 160876)
        self.assertEqual(payload.event_scale_excluded, 3231)
        self.assertEqual(payload.event_skipped, 964)
        self.assertEqual(payload.event_failed, 0)
        self.assertEqual(payload.candidate_count, 4)
        self.assertEqual(payload.signal_start_date, date(2010, 1, 1))
        self.assertEqual(payload.signal_end_date, date(2026, 6, 1))
        range_stmt = session.execute.call_args_list[1].args[0]
        compiled = str(range_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("event_regime_signal.regime_run_id", compiled)
        self.assertNotIn("event_regime_signal.granularity", compiled)

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
        self.assertEqual(payload["evidence_video_ids"], ["v1"])
        self.assertNotIn("train_start", payload)
        self.assertNotIn("valid_start", payload)
        self.assertNotIn("test_start", payload)

    def test_regime_candidate_detail_expands_evidence_videos(self) -> None:
        candidate_id = uuid.uuid4()
        event_id = uuid.uuid4()
        video_id = uuid.uuid4()
        media_id = uuid.uuid4()
        published_at = datetime(2026, 5, 4, 2, 0, tzinfo=timezone.utc)
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
            evidence_video_ids=[str(video_id)],
            evidence_json={
                "preview": "候选断点",
                "granularity": "week",
                "videos": [{"video_id": str(video_id), "shift_score": 0.42}],
            },
            available_at=published_at,
            train_start=date(2025, 1, 1),
            valid_start=date(2026, 1, 1),
            test_start=date(2026, 5, 5),
        )
        video = SimpleNamespace(
            id=video_id,
            media_id=media_id,
            url="https://example.com/watch?v=1",
            title="证据视频标题",
            published_at=published_at,
        )
        media = SimpleNamespace(id=media_id, name="宏观频道")
        session = Mock()
        session.execute.return_value = _RowsResult([(video, media)])

        payload = _candidate_detail_out(candidate, session=session).model_dump()

        self.assertEqual(
            payload["evidence"]["videos"],
            [
                {
                    "video_id": str(video_id),
                    "title": "证据视频标题",
                    "url": "https://example.com/watch?v=1",
                    "published_at": published_at,
                    "media_id": str(media_id),
                    "media_name": "宏观频道",
                    "shift_score": 0.42,
                }
            ],
        )

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

    def test_list_playlist_events_orders_by_observable_time_before_target_time(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            result = list_playlist_events(playlist_id=playlist_id)

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        available_pos = compiled.find("market_event.available_at desc")
        target_pos = compiled.find("market_event.event_time_start desc")
        self.assertGreaterEqual(available_pos, 0)
        self.assertGreater(target_pos, available_pos)

    def test_list_playlist_events_filters_current_period_by_available_time(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            result = list_playlist_events(playlist_id=playlist_id, period=date(2026, 6, 1), granularity="day")

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("market_event.available_at >=", compiled)
        self.assertIn("market_event.available_at <", compiled)

    def test_playlist_events_summary_accepts_current_period_filter(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        summary = PlaylistEventsSummaryOut(playlist_id=playlist_id)

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_events_summary", return_value=summary) as summarize:
                result = get_playlist_events_summary(playlist_id=playlist_id, period=date(2026, 6, 1), granularity="day")

        self.assertEqual(result, summary)
        kwargs = summarize.call_args.kwargs
        self.assertIsNotNone(kwargs.get("available_start"))
        self.assertIsNotNone(kwargs.get("available_end"))

    def test_playlist_event_entity_suggestions_filter_current_period(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _RowsResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            result = list_playlist_event_entity_suggestions(
                playlist_id=playlist_id,
                q="goldman",
                status="accepted",
                period=date(2026, 6, 1),
                granularity="day",
            )

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("market_event.available_at >=", compiled)
        self.assertIn("market_event.available_at <", compiled)
        self.assertIn("market_event.status = 'accepted'", compiled)
        self.assertIn("market_event_entity.name ilike", compiled)
        self.assertIn("group by market_event_entity.entity_type", compiled)

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
        self.assertIn("row_number() over", compiled)
        self.assertIn("left outer join", compiled)
        self.assertIn("asset.type =", compiled)
        self.assertIn("exists (select 1", compiled)

    def test_list_playlist_videos_by_period_requires_playback_admission(self) -> None:
        playlist_id = uuid.uuid4()

        session = Mock()
        session.execute.return_value = _RowsResult([])

        with patch("raelyn.api.playlists.ensure_video_published_at_backfilled"):
            with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
                result = list_playlist_videos_by_period(
                    playlist_id=playlist_id,
                    granularity="day",
                    date=date(2026, 3, 15),
                )

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("video_time_evidence", compiled)
        self.assertIn("make_timestamptz", compiled)
        self.assertIn("coalesce", compiled)
        self.assertIn("row_number() over", compiled)
        self.assertIn("left outer join", compiled)
        self.assertIn("asset.type =", compiled)
        self.assertIn("exists (select 1", compiled)


if __name__ == "__main__":
    unittest.main()
