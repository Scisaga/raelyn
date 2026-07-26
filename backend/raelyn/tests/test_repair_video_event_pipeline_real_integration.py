from __future__ import annotations

import os
import unittest

from sqlalchemy import select, text

from raelyn.db import SessionLocal
from raelyn.models import Asset
from raelyn.tools import repair_video_event_pipeline


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1",
    (
        "设置 RAELYN_RUN_REAL_DB_TESTS=1 后对真实 PostgreSQL 运行只读修复检查；"
        "测试事务会强制 READ ONLY"
    ),
)
class RepairVideoEventPipelineRealIntegrationTests(unittest.TestCase):
    def test_dry_run_is_read_only_and_candidates_satisfy_repair_contract(self) -> None:
        session = SessionLocal()
        try:
            if session.get_bind().dialect.name != "postgresql":
                self.skipTest("需要 PostgreSQL")
            session.execute(
                text(
                    "set transaction isolation level repeatable read, read only"
                )
            )

            latest_failed = repair_video_event_pipeline._latest_failed_downloads(session)
            active_downloads = repair_video_event_pipeline._active_video_ids(
                session,
                repair_video_event_pipeline.DOWNLOAD_JOB_TYPES,
            )
            download_candidates = repair_video_event_pipeline._download_status_candidates(
                session,
                lock=False,
            )
            failed_extraction_ids = repair_video_event_pipeline._failed_extraction_video_ids(
                session
            )
            active_extractions = repair_video_event_pipeline._active_video_ids(
                session,
                repair_video_event_pipeline.EVENT_EXTRACTION_JOB_TYPES,
            )
            result = repair_video_event_pipeline.repair_video_event_pipeline(
                session,
                execute=False,
            )

            candidate_ids = {video.id for video, _error in download_candidates}
            asset_video_ids = set(
                session.execute(
                    select(Asset.video_id).where(
                        Asset.video_id.in_(candidate_ids),
                        Asset.type == "video",
                    )
                ).scalars()
            )
            self.assertTrue(candidate_ids.issubset(set(latest_failed)))
            self.assertTrue(candidate_ids.isdisjoint(active_downloads))
            self.assertTrue(candidate_ids.isdisjoint(asset_video_ids))
            self.assertTrue(
                all(
                    video.status in {"discovered", "downloading"}
                    for video, _error in download_candidates
                )
            )
            self.assertTrue(set(failed_extraction_ids).isdisjoint(active_extractions))
            self.assertEqual(result["dry_run"], True)
            self.assertEqual(
                result["download_status_candidates"],
                len(download_candidates),
            )
            self.assertEqual(
                result["event_extraction_candidates"],
                len(failed_extraction_ids),
            )
            self.assertEqual(result["download_jobs_enqueued"], 0)
            self.assertNotIn("download_status_updated", result)
            self.assertNotIn("event_extraction_enqueued_or_reused", result)
        finally:
            session.rollback()
            session.close()


if __name__ == "__main__":
    unittest.main()
