from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn import config
from raelyn.models import (
    AppConfig,
    Asset,
    Base,
    ExternalServiceUsageDaily,
    Job,
    JobEvent,
    Media,
    ResourceUsageDaily,
    Video,
)
from raelyn.services.usage import (
    LEGACY_USAGE_BACKFILL_CONFIG_KEY,
    backfill_legacy_external_usage,
    build_usage_payload,
    capture_resource_usage_snapshot,
    record_external_service_usage,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


_NOW = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)


class _UsageFixture:
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tempdir.name) / 'usage.sqlite'}")
        Base.metadata.create_all(
            self.engine,
            tables=[
                Media.__table__,
                Video.__table__,
                Asset.__table__,
                Job.__table__,
                JobEvent.__table__,
                AppConfig.__table__,
                ExternalServiceUsageDaily.__table__,
                ResourceUsageDaily.__table__,
            ],
        )
        self.session_factory = sessionmaker(bind=self.engine, class_=Session, expire_on_commit=False)
        self.session = self.session_factory()
        self.media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="usage-channel",
            url="https://www.youtube.com/@usage-channel",
        )
        self.session.add(self.media)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    @contextmanager
    def session_scope(self):
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _record_usage_samples(self) -> None:
        with patch("raelyn.services.usage.session_scope", self.session_scope):
            self.assertTrue(
                record_external_service_usage(
                    service="LLM",
                    operation="event.extract",
                    provider="Volcengine",
                    model="doubao-test",
                    succeeded=True,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    duration_ms=100,
                    called_at=datetime(2026, 9, 2, 17, 0, tzinfo=timezone.utc),
                )
            )
            self.assertTrue(
                record_external_service_usage(
                    service="llm",
                    operation="event.extract",
                    provider="volcengine",
                    model="doubao-test",
                    succeeded=False,
                    duration_ms=50,
                    usage_missing=True,
                    called_at=datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc),
                )
            )
            self.assertTrue(
                record_external_service_usage(
                    service="asr",
                    operation="transcribe",
                    provider="local",
                    model="qwen3-asr",
                    succeeded=True,
                    duration_ms=200,
                    called_at=datetime(2026, 9, 1, 16, 30, tzinfo=timezone.utc),
                )
            )

    def _record_inventory(self) -> None:
        videos = [
            Video(
                id=uuid.uuid4(),
                provider="youtube",
                provider_video_id="before-window",
                media_id=self.media.id,
                url="https://www.youtube.com/watch?v=before-window",
                created_at=datetime(2026, 8, 27, 15, 59, tzinfo=timezone.utc),
            ),
            Video(
                id=uuid.uuid4(),
                provider="youtube",
                provider_video_id="window-start",
                media_id=self.media.id,
                url="https://www.youtube.com/watch?v=window-start",
                created_at=datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc),
            ),
            Video(
                id=uuid.uuid4(),
                provider="youtube",
                provider_video_id="today-local",
                media_id=self.media.id,
                url="https://www.youtube.com/watch?v=today-local",
                created_at=datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc),
            ),
        ]
        self.session.add_all(videos)
        self.session.flush()
        self.session.add_all(
            [
                Asset(
                    id=uuid.uuid4(),
                    video_id=videos[1].id,
                    type="video",
                    format="mp4",
                    source="yt-dlp",
                    s3_bucket="test",
                    s3_key="video/one.mp4",
                    size_bytes=100,
                ),
                Asset(
                    id=uuid.uuid4(),
                    video_id=videos[2].id,
                    type="video",
                    format="webm",
                    source="yt-dlp",
                    s3_bucket="test",
                    s3_key="video/two.webm",
                    size_bytes=50,
                ),
                Asset(
                    id=uuid.uuid4(),
                    video_id=videos[2].id,
                    type="audio",
                    format="m4a",
                    source="ffmpeg",
                    s3_bucket="test",
                    s3_key="audio/two.m4a",
                    size_bytes=None,
                ),
            ]
        )
        self.session.add_all(
            [
                Job(
                    type="video.download.youtube",
                    status="succeeded",
                    params={"video_id": str(videos[1].id)},
                    result={"subtitles": 0},
                    finished_at=datetime(2026, 8, 27, 16, 30, tzinfo=timezone.utc),
                ),
                Job(
                    type="video.download.youtube",
                    status="succeeded",
                    params={"video_id": str(videos[2].id)},
                    result={"subtitles": 1},
                    finished_at=datetime(2026, 9, 2, 16, 30, tzinfo=timezone.utc),
                ),
                Job(
                    type="video.download.youtube",
                    status="succeeded",
                    params={"video_id": str(videos[2].id), "force": True},
                    result={"subtitles": 1},
                    finished_at=datetime(2026, 9, 2, 17, 30, tzinfo=timezone.utc),
                ),
                Job(
                    type="video.download.youtube",
                    status="succeeded",
                    params={"video_id": str(videos[0].id)},
                    result={"skipped": "members_only"},
                    finished_at=datetime(2026, 9, 2, 18, 30, tzinfo=timezone.utc),
                ),
                Job(
                    type="video.download.youtube",
                    status="succeeded",
                    params={"video_id": str(videos[0].id)},
                    result={"rescheduled": True},
                    finished_at=datetime(2026, 9, 2, 19, 30, tzinfo=timezone.utc),
                ),
                Job(
                    type="video.download.youtube",
                    status="failed",
                    params={"video_id": str(videos[0].id)},
                    result=None,
                    finished_at=datetime(2026, 9, 2, 20, 30, tzinfo=timezone.utc),
                ),
            ]
        )
        self.session.commit()


