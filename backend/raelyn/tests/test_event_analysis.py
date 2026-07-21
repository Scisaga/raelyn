from __future__ import annotations

from contextlib import nullcontext
import json
import sys
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.enqueue import _normalize_dedupe_key_and_params
from raelyn.models import (
    Asset,
    EventRegimeCandidate,
    EventRegimeRun,
    EventRegimeSignal,
    EventRegimeState,
    Job,
    MarketEvent,
    MarketEventEmbedding,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
    Media,
    Playlist,
    Video,
    VideoEventExtractionRun,
)
from raelyn.services import event_analysis
from raelyn.services.inference import EffectiveLlmConfig
from raelyn.services.job_cancellation import JobCancelRequested
from raelyn.jobs.reschedule import JobTerminalFailure


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._values)

    def all(self):
        return self._values


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _OneResult:
    def __init__(self, value):
        self._value = value

    def one(self):
        return self._value


class _IterableRows:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def __iter__(self):
        return iter(self._rows)

    def close(self):
        self.closed = True


class EventAnalysisTests(unittest.TestCase):
    def test_event_models_have_expected_constraints(self) -> None:
        self.assertIn("market_event_source_event_ux", {c.name for c in MarketEvent.__table__.constraints})
        self.assertIn("market_event_evidence_ux", {c.name for c in MarketEventEvidence.__table__.constraints})
        self.assertIn("market_event_entity_ux", {c.name for c in MarketEventEntity.__table__.constraints})
        self.assertIn("market_event_embedding_ux", {c.name for c in MarketEventEmbedding.__table__.constraints})
        self.assertIn("event_regime_signal_ux", {c.name for c in EventRegimeSignal.__table__.constraints})
        self.assertIn("event_regime_candidate_ux", {c.name for c in EventRegimeCandidate.__table__.constraints})
        self.assertIn("video_event_extraction_run_ux", {c.name for c in VideoEventExtractionRun.__table__.constraints})

        for model in [MarketEvent, MarketEventEvidence, MarketEventEntity, MarketEventRelation, MarketEventEmbedding]:
            self.assertIn("event_id" if model is not MarketEvent else "source_video_id", model.__table__.columns)
        self.assertIn("last_ready_run_id", EventRegimeState.__table__.columns)
        self.assertIn("event_total", EventRegimeRun.__table__.columns)
        self.assertIn("event_count", VideoEventExtractionRun.__table__.columns)

    def test_source_map_generates_stable_title_description_and_transcript_ids(self) -> None:
        video_id = uuid.uuid4()
        transcript_asset_id = uuid.uuid4()
        video = Video(
            id=video_id,
            provider="youtube",
            provider_video_id="v1",
            media_id=uuid.uuid4(),
            url="https://example.test/watch?v=v1",
            title="Title event",
            description="Description event",
        )

        sources = event_analysis._build_event_sources(
            alias="v1",
            video=video,
            transcript_asset_id=transcript_asset_id,
            transcript_text="Alpha. Beta.",
            chunk_text="Alpha. Beta.",
            chunk_start=0,
        )

        source_ids = [source.source_id for source in sources]
        self.assertEqual(source_ids[:3], ["v1.title", "v1.desc", "v1.t001"])
        self.assertEqual(sources[2].source_kind, "transcript")
        self.assertEqual(sources[2].char_start, 0)
        self.assertEqual(sources[2].char_end, len("Alpha. Beta."))

    def test_event_description_source_text_removes_channel_noise(self) -> None:
        cleaned = event_analysis._event_description_source_text(
            "\n".join(
                [
                    "公司公布五月营收年增 20%。",
                    "https://example.test/video",
                    "更多必看经典影片：上一集分析",
                    "加入会员支持频道",
                    "#AI #台股",
                    "外资连续买超电子股。",
                ]
            )
        )

        self.assertIn("公司公布五月营收年增 20%。", cleaned)
        self.assertIn("外资连续买超电子股。", cleaned)
        self.assertNotIn("https://", cleaned)
        self.assertNotIn("更多必看", cleaned)
        self.assertNotIn("会员", cleaned)
        self.assertNotIn("#AI", cleaned)

    def test_event_llm_call_uses_ollama_stream_and_generation_limits(self) -> None:
        cfg = EffectiveLlmConfig(
            mode="local",
            provider="local",
            source="test",
            url="http://llm.test/api/generate",
            model="qwen3.6:35b",
            api_key="",
            headers_json="",
            timeout_seconds=600,
            configured=True,
        )
        session = Mock()

        with patch("raelyn.services.event_analysis.get_effective_llm_config", return_value=cfg):
            with patch("raelyn.services.event_analysis.llm_generate", return_value={"text": "{}", "usage": {}}) as llm_generate:
                result = event_analysis._generate_event_extraction_llm(session, prompt="prompt")

        self.assertEqual(result["text"], "{}")
        self.assertEqual(llm_generate.call_args.kwargs["think"], False)
        self.assertEqual(llm_generate.call_args.kwargs["response_format"], "json")
        self.assertEqual(llm_generate.call_args.kwargs["stream"], True)
        self.assertEqual(llm_generate.call_args.kwargs["idle_timeout_seconds"], 120)
        self.assertEqual(
            llm_generate.call_args.kwargs["options"],
            {"temperature": 0, "num_ctx": 8192, "num_predict": 2500},
        )

    def test_parse_event_response_strips_think_and_drops_bad_events(self) -> None:
        raw = """
<think>hidden</think>
```json
{
  "events": [
    {"title": "Fed cuts rates", "summary": "Fed lowered rates.", "confidence": 0.91},
    {"title": "", "summary": "", "confidence": 0.8},
    {"title": "bad confidence", "summary": "x", "confidence": "high"}
  ]
}
```
"""
        events, warnings = event_analysis.parse_event_extraction_response(raw)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["confidence"], 0.91)
        self.assertGreaterEqual(len(warnings), 2)

    def test_parse_event_batch_response_groups_by_video_and_reports_missing(self) -> None:
        raw = json.dumps(
            {
                "videos": [
                    {
                        "video_id": "v1",
                        "events": [
                            {
                                "title": "Hegseth warns Europe",
                                "summary": "Warning about Europe.",
                                "confidence": 0.9,
                                "evidence_source_ids": ["v1.title"],
                            }
                        ],
                    }
                ]
            }
        )
        events_by_video, warnings = event_analysis.parse_event_extraction_batch_response(
            raw,
            expected_video_ids=["v1", "v2"],
        )

        self.assertEqual(len(events_by_video["v1"]), 1)
        self.assertEqual(events_by_video["v2"], [])
        self.assertTrue(any("missing video_id v2" in message for message in warnings))

    def test_default_event_prompt_requires_specific_market_scope(self) -> None:
        prompt = event_analysis.DEFAULT_EVENT_EXTRACTION_PROMPT

        self.assertIn("具体市场类别", prompt)
        self.assertIn('"videos"', prompt)
        self.assertIn("evidence_source_ids", prompt)
        self.assertIn("不要输出 evidence_quotes", prompt)
        self.assertIn("房地产市场", prompt)
        self.assertIn("禁止单独写“市场”", prompt)
        self.assertIn("对象口径必须一致", prompt)
        self.assertIn("台股国巨(2327.TW)", prompt)
        self.assertIn("company 表示发行公司", prompt)
        self.assertIn("asset 表示可交易证券", prompt)
        self.assertIn("不要把普通词误标为 company 或 asset", prompt)
        self.assertIn("资金面或买盘", prompt)
        self.assertIn("不是事件的内容必须过滤掉", prompt)
        self.assertIn("操作策略、荐股建议、观察名单", prompt)
        self.assertIn("events 为空数组", prompt)

    def test_event_time_unknown_does_not_parse(self) -> None:
        start, end, precision = event_analysis._event_time({"event_time": {"start": "", "time_precision": "unknown"}})
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertEqual(precision, "unknown")

    def test_invalid_event_time_is_unknown_not_job_failure(self) -> None:
        start, end, precision = event_analysis._event_time({"event_time": {"start": "2026-02-31", "end": "2026-13", "time_precision": "day"}})
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertEqual(precision, "unknown")

    def test_extract_video_events_commits_before_embedding_enqueue(self) -> None:
        video_id = uuid.uuid4()
        media_id = uuid.uuid4()
        transcript_asset_id = uuid.uuid4()
        event_id = uuid.uuid4()
        video = Video(
            id=video_id,
            provider="youtube",
            provider_video_id="v1",
            media_id=media_id,
            url="https://example.test/watch?v=v1",
            title="Fed decision",
            published_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        )
        media = Media(id=media_id, provider="youtube", provider_media_id="m1", url="https://example.test/@m1")
        transcript_asset = Asset(
            id=transcript_asset_id,
            video_id=video_id,
            type="transcript",
            format="txt",
            source="asr",
            variant="plain",
            s3_bucket="b",
            s3_key="k",
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.extract_events",
            status="running",
            params={"video_id": str(video_id)},
            priority=4,
        )
        session = Mock()
        session.begin_nested.return_value = nullcontext()
        session.execute.side_effect = [_ScalarOneOrNone(None), _ScalarResult([]), _ScalarOneOrNone(None)]

        def _get(model, key):
            if model is Video:
                return video
            if model is Media:
                return media
            return None

        def _flush(objects=None):
            for obj in objects or []:
                if isinstance(obj, MarketEvent) and obj.id is None:
                    obj.id = event_id

        session.get.side_effect = _get
        session.flush.side_effect = _flush
        order: list[str] = []
        session.commit.side_effect = lambda: order.append("commit")

        response = json.dumps(
            {
                "videos": [
                    {
                        "video_id": "v1",
                        "events": [
                            {
                                "event_time": {"start": "2026-06-04", "time_precision": "day"},
                                "event_type": "monetary_policy",
                                "title": "Fed keeps rates unchanged",
                                "summary": "Fed kept rates unchanged.",
                                "evidence_source_ids": ["v1.t001"],
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            }
        )
        spec = event_analysis.EventExtractionSpec(model="m", prompt_version="p", prompt_text="prompt", chunk_max_chars=12000)

        def _enqueue_embeddings(*args, **kwargs):
            order.append("enqueue_embeddings")
            return 1

        def _schedule_dirty(*args, **kwargs):
            order.append("schedule_dirty")
            return 1

        with patch("raelyn.services.event_analysis.llm_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.pick_transcript_asset", return_value=transcript_asset):
                with patch("raelyn.services.event_analysis.read_text_asset", return_value=("transcript", {})):
                    with patch("raelyn.services.event_analysis.event_extraction_spec", return_value=spec):
                        with patch("raelyn.services.event_analysis._event_source_hash", return_value="hash"):
                            with patch("raelyn.services.event_analysis.resolve_video_timeline", return_value=SimpleNamespace(content_published_at=video.published_at)):
                                with patch("raelyn.services.event_analysis.llm_generate", return_value={"text": response, "usage": {}}) as llm_generate:
                                    with patch("raelyn.services.event_analysis.set_job_progress"):
                                        with patch("raelyn.services.event_analysis.mark_playlists_event_regime_dirty_for_video") as mark_dirty:
                                            with patch(
                                                "raelyn.services.event_analysis.enqueue_event_embedding_jobs",
                                                side_effect=_enqueue_embeddings,
                                            ) as enqueue_embeddings:
                                                with patch(
                                                    "raelyn.services.event_analysis.schedule_playlists_event_regime_dirty_for_video",
                                                    side_effect=_schedule_dirty,
                                                ) as schedule_dirty:
                                                    result = event_analysis.extract_video_events(session, video_id=video_id, job=job)

        self.assertEqual(order[-3:], ["commit", "enqueue_embeddings", "schedule_dirty"])
        mark_dirty.assert_not_called()
        llm_generate.assert_called_once()
        self.assertEqual(llm_generate.call_args.kwargs["think"], False)
        self.assertEqual(llm_generate.call_args.kwargs["response_format"], "json")
        self.assertEqual(llm_generate.call_args.kwargs["options"]["temperature"], 0)
        enqueue_embeddings.assert_called_once_with(session, event_ids=[event_id], priority=4)
        schedule_dirty.assert_called_once_with(
            session,
            video_id=video_id,
            reason="video_events_extracted",
            source_job_id=job.id,
            priority=4,
        )
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["videos"], 1)
        self.assertEqual(result["embedding_jobs_enqueued"], 1)
        self.assertEqual(result["dirty_jobs_enqueued"], 1)

    def test_force_extract_schedules_dirty_when_llm_returns_no_events(self) -> None:
        video_id = uuid.uuid4()
        media_id = uuid.uuid4()
        transcript_asset_id = uuid.uuid4()
        video = Video(
            id=video_id,
            provider="youtube",
            provider_video_id="v1",
            media_id=media_id,
            url="https://example.test/watch?v=v1",
            title="No events",
            published_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        )
        media = Media(id=media_id, provider="youtube", provider_media_id="m1", url="https://example.test/@m1")
        transcript_asset = Asset(
            id=transcript_asset_id,
            video_id=video_id,
            type="transcript",
            format="txt",
            source="asr",
            variant="plain",
            s3_bucket="b",
            s3_key="k",
        )
        job = Job(id=uuid.uuid4(), type="video.extract_events", status="running", params={"video_id": str(video_id), "force": True}, priority=4)
        session = Mock()
        session.begin_nested.return_value = nullcontext()
        session.execute.side_effect = [_ScalarResult([]), _ScalarResult([]), _ScalarOneOrNone(None)]

        def _get(model, key):
            if model is Video:
                return video
            if model is Media:
                return media
            return None

        session.get.side_effect = _get
        response = json.dumps({"events": []})
        spec = event_analysis.EventExtractionSpec(model="m", prompt_version="p", prompt_text="prompt", chunk_max_chars=12000)

        with patch("raelyn.services.event_analysis.llm_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.pick_transcript_asset", return_value=transcript_asset):
                with patch("raelyn.services.event_analysis.read_text_asset", return_value=("transcript", {})):
                    with patch("raelyn.services.event_analysis.event_extraction_spec", return_value=spec):
                        with patch("raelyn.services.event_analysis._event_source_hash", return_value="hash"):
                            with patch("raelyn.services.event_analysis.resolve_video_timeline", return_value=SimpleNamespace(content_published_at=video.published_at)):
                                with patch("raelyn.services.event_analysis.llm_generate", return_value={"text": response, "usage": {}}):
                                    with patch("raelyn.services.event_analysis.set_job_progress"):
                                        with patch("raelyn.services.event_analysis.enqueue_event_embedding_jobs", return_value=0):
                                            with patch(
                                                "raelyn.services.event_analysis.schedule_playlists_event_regime_dirty_for_video",
                                                return_value=1,
                                            ) as schedule_dirty:
                                                result = event_analysis.extract_video_events(session, video_id=video_id, force=True, job=job)

        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["dirty_jobs_enqueued"], 1)
        schedule_dirty.assert_called_once()

    def test_high_confidence_without_verified_provenance_becomes_draft(self) -> None:
        source = event_analysis._EventSource(
            source_id="v1.title",
            video_alias="v1",
            video_id=uuid.uuid4(),
            source_kind="title",
            source_label="标题",
            text="Title",
            char_start=0,
            char_end=5,
            source_sha256=event_analysis._sha256_text("Title"),
            transcript_asset_id=None,
        )
        rows, warnings = event_analysis._evidence_rows_from_source_ids(
            raw={"confidence": 0.95, "evidence_source_ids": ["bad.id"]},
            source_by_id={source.source_id: source},
        )

        self.assertEqual(rows, [])
        self.assertTrue(warnings)

    def test_event_regime_period_uses_event_time_and_respects_precision(self) -> None:
        event = MarketEvent(
            source_video_id=uuid.uuid4(),
            source_hash="h",
            event_key="k",
            status="accepted",
            event_type="macro",
            time_precision="day",
            event_time_start=datetime(2012, 3, 23, tzinfo=timezone.utc),
            available_at=datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(event_analysis._event_regime_period_date(event, "day"), date(2012, 3, 23))
        self.assertEqual(event_analysis._event_regime_period_date(event, "week"), date(2012, 3, 19))
        self.assertEqual(event_analysis._event_regime_period_date(event, "month"), date(2012, 3, 1))

        event.time_precision = "month"
        self.assertIsNone(event_analysis._event_regime_period_date(event, "day"))
        self.assertIsNone(event_analysis._event_regime_period_date(event, "week"))
        self.assertEqual(event_analysis._event_regime_period_date(event, "month"), date(2012, 3, 1))

        event.time_precision = "year"
        self.assertIsNone(event_analysis._event_regime_period_date(event, "day"))
        self.assertIsNone(event_analysis._event_regime_period_date(event, "week"))
        self.assertIsNone(event_analysis._event_regime_period_date(event, "month"))

    def test_event_regime_coverage_separates_ready_eligible_and_scale_excluded(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.execute.return_value = _OneResult((161840, 161840, 160876, 157645, 0))

        with patch("raelyn.services.event_analysis.embedding_spec", return_value=SimpleNamespace(model="m", dim=1024)):
            coverage = event_analysis.playlist_event_regime_coverage(session, playlist_id)

        self.assertEqual(
            coverage,
            {
                "event_total": 161840,
                "event_embedded": 161840,
                "event_eligible": 160876,
                "event_scale_excluded": 3231,
                "event_skipped": 964,
                "event_failed": 0,
            },
        )
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("market_event.event_time_start is not null", compiled)
        self.assertIn("market_event_embedding.embedding_model = 'm'", compiled)
        self.assertIn("market_event_embedding.embedding_dim = 1024", compiled)
        self.assertNotIn("market_event.available_at is not null", compiled)

    def test_event_regime_rows_use_streaming_and_event_time_order(self) -> None:
        playlist_id = uuid.uuid4()
        result = _IterableRows([])
        session = Mock()
        session.execute.return_value = result

        with patch.object(event_analysis.settings, "analysis_stream_batch_size", 17):
            rows = list(
                event_analysis._event_rows_for_regime(
                    session,
                    playlist_id,
                    embedding_model="m",
                    embedding_dim=2,
                )
            )

        self.assertEqual(rows, [])
        self.assertTrue(result.closed)
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("order by market_event.event_time_start asc, market_event.id asc", compiled)
        self.assertNotIn("market_event.available_at is not null", compiled)
        self.assertTrue(stmt.get_execution_options()["stream_results"])
        self.assertEqual(stmt.get_execution_options()["yield_per"], 17)

    def test_event_regime_snapshot_reader_uses_one_repeatable_read_transaction(self) -> None:
        session = Mock()
        engine = MagicMock()
        engine.dialect.name = "postgresql"
        raw_connection = MagicMock()
        connection = MagicMock()
        engine.connect.return_value.__enter__.return_value = raw_connection
        raw_connection.execution_options.return_value = connection
        session.get_bind.return_value = engine

        with event_analysis._event_regime_snapshot_reader(session) as reader:
            self.assertIs(reader, connection)

        raw_connection.execution_options.assert_called_once_with(isolation_level="REPEATABLE READ")
        connection.begin.assert_called_once_with()
        connection.exec_driver_sql.assert_called_once_with("SET TRANSACTION READ ONLY")

    def test_event_regime_second_pass_reports_unknown_period_as_snapshot_change(self) -> None:
        video_id = uuid.uuid4()
        first_row = (
            uuid.uuid4(),
            video_id,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "day",
            None,
            "first",
            None,
            "macro",
            [1.0, 0.0],
        )
        changed_row = (
            uuid.uuid4(),
            video_id,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            "day",
            None,
            "changed",
            None,
            "macro",
            [0.0, 1.0],
        )
        aggregates, _ = event_analysis._build_event_regime_period_aggregates(
            iter([first_row]),
            embedding_dim=2,
            batch_size=10,
            checkpoint=lambda _processed: None,
        )

        with self.assertRaisesRegex(RuntimeError, "unexpected day period 2026-02-01"):
            event_analysis._populate_event_regime_dispersions_and_evidence(
                iter([changed_row]),
                aggregates=aggregates,
                embedding_dim=2,
                batch_size=10,
                checkpoint=lambda _processed: None,
            )

    def test_event_regime_two_pass_aggregation_does_not_put_coarse_dates_in_finer_scales(self) -> None:
        video_id = uuid.uuid4()

        def row(day: int, precision: str, vector: list[float], *, month: int = 1):
            event_time = datetime(2026, month, day, tzinfo=timezone.utc)
            return (
                uuid.uuid4(),
                video_id,
                event_time,
                precision,
                event_time,
                f"{precision}-{month}-{day}",
                None,
                "macro",
                vector,
            )

        rows = [
            row(1, "day", [1.0, 0.0]),
            row(1, "day", [0.0, 1.0]),
            row(8, "day", [1.0, 1.0]),
            row(1, "month", [1.0, 0.0], month=2),
            row(1, "year", [9.0, 9.0], month=3),
        ]
        checkpoints: list[int] = []

        aggregates, processed = event_analysis._build_event_regime_period_aggregates(
            iter(rows),
            embedding_dim=2,
            batch_size=2,
            checkpoint=checkpoints.append,
        )

        self.assertEqual(processed, 5)
        self.assertEqual([item.event_count for item in aggregates["day"]], [2, 1])
        self.assertEqual([item.event_count for item in aggregates["week"]], [2, 1])
        self.assertEqual([item.event_count for item in aggregates["month"]], [3, 1])
        self.assertEqual(list(aggregates["day"][0].centroid), [0.5, 0.5])
        self.assertAlmostEqual(aggregates["month"][0].centroid[0], 2.0 / 3.0)
        self.assertAlmostEqual(aggregates["month"][0].centroid[1], 2.0 / 3.0)

        second_processed = event_analysis._populate_event_regime_dispersions_and_evidence(
            iter(rows),
            aggregates=aggregates,
            embedding_dim=2,
            batch_size=2,
            checkpoint=checkpoints.append,
        )

        self.assertEqual(second_processed, 5)
        self.assertIsNotNone(aggregates["day"][0].dispersion_mean)
        self.assertIsNotNone(aggregates["month"][0].dispersion_mean)

    def test_event_regime_candidates_use_period_end_and_bounded_centroid_representatives(self) -> None:
        video_id = uuid.uuid4()
        far_event_id = uuid.UUID(int=1)
        rows = []
        for idx in range(21):
            event_time = datetime(2026, 1, 5, 0, 0, idx, tzinfo=timezone.utc)
            rows.append(
                (
                    far_event_id if idx == 0 else uuid.UUID(int=idx + 1),
                    video_id,
                    event_time,
                    "day",
                    event_time,
                    "远离中心" if idx == 0 else f"代表事件{idx}",
                    None,
                    "macro",
                    [-1.0, 0.0] if idx == 0 else [1.0, 0.0],
                )
            )

        aggregates, _ = event_analysis._build_event_regime_period_aggregates(
            iter(rows),
            embedding_dim=2,
            batch_size=10,
            checkpoint=lambda _processed: None,
        )
        aggregates["week"][0].drift_rolling_z = 2.1
        aggregates["month"][0].drift_rolling_z = 2.2
        event_analysis._populate_event_regime_dispersions_and_evidence(
            iter(rows),
            aggregates=aggregates,
            embedding_dim=2,
            batch_size=10,
            checkpoint=lambda _processed: None,
        )

        week_evidence = aggregates["week"][0].evidence
        self.assertEqual(len(week_evidence), 20)
        self.assertNotIn(far_event_id, {item.event_id for item in week_evidence})
        self.assertEqual(
            week_evidence,
            sorted(week_evidence, key=event_analysis._event_regime_evidence_sort_key),
        )

        candidates, _ = event_analysis._event_regime_candidate_models(
            run_id=uuid.uuid4(),
            aggregates=aggregates,
        )
        by_granularity = {candidate.evidence_json["granularity"]: candidate for candidate in candidates}
        self.assertEqual(by_granularity["week"].event_start, date(2026, 1, 5))
        self.assertEqual(by_granularity["week"].event_end, date(2026, 1, 11))
        self.assertEqual(by_granularity["month"].event_start, date(2026, 1, 1))
        self.assertEqual(by_granularity["month"].event_end, date(2026, 1, 31))
        self.assertEqual(by_granularity["week"].evidence_json["sampling"], "centroid_nearest_top_20_v1")
        self.assertNotIn(str(far_event_id), by_granularity["week"].evidence_event_ids)
        self.assertNotIn("远离中心", by_granularity["week"].summary)

    def test_analysis_memory_gate_enforces_configured_rss_limit(self) -> None:
        rss = event_analysis._proc_memory_value_bytes("/proc/self/status", "VmRSS")
        if rss is None:
            self.skipTest("当前平台不提供 /proc/self/status VmRSS")

        with patch.object(event_analysis.settings, "analysis_min_available_memory_bytes", 0):
            with patch.object(event_analysis.settings, "analysis_max_rss_bytes", max(1, rss - 1)):
                with self.assertRaises(JobTerminalFailure) as raised:
                    event_analysis._raise_if_analysis_memory_limit_exceeded()

        self.assertIn("worker RSS", raised.exception.reason)

    def test_snapshot_terminal_failure_rolls_back_partial_output_and_marks_run_failed(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        run = EventRegimeRun(
            id=run_id,
            playlist_id=playlist_id,
            status="running",
            analysis_clock="day",
            embedding_model="m",
            embedding_dim=2,
        )
        state = EventRegimeState(playlist_id=playlist_id, analysis_dirty=False)
        job = Job(
            id=uuid.uuid4(),
            type="playlist.build_event_regime_snapshot",
            status="running",
            params={"playlist_id": str(playlist_id), "regime_run_id": str(run_id)},
        )
        session = Mock()
        session.get.return_value = run
        failure = JobTerminalFailure("analysis aborted: worker RSS exceeded")

        with patch("raelyn.services.event_analysis._build_event_regime_snapshot", side_effect=failure):
            with patch("raelyn.services.event_analysis.ensure_event_regime_state", return_value=state):
                with self.assertRaises(JobTerminalFailure):
                    event_analysis.build_event_regime_snapshot(session, playlist_id=playlist_id, job=job)

        session.rollback.assert_called_once_with()
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)
        self.assertTrue(state.analysis_dirty)
        self.assertEqual(state.last_error, failure.reason)
        session.flush.assert_called_once()

    def test_update_event_status_accepts_and_enqueues_embedding(self) -> None:
        event_id = uuid.uuid4()
        video_id = uuid.uuid4()
        event = MarketEvent(
            id=event_id,
            source_video_id=video_id,
            source_hash="h",
            event_key="k",
            status="draft",
            event_type="macro",
            time_precision="day",
            event_time_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session = Mock()
        session.get.return_value = event

        with patch("raelyn.services.event_analysis.schedule_playlists_event_regime_dirty_for_video") as schedule_dirty:
            with patch("raelyn.services.event_analysis.enqueue_job") as enqueue_job:
                updated = event_analysis.update_event_status(session, event_id=event_id, status="accepted")

        self.assertIs(updated, event)
        self.assertEqual(event.status, "accepted")
        schedule_dirty.assert_called_once_with(session, video_id=video_id, reason="event_status_changed")
        enqueue_job.assert_called_once_with(session, type_="event.embed", params={"event_id": str(event_id)}, priority=0)

    def test_embed_event_failed_status_schedules_dirty_without_retry(self) -> None:
        event_id = uuid.uuid4()
        video_id = uuid.uuid4()
        embedding_id = uuid.uuid4()
        event = MarketEvent(
            id=event_id,
            source_video_id=video_id,
            source_hash="h",
            event_key="k",
            status="accepted",
            event_type="macro",
            title="Event",
        )
        session = Mock()
        session.get.return_value = event
        session.execute.return_value = _ScalarOneOrNone(None)

        def _flush(objects=None):
            for obj in objects or []:
                if isinstance(obj, MarketEventEmbedding) and obj.id is None:
                    obj.id = embedding_id

        session.flush.side_effect = _flush

        with patch("raelyn.services.event_analysis.embedding_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.embedding_spec", return_value=SimpleNamespace(model="m", dim=3)):
                with patch("raelyn.services.event_analysis._event_embedding_text", return_value="text"):
                    with patch("raelyn.services.event_analysis.embed_text", side_effect=event_analysis.EmbeddingError("bad response")):
                        with patch("raelyn.services.event_analysis.schedule_playlists_event_regime_dirty_for_video") as schedule_dirty:
                            result = event_analysis.embed_event(session, event_id=event_id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["embedding_id"], str(embedding_id))
        created_embedding = session.add.call_args.args[0]
        self.assertEqual(created_embedding.embedding_model, "m")
        self.assertEqual(created_embedding.embedding_dim, 3)
        schedule_dirty.assert_called_once_with(session, video_id=video_id, reason="event_embedding_changed")

    def test_job_dedupe_keys_use_event_job_types(self) -> None:
        video_id = uuid.uuid4()
        event_id = uuid.uuid4()
        playlist_id = uuid.uuid4()

        with patch("raelyn.jobs.enqueue.settings.llm_model", "qwen3.6:35b"):
            key, params = _normalize_dedupe_key_and_params("video.extract_events", {"video_id": str(video_id)})
        self.assertEqual(key, f"video_event_extract:{video_id}:qwen3.6:35b:missing")
        self.assertFalse(params["force"])

        with patch("raelyn.jobs.enqueue.settings.llm_model", "qwen3.6:35b"):
            key, params = _normalize_dedupe_key_and_params(
                "video.extract_events_batch",
                {"video_ids": [str(video_id), str(video_id)], "force": True},
            )
        self.assertIn("video_event_extract_batch:", key)
        self.assertEqual(params["video_ids"], [str(video_id)])
        self.assertTrue(params["force"])

        key, _ = _normalize_dedupe_key_and_params("event.embed", {"event_id": str(event_id)})
        self.assertIn(f"event_embedding:{event_id}", key)

        with patch("raelyn.jobs.enqueue.settings.llm_model", "qwen3.6:35b"):
            key, params = _normalize_dedupe_key_and_params("playlist.backfill_events", {"playlist_id": str(playlist_id), "force": True})
        self.assertEqual(key, f"playlist_event_backfill:{playlist_id}:qwen3.6:35b:force")
        self.assertTrue(params["force"])

        with patch("raelyn.jobs.enqueue.settings.llm_model", "qwen3.6:35b"):
            key, params = _normalize_dedupe_key_and_params(
                "playlist.backfill_events_range",
                {
                    "playlist_id": str(playlist_id),
                    "force": True,
                    "range_start": "2026-01-01",
                    "range_end": "2026-02-01",
                },
            )
        self.assertEqual(key, f"playlist_event_backfill_range:{playlist_id}:2026-01-01:2026-02-01:qwen3.6:35b:force")
        self.assertTrue(params["force"])

        key, params = _normalize_dedupe_key_and_params(
            "playlist.mark_event_regime_dirty",
            {"playlist_id": str(playlist_id), "reason": "video_events_extracted", "source_video_id": str(video_id)},
        )
        self.assertEqual(key, f"playlist_event_dirty:{playlist_id}")
        self.assertEqual(params["playlist_id"], str(playlist_id))
        self.assertEqual(params["source_video_id"], str(video_id))

    def test_schedule_playlists_event_regime_dirty_queries_playlists_in_stable_order(self) -> None:
        video_id = uuid.uuid4()
        media_id = uuid.uuid4()
        playlist_id = uuid.uuid4()
        video = Video(
            id=video_id,
            provider="youtube",
            provider_video_id="abc123",
            media_id=media_id,
            url="https://example.test/watch?v=abc123",
        )
        session = Mock()
        session.get.return_value = video
        session.execute.return_value = _ScalarResult([playlist_id])

        with patch("raelyn.services.event_analysis.schedule_playlist_event_regime_dirty", return_value=uuid.uuid4()):
            count = event_analysis.schedule_playlists_event_regime_dirty_for_video(
                session,
                video_id=video_id,
                reason="video_events_extracted",
            )

        self.assertEqual(count, 1)
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("order by playlist_media.playlist_id", compiled)

    def test_mark_playlist_event_regime_dirty_skips_already_dirty_state(self) -> None:
        playlist_id = uuid.uuid4()
        updated_at = datetime(2026, 6, 4, tzinfo=timezone.utc)
        state = EventRegimeState(playlist_id=playlist_id, analysis_dirty=True, updated_at=updated_at)
        session = Mock()
        session.get.return_value = state

        result = event_analysis.mark_playlist_event_regime_dirty_if_needed(session, playlist_id)

        self.assertTrue(result["already_dirty"])
        self.assertIs(state.updated_at, updated_at)
        session.flush.assert_not_called()

    def test_mark_playlist_event_regime_dirty_updates_clean_state(self) -> None:
        playlist_id = uuid.uuid4()
        updated_at = datetime(2026, 6, 4, tzinfo=timezone.utc)
        state = EventRegimeState(playlist_id=playlist_id, analysis_dirty=False, updated_at=updated_at)
        session = Mock()
        session.get.return_value = state

        result = event_analysis.mark_playlist_event_regime_dirty_if_needed(session, playlist_id)

        self.assertTrue(result["dirty"])
        self.assertTrue(state.analysis_dirty)
        self.assertIsNot(state.updated_at, updated_at)
        session.flush.assert_called_once()

    def test_playlist_event_pipeline_job_query_is_scoped_to_playlist(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.execute.return_value = _ScalarResult([])

        event_analysis._active_playlist_event_pipeline_jobs(session, playlist_id)

        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("playlist.backfill_events", compiled)
        self.assertIn("playlist.backfill_events_range", compiled)
        self.assertIn("video.extract_events", compiled)
        self.assertIn("video.extract_events_batch", compiled)
        self.assertIn("event.embed", compiled)
        self.assertIn("playlist.mark_event_regime_dirty", compiled)
        self.assertIn("playlist.build_event_regime_snapshot", compiled)
        self.assertIn(str(playlist_id), compiled)
        self.assertIn("playlist_media.playlist_id", compiled)

    def test_cancel_playlist_event_pipeline_jobs_cancels_active_jobs_and_regime_runs(self) -> None:
        playlist_id = uuid.uuid4()
        pending_job = Job(
            id=uuid.uuid4(),
            type="playlist.backfill_events",
            status="pending",
            params={"playlist_id": str(playlist_id), "force": False},
        )
        pending_range_job = Job(
            id=uuid.uuid4(),
            type="playlist.backfill_events_range",
            status="pending",
            params={"playlist_id": str(playlist_id), "force": False, "range_start": "2026-01-01", "range_end": "2026-02-01"},
        )
        running_job = Job(
            id=uuid.uuid4(),
            type="video.extract_events",
            status="running",
            params={"video_id": str(uuid.uuid4()), "force": True},
        )
        run = EventRegimeRun(
            id=uuid.uuid4(),
            playlist_id=playlist_id,
            status="running",
            analysis_clock="day",
            embedding_model="m",
            embedding_dim=3,
        )
        state = EventRegimeState(playlist_id=playlist_id, analysis_dirty=False)
        session = Mock()
        session.execute.side_effect = [_ScalarResult([pending_job, pending_range_job, running_job]), _ScalarResult([run])]
        session.get.return_value = state

        result = event_analysis.cancel_playlist_event_pipeline_jobs(session, playlist_id, reason="test")

        self.assertEqual(result["jobs"], 3)
        self.assertEqual(result["canceled"], 2)
        self.assertEqual(result["cancel_requested"], 1)
        self.assertEqual(result["regime_runs_canceled"], 1)
        self.assertEqual(pending_job.status, "canceled")
        self.assertIsNotNone(pending_job.finished_at)
        self.assertEqual(pending_range_job.status, "canceled")
        self.assertIsNotNone(pending_range_job.finished_at)
        self.assertEqual(running_job.status, "running")
        self.assertIsNotNone(running_job.cancel_requested_at)
        self.assertEqual(run.status, "canceled")
        self.assertIsNotNone(run.finished_at)
        self.assertTrue(state.analysis_dirty)
        session.flush.assert_called_once()

    def test_build_event_regime_snapshot_cancel_keeps_run_canceled_and_dirty(self) -> None:
        playlist_id = uuid.uuid4()
        run_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        run = EventRegimeRun(
            id=run_id,
            playlist_id=playlist_id,
            status="pending",
            analysis_clock="day",
            embedding_model="m",
            embedding_dim=3,
        )
        state = EventRegimeState(playlist_id=playlist_id, analysis_dirty=False)
        job = Job(
            id=uuid.uuid4(),
            type="playlist.build_event_regime_snapshot",
            status="running",
            params={"playlist_id": str(playlist_id), "regime_run_id": str(run_id)},
            cancel_requested_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        )
        session = Mock()

        def _get(model, key):
            if model is Playlist:
                return playlist
            if model is EventRegimeState:
                return state
            if model is EventRegimeRun:
                return run
            return None

        session.get.side_effect = _get

        with self.assertRaises(JobCancelRequested):
            event_analysis.build_event_regime_snapshot(session, playlist_id=playlist_id, job=job)

        self.assertEqual(run.status, "canceled")
        self.assertIsNotNone(run.finished_at)
        self.assertTrue(state.analysis_dirty)

    def test_request_playlist_event_backfill_force_false_keeps_active_jobs(self) -> None:
        playlist_id = uuid.uuid4()
        job_id = uuid.uuid4()
        job = Job(id=job_id, type="playlist.backfill_events", status="pending", params={"playlist_id": str(playlist_id)})
        session = Mock()
        session.get.return_value = job

        with patch("raelyn.services.event_analysis.cancel_playlist_event_pipeline_jobs") as cancel_jobs:
            with patch("raelyn.services.event_analysis.enqueue_job", return_value=job_id) as enqueue:
                result = event_analysis.request_playlist_event_backfill(session, playlist_id=playlist_id, force=False, priority=1)

        self.assertIs(result, job)
        cancel_jobs.assert_not_called()
        enqueue.assert_called_once_with(
            session,
            type_="playlist.backfill_events",
            params={"playlist_id": str(playlist_id), "force": False},
            priority=1,
        )

    def test_request_playlist_event_backfill_force_true_cancels_then_enqueues_force_job(self) -> None:
        playlist_id = uuid.uuid4()
        job_id = uuid.uuid4()
        job = Job(id=job_id, type="playlist.backfill_events", status="pending", params={"playlist_id": str(playlist_id), "force": True})
        session = Mock()
        session.get.return_value = job
        order: list[str] = []

        def _cancel(*args, **kwargs):
            order.append("cancel")
            return {"jobs": 1}

        def _enqueue(*args, **kwargs):
            order.append("enqueue")
            return job_id

        with patch("raelyn.services.event_analysis.cancel_playlist_event_pipeline_jobs", side_effect=_cancel) as cancel_jobs:
            with patch("raelyn.services.event_analysis.enqueue_job", side_effect=_enqueue) as enqueue:
                result = event_analysis.request_playlist_event_backfill(session, playlist_id=playlist_id, force=True, priority=1)

        self.assertIs(result, job)
        self.assertEqual(order, ["cancel", "enqueue"])
        cancel_jobs.assert_called_once_with(session, playlist_id, reason="playlist_event_force_extract")
        enqueue.assert_called_once_with(
            session,
            type_="playlist.backfill_events",
            params={"playlist_id": str(playlist_id), "force": True},
            priority=1,
        )

    def test_backfill_playlist_events_enqueues_monthly_range_jobs(self) -> None:
        playlist_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        job = Job(
            id=uuid.uuid4(),
            type="playlist.backfill_events",
            status="running",
            params={"playlist_id": str(playlist_id), "force": True},
            priority=3,
        )
        session = Mock()
        session.get.return_value = playlist

        with patch(
            "raelyn.services.event_analysis._playlist_timeline_bounds",
            return_value=(datetime(2026, 1, 15, tzinfo=timezone.utc), datetime(2026, 3, 2, tzinfo=timezone.utc)),
        ):
            with patch("raelyn.services.event_analysis.enqueue_job", return_value=uuid.uuid4()) as enqueue:
                result = event_analysis.backfill_playlist_events(session, playlist_id=playlist_id, force=True, job=job)

        self.assertEqual(result["mode"], "monthly_ranges")
        self.assertEqual(result["range_jobs"], 3)
        self.assertEqual(enqueue.call_count, 3)
        self.assertEqual([call.kwargs["params"]["range_start"] for call in enqueue.call_args_list], ["2026-01-01", "2026-02-01", "2026-03-01"])
        self.assertEqual([call.kwargs["params"]["range_end"] for call in enqueue.call_args_list], ["2026-02-01", "2026-03-01", "2026-04-01"])
        for call in enqueue.call_args_list:
            self.assertEqual(call.kwargs["type_"], "playlist.backfill_events_range")
            self.assertEqual(call.kwargs["priority"], 3)
            self.assertEqual(call.kwargs["parent_job_id"], str(job.id))

    def test_backfill_playlist_events_range_prioritizes_video_extract_over_range_job(self) -> None:
        playlist_id = uuid.uuid4()
        video_id = uuid.uuid4()
        media_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        video = Video(
            id=video_id,
            provider="youtube",
            provider_video_id="v1",
            media_id=media_id,
            url="https://example.test/watch?v=v1",
        )
        transcript_asset = Asset(
            id=uuid.uuid4(),
            video_id=video_id,
            type="transcript",
            format="txt",
            source="asr",
            variant="plain",
            s3_bucket="b",
            s3_key="k",
        )
        job = Job(
            id=uuid.uuid4(),
            type="playlist.backfill_events_range",
            status="running",
            params={
                "playlist_id": str(playlist_id),
                "force": True,
                "range_start": "2026-01-01",
                "range_end": "2026-02-01",
            },
            priority=7,
        )
        session = Mock()
        session.execute.return_value = _ScalarResult([video_id])

        def _get(model, key):
            if model is Playlist:
                return playlist
            if model is Video:
                return video
            return None

        session.get.side_effect = _get

        with patch("raelyn.services.event_analysis.set_job_progress"):
            with patch("raelyn.services.event_analysis.pick_transcript_asset", return_value=transcript_asset):
                with patch("raelyn.services.event_analysis.read_text_asset", return_value=("x" * 4000, {})):
                    with patch("raelyn.services.event_analysis.enqueue_job", return_value=uuid.uuid4()) as enqueue:
                        result = event_analysis.backfill_playlist_events_range(
                            session,
                            playlist_id=playlist_id,
                            range_start=date(2026, 1, 1),
                            range_end=date(2026, 2, 1),
                            force=True,
                            job=job,
                        )

        self.assertEqual(result["enqueued"], 1)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["type_"], "video.extract_events")
        self.assertEqual(enqueue.call_args.kwargs["priority"], 8)
        self.assertEqual(enqueue.call_args.kwargs["parent_job_id"], str(job.id))


if __name__ == "__main__":
    unittest.main()
