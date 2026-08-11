from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import unittest
import uuid

from sqlalchemy import and_, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, aliased, sessionmaker


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.jobs.reschedule import JobTerminalFailure
from raelyn.models import Job, MarketEvent, MarketEventEmbedding
from raelyn.services.embeddings import embed_texts
from raelyn.services.event_analysis import event_embedding_texts
from raelyn.services.event_embedding_backfill import (
    EventEmbeddingCandidate,
    _apply_batch,
    _owned_running_job,
    validate_embedding_batch,
)
from raelyn.services.job_cancellation import JobCancelRequested
from raelyn.timeutil import utcnow


SOURCE_MODEL = "Qwen/Qwen3-Embedding-8B"
TARGET_MODEL = "Qwen/Qwen3-Embedding-4B"
EMBEDDING_DIM = 1024


class EventEmbeddingBackfillIntegrationTests(unittest.TestCase):
    """只在显式隔离库上运行；事件输入必须来自真实上游抽取产物。"""

    SessionFactory: sessionmaker[Session]
    test_engine = None

    @classmethod
    def setUpClass(cls) -> None:
        database_url = str(os.getenv("RAELYN_EVENT_BACKFILL_TEST_DATABASE_URL") or "").strip()
        if not database_url:
            raise unittest.SkipTest(
                "需设置 RAELYN_EVENT_BACKFILL_TEST_DATABASE_URL，并先在隔离库运行真实事件抽取生成 accepted 事件"
            )
        if make_url(database_url) == make_url(settings.database_url):
            raise unittest.SkipTest("隔离测试库不能与当前开发数据库相同")
        cls.test_engine = create_engine(database_url, pool_pre_ping=True)
        cls.SessionFactory = sessionmaker(bind=cls.test_engine, expire_on_commit=False, class_=Session)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.test_engine is not None:
            cls.test_engine.dispose()

    def _session(self) -> Session:
        return self.SessionFactory()

    @staticmethod
    def _new_running_job(session: Session, *, canceled: bool = False) -> Job:
        job = Job(
            type="event.backfill_embeddings",
            status="running",
            worker_id="integration-worker",
            execution_token=uuid.uuid4(),
            params={
                "source_model": SOURCE_MODEL,
                "target_model": TARGET_MODEL,
                "embedding_dim": EMBEDDING_DIM,
                "batch_size": 64,
            },
            cancel_requested_at=utcnow() if canceled else None,
        )
        session.add(job)
        session.commit()
        return job

    @staticmethod
    def _source_events_without_target(session: Session, *, limit: int) -> list[MarketEvent]:
        source = aliased(MarketEventEmbedding)
        target = aliased(MarketEventEmbedding)
        return list(
            session.execute(
                select(MarketEvent)
                .join(
                    source,
                    and_(
                        source.event_id == MarketEvent.id,
                        source.embedding_model == SOURCE_MODEL,
                        source.embedding_dim == EMBEDDING_DIM,
                        source.status == "ready",
                        source.vector.is_not(None),
                    ),
                )
                .outerjoin(
                    target,
                    and_(
                        target.event_id == MarketEvent.id,
                        target.embedding_model == TARGET_MODEL,
                        target.embedding_dim == EMBEDDING_DIM,
                    ),
                )
                .where(MarketEvent.status == "accepted", target.id.is_(None))
                .limit(limit)
            ).scalars()
        )

    @staticmethod
    def _accepted_event_without_embedding(session: Session) -> MarketEvent | None:
        source = aliased(MarketEventEmbedding)
        target = aliased(MarketEventEmbedding)
        return session.execute(
            select(MarketEvent)
            .outerjoin(
                source,
                and_(
                    source.event_id == MarketEvent.id,
                    source.embedding_model == SOURCE_MODEL,
                    source.embedding_dim == EMBEDDING_DIM,
                ),
            )
            .outerjoin(
                target,
                and_(
                    target.event_id == MarketEvent.id,
                    target.embedding_model == TARGET_MODEL,
                    target.embedding_dim == EMBEDDING_DIM,
                ),
            )
            .where(
                MarketEvent.status == "accepted",
                source.id.is_(None),
                target.id.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()

    def test_owned_job_rejects_cancel_and_lost_execution(self) -> None:
        with self._session() as session:
            canceled = self._new_running_job(session, canceled=True)
            with self.assertRaises(JobCancelRequested):
                _owned_running_job(
                    session,
                    job_id=canceled.id,
                    worker_id=str(canceled.worker_id),
                    execution_token=canceled.execution_token,
                    lock=True,
                )
            session.rollback()

            active = self._new_running_job(session)
            with self.assertRaises(JobTerminalFailure):
                _owned_running_job(
                    session,
                    job_id=active.id,
                    worker_id=str(active.worker_id),
                    execution_token=uuid.uuid4(),
                    lock=True,
                )
            session.rollback()
            for job_id in (canceled.id, active.id):
                row = session.get(Job, job_id)
                if row is not None:
                    session.delete(row)
            session.commit()

    def test_real_batch_updates_old_row_in_place_inserts_missing_and_is_resumable(self) -> None:
        with self._session() as session:
            source_events = self._source_events_without_target(session, limit=1)
            missing_event = self._accepted_event_without_embedding(session)
            if not source_events or missing_event is None:
                self.skipTest(
                    "隔离库需同时包含一条 ready 8B accepted 事件和一条尚无 embedding 的 accepted 事件；请先运行真实上游事件任务"
                )
            events = [source_events[0], missing_event]
            texts = event_embedding_texts(session, events)
            source_row = session.execute(
                select(MarketEventEmbedding).where(
                    MarketEventEmbedding.event_id == source_events[0].id,
                    MarketEventEmbedding.embedding_model == SOURCE_MODEL,
                    MarketEventEmbedding.embedding_dim == EMBEDDING_DIM,
                )
            ).scalar_one()
            source_backup = {
                "model": source_row.embedding_model,
                "status": source_row.status,
                "vector": source_row.vector,
                "checksum": source_row.text_checksum,
                "skip_reason": source_row.skip_reason,
                "generated_at": source_row.generated_at,
            }
            job = self._new_running_job(session)
            candidates = [
                EventEmbeddingCandidate(
                    event_id=event.id,
                    text=texts[event.id],
                    checksum=hashlib.sha256(texts[event.id].encode("utf-8")).hexdigest(),
                )
                for event in events
            ]
            vectors = validate_embedding_batch(
                embed_texts([candidate.text for candidate in candidates]),
                expected_count=2,
                model=TARGET_MODEL,
                dim=EMBEDDING_DIM,
            )
            result = _apply_batch(
                session,
                job_id=job.id,
                worker_id=str(job.worker_id),
                execution_token=job.execution_token,
                source_model=SOURCE_MODEL,
                target_model=TARGET_MODEL,
                embedding_dim=EMBEDDING_DIM,
                candidates=candidates,
                vectors=vectors,
                batch_elapsed_seconds=1.0,
            )

            migrated_source = session.get(MarketEventEmbedding, source_row.id)
            inserted = session.execute(
                select(MarketEventEmbedding).where(
                    MarketEventEmbedding.event_id == missing_event.id,
                    MarketEventEmbedding.embedding_model == TARGET_MODEL,
                    MarketEventEmbedding.embedding_dim == EMBEDDING_DIM,
                )
            ).scalar_one()
            self.assertEqual(migrated_source.embedding_model, TARGET_MODEL)
            self.assertEqual(migrated_source.status, "ready")
            self.assertEqual(inserted.status, "ready")
            self.assertEqual(result["converted"], 1)
            self.assertEqual(result["inserted"], 1)

            migrated_source.embedding_model = source_backup["model"]
            migrated_source.status = source_backup["status"]
            migrated_source.vector = source_backup["vector"]
            migrated_source.text_checksum = source_backup["checksum"]
            migrated_source.skip_reason = source_backup["skip_reason"]
            migrated_source.generated_at = source_backup["generated_at"]
            session.delete(inserted)
            session.delete(session.get(Job, job.id))
            session.commit()

    def test_batch_write_rolls_back_all_rows_on_failure(self) -> None:
        with self._session() as session:
            events = self._source_events_without_target(session, limit=2)
            if len(events) < 2:
                self.skipTest("隔离库至少需要两条仅有 ready 8B embedding 的 accepted 事件")
            texts = event_embedding_texts(session, events)
            candidates = [
                EventEmbeddingCandidate(
                    event_id=event.id,
                    text=texts[event.id],
                    checksum=hashlib.sha256(texts[event.id].encode("utf-8")).hexdigest(),
                )
                for event in events
            ]
            vector = session.execute(
                select(MarketEventEmbedding.vector).where(
                    MarketEventEmbedding.event_id == events[0].id,
                    MarketEventEmbedding.embedding_model == SOURCE_MODEL,
                    MarketEventEmbedding.embedding_dim == EMBEDDING_DIM,
                )
            ).scalar_one()
            job = self._new_running_job(session)
            with self.assertRaises(ValueError):
                _apply_batch(
                    session,
                    job_id=job.id,
                    worker_id=str(job.worker_id),
                    execution_token=job.execution_token,
                    source_model=SOURCE_MODEL,
                    target_model=TARGET_MODEL,
                    embedding_dim=EMBEDDING_DIM,
                    candidates=candidates,
                    vectors=[vector],
                    batch_elapsed_seconds=1.0,
                )
            session.rollback()
            target_count = session.execute(
                select(MarketEventEmbedding.id).where(
                    MarketEventEmbedding.event_id.in_([event.id for event in events]),
                    MarketEventEmbedding.embedding_model == TARGET_MODEL,
                    MarketEventEmbedding.embedding_dim == EMBEDDING_DIM,
                )
            ).scalars().all()
            self.assertEqual(target_count, [])
            row = session.get(Job, job.id)
            if row is not None:
                session.delete(row)
            session.commit()


if __name__ == "__main__":
    unittest.main()