class UsageServiceTests(_UsageFixture, unittest.TestCase):
    def test_legacy_external_usage_backfill_is_idempotent_and_keeps_live_rows(self) -> None:
        succeeded_asr_job_id = uuid.uuid4()
        failed_asr_job_id = uuid.uuid4()
        self.session.add_all(
            [
                Job(
                    id=uuid.uuid4(),
                    type="brief.generate_period",
                    status="succeeded",
                    params={},
                    result={
                        "llm_usage": {
                            "call_count": 2,
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        }
                    },
                    finished_at=datetime(2026, 9, 2, 17, 0, tzinfo=timezone.utc),
                ),
                Job(
                    id=uuid.uuid4(),
                    type="video.extract_events_batch",
                    status="succeeded",
                    params={},
                    result={
                        "usage": {
                            "call_count": 3,
                            "input_tokens": 200,
                            "output_tokens": 30,
                            "total_tokens": 230,
                        }
                    },
                    finished_at=datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc),
                ),
                Job(
                    id=succeeded_asr_job_id,
                    type="video.asr_transcribe",
                    status="succeeded",
                    params={},
                    result={"ok": True},
                    finished_at=datetime(2026, 9, 2, 19, 5, tzinfo=timezone.utc),
                ),
                Job(
                    id=failed_asr_job_id,
                    type="video.asr_transcribe",
                    status="failed",
                    params={},
                    result=None,
                    finished_at=datetime(2026, 9, 2, 20, 5, tzinfo=timezone.utc),
                ),
                JobEvent(
                    job_id=succeeded_asr_job_id,
                    ts=datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc),
                    message="asr request started",
                    data={"attempt": 1, "provider": "local"},
                ),
                JobEvent(
                    job_id=succeeded_asr_job_id,
                    ts=datetime(2026, 9, 2, 19, 5, tzinfo=timezone.utc),
                    message="asr request succeeded",
                    data={"attempt": 1, "provider": "local"},
                ),
                JobEvent(
                    job_id=failed_asr_job_id,
                    ts=datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc),
                    message="asr request started",
                    data={"attempt": 1, "provider": "local"},
                ),
                ExternalServiceUsageDaily(
                    day=date(2026, 9, 3),
                    service="llm",
                    operation="event_extraction",
                    provider="openai",
                    model="gpt-test",
                    call_count=1,
                    success_count=1,
                    failure_count=0,
                    input_tokens=7,
                    output_tokens=2,
                    total_tokens=9,
                    duration_ms=10,
                    usage_missing_calls=0,
                    last_called_at=datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc),
                ),
            ]
        )
        self.session.commit()

        first = backfill_legacy_external_usage(self.session)
        self.session.commit()
        second = backfill_legacy_external_usage(self.session)
        self.session.commit()

        self.assertEqual(first, second)
        self.assertEqual(first["source_job_count"], 2)
        self.assertEqual(first["llm_source_job_count"], 2)
        self.assertEqual(first["asr_source_event_count"], 2)
        self.assertEqual(first["call_count"], 7)
        self.assertEqual(first["llm_call_count"], 5)
        self.assertEqual(first["asr_call_count"], 2)
        self.assertEqual(first["total_tokens"], 350)
        rows = self.session.execute(
            select(ExternalServiceUsageDaily).order_by(
                ExternalServiceUsageDaily.operation.asc()
            )
        ).scalars().all()
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(int(row.call_count) for row in rows), 8)
        self.assertEqual(sum(int(row.total_tokens) for row in rows), 359)
        live_row = next(row for row in rows if row.operation == "event_extraction")
        self.assertEqual(live_row.total_tokens, 9)
        marker = self.session.get(AppConfig, LEGACY_USAGE_BACKFILL_CONFIG_KEY)
        self.assertIsNotNone(marker)
        legacy_asr_row = next(
            row for row in rows if row.operation == "legacy.video_transcription"
        )
        self.assertEqual(legacy_asr_row.call_count, 2)
        self.assertEqual(legacy_asr_row.success_count, 1)
        self.assertEqual(legacy_asr_row.failure_count, 1)
        self.assertEqual(marker.value["version"], 2)

        with patch("raelyn.services.usage.utcnow", return_value=_NOW):
            payload = build_usage_payload(self.session, days=7)
        self.assertEqual(payload["summary"]["llm_calls"], 6)
        self.assertEqual(payload["summary"]["asr_calls"], 2)
        self.assertIsNone(payload["summary"]["embedding_calls"])
        self.assertEqual(payload["summary"]["external_calls"], 8)
        self.assertEqual(payload["summary"]["llm_total_tokens"], 359)
        self.assertEqual(
            {item["operation"] for item in payload["service_breakdown"]},
            {
                "event_extraction",
                "legacy.brief_generation",
                "legacy.event_extraction",
                "legacy.video_transcription",
            },
        )

    def test_external_usage_is_incremental_and_failure_isolated(self) -> None:
        self._record_usage_samples()
        self.session.expire_all()

        llm_row = self.session.execute(
            select(ExternalServiceUsageDaily).where(
                ExternalServiceUsageDaily.service == "llm"
            )
        ).scalar_one()
        self.assertEqual(llm_row.day.isoformat(), "2026-09-03")
        self.assertEqual(llm_row.call_count, 2)
        self.assertEqual(llm_row.success_count, 1)
        self.assertEqual(llm_row.failure_count, 1)
        self.assertEqual(llm_row.total_tokens, 15)
        self.assertEqual(llm_row.duration_ms, 150)
        self.assertEqual(llm_row.usage_missing_calls, 1)

        @contextmanager
        def broken_session_scope():
            raise RuntimeError("usage database unavailable")
            yield

        with (
            patch("raelyn.services.usage.session_scope", broken_session_scope),
            self.assertLogs("raelyn.services.usage", level="WARNING"),
        ):
            recorded = record_external_service_usage(
                service="llm",
                operation="brief.generate",
                succeeded=True,
            )
        self.assertFalse(recorded)

    def test_payload_uses_shanghai_days_and_preserves_unsampled_nulls(self) -> None:
        self._record_inventory()
        self._record_usage_samples()
        with patch("raelyn.services.usage.session_scope", self.session_scope):
            self.assertTrue(
                record_external_service_usage(
                    service="asr",
                    operation="transcribe",
                    provider="local",
                    model="qwen3-asr",
                    succeeded=True,
                    called_at=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
                )
            )
        capture_resource_usage_snapshot(self.session, captured_at=_NOW)
        self.session.commit()

        with patch("raelyn.services.usage.utcnow", return_value=_NOW):
            payload = build_usage_payload(self.session, days=7)

        self.assertEqual(payload["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["window"], {
            "days": 7,
            "start": _NOW.astimezone(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=6),
            "end": _NOW.astimezone(ZoneInfo("Asia/Shanghai")).date(),
        })
        self.assertEqual(payload["summary"]["video_count"], 3)
        self.assertEqual(payload["summary"]["downloaded_video_count"], 2)
        self.assertEqual(payload["summary"]["video_downloaded"], 3)
        self.assertEqual(payload["summary"]["external_calls"], 4)
        self.assertEqual(payload["summary"]["llm_calls"], 2)
        self.assertEqual(payload["summary"]["asr_calls"], 2)
        self.assertIsNone(payload["summary"]["embedding_calls"])
        self.assertEqual(payload["summary"]["llm_input_tokens"], 10)
        self.assertEqual(payload["summary"]["llm_output_tokens"], 5)
        self.assertEqual(payload["summary"]["llm_total_tokens"], 15)
        self.assertEqual(payload["summary"]["asset_count"], 3)
        self.assertEqual(payload["summary"]["asset_size_bytes"], 150)
        self.assertEqual(payload["summary"]["asset_missing_size_count"], 1)
        self.assertIsNone(payload["summary"]["database_size_bytes"])

        by_date = {point["date"].isoformat(): point for point in payload["series"]}
        self.assertEqual(by_date["2026-08-28"]["video_downloaded"], 1)
        self.assertEqual(by_date["2026-08-28"]["external_calls"], 0)
        self.assertIsNone(by_date["2026-08-28"]["llm_calls"])
        self.assertEqual(by_date["2026-08-28"]["asr_calls"], 0)
        self.assertIsNone(by_date["2026-08-28"]["embedding_calls"])
        self.assertTrue(by_date["2026-08-28"]["usage_sampled"])
        self.assertEqual(by_date["2026-09-02"]["external_calls"], 1)
        self.assertIsNone(by_date["2026-09-02"]["llm_calls"])
        self.assertEqual(by_date["2026-09-02"]["asr_calls"], 1)
        self.assertIsNone(by_date["2026-09-02"]["embedding_calls"])
        self.assertIsNone(by_date["2026-09-02"]["llm_total_tokens"])
        self.assertEqual(by_date["2026-09-03"]["video_downloaded"], 2)
        self.assertEqual(by_date["2026-09-03"]["external_calls"], 2)
        self.assertEqual(by_date["2026-09-03"]["asr_calls"], 0)
        self.assertIsNone(by_date["2026-09-03"]["embedding_calls"])
        self.assertEqual(by_date["2026-09-03"]["llm_total_tokens"], 15)
        self.assertEqual(by_date["2026-09-03"]["llm_usage_missing_calls"], 1)
        self.assertEqual(payload["summary"]["llm_usage_missing_calls"], 1)
        self.assertTrue(by_date["2026-09-03"]["resource_sampled"])
        self.assertEqual(by_date["2026-09-03"]["asset_size_bytes"], 150)

        self.assertEqual(
            [item["asset_type"] for item in payload["asset_breakdown"]],
            ["audio", "video"],
        )
        self.assertEqual(payload["asset_breakdown"][0]["missing_size_count"], 1)
        llm_breakdown = next(
            item for item in payload["service_breakdown"] if item["service"] == "llm"
        )
        self.assertEqual(llm_breakdown["calls"], 2)
        self.assertEqual(llm_breakdown["usage_missing_calls"], 1)
        self.assertEqual(payload["collection"]["usage_started_at"].isoformat(), "2026-08-01")
        self.assertFalse(payload["collection"]["physical_storage_available"])
        self.assertEqual(
            payload["collection"]["physical_storage_reason"],
            "未接入部署侧指标",
        )

    def test_resource_snapshot_keeps_newest_capture_for_the_day(self) -> None:
        self._record_inventory()
        newer = capture_resource_usage_snapshot(
            self.session,
            captured_at=datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc),
        )
        self.session.commit()
        older = capture_resource_usage_snapshot(
            self.session,
            captured_at=datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc),
        )
        self.session.commit()

        self.assertEqual(newer.captured_at, older.captured_at)
        self.assertEqual(older.video_count, 3)

    def test_live_summary_does_not_require_or_reuse_a_snapshot(self) -> None:
        self._record_inventory()
        with patch("raelyn.services.usage.utcnow", return_value=_NOW):
            payload_without_snapshot = build_usage_payload(self.session, days=7)

        self.assertEqual(payload_without_snapshot["summary"]["video_count"], 3)
        self.assertEqual(payload_without_snapshot["summary"]["downloaded_video_count"], 2)
        self.assertEqual(payload_without_snapshot["summary"]["video_downloaded"], 3)
        self.assertEqual(payload_without_snapshot["summary"]["asset_size_bytes"], 150)
        self.assertIsNone(payload_without_snapshot["summary"]["external_calls"])
        self.assertFalse(payload_without_snapshot["series"][-1]["resource_sampled"])
        self.assertIsNone(payload_without_snapshot["series"][-1]["asset_size_bytes"])

        capture_resource_usage_snapshot(self.session, captured_at=_NOW)
        extra_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="after-snapshot",
            media_id=self.media.id,
            url="https://www.youtube.com/watch?v=after-snapshot",
            created_at=_NOW,
        )
        self.session.add(extra_video)
        self.session.flush()
        self.session.add(
            Asset(
                id=uuid.uuid4(),
                video_id=extra_video.id,
                type="video",
                format="mp4",
                source="yt-dlp",
                s3_bucket="test",
                s3_key="video/after-snapshot.mp4",
                size_bytes=25,
            )
        )
        self.session.commit()

        with patch("raelyn.services.usage.utcnow", return_value=_NOW):
            payload_with_stale_snapshot = build_usage_payload(self.session, days=7)

        self.assertEqual(payload_with_stale_snapshot["summary"]["video_count"], 4)
        self.assertEqual(payload_with_stale_snapshot["summary"]["downloaded_video_count"], 3)
        self.assertEqual(payload_with_stale_snapshot["summary"]["asset_size_bytes"], 175)
        self.assertEqual(payload_with_stale_snapshot["series"][-1]["asset_size_bytes"], 150)
        self.assertFalse(payload_with_stale_snapshot["series"][-1]["usage_sampled"])
        self.assertIsNone(payload_with_stale_snapshot["series"][-1]["external_calls"])


