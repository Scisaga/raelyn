"""复用真实上游产物验证补缺筛选；业务库仅使用 PostgreSQL 只读事务。"""
from __future__ import annotations

import os
import unittest
import uuid

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from raelyn.db import engine
from raelyn.jobs.enqueue import _normalize_dedupe_key_and_params
from raelyn.models import Asset, Video, VideoEventExtractionRun
from raelyn.services.event_analysis import (
    _any_succeeded_event_extraction_run_clause,
    event_extraction_spec,
)


class EventMissingOnlyDedupeTests(unittest.TestCase):
    def test_four_job_types_keep_force_unextracted_and_precise_missing_modes_separate(self) -> None:
        video_id = str(uuid.uuid4())
        playlist_id = str(uuid.uuid4())
        cases = (
            ("video.extract_events", {"video_id": video_id}),
            ("video.extract_events_batch", {"video_ids": [video_id]}),
            ("playlist.backfill_events", {"playlist_id": playlist_id}),
            ("playlist.backfill_events_range", {
                "playlist_id": playlist_id, "range_start": "2026-10-01", "range_end": "2026-11-01",
            }),
        )
        for job_type, base in cases:
            with self.subTest(job_type=job_type):
                default_key, default_params = _normalize_dedupe_key_and_params(job_type, dict(base))
                missing_key, missing_params = _normalize_dedupe_key_and_params(job_type, {**base, "missing_only": True})
                precise_key, precise_params = _normalize_dedupe_key_and_params(job_type, {**base, "missing_only": False})
                force_key, force_params = _normalize_dedupe_key_and_params(job_type, {**base, "force": True, "missing_only": True})
                plain_force_key, _ = _normalize_dedupe_key_and_params(job_type, {**base, "force": True, "missing_only": False})
                self.assertEqual(default_key, missing_key)
                self.assertTrue(default_key and default_key.endswith(":unextracted"))
                self.assertTrue(precise_key and precise_key.endswith(":missing"))
                self.assertTrue(force_key and force_key.endswith(":force"))
                self.assertEqual(force_key, plain_force_key)
                self.assertEqual(len({missing_key, precise_key, force_key}), 3)
                self.assertTrue(default_params["missing_only"])
                self.assertTrue(missing_params["missing_only"])
                self.assertFalse(precise_params["missing_only"])
                self.assertFalse(force_params["missing_only"])
                self.assertTrue(force_params["force"])
                self.assertNotIn("missing_only", base)


@unittest.skipUnless(
    os.environ.get("RUN_EVENT_MISSING_ONLY_READ_INTEGRATION") == "1",
    "设置 RUN_EVENT_MISSING_ONLY_READ_INTEGRATION=1，复用真实 Video/run/plain Asset，只读查询、不生成",
)
class EventMissingOnlyReadOnlyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        if engine.dialect.name != "postgresql":
            self.skipTest("需要包含真实上游事件产物的 PostgreSQL；本测试不手工插入 domain 数据")
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)
        self.connection.execute(text("set transaction read only"))
        self.session = Session(bind=self.connection, autoflush=False)
        self.addCleanup(self.session.close)
        self.assertEqual(self.connection.execute(text("show transaction_read_only")).scalar_one(), "on")

    @staticmethod
    def _run_exists(*conditions):
        return select(VideoEventExtractionRun.id).where(
            VideoEventExtractionRun.video_id == Video.id, *conditions,
        ).exists()

    def _candidate(self, *conditions):
        has_plain = select(Asset.id).where(
            Asset.video_id == Video.id, Asset.type == "transcript", Asset.format == "txt", Asset.variant == "plain",
        ).exists()
        return self.session.execute(select(Video.id).where(has_plain, *conditions).limit(1)).scalar_one_or_none()

    def _unextracted(self, video_ids):
        return set(self.session.execute(select(Video.id).where(
            Video.id.in_(video_ids), ~_any_succeeded_event_extraction_run_clause(Video.id),
        )).scalars())

    def test_successful_zero_event_and_legacy_model_results_are_preserved(self) -> None:
        spec = event_extraction_spec(self.session)
        examples = (
            ("zero_events", self._run_exists(
                VideoEventExtractionRun.status == "succeeded", VideoEventExtractionRun.event_count == 0,
            )),
            ("legacy_model_or_prompt", self._run_exists(
                VideoEventExtractionRun.status == "succeeded",
                or_(
                    VideoEventExtractionRun.extraction_model.is_distinct_from(spec.model),
                    VideoEventExtractionRun.prompt_version.is_distinct_from(spec.prompt_version),
                ),
            )),
        )
        for name, condition in examples:
            with self.subTest(example=name):
                video_id = self._candidate(condition)
                if video_id is None:
                    self.skipTest("需真实上游事件任务生成零事件成功及旧模型/提示词成功记录，并保留 plain 转写")
                self.assertEqual(self._unextracted([video_id]), set())

    def test_failed_without_success_remains_eligible(self) -> None:
        video_id = self._candidate(
            ~_any_succeeded_event_extraction_run_clause(Video.id),
            self._run_exists(VideoEventExtractionRun.status == "failed"),
        )
        if video_id is None:
            self.skipTest("需真实上游事件任务留下仅失败且没有任何成功的 run，并保留 plain 转写")
        self.assertEqual(self._unextracted([video_id]), {video_id})

    def test_new_video_without_any_run_remains_eligible(self) -> None:
        video_id = self._candidate(~self._run_exists())
        if video_id is None:
            self.skipTest("需先由真实采集/转写任务生成尚未执行事件抽取的 plain 转写视频")
        self.assertEqual(self._unextracted([video_id]), {video_id})

    def test_partial_batch_keeps_only_never_successful_members(self) -> None:
        successful = self._candidate(_any_succeeded_event_extraction_run_clause(Video.id))
        missing = self._candidate(~_any_succeeded_event_extraction_run_clause(Video.id))
        if successful is None or missing is None:
            self.skipTest("需真实 plain 视频同时包含已有成功和从未成功两类成员")
        self.assertEqual(self._unextracted([successful, missing]), {missing})
        self.assertEqual(self._unextracted([successful]), set())


if __name__ == "__main__":
    # RUN_EVENT_MISSING_ONLY_READ_INTEGRATION=1 PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_event_missing_only_real_integration
    unittest.main()
