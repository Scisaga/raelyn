from __future__ import annotations

from datetime import date, timedelta
import os
import time
import unittest

from sqlalchemy import select, text

from raelyn.api.playlists import _configure_event_map_query, get_playlist_event_map_entities
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import EventMapSnapshot, EventMapState


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1",
    "设置 RAELYN_RUN_REAL_DB_TESTS=1 后使用真实 PostgreSQL 快照运行",
)
class EventMapQueryRealIntegrationTests(unittest.TestCase):
    def test_query_guard_is_transaction_local(self) -> None:
        with session_scope() as session:
            if session.get_bind().dialect.name != "postgresql":
                self.skipTest("需要 PostgreSQL")
            _configure_event_map_query(session, force_hash_join=True)
            timeout = session.execute(text("select current_setting('statement_timeout')")).scalar_one()
            nestloop = session.execute(text("select current_setting('enable_nestloop')")).scalar_one()
            self.assertEqual(timeout, f"{max(1, int(settings.event_map_entity_query_timeout_seconds))}s")
            self.assertEqual(nestloop, "off")
            session.rollback()

    def test_largest_ready_snapshot_entity_ranking_finishes_within_timeout(self) -> None:
        with session_scope() as session:
            row = session.execute(
                select(EventMapState.playlist_id, EventMapSnapshot)
                .join(EventMapSnapshot, EventMapSnapshot.id == EventMapState.current_snapshot_id)
                .where(EventMapSnapshot.status == "ready")
                .order_by(EventMapSnapshot.canonical_count.desc())
                .limit(1)
            ).one_or_none()
            if row is None:
                self.skipTest("没有 ready 事件地图快照")
            playlist_id, snapshot = row
            snapshot_id = snapshot.id
            months = list(snapshot.monthly_distribution or [])
            if not months:
                self.skipTest("快照没有时间分布")
            start_date = date.fromisoformat(str(months[0]["month"])[:7] + "-01")
            end_month = date.fromisoformat(str(months[-1]["month"])[:7] + "-01")
            if end_month.month == 12:
                end_date = date(end_month.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(end_month.year, end_month.month + 1, 1) - timedelta(days=1)

        started = time.monotonic()
        rows = get_playlist_event_map_entities(
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
            start_date=start_date,
            end_date=end_date,
            q=None,
            limit=40,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, max(1, int(settings.event_map_entity_query_timeout_seconds)))
        self.assertLessEqual(len(rows), 40)


if __name__ == "__main__":
    unittest.main()
