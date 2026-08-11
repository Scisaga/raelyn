from __future__ import annotations

from contextlib import nullcontext
import json
import sys
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.enqueue import _normalize_dedupe_key_and_params
from raelyn.models import (
    Asset,
    EventMapCanonical,
    EventMapCanonicalMember,
    EventMapSnapshot,
    EventMapState,
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


class EventAnalysisTests(unittest.TestCase):
    def test_event_models_have_expected_constraints(self) -> None:
        self.assertIn("market_event_source_event_ux", {c.name for c in MarketEvent.__table__.constraints})
        self.assertIn("market_event_evidence_ux", {c.name for c in MarketEventEvidence.__table__.constraints})
        self.assertIn("market_event_entity_ux", {c.name for c in MarketEventEntity.__table__.constraints})
        self.assertIn("market_event_embedding_ux", {c.name for c in MarketEventEmbedding.__table__.constraints})
        self.assertIn(
            "event_map_canonical_point_index_ux",
            {c.name for c in EventMapCanonical.__table__.constraints},
        )
        self.assertIn("video_event_extraction_run_ux", {c.name for c in VideoEventExtractionRun.__table__.constraints})

        for model in [MarketEvent, MarketEventEvidence, MarketEventEntity, MarketEventRelation, MarketEventEmbedding]:
            self.assertIn("event_id" if model is not MarketEvent else "source_video_id", model.__table__.columns)
        self.assertIn("current_snapshot_id", EventMapState.__table__.columns)
        self.assertIn("dirty_generation", EventMapState.__table__.columns)
        self.assertIn("canonical_count", EventMapSnapshot.__table__.columns)
        self.assertIn("peak_rss_bytes", EventMapSnapshot.__table__.columns)
        self.assertIn("record_revision_id", EventMapCanonicalMember.__table__.columns)
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
            {"temperature": 0, "num_ctx": 8192, "num_predict": 4000},
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

    def test_parse_event_batch_response_preserves_valid_rows_when_expected_video_is_missing(self) -> None:
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
        with self.assertRaises(event_analysis._EventExtractionResponseError) as raised:
            event_analysis.parse_event_extraction_batch_response(
                raw,
                expected_video_ids=["v1", "v2"],
            )

        self.assertEqual(len(raised.exception.parsed_by_video["v1"]), 1)
        self.assertEqual(raised.exception.parsed_by_video["v2"], [])
        self.assertEqual(raised.exception.affected_video_ids, ("v2",))
        self.assertTrue(any("missing video_id v2" in message for message in raised.exception.warnings))

    def test_parse_event_batch_response_accepts_explicit_empty_events(self) -> None:
        events_by_video, warnings = event_analysis.parse_event_extraction_batch_response(
            json.dumps({"videos": [{"video_id": "v1", "events": []}]}),
            expected_video_ids=["v1"],
        )

        self.assertEqual(events_by_video, {"v1": []})
        self.assertEqual(warnings, [])

    def test_event_extraction_repairs_malformed_json_with_one_follow_up_call(self) -> None:
        malformed = '{"videos":[{"video_id":"v1" "events":[]}]}'
        repaired = '{"videos":[{"video_id":"v1","events":[]}]}'
        repair_usage = {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "call_count": 1,
        }
        session = Mock()

        with patch(
            "raelyn.services.event_analysis._generate_event_extraction_llm",
            return_value={"text": repaired, "usage": repair_usage},
        ) as generate:
            rows, warnings, usage = (
                event_analysis._parse_event_extraction_batch_response_with_json_repair(
                    session,
                    response_text=malformed,
                    expected_video_ids=["v1"],
                )
            )

        self.assertEqual(rows, {"v1": []})
        self.assertIn("repaired by follow-up LLM call", warnings[0])
        self.assertEqual(usage, repair_usage)
        generate.assert_called_once()
        repair_prompt = generate.call_args.kwargs["prompt"]
        self.assertIn("只修复 JSON 语法", repair_prompt)
        self.assertIn(malformed, repair_prompt)
        self.assertIn("Expecting ',' delimiter", repair_prompt)

    def test_event_extraction_does_not_repair_truncated_json(self) -> None:
        malformed = '{"videos":[{"video_id":"v1","events":['

        with self.assertRaises(event_analysis._EventExtractionOutputTruncatedError) as raised:
            event_analysis._parse_event_extraction_batch_response_with_json_repair(
                object(),  # type: ignore[arg-type] - 截断分支不访问 session。
                response_text=malformed,
                expected_video_ids=["v1"],
                response_usage={"output_tokens": 4000, "call_count": 1},
                response_meta={"done_reason": "length"},
            )

        self.assertIn("output truncated", str(raised.exception))
        self.assertIn("num_predict=4000", str(raised.exception))
        self.assertEqual(raised.exception.affected_video_ids, ("v1",))

    def test_event_extraction_does_not_repair_valid_json(self) -> None:
        response = '{"videos":[{"video_id":"v1","events":[]}]}'
        session = Mock()

        with patch("raelyn.services.event_analysis._generate_event_extraction_llm") as generate:
            rows, warnings, usage = (
                event_analysis._parse_event_extraction_batch_response_with_json_repair(
                    session,
                    response_text=response,
                    expected_video_ids=["v1"],
                )
            )

        self.assertEqual(rows, {"v1": []})
        self.assertEqual(warnings, [])
        self.assertIsNone(usage)
        generate.assert_not_called()

    def test_event_extraction_reports_failed_json_repair_usage(self) -> None:
        malformed = '{"videos":[{"video_id":"v1" "events":[]}]}'
        repair_usage = {
            "input_tokens": 80,
            "output_tokens": 20,
            "total_tokens": 100,
            "call_count": 1,
        }
        session = Mock()

        with patch(
            "raelyn.services.event_analysis._generate_event_extraction_llm",
            return_value={"text": malformed, "usage": repair_usage},
        ):
            with self.assertRaises(event_analysis._EventExtractionResponseError) as raised:
                event_analysis._parse_event_extraction_batch_response_with_json_repair(
                    session,
                    response_text=malformed,
                    expected_video_ids=["v1"],
                )

        self.assertIn("JSON repair failed", str(raised.exception))
        self.assertEqual(raised.exception.usage, repair_usage)
        self.assertEqual(raised.exception.affected_video_ids, ("v1",))

    def test_parse_event_response_rejects_invalid_envelopes(self) -> None:
        for raw in ("not-json", "[]", "{}"):
            with self.subTest(raw=raw):
                with self.assertRaises(event_analysis._EventExtractionResponseError):
                    event_analysis.parse_event_extraction_response(raw)

        with self.assertRaises(event_analysis._EventExtractionResponseError) as raised:
            event_analysis.parse_event_extraction_batch_response(
                json.dumps({"events": []}),
                expected_video_ids=["v1"],
            )
        self.assertIn("missing videos[]", str(raised.exception))

        with self.assertRaises(event_analysis._EventExtractionResponseError) as raised:
            event_analysis.parse_event_extraction_batch_response(
                json.dumps({"videos": [{"video_id": "v1"}]}),
                expected_video_ids=["v1"],
            )
        self.assertIn("missing events[]", str(raised.exception))

    def test_parse_event_relation_keeps_propositions_and_validates_explicit_endpoint_keys(self) -> None:
        raw = json.dumps(
            {
                "videos": [
                    {
                        "video_id": "v1",
                        "events": [
                            {
                                "title": "美联储维持高利率",
                                "summary": "融资成本继续上升。",
                                "entities": [{"type": "institution", "name": "Federal Reserve"}],
                                "macro_variables": [{"name": "融资成本"}],
                                "cause_effect_chain": [
                                    {
                                        "cause": "通胀持续高于目标迫使美联储维持高利率",
                                        "effect": "美国企业融资成本继续上升",
                                        "source_entity_key": "federal_reserve",
                                        "target_entity_key": "融资成本",
                                    },
                                    {
                                        "cause": "同一命题仍须保留",
                                        "effect": "端点不能按近似名称解析",
                                        "source_entity_key": "Federal Reserve",
                                        "target_entity_key": "不存在的对象",
                                    },
                                ],
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )

        events_by_video, warnings = event_analysis.parse_event_extraction_batch_response(
            raw,
            expected_video_ids=["v1"],
        )

        relations = events_by_video["v1"][0]["cause_effect_chain"]
        self.assertEqual(relations[0]["cause"], "通胀持续高于目标迫使美联储维持高利率")
        self.assertEqual(relations[0]["effect"], "美国企业融资成本继续上升")
        self.assertEqual(relations[0]["source_entity_key"], "federal_reserve")
        self.assertFalse(any("cause_effect_chain[0]" in message for message in warnings))
        self.assertTrue(any("cause_effect_chain[1]" in message and "source_entity_key" in message for message in warnings))
        self.assertTrue(any("cause_effect_chain[1]" in message and "target_entity_key" in message for message in warnings))

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
        self.assertIn("source_entity_key", prompt)
        self.assertIn("target_entity_key", prompt)
        self.assertIn("cause / effect 必须保留为可独立阅读的自然语言命题", prompt)
        self.assertIn("禁止拿 cause / effect 文本猜端点", prompt)
        self.assertIn("source_entity_key", event_analysis.COMPACT_EVENT_EXTRACTION_PROMPT)
        self.assertIn("source_entity_key / target_entity_key 是独立端点", event_analysis.COMPACT_EVENT_EXTRACTION_PROMPT)
        self.assertEqual(
            event_analysis.EVENT_EXTRACTION_PROMPT_BASE_VERSION,
            "llm_event_v4_explicit_relation_endpoints",
        )

    def test_insert_event_relations_only_resolves_explicit_exact_endpoint_keys(self) -> None:
        event = MarketEvent(
            id=uuid.uuid4(),
            source_video_id=uuid.uuid4(),
            source_hash="source",
            event_key="event",
            event_type="monetary_policy",
        )
        video = SimpleNamespace(id=event.source_video_id)
        raw = {
            "confidence": 0.95,
            "entities": [{"type": "institution", "name": "Federal Reserve", "role": "actor"}],
            "macro_variables": [{"name": "融资成本", "role": "affected"}],
            "cause_effect_chain": [
                {
                    "cause": "通胀持续高于目标迫使美联储维持高利率",
                    "effect": "美国企业融资成本继续上升",
                    "source_entity_key": "federal_reserve",
                    "target_entity_key": "融资成本",
                    "relation_type": "affects",
                    "confidence": 0.9,
                },
                {
                    "cause": "Federal Reserve",
                    "effect": "融资成本",
                    "relation_type": "affects",
                    "confidence": 0.8,
                },
                {
                    "cause": "端点键不是显示名称",
                    "effect": "大小写不同也不能隐式归一化",
                    "source_entity_key": "Federal Reserve",
                    "target_entity_key": "融资成本 ",
                    "relation_type": "affects",
                    "confidence": 0.7,
                },
            ],
        }
        session = Mock()

        def _flush(*_args, **_kwargs):
            for call in session.add.call_args_list:
                item = call.args[0]
                if isinstance(item, MarketEventEntity) and item.id is None:
                    item.id = uuid.uuid4()

        session.flush.side_effect = _flush

        event_analysis._insert_event_children(
            session,
            event=event,
            raw=raw,
            video=video,
            transcript_asset_id=uuid.uuid4(),
        )

        relations = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], MarketEventRelation)
        ]
        self.assertEqual(len(relations), 3)
        self.assertIsNotNone(relations[0].source_entity_id)
        self.assertIsNotNone(relations[0].target_entity_id)
        self.assertEqual(relations[0].raw_payload["cause"], "通胀持续高于目标迫使美联储维持高利率")
        self.assertEqual(relations[0].raw_payload["effect"], "美国企业融资成本继续上升")
        self.assertIsNone(relations[1].source_entity_id)
        self.assertIsNone(relations[1].target_entity_id)
        self.assertIsNone(relations[2].source_entity_id)
        self.assertIsNone(relations[2].target_entity_id)

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

    def test_extract_video_events_commits_embedding_and_dirty_outbox_atomically(self) -> None:
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
            worker_id="analysis-worker",
            execution_token=uuid.uuid4(),
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
                                    with patch("raelyn.services.event_analysis.set_job_progress", return_value=True):
                                        with patch("raelyn.services.event_analysis.schedule_playlist_event_map_dirty") as mark_dirty:
                                            with patch(
                                                "raelyn.services.event_analysis.enqueue_event_embedding_jobs",
                                                side_effect=_enqueue_embeddings,
                                            ) as enqueue_embeddings:
                                                with patch(
                                                    "raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video",
                                                    side_effect=_schedule_dirty,
                                                ) as schedule_dirty:
                                                    with patch(
                                                        "raelyn.services.event_analysis._video_ids_with_event_map_inputs",
                                                        return_value={video_id},
                                                    ):
                                                        result = event_analysis.extract_video_events(session, video_id=video_id, job=job)

        self.assertEqual(order[-3:], ["enqueue_embeddings", "schedule_dirty", "commit"])
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
        job = Job(
            id=uuid.uuid4(),
            type="video.extract_events",
            status="running",
            params={"video_id": str(video_id), "force": True},
            priority=4,
            worker_id="analysis-worker",
            execution_token=uuid.uuid4(),
        )
        session = Mock()
        session.begin_nested.return_value = nullcontext()
        session.execute.side_effect = [_ScalarResult([]), _ScalarOneOrNone(None)]

        def _get(model, key):
            if model is Video:
                return video
            if model is Media:
                return media
            return None

        session.get.side_effect = _get
        response = json.dumps({"videos": [{"video_id": "v1", "events": []}]})
        spec = event_analysis.EventExtractionSpec(model="m", prompt_version="p", prompt_text="prompt", chunk_max_chars=12000)

        with patch("raelyn.services.event_analysis.llm_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.pick_transcript_asset", return_value=transcript_asset):
                with patch("raelyn.services.event_analysis.read_text_asset", return_value=("transcript", {})):
                    with patch("raelyn.services.event_analysis.event_extraction_spec", return_value=spec):
                        with patch("raelyn.services.event_analysis._event_source_hash", return_value="hash"):
                            with patch("raelyn.services.event_analysis.resolve_video_timeline", return_value=SimpleNamespace(content_published_at=video.published_at)):
                                with patch("raelyn.services.event_analysis.llm_generate", return_value={"text": response, "usage": {}}):
                                    with patch("raelyn.services.event_analysis.set_job_progress", return_value=True):
                                        with patch("raelyn.services.event_analysis.enqueue_event_embedding_jobs", return_value=0):
                                            with patch(
                                                "raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video",
                                                return_value=1,
                                            ) as schedule_dirty:
                                                with patch(
                                                    "raelyn.services.event_analysis._video_ids_with_event_map_inputs",
                                                    return_value={video_id},
                                                ):
                                                    result = event_analysis.extract_video_events(session, video_id=video_id, force=True, job=job)

        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["dirty_jobs_enqueued"], 1)
        schedule_dirty.assert_called_once()

    def test_extract_video_events_structure_error_persists_failed_run_without_deleting_events(self) -> None:
        video_id = uuid.uuid4()
        transcript_asset_id = uuid.uuid4()
        spec = event_analysis.EventExtractionSpec(
            model="m",
            prompt_version="p",
            prompt_text="prompt",
            chunk_max_chars=12000,
        )
        prepared = event_analysis._PreparedEventVideo(
            alias="v1",
            video=event_analysis._EventExtractionVideoSnapshot(
                id=video_id,
                media_id=None,
                title="Title",
                description=None,
                published_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
                created_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
            ),
            media=None,
            transcript_asset=event_analysis._EventExtractionTranscriptAssetSnapshot(
                id=transcript_asset_id,
                s3_bucket="b",
                s3_key="k",
            ),
            transcript_text="transcript",
            content_time=datetime(2026, 6, 4, tzinfo=timezone.utc),
            source_hash="hash",
            source_chars=10,
        )
        existing_run = VideoEventExtractionRun(
            id=uuid.uuid4(),
            video_id=video_id,
            transcript_asset_id=transcript_asset_id,
            source_hash="hash",
            prompt_version="p",
            extraction_model="m",
            status="succeeded",
            event_count=2,
        )
        session = Mock()
        session.execute.return_value = _ScalarOneOrNone(existing_run)

        with patch("raelyn.services.event_analysis.llm_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.event_extraction_spec", return_value=spec):
                with patch("raelyn.services.event_analysis._prepare_event_videos", return_value=([prepared], [])):
                    with patch("raelyn.services.event_analysis._render_event_batch_prompt", return_value="prompt"):
                        with patch(
                            "raelyn.services.event_analysis._generate_event_extraction_llm",
                            return_value={
                                "text": "not-json",
                                "usage": {"input_tokens": 11, "output_tokens": 2, "total_tokens": 13, "call_count": 1},
                            },
                        ):
                            with patch("raelyn.services.event_analysis._delete_video_events") as delete_events:
                                with self.assertRaises(event_analysis._EventExtractionResponseError):
                                    event_analysis.extract_video_events(
                                        session,
                                        video_id=video_id,
                                        force=True,
                                    )

        self.assertEqual(existing_run.status, "failed")
        self.assertEqual(existing_run.event_count, 0)
        self.assertEqual(existing_run.usage_json["total_tokens"], 26)
        self.assertEqual(existing_run.usage_json["call_count"], 2)
        self.assertIn("JSON repair failed", existing_run.error_message or "")
        delete_events.assert_not_called()
        self.assertGreaterEqual(session.commit.call_count, 2)

    def test_successful_retry_updates_existing_failed_extraction_run(self) -> None:
        video_id = uuid.uuid4()
        existing_run = VideoEventExtractionRun(
            id=uuid.uuid4(),
            video_id=video_id,
            source_hash="hash",
            prompt_version="p",
            extraction_model="m",
            status="failed",
            event_count=0,
            warning_count=1,
            error_message="bad envelope",
        )
        session = Mock()
        session.execute.return_value = _ScalarOneOrNone(existing_run)
        spec = event_analysis.EventExtractionSpec(
            model="m",
            prompt_version="p",
            prompt_text="prompt",
            chunk_max_chars=12000,
        )

        run = event_analysis._write_event_extraction_run(
            session,
            video_id=video_id,
            transcript_asset_id=None,
            source_hash="hash",
            spec=spec,
            status="succeeded",
            event_count=3,
            usage={"total_tokens": 9},
        )

        self.assertIs(run, existing_run)
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.event_count, 3)
        self.assertEqual(run.warning_count, 0)
        self.assertIsNone(run.error_message)
        self.assertEqual(run.usage_json, {"total_tokens": 9})

    def test_multi_video_structure_error_falls_back_only_affected_video(self) -> None:
        first_video_id = uuid.uuid4()
        second_video_id = uuid.uuid4()
        first = event_analysis._PreparedEventVideo(
            alias="v1",
            video=event_analysis._EventExtractionVideoSnapshot(
                id=first_video_id,
                media_id=None,
                title="First",
                description=None,
                published_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
                created_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
            ),
            media=None,
            transcript_asset=event_analysis._EventExtractionTranscriptAssetSnapshot(
                id=uuid.uuid4(),
                s3_bucket="b",
                s3_key="first",
            ),
            transcript_text="first transcript",
            content_time=datetime(2026, 6, 4, tzinfo=timezone.utc),
            source_hash="first-hash",
            source_chars=16,
        )
        second = event_analysis._PreparedEventVideo(
            alias="v2",
            video=event_analysis._EventExtractionVideoSnapshot(
                id=second_video_id,
                media_id=None,
                title="Second",
                description=None,
                published_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
                created_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
            ),
            media=None,
            transcript_asset=event_analysis._EventExtractionTranscriptAssetSnapshot(
                id=uuid.uuid4(),
                s3_bucket="b",
                s3_key="second",
            ),
            transcript_text="second transcript",
            content_time=datetime(2026, 6, 4, tzinfo=timezone.utc),
            source_hash="second-hash",
            source_chars=17,
        )
        spec = event_analysis.EventExtractionSpec(
            model="m",
            prompt_version="p",
            prompt_text="prompt",
            chunk_max_chars=12000,
        )
        session = Mock()
        response = json.dumps({"videos": [{"video_id": "v1", "events": []}]})

        with patch("raelyn.services.event_analysis.llm_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.event_extraction_spec", return_value=spec):
                with patch("raelyn.services.event_analysis._prepare_event_videos", return_value=([first, second], [])):
                    with patch("raelyn.services.event_analysis.EVENT_BATCH_MAX_VIDEOS", 2):
                        with patch("raelyn.services.event_analysis._render_event_batch_prompt", return_value="prompt"):
                            with patch(
                                "raelyn.services.event_analysis._generate_event_extraction_llm",
                                return_value={"text": response, "usage": {}},
                            ):
                                with patch("raelyn.services.event_analysis.enqueue_job", return_value=uuid.uuid4()) as enqueue:
                                    with patch("raelyn.services.event_analysis._delete_video_events") as delete_events:
                                        with patch("raelyn.services.event_analysis._write_event_extraction_run") as write_run:
                                            with patch(
                                                "raelyn.services.event_analysis.enqueue_event_embedding_jobs",
                                                return_value=0,
                                            ):
                                                with patch(
                                                    "raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video",
                                                    return_value=0,
                                                ) as schedule_dirty:
                                                    with patch(
                                                        "raelyn.services.event_analysis._video_ids_with_event_map_inputs",
                                                        return_value=set(),
                                                    ):
                                                        result = event_analysis.extract_video_events_batch(
                                                            session,
                                                            video_ids=[first_video_id, second_video_id],
                                                            force=True,
                                                        )

        self.assertEqual(result["videos"], 1)
        self.assertEqual(result["fallback_jobs_enqueued"], 1)
        enqueue.assert_called_once_with(
            session,
            type_="video.extract_events",
            params={"video_id": str(second_video_id), "force": True},
            priority=0,
            parent_job_id=None,
        )
        delete_events.assert_called_once_with(session, video_id=first_video_id)
        self.assertEqual(write_run.call_count, 1)
        self.assertEqual(write_run.call_args.kwargs["video_id"], first_video_id)
        self.assertEqual(write_run.call_args.kwargs["status"], "succeeded")
        schedule_dirty.assert_not_called()

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
        session.execute.return_value = _ScalarOneOrNone(uuid.uuid4())

        with patch("raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video") as schedule_dirty:
            with patch("raelyn.services.event_analysis.enqueue_job") as enqueue_job:
                updated = event_analysis.update_event_status(session, event_id=event_id, status="accepted")

        self.assertIs(updated, event)
        self.assertEqual(event.status, "accepted")
        schedule_dirty.assert_called_once_with(session, video_id=video_id, reason="event_status_changed")
        enqueue_job.assert_called_once_with(session, type_="event.embed", params={"event_id": str(event_id)}, priority=0)

    def test_update_event_status_without_ready_embedding_does_not_dirty_map(self) -> None:
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
        session.execute.return_value = _ScalarOneOrNone(None)

        with patch("raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video") as schedule_dirty:
            with patch("raelyn.services.event_analysis.enqueue_job") as enqueue_job:
                event_analysis.update_event_status(session, event_id=event_id, status="accepted")

        schedule_dirty.assert_not_called()
        enqueue_job.assert_called_once_with(
            session,
            type_="event.embed",
            params={"event_id": str(event_id)},
            priority=0,
        )

    def test_embed_event_new_failure_does_not_dirty_map(self) -> None:
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
                        with patch("raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video") as schedule_dirty:
                            result = event_analysis.embed_event(session, event_id=event_id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["embedding_id"], str(embedding_id))
        created_embedding = session.add.call_args.args[0]
        self.assertEqual(created_embedding.embedding_model, "m")
        self.assertEqual(created_embedding.embedding_dim, 3)
        schedule_dirty.assert_not_called()

    def test_embed_event_failure_removes_visible_input_and_dirties_map(self) -> None:
        event_id = uuid.uuid4()
        video_id = uuid.uuid4()
        event = MarketEvent(
            id=event_id,
            source_video_id=video_id,
            source_hash="h",
            event_key="k",
            status="accepted",
            event_type="macro",
            title="Event",
            event_time_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        embedding = MarketEventEmbedding(
            id=uuid.uuid4(),
            event_id=event_id,
            embedding_model="m",
            embedding_dim=3,
            status="ready",
            text_checksum="old-checksum",
            vector=[1.0, 0.0, 0.0],
        )
        session = Mock()
        session.get.return_value = event
        session.execute.return_value = _ScalarOneOrNone(embedding)

        with patch("raelyn.services.event_analysis.embedding_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.embedding_spec", return_value=SimpleNamespace(model="m", dim=3)):
                with patch("raelyn.services.event_analysis._event_embedding_text", return_value="text"):
                    with patch("raelyn.services.event_analysis.embed_text", side_effect=event_analysis.EmbeddingError("bad response")):
                        with patch("raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video") as schedule_dirty:
                            result = event_analysis.embed_event(session, event_id=event_id)

        self.assertEqual(result["status"], "failed")
        schedule_dirty.assert_called_once_with(
            session,
            video_id=video_id,
            reason="event_embedding_changed",
        )

    def test_embed_event_replaces_invalid_cached_vector_and_marks_map_dirty(self) -> None:
        event_id = uuid.uuid4()
        video_id = uuid.uuid4()
        event = MarketEvent(
            id=event_id,
            source_video_id=video_id,
            source_hash="h",
            event_key="k",
            status="accepted",
            event_type="macro",
            title="Event",
            event_time_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        embedding = MarketEventEmbedding(
            id=uuid.uuid4(),
            event_id=event_id,
            embedding_model="m",
            embedding_dim=3,
            status="ready",
            text_checksum=event_analysis._sha256_text("text"),
            vector=[1.0, 0.0],
        )
        session = Mock()
        session.get.return_value = event
        session.execute.return_value = _ScalarOneOrNone(embedding)

        with patch("raelyn.services.event_analysis.embedding_enabled", return_value=True):
            with patch("raelyn.services.event_analysis.embedding_spec", return_value=SimpleNamespace(model="m", dim=3)):
                with patch("raelyn.services.event_analysis._event_embedding_text", return_value="text"):
                    with patch("raelyn.services.event_analysis.embed_text", return_value=[0.0, 1.0, 0.0]):
                        with patch("raelyn.services.event_analysis.schedule_playlists_event_map_dirty_for_video") as schedule_dirty:
                            result = event_analysis.embed_event(session, event_id=event_id)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(embedding.vector, [0.0, 1.0, 0.0])
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
            "playlist.mark_event_map_dirty",
            {"playlist_id": str(playlist_id), "reason": "video_events_extracted", "source_video_id": str(video_id)},
        )
        self.assertEqual(key, f"playlist_event_map_dirty:{playlist_id}")
        self.assertEqual(params["playlist_id"], str(playlist_id))
        self.assertEqual(params["source_video_id"], str(video_id))

    def test_schedule_playlists_event_map_dirty_queries_playlists_in_stable_order(self) -> None:
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

        with patch("raelyn.services.event_analysis.schedule_playlist_event_map_dirty", return_value=uuid.uuid4()):
            count = event_analysis.schedule_playlists_event_map_dirty_for_video(
                session,
                video_id=video_id,
                reason="video_events_extracted",
            )

        self.assertEqual(count, 1)
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("order by playlist_media.playlist_id", compiled)

    def test_schedule_playlists_event_map_dirty_skips_video_without_map_input(self) -> None:
        video_id = uuid.uuid4()
        video = Video(
            id=video_id,
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://example.test/watch?v=abc123",
        )
        session = Mock()
        session.get.return_value = video

        with patch(
            "raelyn.services.event_analysis.video_has_event_map_input",
            return_value=False,
        ):
            with patch(
                "raelyn.services.event_analysis.schedule_playlist_event_map_dirty",
            ) as schedule_dirty:
                count = event_analysis.schedule_playlists_event_map_dirty_for_video(
                    session,
                    video_id=video_id,
                    reason="video_published_at_changed",
                    require_event_map_input=True,
                )

        self.assertEqual(count, 0)
        schedule_dirty.assert_not_called()
        session.execute.assert_not_called()

    def test_manual_event_map_rebuild_promotes_a_deduped_pending_build(self) -> None:
        playlist_id = uuid.uuid4()
        job_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        state = EventMapState(playlist_id=playlist_id, dirty_generation=4, built_generation=4)
        job = Job(
            id=job_id,
            type="playlist.build_event_map_snapshot",
            status="pending",
            params={"playlist_id": str(playlist_id), "trigger": "dirty", "requested_generation": 4},
        )
        session = Mock()

        def _get(model, key):
            if model is Playlist:
                return playlist
            if model is EventMapState:
                return state
            if model is Job:
                return job
            return None

        session.get.side_effect = _get
        with patch("raelyn.services.event_analysis.enqueue_job", return_value=job_id):
            result = event_analysis.request_event_map_rebuild(session, playlist_id, priority=3)

        self.assertIs(result, job)
        self.assertEqual(state.dirty_generation, 5)
        self.assertEqual(job.params["trigger"], "manual")
        self.assertEqual(job.params["requested_generation"], 5)
        self.assertEqual(job.scheduled_for, state.last_requested_at)
        self.assertEqual(state.active_job_id, job.id)

    def test_manual_event_map_rebuild_does_not_replace_the_running_owner_with_a_pending_job(self) -> None:
        playlist_id = uuid.uuid4()
        running = Job(
            id=uuid.uuid4(),
            type="playlist.build_event_map_snapshot",
            status="running",
            params={"playlist_id": str(playlist_id)},
        )
        pending = Job(
            id=uuid.uuid4(),
            type="playlist.build_event_map_snapshot",
            status="pending",
            params={"playlist_id": str(playlist_id)},
        )
        playlist = Playlist(id=playlist_id, name="p")
        state = EventMapState(
            playlist_id=playlist_id,
            active_job_id=running.id,
            dirty_generation=4,
            built_generation=3,
        )
        session = Mock()

        def _get(model, key):
            if model is Playlist:
                return playlist
            if model is EventMapState:
                return state
            if model is Job and key == running.id:
                return running
            if model is Job and key == pending.id:
                return pending
            return None

        session.get.side_effect = _get
        with patch("raelyn.services.event_analysis.enqueue_job", return_value=pending.id):
            event_analysis.request_event_map_rebuild(session, playlist_id)

        self.assertEqual(state.active_job_id, running.id)

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
        self.assertIn("playlist.mark_event_map_dirty", compiled)
        self.assertIn("playlist.build_event_map_snapshot", compiled)
        self.assertIn("playlist.prune_event_map_snapshots", compiled)
        self.assertIn(str(playlist_id), compiled)
        self.assertIn("playlist_media.playlist_id", compiled)

    def test_backfill_playlist_events_enqueues_monthly_range_jobs(self) -> None:
        playlist_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        job = Job(
            id=uuid.uuid4(),
            type="playlist.backfill_events",
            status="running",
            params={"playlist_id": str(playlist_id), "force": True},
            priority=3,
            worker_id="analysis-worker",
            execution_token=uuid.uuid4(),
        )
        session = Mock()
        session.get.return_value = playlist

        with patch(
            "raelyn.services.event_analysis._playlist_timeline_bounds",
            return_value=(datetime(2026, 1, 15, tzinfo=timezone.utc), datetime(2026, 3, 2, tzinfo=timezone.utc)),
        ):
            with patch("raelyn.services.event_analysis.set_job_progress", return_value=True):
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
            worker_id="analysis-worker",
            execution_token=uuid.uuid4(),
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

        with patch("raelyn.services.event_analysis.set_job_progress", return_value=True):
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
