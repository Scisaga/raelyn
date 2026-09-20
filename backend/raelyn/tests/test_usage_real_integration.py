from __future__ import annotations

"""复用真实调用日聚合，在隔离 SQLite 中验证资源大屏的累计与趋势口径。"""

from contextlib import contextmanager
from datetime import timedelta
import unittest

from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from raelyn.db import engine as source_engine
from raelyn.models import Asset, Base, ExternalServiceUsageDaily, Job, Media, ResourceUsageDaily, Video
from raelyn.services.usage import build_usage_payload


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _sqlite_array(_type, _compiler, **_kwargs):
    return "JSON"


class UsageRealIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with source_engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout = '10s'"))
            cls.rows = [dict(row) for row in conn.execute(select(ExternalServiceUsageDaily.__table__)).mappings()]
        if not cls.rows:
            raise unittest.SkipTest("缺少真实用量日聚合；先运行 LLM/ASR 任务或 system.backfill_legacy_usage。")

    @contextmanager
    def isolated_usage(self, rows):
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(engine, tables=[
                Media.__table__, Video.__table__, Asset.__table__, Job.__table__,
                ExternalServiceUsageDaily.__table__, ResourceUsageDaily.__table__,
            ])
            with Session(engine) as session:
                if rows:
                    session.execute(insert(ExternalServiceUsageDaily), rows)
                    session.commit()
                yield session
        finally:
            engine.dispose()

    def test_lifetime_totals_do_not_change_with_trend_window(self):
        with self.isolated_usage(self.rows) as session:
            payloads = [build_usage_payload(session, days=days) for days in (7, 30, 90)]
        latest = payloads[-1]
        rows = [row for row in self.rows if row["day"] <= latest["window"]["end"]]
        llm = [row for row in rows if row["service"] == "llm"]
        if not llm:
            self.skipTest("缺少真实 LLM 用量；先运行事件抽取、转写润色或简报生成。")
        self.assertEqual(latest["summary"]["external_calls"], sum(row["call_count"] for row in rows))
        for field in ("input_tokens", "output_tokens", "total_tokens", "usage_missing_calls"):
            self.assertEqual(latest["summary"][f"llm_{field}"], sum(row[field] for row in llm))
        for payload in payloads:
            self.assertEqual(payload["summary"], latest["summary"])
            self.assertEqual(payload["service_breakdown"], latest["service_breakdown"])
            self.assertEqual(payload["series"], latest["series"][-payload["window"]["days"]:])
        self.assertEqual(latest["summary"]["llm_total_tokens"], sum(
            row["total_tokens"] for row in latest["service_breakdown"] if row["service"] == "llm"
        ))

    def test_missing_usage_preserves_known_daily_tokens(self):
        with self.isolated_usage(self.rows) as session:
            payload = build_usage_payload(session, days=90)
        missing_days = {row["day"] for row in self.rows if row["service"] == "llm" and row["usage_missing_calls"] > 0}
        points = [point for point in payload["series"] if point["date"] in missing_days]
        if not points:
            self.skipTest("近 90 天没有真实的 LLM 用量缺失记录，无需制造缺失调用。")
        for point in points:
            rows = [row for row in self.rows if row["day"] == point["date"] and row["service"] == "llm"]
            for field in ("input_tokens", "output_tokens", "total_tokens", "usage_missing_calls"):
                self.assertEqual(point[f"llm_{field}"], sum(row[field] for row in rows))

    def test_service_coverage_distinguishes_unsampled_and_zero(self):
        with self.isolated_usage(self.rows) as session:
            end = build_usage_payload(session, days=7)["window"]["end"]
        candidates = [row for row in self.rows if row["service"] == "asr" and end - timedelta(days=4) <= row["day"] < end]
        if not candidates:
            self.skipTest("需要最近四天内且早于今天的真实 ASR 聚合，先运行视频转写。")
        row = candidates[0]
        with self.isolated_usage([row]) as session:
            payload = build_usage_payload(session, days=7)
        self.assertIsNone(payload["summary"]["llm_total_tokens"])
        self.assertIsNone(payload["summary"]["llm_usage_missing_calls"])
        for point in payload["series"]:
            self.assertIsNone(point["llm_total_tokens"])
            self.assertIsNone(point["llm_usage_missing_calls"])
            if point["date"] < row["day"]:
                self.assertIsNone(point["asr_calls"])
            elif point["date"] == row["day"]:
                self.assertEqual(point["asr_calls"], row["call_count"])
            else:
                self.assertEqual(point["asr_calls"], 0)

    def test_calls_with_only_missing_usage_return_zero(self):
        rows = [row for row in self.rows if row["service"] == "llm" and row["usage_missing_calls"] == row["call_count"] and row["call_count"] > 0 and row["total_tokens"] == 0]
        if not rows:
            self.skipTest("没有全部调用缺失用量的真实聚合，不制造上游调用记录。")
        with self.isolated_usage(rows) as session:
            payload = build_usage_payload(session, days=90)
        self.assertEqual(payload["summary"]["llm_total_tokens"], 0)
        self.assertGreater(payload["summary"]["llm_usage_missing_calls"], 0)
