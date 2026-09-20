from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import sys
from threading import Event
import unittest
import uuid

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Job, JobEvent, Video
from raelyn.services.pg_lock import advisory_lock, advisory_lock_any, lock_key
from raelyn.timeutil import utcnow


class JobPostgresIntegrationTests(unittest.TestCase):
    """真实数据库验证：只写显式隔离库，视频参数复用上游同步产物。"""

    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.getenv("RAELYN_JOB_TEST_DATABASE_URL", "").strip()
        if not database_url:
            raise unittest.SkipTest("需设置 RAELYN_JOB_TEST_DATABASE_URL，指向独立 PostgreSQL 测试库")
        target = make_url(database_url)
        source = make_url(settings.database_url)
        if target.get_backend_name() != "postgresql":
            raise unittest.SkipTest("本测试需要真实 PostgreSQL")
        if (target.host, target.port, target.database) == (source.host, source.port, source.database):
            raise unittest.SkipTest("隔离测试库不能与当前开发数据库相同")

        source_engine = create_engine(settings.database_url)
        try:
            with source_engine.connect() as connection:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                cls.videos = connection.execute(
                    select(Video.id, Video.media_id).where(Video.provider == "youtube").limit(2000)
                ).all()
        finally:
            source_engine.dispose()
        if len(cls.videos) < 2000:
            raise unittest.SkipTest("需先通过真实 YouTube 媒体同步生成至少 2000 条视频记录")

        cls.schema = f"job_sync_test_{uuid.uuid4().hex}"
        cls.admin_engine = create_engine(database_url)
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {cls.schema}"))
        cls.engine = create_engine(
            database_url,
            pool_size=4,
            max_overflow=2,
            connect_args={"options": f"-c search_path={cls.schema} -c statement_timeout=10000"},
        )
        Job.__table__.create(cls.engine)
        JobEvent.__table__.create(cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(text(
                "CREATE UNIQUE INDEX job_brief_dedupe_pending_ux ON job(dedupe_key) "
                "WHERE dedupe_key IS NOT NULL AND status='pending'"
            ))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {cls.schema} CASCADE"))
        cls.admin_engine.dispose()

    def setUp(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("TRUNCATE job_event, job"))

    def _enqueue(self, session: Session, index: int = 0) -> uuid.UUID:
        return enqueue_job(
            session,
            type_="video.enrich_metadata.youtube",
            params={"video_id": str(self.videos[index].id)},
        )

    @staticmethod
    def _lock_counts(session: Session) -> dict[str, int]:
        return dict(session.execute(text(
            "SELECT locktype, count(*) FROM pg_locks "
            "WHERE pid=pg_backend_pid() GROUP BY locktype"
        )).all())

    def test_large_sync_enqueue_keeps_lock_count_bounded(self) -> None:
        with Session(self.engine) as session:
            baseline: dict[str, int] = {}
            for index in range(2000):
                self._enqueue(session, index)
                if index == 99:
                    baseline = self._lock_counts(session)
            final = self._lock_counts(session)
            self.assertEqual(final.get("advisory", 0), 0)
            self.assertLessEqual(sum(final.values()), sum(baseline.values()) + 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 2000)
            self.assertEqual(session.scalar(select(func.count()).select_from(JobEvent)), 2000)

    def test_pending_reuse_preserves_retry_budget_params_and_schedule(self) -> None:
        cases = [
            ("video.enrich_metadata.youtube", {"video_id": str(self.videos[0].id)}),
            ("video.backfill_subtitles.youtube", {"video_id": str(self.videos[0].id)}),
            ("media.sync_videos", {"media_id": str(self.videos[0].media_id)}),
        ]
        for type_, params in cases:
            with self.subTest(type=type_), Session(self.engine) as session:
                scheduled_for = utcnow() + timedelta(hours=1)
                job_id = enqueue_job(session, type_=type_, params=params, priority=7, scheduled_for=scheduled_for)
                job = session.get(Job, job_id)
                self.assertIsNotNone(job)
                assert job is not None
                job.attempt, job.max_attempts = 1, 4
                job.params = {**job.params, "已有参数": True}
                session.commit()
                reused = enqueue_job(session, type_=type_, params=params, priority=99)
                self.assertEqual(reused, job_id)
                session.refresh(job)
                self.assertEqual((job.attempt, job.max_attempts, job.priority), (1, 4, 7))
                self.assertEqual(job.scheduled_for, scheduled_for)
                self.assertTrue(job.params["已有参数"])
                self.assertEqual(session.scalar(select(func.count()).select_from(JobEvent).where(JobEvent.job_id == job_id)), 1)

    def test_running_job_allows_new_pending_job(self) -> None:
        with Session(self.engine) as session:
            first = self._enqueue(session)
            job = session.get(Job, first)
            assert job is not None
            job.status = "running"
            second = self._enqueue(session)
            self.assertNotEqual(first, second)
            pending = session.get(Job, second)
            assert pending is not None
            self.assertEqual(pending.status, "pending")

    def test_rollback_removes_job_and_enqueue_event(self) -> None:
        with Session(self.engine) as session:
            self._enqueue(session)
            session.rollback()
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(JobEvent)), 0)

    def test_concurrent_enqueue_returns_one_job_and_one_event(self) -> None:
        ready = Event()
        with Session(self.engine) as first, ThreadPoolExecutor(max_workers=1) as pool:
            first_id = self._enqueue(first)

            def enqueue_second() -> uuid.UUID:
                with Session(self.engine) as second:
                    ready.set()
                    job_id = self._enqueue(second)
                    second.commit()
                    return job_id

            future = pool.submit(enqueue_second)
            try:
                self.assertTrue(ready.wait(5))
                first.commit()
                self.assertEqual(future.result(timeout=10), first_id)
            finally:
                first.rollback()
            self.assertEqual(first.scalar(select(func.count()).select_from(Job)), 1)
            self.assertEqual(first.scalar(select(func.count()).select_from(JobEvent)), 1)

    def test_special_dirty_and_active_dedupe_keep_their_semantics(self) -> None:
        with Session(self.engine) as session:
            playlist_id = str(uuid.uuid4())
            first = enqueue_job(session, type_="playlist.mark_event_map_dirty", params={"playlist_id": playlist_id, "reason": "first"}, priority=1)
            second = enqueue_job(session, type_="playlist.mark_event_map_dirty", params={"playlist_id": playlist_id, "reason": "second"}, priority=5)
            self.assertEqual(first, second)
            job = session.get(Job, first)
            assert job is not None
            self.assertEqual(job.priority, 5)
            self.assertEqual(job.params["reasons"], ["first", "second"])
            params = {"source_model": "source", "target_model": "target", "embedding_dim": 1024}
            backfill = enqueue_job(session, type_="event.backfill_embeddings", params=params)
            running = session.get(Job, backfill)
            assert running is not None
            running.status = "running"
            session.flush()
            self.assertEqual(enqueue_job(session, type_="event.backfill_embeddings", params=params), backfill)
            self.assertEqual(self._lock_counts(session).get("advisory"), 2)

    def test_session_guard_survives_business_commit_and_releases_after_exit(self) -> None:
        name = f"{self.schema}:guard"
        with Session(self.engine) as owner, Session(self.engine) as contender:
            with advisory_lock(owner, name) as acquired:
                self.assertTrue(acquired)
                self._enqueue(owner)
                owner.commit()
                with advisory_lock_any(contender, [name]) as other:
                    self.assertIsNone(other)
            with advisory_lock(contender, name) as acquired:
                self.assertTrue(acquired)

    def test_aborted_business_transaction_preserves_first_error_and_unlocks(self) -> None:
        name = f"{self.schema}:abort"
        with Session(self.engine) as session:
            with self.assertRaises(DBAPIError) as raised:
                with advisory_lock_any(session, [name]):
                    session.execute(text("SELECT 1 / 0"))
            self.assertEqual(getattr(raised.exception.orig, "sqlstate", None), "22012")
            session.rollback()
        with Session(self.engine) as contender:
            with advisory_lock(contender, name) as acquired:
                self.assertTrue(acquired)
        with self.engine.connect() as connection:
            key = lock_key(name)
            self.assertEqual(connection.execute(text(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                "AND classid=:high AND objid=:low AND objsubid=1"
            ), {"high": key >> 32, "low": key & 0xFFFFFFFF}).scalar_one(), 0)


if __name__ == "__main__":
    # 示例：RAELYN_JOB_TEST_DATABASE_URL=<隔离库连接串> ./.venv/bin/python -m unittest backend.raelyn.tests.test_job_postgres_integration
    unittest.main()