class UsageApiTests(_UsageFixture, unittest.IsolatedAsyncioTestCase):
    async def test_usage_endpoint_has_fixed_shape_and_validates_days(self) -> None:
        self._record_inventory()
        self._record_usage_samples()
        with ExitStack() as stack:
            stack.enter_context(patch.object(config.settings, "api_bearer_token", ""))
            stack.enter_context(patch("raelyn.db.init_db"))
            stack.enter_context(patch("raelyn.services.s3.s3_ensure_bucket"))
            stack.enter_context(patch("raelyn.recover_orphan_jobs.recover"))
            stack.enter_context(patch("raelyn.api.usage.session_scope", self.session_scope))
            stack.enter_context(patch("raelyn.services.usage.utcnow", return_value=_NOW))
            sys.modules.pop("raelyn.main", None)
            main_module = importlib.import_module("raelyn.main")
            transport = ASGITransport(app=main_module.create_app())
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get("/api/usage?days=7")
                default_response = await client.get("/api/usage")
                invalid_response = await client.get("/api/usage?days=8")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {
                "generated_at",
                "timezone",
                "window",
                "summary",
                "series",
                "service_breakdown",
                "asset_breakdown",
                "collection",
            },
        )
        self.assertEqual(len(payload["series"]), 7)
        self.assertEqual(payload["summary"]["video_count"], 3)
        self.assertFalse(payload["series"][-1]["resource_sampled"])
        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(default_response.json()["window"]["days"], 30)
        self.assertEqual(invalid_response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
