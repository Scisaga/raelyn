from __future__ import annotations

import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, literal, select, text, union_all
from sqlalchemy.dialects.postgresql import JSONB, UUID

from raelyn.db import SessionLocal
from raelyn.jobs.claim import _ai_job_content_time, _claim_order_by
from raelyn.models import Job, PlaylistMedia, Video
from raelyn.services.periods import local_date, period_bounds_utc


@unittest.skipUnless(
    os.environ.get("RAELYN_RUN_REAL_DB_TESTS") == "1",
    "设置 RAELYN_RUN_REAL_DB_TESTS=1；只读 PostgreSQL，复用真实同步视频，不领取或写入任务",
)
class AiClaimOrderingRealIntegrationTests(unittest.TestCase):
    """虚拟 Job 参数仅放在查询 CTE 中；Video 来自上游真实同步产物。"""

    now = datetime(2026, 10, 16, 4, tzinfo=timezone.utc)

    @classmethod
    def setUpClass(cls) -> None:
        with SessionLocal() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise unittest.SkipTest("需要 PostgreSQL 16，与项目部署保持一致")
            session.execute(text("SET TRANSACTION READ ONLY"))
            rows = session.execute(
                select(Video.id, Video.published_at)
                .where(Video.published_at.is_not(None))
                .order_by(Video.published_at.desc())
                .limit(50)
            ).all()
            if not rows:
                raise unittest.SkipTest("需先通过真实媒体同步生成带 published_at 的视频")
            older = next((row for row in rows if row.published_at < rows[0].published_at), None)
            if older is None:
                raise unittest.SkipTest("需真实同步生成至少两个发布时间不同的视频")
            cls.newer, cls.older = rows[0], older

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.addCleanup(self.session.close)
        self.session.execute(text("SET TRANSACTION READ ONLY"))
        self.session.execute(text("SET LOCAL statement_timeout='10s'"))

    def content_time(self, job_type: str, params: Any):
        virtual_job = select(
            literal(job_type).label("type"), literal(params, type_=JSONB).label("params"),
        ).cte("job")
        statement = select(_ai_job_content_time(self.now)).add_cte(virtual_job)
        return self.session.scalar(statement)

    def test_single_video_and_polish_use_actual_published_time(self) -> None:
        for job_type in ("video.extract_events", "video.polish_transcript"):
            with self.subTest(job_type=job_type):
                self.assertEqual(
                    self.content_time(job_type, {"video_id": str(self.newer.id)}), self.newer.published_at,
                )

    def test_batch_uses_latest_member_and_ignores_invalid_member_ids(self) -> None:
        actual = self.content_time("video.extract_events_batch", {
            "video_ids": [str(self.older.id), "invalid-uuid", {}, str(self.newer.id)],
        })
        self.assertEqual(actual, self.newer.published_at)

    def test_playlist_parent_uses_latest_published_source_video(self) -> None:
        playlist_id = self.session.scalar(select(PlaylistMedia.playlist_id).limit(1))
        if playlist_id is None:
            self.skipTest("需要先把真实同步媒体加入观测域")
        expected = self.session.scalar(
            select(Video.published_at)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(PlaylistMedia.playlist_id == playlist_id, Video.published_at.is_not(None))
            .order_by(Video.published_at.desc())
            .limit(1)
        )
        self.assertEqual(self.content_time("playlist.backfill_events", {"playlist_id": str(playlist_id)}), expected)

    def test_bad_json_types_ids_and_calendar_dates_stay_unknown(self) -> None:
        cases = [
            ("video.extract_events", {"video_id": "not-a-uuid"}),
            ("video.polish_transcript", {"video_id": {"id": str(self.newer.id)}}),
            ("video.extract_events", []),
            ("video.extract_events_batch", {"video_ids": str(self.newer.id)}),
            ("video.extract_events_batch", {"video_ids": [None, 12, {}, "not-a-uuid"]}),
            ("brief.generate_period", {"period_start": "2026-02-31"}),
            ("brief.generate_daily", {"date": "infinity"}),
            ("playlist.backfill_events_range", {"range_end": ["2026-10-01"]}),
            ("playlist.backfill_events", {"playlist_id": "not-a-uuid"}),
        ]
        for job_type, params in cases:
            with self.subTest(job_type=job_type, params=params):
                self.assertIsNone(self.content_time(job_type, params))

    def test_brief_business_day_and_future_range_boundary_use_local_midnight(self) -> None:
        expected, _ = period_bounds_utc(date(2026, 10, 15), "day")
        self.assertEqual(self.content_time("brief.generate_period", {"period_start": "2026-10-15"}), expected)
        self.assertEqual(self.content_time("brief.generate_daily", {"date": "2026-10-15"}), expected)
        today = local_date(self.now)
        assert today is not None
        capped, _ = period_bounds_utc(today, "day")
        self.assertEqual(
            self.content_time("playlist.backfill_events_range", {"range_end": "2026-11-01"}), capped,
        )

    def test_ai_subset_prefers_new_content_while_other_roles_keep_priority_order(self) -> None:
        newer_job, older_job, unknown_job = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        def row(job_id, video_id, priority, created_at):
            return select(
                literal(job_id, type_=UUID(as_uuid=True)).label("id"),
                literal("video.extract_events").label("type"),
                literal({"video_id": str(video_id)}, type_=JSONB).label("params"),
                literal(priority).label("priority"),
                literal(self.now, type_=DateTime(timezone=True)).label("scheduled_for"),
                literal(created_at, type_=DateTime(timezone=True)).label("created_at"),
            )

        virtual_jobs = union_all(
            row(newer_job, self.newer.id, 1, self.now - timedelta(days=1)),
            row(older_job, self.older.id, 100, self.now),
            row(unknown_job, "not-a-uuid", 1000, self.now),
        ).cte("job")
        for types, expected in (
            (["video.extract_events"], [newer_job, older_job, unknown_job]),
            (["video.download.youtube"], [unknown_job, older_job, newer_job]),
            (None, [unknown_job, older_job, newer_job]),
            (["video.extract_events", "video.download.youtube"], [unknown_job, older_job, newer_job]),
        ):
            with self.subTest(type_in=types):
                statement = select(Job.id).add_cte(virtual_jobs).order_by(*_claim_order_by(types, now=self.now))
                self.assertEqual(self.session.scalars(statement).all(), expected)


if __name__ == "__main__":
    # RAELYN_RUN_REAL_DB_TESTS=1 PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_ai_claim_ordering_real_integration
    unittest.main()
