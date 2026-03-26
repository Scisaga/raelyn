from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api import jobs as jobs_api
from raelyn.api import ws as ws_api


@contextmanager
def _fake_session_scope(session):
    yield session


class _FakeScalarRows:
    def all(self):
        return []


class _FakeExecuteResult:
    def scalars(self):
        return _FakeScalarRows()


class _CaptureStmtSession:
    def __init__(self) -> None:
        self.statements = []

    def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeExecuteResult()


class JobsActiveOrderingTests(unittest.TestCase):
    def test_jobs_api_active_list_orders_running_by_started_at_asc(self) -> None:
        session = _CaptureStmtSession()

        with patch("raelyn.api.jobs.session_scope", lambda: _fake_session_scope(session)):
            jobs_api.list_jobs(status_in="pending,running", limit=20, offset=0)

        sql = str(session.statements[0])
        self.assertIn("job.started_at ASC NULLS LAST", sql)
        self.assertNotIn("job.started_at DESC", sql)

    def test_jobs_ws_active_list_orders_running_by_started_at_asc(self) -> None:
        session = _CaptureStmtSession()

        with patch("raelyn.api.ws.session_scope", lambda: _fake_session_scope(session)):
            ws_api._query_jobs(status_in="pending,running", limit=20)

        sql = str(session.statements[0])
        self.assertIn("job.started_at ASC NULLS LAST", sql)
        self.assertNotIn("job.started_at DESC", sql)


if __name__ == "__main__":
    unittest.main()
