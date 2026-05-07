from __future__ import annotations

import unittest
from types import SimpleNamespace

from raelyn.db import _execute_best_effort_ddl


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
    def __init__(self, *, dialect_name: str = "postgresql", fail_on: str = "") -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.fail_on = fail_on
        self.calls: list[str] = []
        self.nested_entered = 0
        self.nested_exits: list[type[BaseException] | None] = []

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction(self)

    def execute(self, statement) -> None:
        sql = str(statement)
        self.calls.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("lock timeout")


class DbMigrationHelperTests(unittest.TestCase):
    def test_postgres_best_effort_ddl_resets_lock_timeout_on_success(self) -> None:
        conn = _FakeConn()

        _execute_best_effort_ddl(conn, "create index if not exists demo_idx on demo(id)")

        self.assertEqual(conn.nested_entered, 1)
        self.assertEqual(conn.nested_exits, [None])
        self.assertEqual(
            conn.calls,
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
            conn.calls,
            [
                "set local lock_timeout = '2s'",
                "create index if not exists demo_idx on demo(id)",
            ],
        )

    def test_non_postgres_best_effort_ddl_runs_without_lock_timeout(self) -> None:
        conn = _FakeConn(dialect_name="sqlite")

        _execute_best_effort_ddl(conn, "create index if not exists demo_idx on demo(id)")

        self.assertEqual(conn.nested_entered, 0)
        self.assertEqual(conn.calls, ["create index if not exists demo_idx on demo(id)"])


if __name__ == "__main__":
    unittest.main()
