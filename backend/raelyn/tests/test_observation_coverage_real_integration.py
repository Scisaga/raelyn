"""复用真实转写、抽取与证据产物，验证覆盖率聚合；业务库只读。"""
from __future__ import annotations

from collections import Counter
import os
from time import perf_counter
import unittest

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from raelyn.db import engine
from raelyn.models import Asset, MarketEvent, MarketEventEvidence, PlaylistMedia, Video, VideoEventExtractionRun
from raelyn.services.v2_observation import domain_observation


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1",
    "设置 RAELYN_RUN_REAL_DB_TESTS=1，复用真实采集、转写和事件抽取产物",
)
class ObservationCoverageRealIntegrationTests(unittest.TestCase):
    def test_query_compilation_setting_is_transaction_local(self) -> None:
        with engine.connect() as connection:
            if connection.dialect.name != "postgresql":
                self.skipTest("需要 PostgreSQL")
            original_jit = connection.scalar(text("SHOW jit"))
            connection.rollback()
            with Session(bind=connection) as session:
                session.execute(text("SET TRANSACTION READ ONLY"))
                playlist_id = session.scalar(select(PlaylistMedia.playlist_id).limit(1))
                if playlist_id is None:
                    self.skipTest("没有真实观测域；请先创建观测域并关联信源")
                domain_observation(session, playlist_id)
                self.assertEqual(session.scalar(text("SHOW jit")), "off")
                session.rollback()
            self.assertEqual(connection.scalar(text("SHOW jit")), original_jit)

    def test_largest_domain_coverage_matches_upstream_products(self) -> None:
        with Session(engine) as session:
            if session.get_bind().dialect.name != "postgresql":
                self.skipTest("需要 PostgreSQL")
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout = '15s'"))
            playlist_id = session.scalar(
                select(PlaylistMedia.playlist_id)
                .join(Video, Video.media_id == PlaylistMedia.media_id)
                .group_by(PlaylistMedia.playlist_id)
                .order_by(func.count().desc())
                .limit(1)
            )
            if playlist_id is None:
                self.skipTest("没有真实域内来源记录；请先创建观测域并同步信源")
            started = perf_counter()
            payload = domain_observation(session, playlist_id)
            elapsed = perf_counter() - started
            coverage = payload["coverage"]
            if not coverage["source_processing"]["numerator"]:
                self.skipTest("没有真实文本转写；请先完成视频转写与事件抽取任务")

            # 在同一只读快照下逐类核对上游产物，不向业务库插入测试记录。
            videos = session.execute(
                select(Video.id, Video.status)
                .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
                .where(PlaylistMedia.playlist_id == playlist_id)
            ).all()
            transcript_ids = select(Asset.video_id).join(Video, Video.id == Asset.video_id).join(
                PlaylistMedia, PlaylistMedia.media_id == Video.media_id,
            ).where(
                PlaylistMedia.playlist_id == playlist_id, Asset.type == "transcript", Asset.format == "txt",
            ).distinct()
            transcripts = set(session.scalars(transcript_ids))
            self.assertEqual(coverage["source_processing"]["denominator"], len(videos))
            self.assertEqual(coverage["source_processing"]["numerator"], len(transcripts))
            self.assertEqual(coverage["source_processing"]["failed_or_skipped"], dict(Counter(status for _, status in videos)))

            current, with_events, failed, legacy = set(), set(), set(), set()
            details = coverage["event_extraction"]["details"]
            runs = session.execute(select(
                VideoEventExtractionRun.video_id, VideoEventExtractionRun.prompt_version,
                VideoEventExtractionRun.extraction_model, VideoEventExtractionRun.status,
                VideoEventExtractionRun.event_count,
            ).where(VideoEventExtractionRun.video_id.in_(transcript_ids)))
            for video_id, prompt, model, status, count in runs:
                is_current = prompt == details["prompt_version"] and model == details["model"]
                if is_current and status == "succeeded":
                    current.add(video_id)
                    if count > 0:
                        with_events.add(video_id)
                elif is_current and status == "failed":
                    failed.add(video_id)
                elif not is_current and status == "succeeded":
                    legacy.add(video_id)
            self.assertEqual(coverage["event_extraction"]["numerator"], len(current))
            self.assertEqual(coverage["event_extraction"]["denominator"], len(transcripts))
            self.assertEqual(details["with_events"], len(with_events))
            self.assertEqual(details["zero_events"], len(current - with_events))
            self.assertEqual(details["current_spec_failed"], len(failed - current))
            self.assertEqual(details["legacy_spec_only"], len(legacy - current - failed))
            self.assertEqual(details["no_successful_result"], len(transcripts - current - failed - legacy))
            self.assertEqual(details["missing_transcript"], len(videos) - len(transcripts))

            domain_events = select(MarketEvent.id).join(Video, Video.id == MarketEvent.source_video_id).join(
                PlaylistMedia, PlaylistMedia.media_id == Video.media_id,
            ).where(PlaylistMedia.playlist_id == playlist_id)
            events = set(session.scalars(domain_events))
            verified = set(session.scalars(select(MarketEventEvidence.event_id).where(
                MarketEventEvidence.event_id.in_(domain_events),
                MarketEventEvidence.evidence_json["verified"].as_boolean().is_(True),
            )))
            self.assertEqual(coverage["field_admission"]["denominator"], len(events))
            self.assertEqual(coverage["evidence_verification"]["denominator"], len(events))
            self.assertEqual(coverage["evidence_verification"]["numerator"], len(verified))
            self.assertEqual(coverage["evidence_verification"]["failed_or_skipped"]["unverified_or_missing"], len(events - verified))
            self.assertLess(elapsed, 10, f"最大观测域覆盖率仍耗时 {elapsed:.3f}s")
            print(f"最大观测域覆盖率：{len(videos)} 条来源，{len(events)} 个事件，{elapsed:.3f}s", flush=True)


if __name__ == "__main__":
    # RAELYN_RUN_REAL_DB_TESTS=1 PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_observation_coverage_real_integration
    unittest.main()
