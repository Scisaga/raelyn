from __future__ import annotations

"""复用真实资产与快照，在隔离库验证历史估算，不修改运行数据库。"""

from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
import os
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from raelyn.db import engine as source_engine
from raelyn.models import Asset, Base, ExternalServiceUsageDaily, Job, Media, ResourceUsageDaily, Video
from raelyn.services.usage import _asset_size_history_estimates, build_usage_payload


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _sqlite_array(_type, _compiler, **_kwargs):
    return "JSON"


_SHANGHAI = ZoneInfo("Asia/Shanghai")


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1",
    "设置 RAELYN_RUN_REAL_DB_TESTS=1 后只读复用真实资产与资源快照",
)
class UsageStorageHistoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with source_engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout = '15s'"))
            cls.snapshots = [dict(row) for row in conn.execute(
                select(ResourceUsageDaily.__table__).order_by(ResourceUsageDaily.day)
            ).mappings()]
            if not cls.snapshots:
                raise unittest.SkipTest("缺少真实资源快照；先让 sync worker 执行 system.capture_usage_snapshot。")
            cls.first_snapshot_day = cls.snapshots[0]["day"]
            cutoff = datetime.combine(cls.first_snapshot_day, time.min, tzinfo=_SHANGHAI).astimezone(timezone.utc)
            before = [dict(row) for row in conn.execute(
                select(Asset.__table__).where(Asset.created_at < cutoff, Asset.size_bytes.is_not(None))
                .order_by(Asset.created_at.desc()).limit(12)
            ).mappings()]
            after = [dict(row) for row in conn.execute(
                select(Asset.__table__).where(Asset.created_at >= cutoff, Asset.size_bytes.is_not(None))
                .order_by(Asset.created_at).limit(12)
            ).mappings()]
            if not before or not after:
                raise unittest.SkipTest("需要首次快照前后的真实资产；先完成视频下载或资产生成任务。")
            cls.assets = before + after
            video_ids = {row["video_id"] for row in cls.assets if row["video_id"] is not None}
            cls.videos = [dict(row) for row in conn.execute(
                select(Video.__table__).where(Video.id.in_(video_ids))
            ).mappings()]
            media_ids = {row["media_id"] for row in cls.videos}
            cls.media = [dict(row) for row in conn.execute(
                select(Media.__table__).where(Media.id.in_(media_ids))
            ).mappings()]

    @contextmanager
    def isolated_storage(self, snapshots):
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(engine, tables=[
                Media.__table__, Video.__table__, Asset.__table__, Job.__table__,
                ExternalServiceUsageDaily.__table__, ResourceUsageDaily.__table__,
            ])
            with Session(engine) as session:
                for model, rows in ((Media, self.media), (Video, self.videos), (Asset, self.assets), (ResourceUsageDaily, snapshots)):
                    if rows:
                        session.execute(insert(model.__table__), rows)
                session.commit()
                yield session
        finally:
            engine.dispose()

    def expected_size(self, day):
        return sum(row["size_bytes"] for row in self.assets if row["created_at"].astimezone(_SHANGHAI).date() <= day)

    def test_estimate_uses_shanghai_day_and_carries_starting_inventory(self):
        start = self.first_snapshot_day - timedelta(days=1)
        until = self.first_snapshot_day + timedelta(days=2)
        with self.isolated_storage([]) as session:
            full = _asset_size_history_estimates(session, start_day=start, until_day=until)
            shortened = _asset_size_history_estimates(session, start_day=start + timedelta(days=1), until_day=until)
        self.assertEqual(full[start], self.expected_size(start))
        self.assertEqual(full[self.first_snapshot_day], self.expected_size(self.first_snapshot_day))
        self.assertEqual(shortened, {day: size for day, size in full.items() if day > start})
        self.assertNotIn(until, full)

    def test_dates_before_first_known_asset_stay_unknown(self):
        first_day = min(row["created_at"].astimezone(_SHANGHAI).date() for row in self.assets)
        with self.isolated_storage([]) as session:
            result = _asset_size_history_estimates(session, start_day=first_day - timedelta(days=2), until_day=first_day + timedelta(days=1))
        self.assertEqual(result, {first_day: self.expected_size(first_day)})

    def test_payload_preserves_snapshots_and_does_not_fill_later_gaps(self):
        if self.first_snapshot_day < datetime.now(_SHANGHAI).date() - timedelta(days=88):
            self.skipTest("首次真实快照已超出90天窗口，无法用当前日期验证历史估算与实测交界。")
        with self.isolated_storage(self.snapshots[:1]) as session:
            before = session.execute(select(func.count()).select_from(ResourceUsageDaily)).scalar_one()
            payload = build_usage_payload(session, days=90)
            after = session.execute(select(func.count()).select_from(ResourceUsageDaily)).scalar_one()
        self.assertEqual(before, after)
        points = {point["date"]: point for point in payload["series"]}
        estimated = points[self.first_snapshot_day - timedelta(days=1)]
        self.assertEqual(estimated["asset_size_estimate_bytes"], self.expected_size(estimated["date"]))
        self.assertIsNone(estimated["asset_size_bytes"])
        self.assertIsNone(estimated["database_size_bytes"])
        self.assertFalse(estimated["resource_sampled"])
        measured = points[self.first_snapshot_day]
        self.assertEqual(measured["asset_size_bytes"], self.snapshots[0]["asset_size_bytes"])
        self.assertEqual(measured["database_size_bytes"], self.snapshots[0]["database_size_bytes"])
        self.assertIsNone(measured["asset_size_estimate_bytes"])
        self.assertTrue(measured["resource_sampled"])
        for day, point in points.items():
            if day > self.first_snapshot_day:
                self.assertIsNone(point["asset_size_estimate_bytes"])
                self.assertIsNone(point["asset_size_bytes"])
                self.assertIsNone(point["database_size_bytes"])
                self.assertFalse(point["resource_sampled"])
        self.assertEqual(payload["collection"]["resource_started_at"], self.first_snapshot_day)

    def test_payload_without_snapshots_keeps_estimates_separate(self):
        with self.isolated_storage([]) as session:
            payload = build_usage_payload(session, days=90)
            self.assertEqual(session.execute(select(func.count()).select_from(ResourceUsageDaily)).scalar_one(), 0)
        self.assertIsNone(payload["collection"]["resource_started_at"])
        latest = payload["series"][-1]
        self.assertEqual(latest["asset_size_estimate_bytes"], sum(row["size_bytes"] for row in self.assets))
        for point in payload["series"]:
            self.assertIsNone(point["asset_size_bytes"])
            self.assertIsNone(point["database_size_bytes"])
            self.assertFalse(point["resource_sampled"])


if __name__ == "__main__":
    # 示例：PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_usage_storage_history_integration -q
    unittest.main()
