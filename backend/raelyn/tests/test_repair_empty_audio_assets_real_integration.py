from __future__ import annotations

import os
import unittest

from sqlalchemy import text

from raelyn.db import SessionLocal
from raelyn.tools import repair_empty_audio_assets


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1",
    (
        "设置 RAELYN_RUN_REAL_DB_TESTS=1 后对真实 PostgreSQL 运行空音频只读检查；"
        "测试事务会强制 READ ONLY"
    ),
)
class RepairEmptyAudioAssetsRealIntegrationTests(unittest.TestCase):
    def test_dry_run_is_read_only_and_matches_candidate_query(self) -> None:
        session = SessionLocal()
        try:
            if session.get_bind().dialect.name != "postgresql":
                self.skipTest("需要 PostgreSQL")
            session.execute(text("set transaction isolation level repeatable read, read only"))

            candidate_ids = repair_empty_audio_assets._candidate_video_ids(session, limit=None)
            result = repair_empty_audio_assets.repair_empty_audio_assets(
                session,
                execute=False,
            )

            self.assertEqual(result["dry_run"], True)
            self.assertEqual(result["candidates"], len(candidate_ids))
            self.assertEqual(result["download_jobs_enqueued"], 0)
            self.assertEqual(result["empty_audio_size_bytes"], 257)
            self.assertEqual(result["priority"], 20)
        finally:
            session.rollback()
            session.close()


if __name__ == "__main__":
    unittest.main()
