from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine, event, text

from raelyn.db import _cancel_legacy_analysis_jobs, _database_engine_kwargs, _execute_best_effort_ddl
from raelyn.models import MarketEvent, MarketEventEmbedding, VideoTimeEvidence


class _NestedTransaction:
    def __init__(self, conn: "_FakeConn") -> None:
        self.conn = conn

    def __enter__(self) -> "_NestedTransaction":
        self.conn.nested_entered += 1
        return self

    def __exit__(self, exc_type, _exc, _tb) -> bool:
        self.conn.nested_exits.append(exc_type)
        return False


class _FakeConn:
    def __init__(
        self,
        *,
        dialect_name: str = "postgresql",
        fail_on: str = "",
        index_exists: bool = False,
    ) -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.fail_on = fail_on
        self.index_exists = index_exists
        self.calls: list[str] = []
        self.nested_entered = 0
        self.nested_exits: list[type[BaseException] | None] = []

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction(self)

    def execute(self, statement, _params=None):
        sql = str(statement)
        self.calls.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("lock timeout")
        return SimpleNamespace(
            scalar_one_or_none=lambda: 1 if self.index_exists else None,
        )


class DbMigrationHelperTests(unittest.TestCase):
    def test_postgres_engine_uses_bounded_pool_settings(self) -> None:
        kwargs = _database_engine_kwargs(
            "postgresql+psycopg://user:pass@127.0.0.1:5432/db",
            pool_size=2,
            max_overflow=2,
            pool_timeout_seconds=30,
        )

        self.assertEqual(
            kwargs,
            {
                "pool_pre_ping": True,
                "pool_size": 2,
                "max_overflow": 2,
                "pool_timeout": 30,
            },
        )

    def test_engine_pool_settings_are_not_passed_to_sqlite(self) -> None:
        kwargs = _database_engine_kwargs(
            "sqlite:///:memory:",
            pool_size=2,
            max_overflow=2,
            pool_timeout_seconds=30,
        )

        self.assertEqual(kwargs, {"pool_pre_ping": True})

    def test_engine_pool_settings_clamp_invalid_low_values(self) -> None:
        kwargs = _database_engine_kwargs(
            "postgresql+psycopg://user:pass@127.0.0.1:5432/db",
            pool_size=0,
            max_overflow=-1,
            pool_timeout_seconds=0,
        )

        self.assertEqual(kwargs["pool_size"], 1)
        self.assertEqual(kwargs["max_overflow"], 0)
        self.assertEqual(kwargs["pool_timeout"], 1)

    def test_video_time_evidence_schema_keeps_partial_dates_and_dedup_key(self) -> None:
        columns = VideoTimeEvidence.__table__.columns

        for name in [
            "video_id",
            "time_role",
            "source",
            "evidence_key",
            "date_year",
            "date_month",
            "date_day",
            "time_start",
            "time_end",
            "precision",
            "confidence",
            "status",
            "evidence_text",
            "evidence_json",
            "reliability_flags",
        ]:
            self.assertIn(name, columns)

        constraints = {c.name for c in VideoTimeEvidence.__table__.constraints}
        self.assertIn("video_time_evidence_source_ux", constraints)

    def test_market_event_schema_keeps_event_time_and_embedding_key(self) -> None:
        event_columns = MarketEvent.__table__.columns
        for name in [
            "event_time_start",
            "event_time_end",
            "time_precision",
            "available_at",
            "event_type",
            "source_video_id",
            "transcript_asset_id",
            "source_hash",
            "event_key",
            "raw_payload",
        ]:
            self.assertIn(name, event_columns)

        event_constraints = {c.name for c in MarketEvent.__table__.constraints}
        embedding_constraints = {c.name for c in MarketEventEmbedding.__table__.constraints}
        self.assertIn("market_event_source_event_ux", event_constraints)
        self.assertIn("market_event_embedding_ux", embedding_constraints)

    def test_postgres_best_effort_ddl_resets_lock_timeout_on_success(self) -> None:
        conn = _FakeConn()

        _execute_best_effort_ddl(conn, "create index if not exists demo_idx on demo(id)")

        self.assertEqual(conn.nested_entered, 1)
        self.assertEqual(conn.nested_exits, [None])
        self.assertEqual(
            conn.calls[-3:],
            [
                "set local lock_timeout = '2s'",
                "create index if not exists demo_idx on demo(id)",
                "set local lock_timeout = '0'",
            ],
        )

    def test_postgres_best_effort_ddl_swallows_statement_failure_in_savepoint(self) -> None:
        conn = _FakeConn(fail_on="create index")

        _execute_best_effort_ddl(conn, "create index if not exists demo_idx on demo(id)")

        self.assertEqual(conn.nested_entered, 1)
        self.assertEqual(conn.nested_exits, [RuntimeError])
        self.assertEqual(
            conn.calls[-2:],
            [
                "set local lock_timeout = '2s'",
                "create index if not exists demo_idx on demo(id)",
            ],
        )

    def test_non_postgres_best_effort_ddl_runs_without_lock_timeout(self) -> None:
        conn = _FakeConn(dialect_name="sqlite")

        _execute_best_effort_ddl(conn, "create index if not exists demo_idx on demo(id)")

        self.assertEqual(conn.nested_entered, 0)
        self.assertEqual(conn.calls[-1], "create index if not exists demo_idx on demo(id)")

    def test_existing_index_skips_ddl_and_relation_lock(self) -> None:
        conn = _FakeConn(index_exists=True)

        _execute_best_effort_ddl(conn, "create index if not exists demo_idx on demo(id)")

        self.assertEqual(conn.nested_entered, 0)
        self.assertEqual(len(conn.calls), 1)
        self.assertIn("pg_class", conn.calls[0])

    def test_no_legacy_jobs_skips_empty_job_update(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        statements: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def _record_statement(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
            statements.append(str(statement).strip().lower())

        try:
            with engine.begin() as conn:
                conn.execute(text("create table job (id text primary key, type text, status text)"))
                statements.clear()
                _cancel_legacy_analysis_jobs(conn)
            self.assertFalse(any(statement.startswith("update job") for statement in statements))
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
