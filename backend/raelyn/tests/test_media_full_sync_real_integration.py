"""真实 PostgreSQL 去重回归；复用同步来源，只写事务内临时 schema，结束回滚。

运行：RAELYN_RUN_REAL_DB_TESTS=1 PYTHONPATH=backend ./.venv/bin/python -m unittest \
    raelyn.tests.test_media_full_sync_real_integration
"""
from __future__ import annotations

import os
import unittest
import uuid
from datetime import timedelta

from sqlalchemy import MetaData, insert, select, text
from sqlalchemy.orm import Session

from raelyn.api.config_api import _schedule_cookie_recovery_sync
from raelyn.db import engine
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Job, JobEvent, Media
from raelyn.services.media_actions import schedule_all_media_sync, schedule_media_sync
from raelyn.timeutil import utcnow


@unittest.skipUnless(os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1", "设置 RAELYN_RUN_REAL_DB_TESTS=1；使用回滚的独立 schema")
class MediaFullSyncRealIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if engine.dialect.name != "postgresql":
            raise unittest.SkipTest("需要项目实际使用的 PostgreSQL")
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            cls.fixtures = [dict(row) for row in connection.execute(
                select(Media.__table__).where(Media.monitor_enabled.is_(True)).order_by(Media.id).limit(2)
            ).mappings()]
        if len(cls.fixtures) < 2:
            raise unittest.SkipTest("需先通过真实来源导入与同步生成至少两个开启监控的 Media")

    def setUp(self) -> None:
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.transaction.rollback)
        self.schema = "test_media_full_sync_" + uuid.uuid4().hex
        self.connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        metadata = MetaData()
        for model in (Media, Job, JobEvent):
            table = model.__table__.to_metadata(metadata, schema=self.schema)
            # 不测试来源外键；来源完整行直接复用上游真实产物。
            for constraint in list(table.foreign_key_constraints):
                table.constraints.remove(constraint)
            table.foreign_keys.clear()
            for column in table.columns:
                column.foreign_keys.clear()
        metadata.create_all(self.connection)
        self.connection.execute(text(
            f'CREATE UNIQUE INDEX job_pending_dedupe_ux ON "{self.schema}".job(dedupe_key) '
            "WHERE dedupe_key IS NOT NULL AND status='pending'"
        ))
        self.connection.execute(insert(metadata.tables[f"{self.schema}.media"]), self.fixtures)
        self.session = Session(bind=self.connection.execution_options(schema_translate_map={None: self.schema}))
        self.addCleanup(self.session.close)
        self.media_id = self.fixtures[0]["id"]

    def test_full_request_upgrades_pending_recent_and_preserves_retry_budget(self) -> None:
        job_id = enqueue_job(self.session, type_="media.sync_videos", params={
            "media_id": str(self.media_id), "max_entries": 200, "public_discovery": True,
        }, priority=7, scheduled_for=utcnow() + timedelta(hours=1))
        job = self.session.get(Job, job_id)
        assert job is not None
        job.attempt, job.max_attempts = 1, 4
        self.session.flush()

        result = schedule_media_sync(self.session, self.media_id, scope="all")
        self.session.refresh(job)
        self.assertEqual(result["job_id"], str(job_id))
        self.assertEqual(job.params["max_entries"], 0)
        self.assertTrue(job.params["enqueue_existing_downloads"])
        self.assertNotIn("public_discovery", job.params)
        self.assertEqual((job.attempt, job.max_attempts, job.priority), (1, 4, 7))
        self.assertLessEqual(job.scheduled_for, utcnow())

    def test_recent_request_does_not_shrink_pending_full_scan(self) -> None:
        full = schedule_media_sync(self.session, self.media_id, scope="all")
        recent = schedule_media_sync(self.session, self.media_id, scope="recent")
        self.assertEqual(full["job_id"], recent["job_id"])
        job = self.session.get(Job, uuid.UUID(full["job_id"]))
        assert job is not None
        self.assertEqual(job.params["max_entries"], 0)
        self.assertTrue(job.params["enqueue_existing_downloads"])

    def test_bulk_full_scan_upgrades_each_monitored_media(self) -> None:
        for row in self.fixtures:
            enqueue_job(self.session, type_="media.sync_videos", params={
                "media_id": str(row["id"]), "max_entries": 200,
            })
        result = schedule_all_media_sync(self.session, scope="all")
        jobs = list(self.session.scalars(select(Job).where(Job.type == "media.sync_videos")))
        self.assertEqual(result["count"], len(self.fixtures))
        self.assertEqual(len(jobs), len(self.fixtures))
        self.assertTrue(all(j.params["max_entries"] == 0 and j.params["enqueue_existing_downloads"] for j in jobs))

    def test_cookie_recovery_preserves_full_scan_and_earlier_schedule(self) -> None:
        result = schedule_media_sync(self.session, self.media_id, scope="all")
        job = self.session.get(Job, uuid.UUID(result["job_id"]))
        assert job is not None
        original_schedule = job.scheduled_for
        job.priority, job.attempt, job.max_attempts = 11, 1, 4
        job.params = {**job.params, "download_priority": 12}
        self.session.flush()

        _schedule_cookie_recovery_sync(
            self.session, provider=self.fixtures[0]["provider"], media_id=self.media_id,
            max_entries=200, scheduled_for=utcnow() + timedelta(hours=2),
        )
        self.session.flush()
        self.session.refresh(job)

        self.assertEqual(job.params["max_entries"], 0)
        self.assertTrue(job.params["enqueue_existing_downloads"])
        self.assertTrue(job.params["cookie_recovery"])
        self.assertEqual(job.params["download_priority"], 12)
        self.assertEqual(job.scheduled_for, original_schedule)
        self.assertEqual((job.priority, job.attempt, job.max_attempts), (11, 1, 4))
        event = self.session.scalars(
            select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id.desc())
        ).first()
        assert event is not None
        assert event.data is not None
        self.assertEqual(event.data["max_entries"], 0)

    def test_cookie_recovery_preserves_unlimited_scan_and_can_advance_schedule(self) -> None:
        job_id = enqueue_job(self.session, type_="media.sync_videos", params={
            "media_id": str(self.media_id), "max_entries": 0, "public_discovery": True,
        }, scheduled_for=utcnow() + timedelta(hours=3))
        recovery_schedule = utcnow() + timedelta(hours=1)
        _schedule_cookie_recovery_sync(
            self.session, provider=self.fixtures[0]["provider"], media_id=self.media_id,
            max_entries=200, scheduled_for=recovery_schedule,
        )
        self.session.flush()
        job = self.session.get(Job, job_id)
        assert job is not None
        self.assertEqual(job.params["max_entries"], 0)
        self.assertNotIn("public_discovery", job.params)
        self.assertEqual(job.scheduled_for, recovery_schedule)

    def test_cookie_recovery_still_converts_recent_public_discovery(self) -> None:
        job_id = enqueue_job(self.session, type_="media.sync_videos", params={
            "media_id": str(self.media_id), "max_entries": 200, "public_discovery": True,
        }, priority=7)
        recovery_schedule = utcnow() + timedelta(hours=1)
        _schedule_cookie_recovery_sync(
            self.session, provider=self.fixtures[0]["provider"], media_id=self.media_id,
            max_entries=200, scheduled_for=recovery_schedule,
        )
        self.session.flush()
        job = self.session.get(Job, job_id)
        assert job is not None
        self.assertEqual(job.params["max_entries"], 200)
        self.assertTrue(job.params["cookie_recovery"])
        self.assertNotIn("public_discovery", job.params)
        self.assertEqual((job.priority, job.scheduled_for), (7, recovery_schedule))

    def test_running_recent_scan_is_unchanged_and_full_scan_is_pending(self) -> None:
        old_id = enqueue_job(self.session, type_="media.sync_videos", params={
            "media_id": str(self.media_id), "max_entries": 200,
        })
        old = self.session.get(Job, old_id)
        assert old is not None
        old.status = "running"
        self.session.flush()
        result = schedule_media_sync(self.session, self.media_id, scope="all")
        self.session.refresh(old)
        new = self.session.get(Job, uuid.UUID(result["job_id"]))
        assert new is not None
        self.assertNotEqual(old_id, new.id)
        self.assertEqual((old.status, old.params["max_entries"]), ("running", 200))
        self.assertEqual((new.status, new.params["max_entries"]), ("pending", 0))
