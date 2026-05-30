from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Job, Playlist
from raelyn.services import brief_schedule


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class BriefScheduleTests(unittest.TestCase):
    def test_desired_schedule_for_auto_uses_latest_period_cooldown(self) -> None:
        now = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc)
        last_ready_at = datetime(2026, 3, 8, 1, 0, tzinfo=timezone.utc)

        scheduled_for, policy = brief_schedule._desired_schedule_for_auto(
            now=now,
            granularity="day",
            period_start=date(2026, 3, 8),
            last_ready_at=last_ready_at,
            policy={"latest_cooldown_minutes": 120, "historical_daily_run_time": "04:00"},
        )

        self.assertEqual(policy, "latest_cooldown")
        self.assertEqual(scheduled_for, datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc))

    def test_desired_schedule_for_auto_first_latest_trigger_is_immediate(self) -> None:
        now = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc)

        scheduled_for, policy = brief_schedule._desired_schedule_for_auto(
            now=now,
            granularity="day",
            period_start=date(2026, 3, 8),
            last_ready_at=None,
            policy={"latest_cooldown_minutes": 120, "historical_daily_run_time": "04:00"},
        )

        self.assertEqual(policy, "latest_cooldown")
        self.assertEqual(scheduled_for, now)

    def test_desired_schedule_for_auto_uses_next_historical_batch(self) -> None:
        now = datetime(2026, 3, 8, 18, 0, tzinfo=timezone.utc)

        scheduled_for, policy = brief_schedule._desired_schedule_for_auto(
            now=now,
            granularity="day",
            period_start=date(2026, 3, 7),
            last_ready_at=None,
            policy={"latest_cooldown_minutes": 120, "historical_daily_run_time": "04:00"},
        )

        self.assertEqual(policy, "historical_batch")
        self.assertEqual(scheduled_for, datetime(2026, 3, 8, 20, 0, tzinfo=timezone.utc))

    def test_schedule_brief_refresh_manual_promotes_pending_job(self) -> None:
        now = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc)
        playlist_id = uuid.uuid4()
        pending_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="pending",
            priority=2,
            dedupe_key=f"brief:{playlist_id}:2026-03-08",
            params={"playlist_id": str(playlist_id), "granularity": "day", "period_start": "2026-03-08"},
            scheduled_for=datetime(2026, 3, 8, 4, 0, tzinfo=timezone.utc),
        )

        session = Mock()
        session.get.return_value = Playlist(id=playlist_id, name="p", brief_granularity="day")
        session.execute.side_effect = [_scalar_one_or_none(pending_job)]

        with patch("raelyn.services.brief_schedule.utcnow", return_value=now):
            job_id = brief_schedule.schedule_brief_refresh(
                session,
                playlist_id=playlist_id,
                granularity="day",
                period_start=date(2026, 3, 8),
                trigger_mode="manual",
                reason="manual_generate",
            )

        self.assertEqual(job_id, pending_job.id)
        self.assertEqual(pending_job.scheduled_for, now)
        self.assertEqual(pending_job.priority, 5)
        self.assertEqual(pending_job.params["trigger_mode"], "manual")
        self.assertEqual(pending_job.params["trigger_reason"], "manual_generate")
        self.assertEqual(pending_job.params["scheduled_by_policy"], "manual_immediate")
        self.assertTrue(session.add.called)

    def test_schedule_brief_refresh_auto_with_running_job_enqueues_follow_up(self) -> None:
        now = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc)
        playlist_id = uuid.uuid4()
        running_job = Job(
            id=uuid.uuid4(),
            type="brief.generate_period",
            status="running",
            dedupe_key=f"brief:{playlist_id}:2026-03-08",
            params={"playlist_id": str(playlist_id), "granularity": "day", "period_start": "2026-03-08"},
            started_at=now,
        )

        session = Mock()
        playlist = Playlist(id=playlist_id, name="p", brief_granularity="day")
        session.get.side_effect = lambda model, key: playlist if model is Playlist else None
        session.begin_nested.return_value = nullcontext()

        def _flush(objs=None):
            for obj in list(objs or []):
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush.side_effect = _flush
        session.execute.side_effect = [
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
            _scalar_one_or_none(running_job),
        ]

        with patch("raelyn.services.brief_schedule.settings.auto_generate_briefs", True):
            with patch("raelyn.services.brief_schedule.utcnow", return_value=now):
                job_id = brief_schedule.schedule_brief_refresh(
                    session,
                    playlist_id=playlist_id,
                    granularity="day",
                    period_start=date(2026, 3, 8),
                    trigger_mode="auto",
                    reason="transcript_ready",
                )

        added_jobs = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], Job)]
        self.assertEqual(len(added_jobs), 1)
        self.assertEqual(job_id, added_jobs[0].id)
        self.assertEqual(added_jobs[0].status, "pending")
        self.assertEqual(added_jobs[0].params["trigger_mode"], "auto")
        self.assertEqual(added_jobs[0].params["scheduled_by_policy"], "latest_cooldown")

    def test_schedule_brief_refresh_for_media_change_only_schedules_affected_periods(self) -> None:
        playlist_id = uuid.uuid4()
        media_a = uuid.uuid4()
        media_b = uuid.uuid4()
        timestamps = [
            datetime(2026, 3, 2, 1, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 3, 3, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 9, 5, 0, tzinfo=timezone.utc),
        ]
        session = Mock()
        session.get.return_value = Playlist(id=playlist_id, name="p", brief_granularity="week")
        session.execute.return_value = _scalars_all(timestamps)

        scheduled_periods: list[date] = []

        def _record_schedule(*args, **kwargs):
            scheduled_periods.append(kwargs["period_start"])
            return uuid.uuid4()

        with patch("raelyn.services.brief_schedule.settings.auto_generate_briefs", True):
            with patch("raelyn.services.brief_schedule.llm_enabled", return_value=True):
                with patch("raelyn.services.brief_schedule.ensure_video_published_at_backfilled") as ensure_backfilled:
                    with patch("raelyn.services.brief_schedule.schedule_brief_refresh", side_effect=_record_schedule):
                        count = brief_schedule.schedule_brief_refresh_for_media_change(
                            session,
                            playlist_id=playlist_id,
                            changed_media_ids=[media_a, media_b],
                            change_type="media_replaced",
                        )

        self.assertEqual(count, 2)
        self.assertEqual(scheduled_periods, [date(2026, 3, 2), date(2026, 3, 9)])
        ensure_backfilled.assert_called_once_with(session)
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("asset.type =", compiled)
        self.assertIn("asset.format =", compiled)
        self.assertIn("video_time_evidence", compiled)
        self.assertIn("coalesce", compiled)
        self.assertIn("exists (select 1", compiled)

    def test_schedule_brief_refresh_auto_is_disabled_by_default(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = Playlist(id=playlist_id, name="p", brief_granularity="day")

        with patch("raelyn.services.brief_schedule.settings.auto_generate_briefs", False):
            job_id = brief_schedule.schedule_brief_refresh(
                session,
                playlist_id=playlist_id,
                granularity="day",
                period_start=date(2026, 3, 8),
                trigger_mode="auto",
                reason="transcript_ready",
            )

        self.assertIsNone(job_id)
        session.execute.assert_not_called()

    def test_schedule_brief_refresh_for_media_change_is_disabled_by_default(self) -> None:
        session = Mock()

        with patch("raelyn.services.brief_schedule.settings.auto_generate_briefs", False):
            count = brief_schedule.schedule_brief_refresh_for_media_change(
                session,
                playlist_id=uuid.uuid4(),
                changed_media_ids=[uuid.uuid4()],
                change_type="media_replaced",
            )

        self.assertEqual(count, 0)
        session.get.assert_not_called()
        session.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
