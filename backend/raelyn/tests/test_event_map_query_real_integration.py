from __future__ import annotations

from datetime import date, datetime, timedelta
import os
import time
import unittest

from sqlalchemy import select, text

from raelyn.api.playlists import _configure_event_map_query, get_playlist_event_map_entities
from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.models import EventMapSnapshot, EventMapState
from raelyn.services.v2_observation import event_highlights


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1",
    "设置 RAELYN_RUN_REAL_DB_TESTS=1 后使用真实 PostgreSQL 快照运行",
)
class EventMapQueryRealIntegrationTests(unittest.TestCase):
    def test_24h_highlights_match_recent_platform_videos_and_their_snapshot_events(self) -> None:
        with session_scope() as session:
            if session.get_bind().dialect.name != "postgresql":
                self.skipTest("需要 PostgreSQL")
            session.execute(text("SET TRANSACTION READ ONLY"))
            row = session.execute(
                select(EventMapState.playlist_id, EventMapSnapshot.id)
                .join(EventMapSnapshot, EventMapSnapshot.id == EventMapState.current_snapshot_id)
                .where(EventMapSnapshot.status == "ready")
                .order_by(EventMapSnapshot.canonical_count.desc())
                .limit(1)
            ).one_or_none()
            if row is None:
                self.skipTest("没有 ready 事件地图快照；请先完成真实事件抽取和星域构建")
            playlist_id, snapshot_id = row
            payload = event_highlights(session, playlist_id, snapshot_id=snapshot_id, scope="24h")
            since = datetime.fromisoformat(payload["published_since"])
            before = datetime.fromisoformat(payload["published_before"])
            self.assertEqual(before - since, timedelta(hours=24))
            self.assertEqual(payload["scope"], "24h")
            self.assertIsNone(payload["event_date_start"])
            self.assertIsNone(payload["event_date_end"])
            self.assertEqual(payload["excluded_imprecise_total"], 0)

            # 独立读取真实成员与视频，核对所有高亮点及每张卡片的来源。
            expected = session.execute(text("""
                SELECT DISTINCT c.point_index, c.canonical_id, v.id AS video_id, v.published_at
                FROM event_map_canonical c
                JOIN event_map_canonical_member cm
                  ON cm.snapshot_id = c.snapshot_id AND cm.canonical_id = c.canonical_id
                JOIN event_map_record_revision r ON r.id = cm.record_revision_id
                JOIN video v ON v.id = r.source_video_id
                JOIN playlist_media pm ON pm.media_id = v.media_id
                WHERE c.snapshot_id = :snapshot_id AND pm.playlist_id = :playlist_id
                  AND v.published_at >= :since AND v.published_at <= :before
            """), {
                "snapshot_id": snapshot_id, "playlist_id": playlist_id,
                "since": since, "before": before,
            }).mappings().all()
            expected_points = {row["point_index"] for row in expected}
            expected_pairs = {(str(row["canonical_id"]), str(row["video_id"])) for row in expected}
            self.assertEqual(set(payload["point_indices"]), expected_points)
            self.assertEqual(payload["matched_event_total"], len(expected_points))
            self.assertLessEqual(len(payload["items"]), 10)
            for item in payload["items"]:
                video = item["primary_video"]
                self.assertIn((item["canonical_id"], video["video_id"]), expected_pairs)
                published_at = datetime.fromisoformat(video["published_at"])
                self.assertGreaterEqual(published_at, since)
                self.assertLessEqual(published_at, before)
            if not expected:
                self.skipTest("没有过去 24 小时发布且已入图的真实视频；请先同步、抽取并更新星域")

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
