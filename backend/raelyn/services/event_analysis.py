from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
import statistics
import uuid
from typing import Any

from sqlalchemy import String, and_, cast, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
from raelyn.jobs.progress import set_job_progress
from raelyn.models import (
    AppConfig,
    Asset,
    EventRegimeCandidate,
    EventRegimeRun,
    EventRegimeSignal,
    EventRegimeState,
    Job,
    JobEvent,
    MarketEvent,
    MarketEventEmbedding,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
    Media,
    Playlist,
    PlaylistMedia,
    Video,
    VideoEventExtractionRun,
)
from raelyn.services.embeddings import (
    EmbeddingError,
    EmbeddingOverBudgetError,
    EmbeddingTransientError,
    embed_text,
    embedding_enabled,
    embedding_spec,
)
from raelyn.services.inference import get_effective_llm_config
from raelyn.services.job_cancellation import JobCancelRequested, raise_if_job_cancel_requested, request_job_cancel
from raelyn.services.llm import llm_enabled, llm_generate
from raelyn.services.periods import iter_period_starts, local_date, month_add_one, period_bounds_utc
from raelyn.services.transcripts import pick_transcript_asset, read_text_asset
from raelyn.services.video_time import resolve_video_timeline, timeline_time_expr
from raelyn.timeutil import utcnow


EVENT_EXTRACTION_PROMPT_CONFIG_KEY = "llm_event_extraction_prompt"
EVENT_EXTRACTION_PROMPT_BASE_VERSION = "llm_event_v2_source_provenance"
EVENT_ACCEPT_CONFIDENCE = 0.8
EVENT_BATCH_MAX_VIDEOS = 4
EVENT_BATCH_MAX_SOURCE_CHARS = 10000
EVENT_BATCH_SINGLE_MAX_SOURCE_CHARS = 3500
EVENT_SOURCE_SEGMENT_MAX_CHARS = 700
EVENT_DESCRIPTION_SOURCE_MAX_CHARS = 1600
EVENT_REGIME_GRANULARITIES = ("day", "week", "month")
EVENT_REGIME_WINDOWS = {"day": 20, "week": 12, "month": 12}
_EVENT_PIPELINE_ACTIVE_JOB_STATUSES = ("pending", "running")
EVENT_EXTRACTION_PROGRESS_TOTAL = 10000
EVENT_EXTRACTION_PROGRESS_LLM_DONE = 9000
EVENT_EXTRACTION_PROGRESS_WRITTEN = 9600
EVENT_EXTRACTION_PROGRESS_POST_PROCESSED = 9900
EVENT_REGIME_DIRTY_DEBOUNCE_SECONDS = 60


@dataclass(frozen=True)
class _EventExtractionVideoSnapshot:
    id: uuid.UUID
    media_id: uuid.UUID | None
    title: str | None
    description: str | None
    published_at: Any | None
    created_at: Any | None


@dataclass(frozen=True)
class _EventExtractionMediaSnapshot:
    id: uuid.UUID
    name: str | None


@dataclass(frozen=True)
class _EventExtractionTranscriptAssetSnapshot:
    id: uuid.UUID
    s3_bucket: str
    s3_key: str


DEFAULT_EVENT_EXTRACTION_PROMPT = """
你是金融市场事件抽取器。请只输出 JSON，不要 Markdown，不要解释。

任务：从一个或多个视频 source 中抽取会影响市场 regime 分析的原子事件。忽略寒暄、主持人串场、重复免责声明、泛泛评论和没有明确事实支撑的预测。

输出格式：
{
  "videos": [
    {"video_id": "输入中的 video_id，例如 v1", "events": [
      {
        "event_time": {
          "start": "YYYY-MM-DD 或 ISO8601，无法确定则为空字符串",
          "end": "YYYY-MM-DD 或 ISO8601，无法确定则为空字符串",
          "time_precision": "second|day|month|year|unknown",
          "basis": "content_time|explicit_transcript|title|description|unknown"
        },
        "available_at_basis": "video_published_at|content_published_at|explicit_transcript|unknown",
        "event_type": "macro|monetary_policy|earnings|guidance|credit|rates|fx|commodities|equity|geopolitical|policy|liquidity|market_structure|other",
        "title": "不超过 40 个中文字的事件标题",
        "summary": "1-3 句话，保留可验证事实粒度；equity 事件必须覆盖标的市场、触发因素、持续性/估值/风险或影响对象",
        "entities": [{"type": "company|person|country|institution|indicator|asset|sector|other", "name": "...", "role": "actor|affected|indicator|source|other", "confidence": 0.0}],
        "assets": [{"name": "...", "ticker": "若可可靠判断则填写，如 2327.TW，否则空字符串", "market": "TWSE|TPEX|NYSE|NASDAQ|HKEX|A-share|other|unknown", "role": "affected|signal|other", "confidence": 0.0}],
        "sectors": [{"name": "...", "role": "affected|signal|other", "confidence": 0.0}],
        "macro_variables": [{"name": "...", "role": "indicator|affected|cause|other", "confidence": 0.0}],
        "direction": "positive|negative|mixed|neutral|unknown",
        "magnitude": {"value": null, "unit": "", "description": ""},
        "surprise_or_delta": {"value": null, "unit": "", "description": ""},
        "cause_effect_chain": [{"cause": "...", "effect": "...", "relation_type": "cause|effect|affects|mentions", "direction": "positive|negative|mixed|neutral|unknown", "magnitude": {"description": ""}, "confidence": 0.0}],
        "evidence_source_ids": ["v1.title", "v1.desc", "v1.t001"],
        "confidence": 0.0
      }
    ]}
  ]
}

约束：
- 如果前文或自定义提示词与本输出格式冲突，必须以本段 v2 JSON 输出格式为准。
- 相对时间必须基于输入中的“视频内容时间”解析；无法解析时 time_precision 必须为 "unknown"。
- evidence_source_ids 只能引用输入 source 列表中的 id；不要输出证据原文，不要输出 evidence_quotes/evidence_text，不要编造 source id。
- 单个事件只表达一个事实变化；同一事实的原因、影响可以放入 cause_effect_chain。
- 不是事件的内容必须过滤掉，不要为了覆盖率而抽取：
  * 操作策略、荐股建议、观察名单、持股建议、进出场点位、回档布局、逢低买进、获利了结、族群轮动建议。
  * 分析师/法人对股票或产业的主观看法、评级框架、分类标签、题材归类，例如“老 AI 股”“金融股操作策略”。
  * 仅提醒关注财报、营收、法说会、政策或数据发布，但没有给出已经发生的结果。
  * 纯预测、展望、交易计划或条件句，例如“若回档可布局”“建议关注”“可能受惠”。
- 如果一段内容同时包含策略建议和已发生事实变化，只抽取已发生事实变化；例如“股价创历史新高”“公司公布营收 YoY +x%”“外资买超 x 张”“董事会通过并购”。不要把“六月操作策略”“建议回档布局”本身作为事件。
- 对仅由观点/建议构成、没有可验证事实变化的视频，输出对应 video_id 且 events 为空数组。
- confidence 表示该事件是否由当前视频证据充分支持。
- 市场对象必须完整：title、summary、cause_effect_chain 中不得把具体市场类别泛化为“市场”。
  当原文或实体指向 housing market / real estate market / 房地产市场 / 住房市场 / 楼市时，必须写“房地产市场”“住房市场”或“美国房地产市场”等完整口径，禁止单独写“市场”。
- 选择 evidence_source_ids 时必须覆盖足够上下文，让读者能看出具体市场对象。
- title、summary、assets、sectors、entities 的对象口径必须一致，不能标题写上位概念而标签写具体资产或行业。
- equity 事件必须说清股票市场口径：如果可由原文、标题、描述或公认证券代码可靠判断上市地/交易所/代码，title 和 summary 要写明，例如“台股国巨(2327.TW)”；无法可靠判断时写“上市市场未明”，不要猜代码。
- equity 事件的 summary 不能只复述“股价上涨/下跌”，还必须覆盖至少两项：资金面或买盘、基本面/获利、估值或本益比、行业主题、持续性风险、影响到的公司/行业。
- equity 事件的实体必须拆分：company 表示发行公司，asset 表示可交易证券，sector 表示所属行业，country/institution 表示上市市场或交易所；若标的是台股，应包含 country:台湾 与 institution:台湾证券交易所或柜买中心（能可靠判断时）。
- 不要把普通词误标为 company 或 asset：例如“黄金”只有在原文确实讨论黄金商品、黄金 ETF、黄金股或名为“黄金”的证券时才可标为 asset/company；“黄金交叉”、形容词、人名片段不得标为资产或公司。
- 只输出当前事件有证据支持的实体；不要把同段视频其他无关公司硬塞进本事件，例如未与该事件形成因果或影响关系的公司不应出现在 entities/assets/sectors。
""".strip()


def event_extraction_prompt_defaults() -> dict[str, Any]:
    return {
        EVENT_EXTRACTION_PROMPT_CONFIG_KEY: {
            "text": DEFAULT_EVENT_EXTRACTION_PROMPT,
            "version": EVENT_EXTRACTION_PROMPT_BASE_VERSION,
        }
    }


@dataclass(frozen=True)
class EventExtractionSpec:
    model: str
    prompt_version: str
    prompt_text: str
    chunk_max_chars: int


@dataclass(frozen=True)
class _EventSource:
    source_id: str
    video_alias: str
    video_id: uuid.UUID
    source_kind: str
    source_label: str
    text: str
    char_start: int
    char_end: int
    source_sha256: str
    transcript_asset_id: uuid.UUID | None = None


@dataclass(frozen=True)
class _PreparedEventVideo:
    alias: str
    video: _EventExtractionVideoSnapshot
    media: _EventExtractionMediaSnapshot | None
    transcript_asset: _EventExtractionTranscriptAssetSnapshot
    transcript_text: str
    content_time: datetime | None
    source_hash: str
    source_chars: int


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return max(0.0, min(1.0, parsed))


def _short_text(value: Any, *, max_len: int = 600) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _jsonable(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return None
    return {"description": str(value)}


def _normalize_key(value: Any) -> str:
    text = _short_text(value, max_len=160).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff._:-]+", "_", text).strip("_")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def event_extraction_spec(session: Session | None = None) -> EventExtractionSpec:
    prompt_text = DEFAULT_EVENT_EXTRACTION_PROMPT
    if session is not None:
        item = session.get(AppConfig, EVENT_EXTRACTION_PROMPT_CONFIG_KEY)
        value = item.value if item else None
        if isinstance(value, dict):
            custom = str(value.get("text") or "").strip()
            if custom:
                prompt_text = custom
        elif isinstance(value, str) and value.strip():
            prompt_text = value.strip()

    prompt_hash = _sha256_text(prompt_text)[:12]
    model = str(get_effective_llm_config(session).model or settings.llm_model or "").strip()
    try:
        chunk_max_chars = int(settings.event_extraction_chunk_max_chars or 12000)
    except Exception:
        chunk_max_chars = 12000
    return EventExtractionSpec(
        model=model,
        prompt_version=f"{EVENT_EXTRACTION_PROMPT_BASE_VERSION}:{prompt_hash}",
        prompt_text=prompt_text,
        chunk_max_chars=max(2000, chunk_max_chars),
    )


def _strip_llm_wrappers(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"<think>.*?</think>", "", value, flags=re.IGNORECASE | re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", value, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        value = fence.group(1).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        value = value[start : end + 1]
    return value


def parse_event_extraction_response(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    cleaned = _strip_llm_wrappers(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return [], [f"event extraction json parse failed: {exc}"]
    if not isinstance(payload, dict):
        return [], ["event extraction payload is not a json object"]
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        return [], ["event extraction payload missing events[]"]

    events: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            warnings.append(f"event[{idx}] dropped: not object")
            continue
        confidence = _safe_float(raw.get("confidence"))
        if confidence is None:
            warnings.append(f"event[{idx}] dropped: invalid confidence")
            continue
        title = _short_text(raw.get("title"), max_len=220)
        summary = _short_text(raw.get("summary"), max_len=1500)
        if not title and not summary:
            warnings.append(f"event[{idx}] dropped: missing title and summary")
            continue
        raw["confidence"] = confidence
        events.append(raw)
    return events, warnings


def parse_event_extraction_batch_response(
    text: str,
    *,
    expected_video_ids: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    warnings: list[str] = []
    cleaned = _strip_llm_wrappers(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return {}, [f"event extraction json parse failed: {exc}"]
    if not isinstance(payload, dict):
        return {}, ["event extraction payload is not a json object"]

    expected = [str(item) for item in expected_video_ids if str(item)]
    expected_set = set(expected)
    rows: dict[str, list[dict[str, Any]]] = {item: [] for item in expected}
    raw_videos = payload.get("videos")
    if raw_videos is None and len(expected) == 1 and isinstance(payload.get("events"), list):
        raw_videos = [{"video_id": expected[0], "events": payload.get("events")}]
    if not isinstance(raw_videos, list):
        return {}, ["event extraction payload missing videos[]"]

    seen_videos: set[str] = set()
    for video_idx, raw_video in enumerate(raw_videos):
        if not isinstance(raw_video, dict):
            warnings.append(f"videos[{video_idx}] dropped: not object")
            continue
        alias = str(raw_video.get("video_id") or raw_video.get("id") or "").strip()
        if alias not in expected_set:
            warnings.append(f"videos[{video_idx}] dropped: unexpected video_id {alias or '<empty>'}")
            continue
        seen_videos.add(alias)
        raw_events = raw_video.get("events")
        if not isinstance(raw_events, list):
            warnings.append(f"videos[{alias}] dropped: missing events[]")
            continue
        events: list[dict[str, Any]] = []
        for event_idx, raw in enumerate(raw_events):
            if not isinstance(raw, dict):
                warnings.append(f"videos[{alias}].event[{event_idx}] dropped: not object")
                continue
            confidence = _safe_float(raw.get("confidence"))
            if confidence is None:
                warnings.append(f"videos[{alias}].event[{event_idx}] dropped: invalid confidence")
                continue
            title = _short_text(raw.get("title"), max_len=220)
            summary = _short_text(raw.get("summary"), max_len=1500)
            if not title and not summary:
                warnings.append(f"videos[{alias}].event[{event_idx}] dropped: missing title and summary")
                continue
            raw["confidence"] = confidence
            events.append(raw)
        rows[alias].extend(events)

    for alias in expected:
        if alias not in seen_videos:
            warnings.append(f"event extraction response missing video_id {alias}")
    return rows, warnings


def _chunk_text(text: str, max_chars: int) -> list[str]:
    value = str(text or "")
    if not value.strip():
        return []
    if len(value) <= max_chars:
        return [value]
    chunks: list[str] = []
    pos = 0
    while pos < len(value):
        end = min(len(value), pos + max_chars)
        if end < len(value):
            newline = value.rfind("\n", pos + max_chars // 2, end)
            if newline > pos:
                end = newline
        chunks.append(value[pos:end].strip())
        pos = end
    return [chunk for chunk in chunks if chunk]


def _chunk_text_with_offsets(text: str, max_chars: int) -> list[tuple[str, int, int]]:
    value = str(text or "")
    if not value.strip():
        return []
    if len(value) <= max_chars:
        stripped = value.strip()
        start = len(value) - len(value.lstrip())
        end = len(value.rstrip())
        return [(stripped, start, end)] if stripped else []

    chunks: list[tuple[str, int, int]] = []
    pos = 0
    while pos < len(value):
        end = min(len(value), pos + max_chars)
        if end < len(value):
            newline = value.rfind("\n", pos + max_chars // 2, end)
            if newline > pos:
                end = newline
        raw = value[pos:end]
        stripped = raw.strip()
        if stripped:
            start_offset = pos + len(raw) - len(raw.lstrip())
            end_offset = pos + len(raw.rstrip())
            chunks.append((stripped, start_offset, end_offset))
        pos = max(end, pos + 1)
    return chunks


def _text_segments_with_offsets(text: str, *, base_start: int, max_chars: int) -> list[tuple[str, int, int]]:
    value = str(text or "")
    if not value.strip():
        return []
    if len(value) <= max_chars:
        stripped = value.strip()
        start = base_start + len(value) - len(value.lstrip())
        end = base_start + len(value.rstrip())
        return [(stripped, start, end)] if stripped else []

    rows: list[tuple[str, int, int]] = []
    pos = 0
    while pos < len(value):
        end = min(len(value), pos + max_chars)
        if end < len(value):
            boundary = max(
                value.rfind("\n", pos + max_chars // 2, end),
                value.rfind(". ", pos + max_chars // 2, end),
                value.rfind("。", pos + max_chars // 2, end),
                value.rfind("！", pos + max_chars // 2, end),
                value.rfind("？", pos + max_chars // 2, end),
                value.rfind(" ", pos + max_chars // 2, end),
            )
            if boundary > pos:
                end = boundary + 1
        raw = value[pos:end]
        stripped = raw.strip()
        if stripped:
            start_offset = base_start + pos + len(raw) - len(raw.lstrip())
            end_offset = base_start + pos + len(raw.rstrip())
            rows.append((stripped, start_offset, end_offset))
        pos = max(end, pos + 1)
    return rows


def _build_event_sources(
    *,
    alias: str,
    video: Video | _EventExtractionVideoSnapshot,
    transcript_asset_id: uuid.UUID,
    transcript_text: str,
    chunk_text: str,
    chunk_start: int,
) -> list[_EventSource]:
    sources: list[_EventSource] = []
    title = str(video.title or "").strip()
    if title:
        sources.append(
            _EventSource(
                source_id=f"{alias}.title",
                video_alias=alias,
                video_id=video.id,
                source_kind="title",
                source_label="标题",
                text=title,
                char_start=0,
                char_end=len(title),
                source_sha256=_sha256_text(title),
                transcript_asset_id=None,
            )
        )

    description = str(video.description or "").strip()
    if description:
        desc_text = description[:EVENT_DESCRIPTION_SOURCE_MAX_CHARS].strip()
        if desc_text:
            sources.append(
                _EventSource(
                    source_id=f"{alias}.desc",
                    video_alias=alias,
                    video_id=video.id,
                    source_kind="description",
                    source_label="描述",
                    text=desc_text,
                    char_start=0,
                    char_end=len(desc_text),
                    source_sha256=_sha256_text(desc_text),
                    transcript_asset_id=None,
                )
            )

    for index, (segment, start, end) in enumerate(
        _text_segments_with_offsets(
            chunk_text,
            base_start=chunk_start,
            max_chars=EVENT_SOURCE_SEGMENT_MAX_CHARS,
        ),
        start=1,
    ):
        sources.append(
            _EventSource(
                source_id=f"{alias}.t{index:03d}",
                video_alias=alias,
                video_id=video.id,
                source_kind="transcript",
                source_label=f"转写片段 {index:03d}",
                text=segment,
                char_start=start,
                char_end=end,
                source_sha256=_sha256_text(segment),
                transcript_asset_id=transcript_asset_id,
            )
        )
    return sources


def _event_sources_total_chars(sources: list[_EventSource]) -> int:
    return sum(len(item.text or "") for item in sources)


def _render_event_batch_prompt(
    *,
    spec: EventExtractionSpec,
    videos: list[_PreparedEventVideo],
    video_sources: dict[str, list[_EventSource]],
    chunk_label: str,
) -> str:
    blocks: list[str] = []
    for prepared in videos:
        video = prepared.video
        content_time_text = prepared.content_time.isoformat() if prepared.content_time else "unknown"
        published_at_text = video.published_at.isoformat() if video.published_at else "unknown"
        source_lines = []
        for source in video_sources.get(prepared.alias, []):
            source_lines.append(f"[{source.source_id}] ({source.source_label}) {source.text}")
        blocks.append(
            "\n".join(
                [
                    f"video_id: {prepared.alias}",
                    f"视频标题: {video.title or ''}",
                    f"媒体名: {getattr(prepared.media, 'name', None) or ''}",
                    f"视频内容时间: {content_time_text}",
                    f"视频发布时间: {published_at_text}",
                    "sources:",
                    *source_lines,
                ]
            )
        )

    protocol = """
v2 输出协议：
- 顶层必须是 {"videos": [...]}，每个输入 video_id 必须出现一次。
- 每个事件只能使用 evidence_source_ids 引用上方 sources 中的 id；不要输出 evidence_quotes / evidence_text。
- 同一事件至少给 1 个 evidence_source_ids；无法定位证据的内容必须过滤，不能作为 accepted 事件。
- sports celebration、皇室婚礼、娱乐闲聊、普通人物故事等不影响金融市场 regime 的内容应输出 events: []。
- 如果输入内有多个视频，不要把一个视频的事实归到另一个 video_id。
""".strip()
    return f"""{spec.prompt_text}

{protocol}

输入批次：{chunk_label}
{chr(10).join(blocks)}
""".strip()


def _evidence_rows_from_source_ids(
    *,
    raw: dict[str, Any],
    source_by_id: dict[str, _EventSource],
) -> tuple[list[dict[str, Any]], list[str]]:
    value = raw.get("evidence_source_ids")
    if not isinstance(value, list):
        return [], ["event dropped provenance: evidence_source_ids is missing or not list"]
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for item in value[:12]:
        source_id = str(item or "").strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        source = source_by_id.get(source_id)
        if not source:
            warnings.append(f"event evidence source id not found: {source_id}")
            continue
        evidence_json = {
            "schema_version": "event_provenance_v1",
            "source_id": source.source_id,
            "source_kind": source.source_kind,
            "source_label": source.source_label,
            "char_start": source.char_start,
            "char_end": source.char_end,
            "source_sha256": source.source_sha256,
            "verified": True,
        }
        rows.append(
            {
                "video_id": source.video_id,
                "transcript_asset_id": source.transcript_asset_id,
                "evidence_key": _sha256_text(f"{source.source_id}:{source.source_sha256}")[:24],
                "evidence_text": source.text,
                "evidence_json": evidence_json,
                "confidence": _safe_float(raw.get("confidence")),
            }
        )
    return rows, warnings


def _parse_event_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}", text):
        try:
            return datetime(int(text), 1, 1, tzinfo=timezone.utc)
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = [int(part) for part in text.split("-", 1)]
        try:
            return datetime(year, month, 1, tzinfo=timezone.utc)
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.combine(date.fromisoformat(text), time.min, tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_time(raw: dict[str, Any]) -> tuple[datetime | None, datetime | None, str]:
    raw_time = raw.get("event_time")
    if isinstance(raw_time, dict):
        start = _parse_event_datetime(raw_time.get("start") or raw_time.get("date"))
        end = _parse_event_datetime(raw_time.get("end"))
        precision = str(raw_time.get("time_precision") or raw_time.get("precision") or "").strip().lower()
    else:
        start = _parse_event_datetime(raw_time)
        end = None
        precision = ""
    if start is None and end is None:
        precision = "unknown"
    if precision not in {"second", "day", "month", "year", "range", "unknown"}:
        precision = "unknown" if start is None else "day"
    return start, end, precision


def _event_key(raw: dict[str, Any], start: datetime | None) -> str:
    entity_names: list[str] = []
    for field_name in ("entities", "assets", "sectors", "macro_variables"):
        value = raw.get(field_name)
        if isinstance(value, list):
            for item in value[:10]:
                if isinstance(item, dict):
                    entity_names.append(_normalize_key(item.get("name") or item.get("ticker")))
                else:
                    entity_names.append(_normalize_key(item))
    payload = {
        "event_type": _normalize_key(raw.get("event_type")),
        "title": _normalize_key(raw.get("title")),
        "summary": _normalize_key(raw.get("summary"))[:160],
        "start": start.isoformat() if start else "",
        "entities": sorted([name for name in entity_names if name])[:12],
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))[:24]


def _event_source_hash(
    *,
    video: Video | _EventExtractionVideoSnapshot,
    media: Media | _EventExtractionMediaSnapshot | None,
    transcript_asset_id: uuid.UUID,
    transcript_text: str,
    content_published_at: datetime | None = None,
) -> str:
    timeline = content_published_at if content_published_at is not None else resolve_video_timeline_static(video)
    payload = {
        "video_id": str(video.id),
        "title": video.title or "",
        "description": (video.description or "")[:2000],
        "media_name": getattr(media, "name", None) or "",
        "published_at": video.published_at.isoformat() if video.published_at else "",
        "content_published_at": timeline.isoformat() if timeline else "",
        "transcript_asset_id": str(transcript_asset_id),
        "transcript_sha256": _sha256_text(transcript_text),
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def resolve_video_timeline_static(video: Video) -> datetime | None:
    try:
        for item in getattr(video, "time_evidence", []) or []:
            if (
                str(getattr(item, "time_role", "") or "") == "content_published_at"
                and str(getattr(item, "status", "") or "") == "accepted"
                and getattr(item, "date_year", None)
                and getattr(item, "date_month", None)
                and getattr(item, "date_day", None)
            ):
                return datetime(
                    int(item.date_year),
                    int(item.date_month),
                    int(item.date_day),
                    tzinfo=timezone.utc,
                )
    except Exception:
        pass
    return video.published_at


def _render_event_prompt(
    *,
    spec: EventExtractionSpec,
    video: Video | _EventExtractionVideoSnapshot,
    media: Media | _EventExtractionMediaSnapshot | None,
    content_time: datetime | None,
    chunk: str,
    chunk_index: int,
    chunk_total: int,
) -> str:
    description = _short_text(video.description, max_len=1600)
    content_time_text = content_time.isoformat() if content_time else "unknown"
    published_at_text = video.published_at.isoformat() if video.published_at else "unknown"
    return f"""{spec.prompt_text}

输入元数据：
- 视频标题：{video.title or ""}
- 媒体名：{getattr(media, "name", None) or ""}
- 视频内容时间：{content_time_text}
- 视频发布时间：{published_at_text}
- description 摘要：{description}
- chunk：{chunk_index + 1}/{chunk_total}

transcript chunk:
{chunk}
""".strip()


def _job_warning(session: Session, job: Job | None, message: str, data: dict[str, Any] | None = None) -> None:
    if not job:
        return
    session.add(JobEvent(job_id=job.id, level="warning", message=message[:1000], data=data or {}))


def _iter_entity_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    field_specs = {
        "entities": None,
        "assets": "asset",
        "sectors": "sector",
        "macro_variables": "macro_variable",
    }
    for field_name, forced_type in field_specs.items():
        value = raw.get(field_name)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("ticker") or item.get("symbol")
                entity_type = forced_type or item.get("type") or "other"
                role = item.get("role")
                confidence = _safe_float(item.get("confidence"))
                source_text = item.get("source_text") or item.get("evidence_text")
            else:
                name = item
                entity_type = forced_type or "other"
                role = None
                confidence = None
                source_text = None
            name_text = _short_text(name, max_len=180)
            if not name_text:
                continue
            items.append(
                {
                    "entity_type": _normalize_key(entity_type) or "other",
                    "name": name_text,
                    "normalized_key": _normalize_key(name_text) or name_text.lower(),
                    "role": _short_text(role, max_len=80) or None,
                    "confidence": confidence,
                    "source_text": _short_text(source_text, max_len=500) or None,
                }
            )
    return items


def _insert_event_children(
    session: Session,
    *,
    event: MarketEvent,
    raw: dict[str, Any],
    video: Video | _EventExtractionVideoSnapshot,
    transcript_asset_id: uuid.UUID,
    evidence_rows: list[dict[str, Any]] | None = None,
) -> None:
    rows = evidence_rows if evidence_rows is not None else []
    for idx, evidence_row in enumerate(rows[:12]):
        evidence_text = _short_text(evidence_row.get("evidence_text"), max_len=2400)
        if not evidence_text:
            continue
        confidence = _safe_float(evidence_row.get("confidence"))
        evidence_json = evidence_row.get("evidence_json") if isinstance(evidence_row.get("evidence_json"), dict) else None
        evidence_transcript_asset_id = evidence_row.get("transcript_asset_id") if "transcript_asset_id" in evidence_row else transcript_asset_id
        session.add(
            MarketEventEvidence(
                event_id=event.id,
                video_id=evidence_row.get("video_id") or video.id,
                transcript_asset_id=evidence_transcript_asset_id,
                evidence_key=str(evidence_row.get("evidence_key") or _sha256_text(f"{idx}:{evidence_text}")[:24]),
                evidence_text=evidence_text,
                evidence_json=evidence_json,
                confidence=confidence,
            )
        )

    entity_by_name: dict[str, MarketEventEntity] = {}
    for item in _iter_entity_items(raw):
        entity = MarketEventEntity(event_id=event.id, **item)
        session.add(entity)
        entity_by_name[item["normalized_key"]] = entity
    session.flush()

    chain = raw.get("cause_effect_chain")
    if not isinstance(chain, list):
        chain = []
    for relation_raw in chain[:24]:
        if isinstance(relation_raw, dict):
            cause = relation_raw.get("cause") or relation_raw.get("source")
            effect = relation_raw.get("effect") or relation_raw.get("target")
            relation_type = _normalize_key(relation_raw.get("relation_type") or "affects") or "affects"
            direction = _short_text(relation_raw.get("direction"), max_len=80) or None
            magnitude = _jsonable(relation_raw.get("magnitude"))
            confidence = _safe_float(relation_raw.get("confidence"))
            evidence_text = _short_text(relation_raw.get("evidence_text"), max_len=800) or None
            raw_payload = relation_raw
        else:
            cause = None
            effect = None
            relation_type = "mentions"
            direction = None
            magnitude = None
            confidence = _safe_float(raw.get("confidence"))
            evidence_text = _short_text(relation_raw, max_len=800) or None
            raw_payload = {"text": evidence_text}
        source_entity = entity_by_name.get(_normalize_key(cause))
        target_entity = entity_by_name.get(_normalize_key(effect))
        session.add(
            MarketEventRelation(
                event_id=event.id,
                source_entity_id=getattr(source_entity, "id", None),
                target_entity_id=getattr(target_entity, "id", None),
                relation_type=relation_type,
                direction=direction,
                magnitude=magnitude,
                confidence=confidence,
                evidence_text=evidence_text,
                raw_payload=raw_payload,
            )
        )


def _delete_video_events(session: Session, *, video_id: uuid.UUID) -> None:
    event_ids = list(
        session.execute(
            select(MarketEvent.id).where(MarketEvent.source_video_id == video_id)
        ).scalars()
    )
    if not event_ids:
        return
    for model in (MarketEventRelation, MarketEventEntity, MarketEventEvidence, MarketEventEmbedding):
        session.execute(delete(model).where(getattr(model, "event_id").in_(event_ids)))
    session.execute(delete(MarketEvent).where(MarketEvent.id.in_(event_ids)))


def _delete_video_event_extraction_runs(session: Session, *, video_id: uuid.UUID) -> None:
    session.execute(delete(VideoEventExtractionRun).where(VideoEventExtractionRun.video_id == video_id))


def _succeeded_event_extraction_run(
    session: Session,
    *,
    video_id: uuid.UUID,
    source_hash: str,
    spec: EventExtractionSpec,
) -> VideoEventExtractionRun | None:
    return session.execute(
        select(VideoEventExtractionRun)
        .where(
            VideoEventExtractionRun.video_id == video_id,
            VideoEventExtractionRun.source_hash == source_hash,
            VideoEventExtractionRun.prompt_version == spec.prompt_version,
            VideoEventExtractionRun.extraction_model == spec.model,
            VideoEventExtractionRun.status == "succeeded",
        )
        .limit(1)
    ).scalar_one_or_none()


def _write_event_extraction_run(
    session: Session,
    *,
    video_id: uuid.UUID,
    transcript_asset_id: uuid.UUID | None,
    source_hash: str,
    spec: EventExtractionSpec,
    status: str,
    event_count: int,
    warning_count: int = 0,
    usage: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> VideoEventExtractionRun:
    run = session.execute(
        select(VideoEventExtractionRun)
        .where(
            VideoEventExtractionRun.video_id == video_id,
            VideoEventExtractionRun.source_hash == source_hash,
            VideoEventExtractionRun.prompt_version == spec.prompt_version,
            VideoEventExtractionRun.extraction_model == spec.model,
        )
        .limit(1)
    ).scalar_one_or_none()
    if not run:
        run = VideoEventExtractionRun(
            video_id=video_id,
            source_hash=source_hash,
            prompt_version=spec.prompt_version,
            extraction_model=spec.model,
        )
        session.add(run)
    run.transcript_asset_id = transcript_asset_id
    run.status = status
    run.event_count = max(0, int(event_count or 0))
    run.warning_count = max(0, int(warning_count or 0))
    run.usage_json = usage
    run.error_message = _short_text(error_message, max_len=2000) or None
    run.updated_at = utcnow()
    return run


def _accepted_event_ids_for_extraction(
    session: Session,
    *,
    video_id: uuid.UUID,
    source_hash: str,
    spec: EventExtractionSpec,
) -> list[uuid.UUID]:
    return list(
        session.execute(
            select(MarketEvent.id)
            .where(
                MarketEvent.source_video_id == video_id,
                MarketEvent.source_hash == source_hash,
                MarketEvent.prompt_version == spec.prompt_version,
                MarketEvent.extraction_model == spec.model,
                MarketEvent.status == "accepted",
            )
            .order_by(MarketEvent.created_at.asc(), MarketEvent.id.asc())
        )
        .scalars()
        .all()
    )


def _ready_embedding_event_ids(session: Session, event_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not event_ids:
        return set()
    spec = embedding_spec()
    return set(
        session.execute(
            select(MarketEventEmbedding.event_id).where(
                MarketEventEmbedding.event_id.in_(event_ids),
                MarketEventEmbedding.embedding_model == spec.model,
                MarketEventEmbedding.embedding_dim == spec.dim,
                MarketEventEmbedding.status == "ready",
            )
        )
        .scalars()
        .all()
    )


def enqueue_event_embedding_jobs(session: Session, *, event_ids: list[uuid.UUID], priority: int = 0) -> int:
    unique_event_ids = sorted(set(event_ids), key=lambda item: str(item))
    ready_event_ids = _ready_embedding_event_ids(session, unique_event_ids)
    enqueued = 0
    for event_id in unique_event_ids:
        if event_id in ready_event_ids:
            continue
        enqueue_job(session, type_="event.embed", params={"event_id": str(event_id)}, priority=priority)
        enqueued += 1
    return enqueued


def _set_event_extraction_progress(job_id: uuid.UUID | None, current: int) -> None:
    if not job_id:
        return
    set_job_progress(
        job_id=job_id,
        current=max(0, min(EVENT_EXTRACTION_PROGRESS_TOTAL, int(current))),
        total=EVENT_EXTRACTION_PROGRESS_TOTAL,
    )


def _event_extraction_llm_progress(idx: int, total: int) -> int:
    if total <= 0:
        return 0
    return min(EVENT_EXTRACTION_PROGRESS_LLM_DONE, int(EVENT_EXTRACTION_PROGRESS_LLM_DONE * idx / total))


def _video_snapshot(video: Video) -> _EventExtractionVideoSnapshot:
    return _EventExtractionVideoSnapshot(
        id=video.id,
        media_id=video.media_id,
        title=video.title,
        description=video.description,
        published_at=video.published_at,
        created_at=video.created_at,
    )


def _media_snapshot(media: Media | None) -> _EventExtractionMediaSnapshot | None:
    if not media:
        return None
    return _EventExtractionMediaSnapshot(id=media.id, name=media.name)


def _transcript_asset_snapshot(asset: Asset) -> _EventExtractionTranscriptAssetSnapshot:
    return _EventExtractionTranscriptAssetSnapshot(id=asset.id, s3_bucket=asset.s3_bucket, s3_key=asset.s3_key)


def _prepare_event_videos(
    session: Session,
    *,
    video_ids: list[uuid.UUID],
    spec: EventExtractionSpec,
) -> tuple[list[_PreparedEventVideo], list[dict[str, Any]]]:
    pending: list[tuple[str, _EventExtractionVideoSnapshot, _EventExtractionMediaSnapshot | None, _EventExtractionTranscriptAssetSnapshot, datetime | None]] = []
    skipped: list[dict[str, Any]] = []
    for index, video_id in enumerate(video_ids, start=1):
        video = session.get(Video, video_id)
        if not video:
            skipped.append({"video_id": str(video_id), "reason": "video not found"})
            continue
        media = session.get(Media, video.media_id) if video.media_id else None
        transcript_asset = pick_transcript_asset(session, video_id, variant="plain")
        if not transcript_asset:
            skipped.append({"video_id": str(video_id), "reason": "plain transcript not found"})
            continue
        timeline = resolve_video_timeline(session, video)
        content_time = timeline.content_published_at or video.published_at
        pending.append(
            (
                f"v{index}",
                _video_snapshot(video),
                _media_snapshot(media),
                _transcript_asset_snapshot(transcript_asset),
                content_time,
            )
        )

    # 关闭当前事务后再读 S3 / 调 LLM，避免外部 I/O 持有 DB 锁。
    session.commit()

    prepared: list[_PreparedEventVideo] = []
    for alias, video_info, media_info, transcript_asset_info, content_time in pending:
        transcript_text, _ = read_text_asset(transcript_asset_info)
        if not transcript_text.strip():
            skipped.append({"video_id": str(video_info.id), "reason": "empty transcript"})
            continue
        source_hash = _event_source_hash(
            video=video_info,
            media=media_info,
            transcript_asset_id=transcript_asset_info.id,
            transcript_text=transcript_text,
            content_published_at=content_time,
        )
        title_chars = len(str(video_info.title or ""))
        desc_chars = len(str(video_info.description or "")[:EVENT_DESCRIPTION_SOURCE_MAX_CHARS])
        prepared.append(
            _PreparedEventVideo(
                alias=alias,
                video=video_info,
                media=media_info,
                transcript_asset=transcript_asset_info,
                transcript_text=transcript_text,
                content_time=content_time,
                source_hash=source_hash,
                source_chars=title_chars + desc_chars + len(transcript_text),
            )
        )
    return prepared, skipped


def _enqueue_single_event_fallbacks(
    session: Session,
    *,
    videos: list[_PreparedEventVideo],
    force: bool,
    priority: int,
    parent_job_id: uuid.UUID | None,
    reason: str,
    job: Job | None,
) -> int:
    enqueued = 0
    for prepared in videos:
        _job_warning(
            session,
            job,
            "event batch fallback to single video extraction",
            {"video_id": str(prepared.video.id), "reason": reason},
        )
        enqueue_job(
            session,
            type_="video.extract_events",
            params={"video_id": str(prepared.video.id), "force": bool(force)},
            priority=priority,
            parent_job_id=str(parent_job_id) if parent_job_id else None,
        )
        enqueued += 1
    return enqueued


def extract_video_events_batch(
    session: Session,
    *,
    video_ids: list[uuid.UUID],
    force: bool = False,
    job: Job | None = None,
    fallback_to_single: bool = True,
) -> dict[str, Any]:
    if not llm_enabled():
        return {"skipped": "llm not configured"}

    job_id = getattr(job, "id", None) if job else None
    job_priority = int(getattr(job, "priority", 0) or 0) if job else 0
    spec = event_extraction_spec(session)
    if job_id:
        _set_event_extraction_progress(job_id, 0)

    unique_video_ids = list(dict.fromkeys([uuid.UUID(str(item)) for item in video_ids]))
    prepared, skipped_rows = _prepare_event_videos(session, video_ids=unique_video_ids, spec=spec)
    if not prepared:
        reason = skipped_rows[0]["reason"] if len(skipped_rows) == 1 else "no extractable videos"
        return {"skipped": reason, "skipped_rows": skipped_rows}

    work: list[_PreparedEventVideo] = []
    cached = 0
    embedding_jobs_enqueued = 0
    for prepared_video in prepared:
        if not force and _succeeded_event_extraction_run(
            session,
            video_id=prepared_video.video.id,
            source_hash=prepared_video.source_hash,
            spec=spec,
        ):
            cached += 1
            embedding_jobs_enqueued += enqueue_event_embedding_jobs(
                session,
                event_ids=_accepted_event_ids_for_extraction(
                    session,
                    video_id=prepared_video.video.id,
                    source_hash=prepared_video.source_hash,
                    spec=spec,
                ),
                priority=job_priority,
            )
            continue
        work.append(prepared_video)
    session.commit()
    if not work:
        _set_event_extraction_progress(job_id, EVENT_EXTRACTION_PROGRESS_POST_PROCESSED)
        return {
            "ok": True,
            "cached": True,
            "cached_videos": cached,
            "embedding_jobs_enqueued": embedding_jobs_enqueued,
            "skipped_rows": skipped_rows,
        }

    accepted = 0
    draft = 0
    inserted = 0
    duplicate = 0
    fallback_jobs_enqueued = 0
    warnings: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}
    seen_keys_by_video: dict[uuid.UUID, set[str]] = defaultdict(set)
    parsed_event_rows: dict[
        uuid.UUID,
        list[tuple[dict[str, Any], datetime | None, datetime | None, str, str, str, list[dict[str, Any]]]],
    ] = defaultdict(list)
    warning_events: list[tuple[str, dict[str, Any]]] = []

    batches: list[list[tuple[_PreparedEventVideo, str, int, str]]] = []
    if len(work) > 1:
        current: list[tuple[_PreparedEventVideo, str, int, str]] = []
        current_chars = 0
        for prepared_video in work:
            chunks = _chunk_text_with_offsets(prepared_video.transcript_text, spec.chunk_max_chars)
            if len(chunks) != 1:
                if fallback_to_single:
                    fallback_jobs_enqueued += _enqueue_single_event_fallbacks(
                        session,
                        videos=[prepared_video],
                        force=force,
                        priority=job_priority,
                        parent_job_id=job_id,
                        reason="long transcript requires chunked single extraction",
                        job=job,
                    )
                else:
                    work = [prepared_video]
                continue
            chunk, chunk_start, _chunk_end = chunks[0]
            if (
                prepared_video.source_chars > EVENT_BATCH_SINGLE_MAX_SOURCE_CHARS
                or current_chars + prepared_video.source_chars > EVENT_BATCH_MAX_SOURCE_CHARS
                or len(current) >= EVENT_BATCH_MAX_VIDEOS
            ):
                if current:
                    batches.append(current)
                    current = []
                    current_chars = 0
            if prepared_video.source_chars > EVENT_BATCH_SINGLE_MAX_SOURCE_CHARS and fallback_to_single:
                fallback_jobs_enqueued += _enqueue_single_event_fallbacks(
                    session,
                    videos=[prepared_video],
                    force=force,
                    priority=job_priority,
                    parent_job_id=job_id,
                    reason="video exceeds short-video batch source budget",
                    job=job,
                )
                continue
            current.append((prepared_video, chunk, chunk_start, "1/1"))
            current_chars += prepared_video.source_chars
        if current:
            batches.append(current)
    else:
        prepared_video = work[0]
        chunks = _chunk_text_with_offsets(prepared_video.transcript_text, spec.chunk_max_chars)
        batches = [
            [(prepared_video, chunk, chunk_start, f"{idx + 1}/{len(chunks)}")]
            for idx, (chunk, chunk_start, _chunk_end) in enumerate(chunks)
        ]

    if not batches and len(work) == 1:
        return {"skipped": "empty transcript"}

    for idx, batch in enumerate(batches):
        if job and job_id:
            raise_if_job_cancel_requested(session, job)
            session.commit()
            _set_event_extraction_progress(job_id, _event_extraction_llm_progress(idx, len(batches)))
        video_sources: dict[str, list[_EventSource]] = {}
        source_by_id: dict[str, _EventSource] = {}
        batch_videos: list[_PreparedEventVideo] = []
        for prepared_video, chunk, chunk_start, _chunk_label in batch:
            sources = _build_event_sources(
                alias=prepared_video.alias,
                video=prepared_video.video,
                transcript_asset_id=prepared_video.transcript_asset.id,
                transcript_text=prepared_video.transcript_text,
                chunk_text=chunk,
                chunk_start=chunk_start,
            )
            video_sources[prepared_video.alias] = sources
            source_by_id.update({source.source_id: source for source in sources})
            batch_videos.append(prepared_video)
        prompt = _render_event_batch_prompt(
            spec=spec,
            videos=batch_videos,
            video_sources=video_sources,
            chunk_label=", ".join([item[3] for item in batch]),
        )
        result = llm_generate(
            prompt=prompt,
            think=False,
            response_format="json",
            options={"temperature": 0},
        )
        for key, value in (result.get("usage") or {}).items():
            if key in usage:
                try:
                    usage[key] += int(value or 0)
                except Exception:
                    pass
        expected_aliases = [prepared_video.alias for prepared_video in batch_videos]
        parsed_by_alias, parse_warnings = parse_event_extraction_batch_response(
            str(result.get("text") or ""),
            expected_video_ids=expected_aliases,
        )
        for message in parse_warnings:
            warnings.append(message)
            warning_events.append((message, {"batch": idx + 1}))
        missing_aliases = {
            message.rsplit(" ", 1)[-1]
            for message in parse_warnings
            if message.startswith("event extraction response missing video_id ")
        }
        if fallback_to_single and len(batch_videos) > 1 and missing_aliases:
            missing_videos = [prepared_video for prepared_video in batch_videos if prepared_video.alias in missing_aliases]
            if missing_videos:
                fallback_jobs_enqueued += _enqueue_single_event_fallbacks(
                    session,
                    videos=missing_videos,
                    force=force,
                    priority=job_priority,
                    parent_job_id=job_id,
                    reason="batch response missing video id",
                    job=job,
                )
                batch_videos = [prepared_video for prepared_video in batch_videos if prepared_video.alias not in missing_aliases]
                if not batch_videos:
                    continue
        if any("json parse failed" in message or "missing videos[]" in message for message in parse_warnings):
            if fallback_to_single and len(batch_videos) > 1:
                fallback_jobs_enqueued += _enqueue_single_event_fallbacks(
                    session,
                    videos=batch_videos,
                    force=force,
                    priority=job_priority,
                    parent_job_id=job_id,
                    reason="batch response parse failed",
                    job=job,
                )
                continue
            if len(batch_videos) == 1:
                _write_event_extraction_run(
                    session,
                    video_id=batch_videos[0].video.id,
                    transcript_asset_id=batch_videos[0].transcript_asset.id,
                    source_hash=batch_videos[0].source_hash,
                    spec=spec,
                    status="failed",
                    event_count=0,
                    warning_count=len(parse_warnings),
                    usage=usage,
                    error_message="; ".join(parse_warnings[:3]),
                )
                session.commit()
                continue

        for prepared_video in batch_videos:
            parsed_event_rows.setdefault(prepared_video.video.id, [])
            raw_events = parsed_by_alias.get(prepared_video.alias, [])
            video_valid_evidence = 0
            video_rows_before = len(parsed_event_rows[prepared_video.video.id])
            for raw in raw_events:
                evidence_rows, evidence_warnings = _evidence_rows_from_source_ids(raw=raw, source_by_id=source_by_id)
                for message in evidence_warnings:
                    warnings.append(message)
                    warning_events.append((message, {"batch": idx + 1, "video_id": str(prepared_video.video.id)}))
                if evidence_rows:
                    video_valid_evidence += len(evidence_rows)
                start, end, precision = _event_time(raw)
                event_key = _event_key(raw, start)
                seen_keys = seen_keys_by_video[prepared_video.video.id]
                if event_key in seen_keys:
                    duplicate += 1
                    continue
                seen_keys.add(event_key)
                confidence = float(raw["confidence"])
                status = "accepted" if confidence >= EVENT_ACCEPT_CONFIDENCE and evidence_rows else "draft"
                parsed_event_rows[prepared_video.video.id].append(
                    (raw, start, end, precision, event_key, status, evidence_rows)
                )
                if status == "accepted":
                    accepted += 1
                else:
                    draft += 1
            if fallback_to_single and len(batch_videos) > 1 and raw_events and video_valid_evidence <= 0:
                for _raw, _start, _end, _precision, _existing_event_key, status, _evidence_rows in parsed_event_rows[prepared_video.video.id][video_rows_before:]:
                    if status == "accepted":
                        accepted -= 1
                    else:
                        draft -= 1
                parsed_event_rows[prepared_video.video.id] = parsed_event_rows[prepared_video.video.id][:video_rows_before]
                fallback_jobs_enqueued += _enqueue_single_event_fallbacks(
                    session,
                    videos=[prepared_video],
                    force=force,
                    priority=job_priority,
                    parent_job_id=job_id,
                    reason="batch video returned events without legal provenance",
                    job=job,
                )
        _set_event_extraction_progress(job_id, _event_extraction_llm_progress(idx + 1, len(batches)))

    if job:
        raise_if_job_cancel_requested(session, job)
    performed_videos = [prepared_video for prepared_video in work if prepared_video.video.id in parsed_event_rows]
    for prepared_video in performed_videos:
        if force:
            _delete_video_event_extraction_runs(session, video_id=prepared_video.video.id)
        _delete_video_events(session, video_id=prepared_video.video.id)

    accepted_event_ids: list[uuid.UUID] = []
    inserted_by_video: dict[uuid.UUID, int] = defaultdict(int)
    for prepared_video in performed_videos:
        for raw, start, end, precision, event_key, status, evidence_rows in parsed_event_rows.get(prepared_video.video.id, []):
            confidence = float(raw["confidence"])
            event = MarketEvent(
                event_time_start=start,
                event_time_end=end,
                time_precision=precision,
                available_at=prepared_video.video.published_at or prepared_video.video.created_at,
                event_type=_normalize_key(raw.get("event_type")) or "other",
                title=_short_text(raw.get("title"), max_len=220) or None,
                summary=_short_text(raw.get("summary"), max_len=2400) or None,
                direction=_short_text(raw.get("direction"), max_len=80) or None,
                magnitude=_jsonable(raw.get("magnitude")),
                surprise_or_delta=_jsonable(raw.get("surprise_or_delta")),
                confidence=confidence,
                status=status,
                source_video_id=prepared_video.video.id,
                transcript_asset_id=prepared_video.transcript_asset.id,
                extraction_model=spec.model,
                prompt_version=spec.prompt_version,
                source_hash=prepared_video.source_hash,
                event_key=event_key,
                raw_payload=raw,
            )
            try:
                with session.begin_nested():
                    session.add(event)
                    session.flush([event])
                    _insert_event_children(
                        session,
                        event=event,
                        raw=raw,
                        video=prepared_video.video,
                        transcript_asset_id=prepared_video.transcript_asset.id,
                        evidence_rows=evidence_rows,
                    )
            except IntegrityError:
                duplicate += 1
                if status == "accepted":
                    accepted -= 1
                else:
                    draft -= 1
                continue
            inserted += 1
            inserted_by_video[prepared_video.video.id] += 1
            if status == "accepted":
                accepted_event_ids.append(event.id)

        _write_event_extraction_run(
            session,
            video_id=prepared_video.video.id,
            transcript_asset_id=prepared_video.transcript_asset.id,
            source_hash=prepared_video.source_hash,
            spec=spec,
            status="succeeded",
            event_count=inserted_by_video.get(prepared_video.video.id, 0),
            warning_count=len(warnings),
            usage=usage,
        )

    if job_id:
        for message, data in warning_events:
            session.add(JobEvent(job_id=job_id, level="warning", message=message[:1000], data=data))
    session.flush()
    session.commit()
    _set_event_extraction_progress(job_id, EVENT_EXTRACTION_PROGRESS_WRITTEN)

    embedding_jobs_enqueued += enqueue_event_embedding_jobs(
        session,
        event_ids=accepted_event_ids,
        priority=job_priority,
    )
    dirty_jobs_enqueued = 0
    for prepared_video in performed_videos:
        dirty_jobs_enqueued += schedule_playlists_event_regime_dirty_for_video(
            session,
            video_id=prepared_video.video.id,
            reason="video_events_extracted",
            source_job_id=job_id,
            priority=job_priority,
        )
    _set_event_extraction_progress(job_id, EVENT_EXTRACTION_PROGRESS_POST_PROCESSED)
    return {
        "ok": True,
        "videos": len(performed_videos),
        "cached_videos": cached,
        "skipped_rows": skipped_rows,
        "batches": len(batches),
        "inserted": inserted,
        "accepted": accepted,
        "draft": draft,
        "duplicate": duplicate,
        "fallback_jobs_enqueued": fallback_jobs_enqueued,
        "warnings": warnings[:50],
        "usage": usage,
        "embedding_jobs_enqueued": embedding_jobs_enqueued,
        "dirty_jobs_enqueued": dirty_jobs_enqueued,
    }


def extract_video_events(session: Session, *, video_id: uuid.UUID, force: bool = False, job: Job | None = None) -> dict[str, Any]:
    return extract_video_events_batch(
        session,
        video_ids=[video_id],
        force=force,
        job=job,
        fallback_to_single=False,
    )


def schedule_video_event_extraction(
    session: Session,
    *,
    video_id: uuid.UUID,
    force: bool = False,
    priority: int = 0,
    parent_job_id: str | None = None,
) -> uuid.UUID | None:
    if not bool(settings.auto_extract_new_video_events):
        return None
    if not llm_enabled():
        return None
    return enqueue_job(
        session,
        type_="video.extract_events",
        params={"video_id": str(video_id), "force": force},
        priority=priority,
        parent_job_id=parent_job_id,
    )


def _playlist_video_id_texts(playlist_id: uuid.UUID):
    return (
        select(cast(Video.id, String))
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .where(PlaylistMedia.playlist_id == playlist_id)
    )


def _playlist_event_id_texts(playlist_id: uuid.UUID):
    return (
        select(cast(MarketEvent.id, String))
        .join(Video, Video.id == MarketEvent.source_video_id)
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .where(PlaylistMedia.playlist_id == playlist_id)
    )


def _active_playlist_event_pipeline_jobs(session: Session, playlist_id: uuid.UUID) -> list[Job]:
    playlist_id_text = str(playlist_id)
    video_ids = _playlist_video_id_texts(playlist_id)
    event_ids = _playlist_event_id_texts(playlist_id)
    range_job_ids = (
        select(Job.id)
        .where(
            Job.type == "playlist.backfill_events_range",
            Job.params["playlist_id"].as_string() == playlist_id_text,
        )
    )
    stmt = (
        select(Job)
        .where(
            Job.status.in_(_EVENT_PIPELINE_ACTIVE_JOB_STATUSES),
            or_(
                and_(
                    Job.type.in_(["playlist.backfill_events", "playlist.backfill_events_range"]),
                    Job.params["playlist_id"].as_string() == playlist_id_text,
                ),
                and_(
                    Job.type == "video.extract_events",
                    Job.params["video_id"].as_string().in_(video_ids),
                ),
                and_(
                    Job.type == "video.extract_events_batch",
                    Job.parent_job_id.in_(range_job_ids),
                ),
                and_(
                    Job.type == "event.embed",
                    Job.params["event_id"].as_string().in_(event_ids),
                ),
                and_(
                    Job.type.in_(["playlist.mark_event_regime_dirty", "playlist.build_event_regime_snapshot"]),
                    Job.params["playlist_id"].as_string() == playlist_id_text,
                ),
            ),
        )
        .order_by(Job.created_at.asc(), Job.id.asc())
    )
    return list(session.execute(stmt).scalars().all())


def cancel_playlist_event_pipeline_jobs(session: Session, playlist_id: uuid.UUID, *, reason: str) -> dict[str, int]:
    jobs = _active_playlist_event_pipeline_jobs(session, playlist_id)
    canceled = 0
    cancel_requested = 0
    for job in jobs:
        action = request_job_cancel(session, job, reason=reason)
        if action == "canceled":
            canceled += 1
        elif action in {"requested", "already_requested"}:
            cancel_requested += 1

    now = utcnow()
    runs = (
        session.execute(
            select(EventRegimeRun).where(
                EventRegimeRun.playlist_id == playlist_id,
                EventRegimeRun.status.in_(["pending", "running"]),
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        run.status = "canceled"
        run.finished_at = now
        run.updated_at = now

    state = ensure_event_regime_state(session, playlist_id)
    state.analysis_dirty = True
    state.updated_at = now
    session.flush()
    return {
        "jobs": len(jobs),
        "canceled": canceled,
        "cancel_requested": cancel_requested,
        "regime_runs_canceled": len(runs),
    }


def request_playlist_event_backfill(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    force: bool = False,
    priority: int = 0,
) -> Job:
    if force:
        cancel_playlist_event_pipeline_jobs(session, playlist_id, reason="playlist_event_force_extract")
    job_id = enqueue_job(
        session,
        type_="playlist.backfill_events",
        params={"playlist_id": str(playlist_id), "force": bool(force)},
        priority=priority,
    )
    job = session.get(Job, job_id)
    if not job:
        raise RuntimeError("failed to enqueue playlist.backfill_events")
    return job


def _parse_event_backfill_range_date(value: Any, *, name: str) -> date:
    try:
        parsed = date.fromisoformat(str(value or "").strip()[:10])
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc
    return parsed


def _playlist_timeline_bounds(session: Session, playlist_id: uuid.UUID) -> tuple[Any | None, Any | None]:
    timeline_at = timeline_time_expr(Video, time_basis="content")
    row = session.execute(
        select(func.min(timeline_at), func.max(timeline_at))
        .select_from(Video)
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .where(PlaylistMedia.playlist_id == playlist_id, timeline_at.is_not(None))
    ).one()
    return row[0], row[1]


def backfill_playlist_events(session: Session, *, playlist_id: uuid.UUID, force: bool = False, job: Job | None = None) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}

    start_at, end_at = _playlist_timeline_bounds(session, playlist_id)
    start_date = local_date(start_at)
    end_date = local_date(end_at)
    if not start_date or not end_date:
        if job:
            set_job_progress(job_id=job.id, current=0, total=0)
        return {"ok": True, "playlist_id": str(playlist_id), "range_jobs": 0, "enqueued": 0, "skipped": 0}

    ranges = [(month_start, month_add_one(month_start)) for month_start in iter_period_starts(start_date, end_date, "month")]
    total = len(ranges)
    enqueued = 0
    for idx, (range_start, range_end) in enumerate(ranges):
        if job:
            raise_if_job_cancel_requested(session, job)
            set_job_progress(job_id=job.id, current=idx, total=total)
        enqueue_job(
            session,
            type_="playlist.backfill_events_range",
            params={
                "playlist_id": str(playlist_id),
                "force": bool(force),
                "range_start": range_start.isoformat(),
                "range_end": range_end.isoformat(),
            },
            priority=getattr(job, "priority", 0) if job else 0,
            parent_job_id=str(job.id) if job else None,
        )
        enqueued += 1
    if job:
        set_job_progress(job_id=job.id, current=total, total=total)
    return {
        "ok": True,
        "playlist_id": str(playlist_id),
        "mode": "monthly_ranges",
        "range_start": start_date.isoformat(),
        "range_end": month_add_one(date(end_date.year, end_date.month, 1)).isoformat(),
        "range_jobs": enqueued,
        "enqueued": enqueued,
        "skipped": 0,
        "force": bool(force),
    }


def backfill_playlist_events_range(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    range_start: date,
    range_end: date,
    force: bool = False,
    job: Job | None = None,
) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}
    if range_start >= range_end:
        raise ValueError("range_start must be before range_end")

    start_at, _ = period_bounds_utc(range_start, "day")
    end_at, _ = period_bounds_utc(range_end, "day")
    timeline_at = timeline_time_expr(Video, time_basis="content")
    has_plain_transcript = (
        select(Asset.id)
        .where(
            Asset.video_id == Video.id,
            Asset.type == "transcript",
            Asset.format == "txt",
            Asset.variant == "plain",
        )
        .limit(1)
        .exists()
    )
    rows = (
        session.execute(
            select(Video.id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(
                PlaylistMedia.playlist_id == playlist_id,
                timeline_at >= start_at,
                timeline_at < end_at,
                has_plain_transcript,
            )
            .order_by(timeline_at.asc(), Video.created_at.asc(), Video.id.asc())
        )
        .scalars()
        .all()
    )
    total = len(rows)
    enqueued = 0
    skipped = 0
    batch_enqueued = 0
    single_enqueued = 0
    range_priority = int(getattr(job, "priority", 0) or 0) if job else 0
    video_extract_priority = range_priority + 1
    spec = None if force else event_extraction_spec(session)
    pending_batch: list[tuple[uuid.UUID, int]] = []
    pending_batch_chars = 0

    def _flush_batch() -> None:
        nonlocal batch_enqueued, enqueued, pending_batch, pending_batch_chars
        if not pending_batch:
            return
        enqueue_job(
            session,
            type_="video.extract_events_batch",
            params={"video_ids": [str(video_id) for video_id, _source_chars in pending_batch], "force": bool(force)},
            priority=video_extract_priority,
            parent_job_id=str(job.id) if job else None,
        )
        batch_enqueued += 1
        enqueued += len(pending_batch)
        pending_batch = []
        pending_batch_chars = 0

    for idx, video_id in enumerate(rows):
        if job:
            raise_if_job_cancel_requested(session, job)
            set_job_progress(job_id=job.id, current=idx, total=total)
        video = session.get(Video, video_id)
        if not video:
            skipped += 1
            continue
        transcript_asset = pick_transcript_asset(session, video_id, variant="plain")
        if not transcript_asset:
            skipped += 1
            continue
        transcript_text, _ = read_text_asset(transcript_asset)
        if not transcript_text.strip():
            skipped += 1
            continue
        media = session.get(Media, video.media_id) if video.media_id else None
        content_time = resolve_video_timeline_static(video)
        source_hash = _event_source_hash(
            video=video,
            media=media,
            transcript_asset_id=transcript_asset.id,
            transcript_text=transcript_text,
            content_published_at=content_time,
        )
        if not force and spec is not None and _succeeded_event_extraction_run(
            session,
            video_id=video_id,
            source_hash=source_hash,
            spec=spec,
        ):
            skipped += 1
            continue
        source_chars = (
            len(str(video.title or ""))
            + len(str(video.description or "")[:EVENT_DESCRIPTION_SOURCE_MAX_CHARS])
            + len(transcript_text)
        )
        if source_chars > EVENT_BATCH_SINGLE_MAX_SOURCE_CHARS:
            _flush_batch()
            enqueue_job(
                session,
                type_="video.extract_events",
                params={"video_id": str(video_id), "force": bool(force)},
                priority=video_extract_priority,
                parent_job_id=str(job.id) if job else None,
            )
            single_enqueued += 1
            enqueued += 1
            continue
        if (
            len(pending_batch) >= EVENT_BATCH_MAX_VIDEOS
            or pending_batch_chars + source_chars > EVENT_BATCH_MAX_SOURCE_CHARS
        ):
            _flush_batch()
        pending_batch.append((video_id, source_chars))
        pending_batch_chars += source_chars
    _flush_batch()
    if job:
        set_job_progress(job_id=job.id, current=total, total=total)
    return {
        "ok": True,
        "playlist_id": str(playlist_id),
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "scanned": total,
        "enqueued": enqueued,
        "batch_jobs_enqueued": batch_enqueued,
        "single_jobs_enqueued": single_enqueued,
        "skipped": skipped,
        "force": bool(force),
    }


def _event_embedding_text(session: Session, event: MarketEvent) -> str:
    entities = (
        session.execute(
            select(MarketEventEntity).where(MarketEventEntity.event_id == event.id).order_by(MarketEventEntity.entity_type.asc(), MarketEventEntity.name.asc())
        )
        .scalars()
        .all()
    )
    entity_text = "；".join(
        f"{entity.entity_type}:{entity.name}:{entity.role or ''}" for entity in entities[:40]
    )
    payload = {
        "event_type": event.event_type,
        "event_time": event.event_time_start.isoformat() if event.event_time_start else "",
        "title": event.title or "",
        "summary": event.summary or "",
        "direction": event.direction or "",
        "magnitude": event.magnitude or {},
        "surprise_or_delta": event.surprise_or_delta or {},
        "entities": entity_text,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def embed_event(session: Session, *, event_id: uuid.UUID) -> dict[str, Any]:
    if not embedding_enabled():
        return {"skipped": "embedding service not configured"}
    event = session.get(MarketEvent, event_id)
    if not event:
        return {"skipped": "event not found"}
    if event.status != "accepted":
        return {"skipped": f"event status is {event.status}"}

    spec = embedding_spec()
    text = _event_embedding_text(session, event)
    checksum = _sha256_text(text)
    existing = session.execute(
        select(MarketEventEmbedding).where(
            MarketEventEmbedding.event_id == event_id,
            MarketEventEmbedding.embedding_model == spec.model,
            MarketEventEmbedding.embedding_dim == spec.dim,
        )
    ).scalar_one_or_none()
    if existing and existing.status == "ready" and existing.text_checksum == checksum and existing.vector:
        return {"ok": True, "cached": True, "embedding_id": str(existing.id)}
    if not existing:
        existing = MarketEventEmbedding(
            event_id=event_id,
            embedding_model=spec.model,
            embedding_dim=spec.dim,
            status="pending",
            text_checksum=checksum,
        )
        session.add(existing)
        session.flush([existing])

    previous_status = existing.status
    previous_checksum = existing.text_checksum
    try:
        vector = embed_text(text)
        existing.status = "ready"
        existing.vector = vector
        existing.text_checksum = checksum
        existing.skip_reason = None
        existing.generated_at = utcnow()
    except EmbeddingOverBudgetError as exc:
        existing.status = "skipped_over_budget"
        existing.vector = None
        existing.text_checksum = checksum
        existing.skip_reason = str(exc)[:500]
        existing.generated_at = utcnow()
    except EmbeddingTransientError:
        raise
    except EmbeddingError as exc:
        existing.status = "failed"
        existing.vector = None
        existing.text_checksum = checksum
        existing.skip_reason = str(exc)[:500]
        existing.generated_at = utcnow()

    session.flush([existing])
    if previous_status != existing.status or previous_checksum != checksum:
        schedule_playlists_event_regime_dirty_for_video(
            session,
            video_id=event.source_video_id,
            reason="event_embedding_changed",
        )
    return {"ok": True, "status": existing.status, "embedding_id": str(existing.id)}


def ensure_event_regime_state(session: Session, playlist_id: uuid.UUID) -> EventRegimeState:
    state = session.get(EventRegimeState, playlist_id)
    if state:
        return state
    state = EventRegimeState(playlist_id=playlist_id, analysis_dirty=False)
    session.add(state)
    session.flush([state])
    return state


def mark_playlist_event_regime_dirty(session: Session, playlist_id: uuid.UUID, *, changed_at: datetime | None = None) -> None:
    state = ensure_event_regime_state(session, playlist_id)
    state.analysis_dirty = True
    state.updated_at = changed_at or utcnow()


def mark_playlist_event_regime_dirty_if_needed(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    changed_at: datetime | None = None,
) -> dict[str, Any]:
    state = session.get(EventRegimeState, playlist_id)
    if state and state.analysis_dirty:
        return {"ok": True, "playlist_id": str(playlist_id), "dirty": False, "already_dirty": True}

    now = changed_at or utcnow()
    if state:
        state.analysis_dirty = True
        state.updated_at = now
    else:
        session.add(EventRegimeState(playlist_id=playlist_id, analysis_dirty=True, updated_at=now))
    session.flush()
    return {"ok": True, "playlist_id": str(playlist_id), "dirty": True, "already_dirty": False}


def schedule_playlist_event_regime_dirty(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    reason: str,
    source_video_id: uuid.UUID | None = None,
    source_job_id: uuid.UUID | None = None,
    priority: int = 0,
    delay_seconds: int = EVENT_REGIME_DIRTY_DEBOUNCE_SECONDS,
) -> uuid.UUID:
    params: dict[str, Any] = {
        "playlist_id": str(playlist_id),
        "reason": str(reason or "").strip() or "event_regime_dirty",
    }
    if source_video_id:
        params["source_video_id"] = str(source_video_id)
    if source_job_id:
        params["source_job_id"] = str(source_job_id)
    return enqueue_job(
        session,
        type_="playlist.mark_event_regime_dirty",
        params=params,
        priority=priority,
        scheduled_for=utcnow() + timedelta(seconds=max(0, int(delay_seconds or 0))),
    )


def schedule_playlists_event_regime_dirty_for_video(
    session: Session,
    *,
    video_id: uuid.UUID,
    reason: str,
    source_job_id: uuid.UUID | None = None,
    priority: int = 0,
    delay_seconds: int = EVENT_REGIME_DIRTY_DEBOUNCE_SECONDS,
) -> int:
    video = session.get(Video, video_id)
    if not video:
        return 0
    playlist_ids = (
        session.execute(
            select(PlaylistMedia.playlist_id)
            .where(PlaylistMedia.media_id == video.media_id)
            .order_by(PlaylistMedia.playlist_id.asc())
        )
        .scalars()
        .all()
    )
    enqueued = 0
    for playlist_id in list(dict.fromkeys(playlist_ids)):
        schedule_playlist_event_regime_dirty(
            session,
            playlist_id=playlist_id,
            reason=reason,
            source_video_id=video_id,
            source_job_id=source_job_id,
            priority=priority,
            delay_seconds=delay_seconds,
        )
        enqueued += 1
    return enqueued


def mark_playlists_event_regime_dirty_for_video(session: Session, video_id: uuid.UUID, *, changed_at: datetime | None = None) -> None:
    video = session.get(Video, video_id)
    if not video:
        return
    playlist_ids = (
        session.execute(
            select(PlaylistMedia.playlist_id)
            .where(PlaylistMedia.media_id == video.media_id)
            .order_by(PlaylistMedia.playlist_id.asc())
        )
        .scalars()
        .all()
    )
    for playlist_id in list(dict.fromkeys(playlist_ids)):
        mark_playlist_event_regime_dirty(session, playlist_id, changed_at=changed_at)


def active_event_regime_run(session: Session, playlist_id: uuid.UUID) -> EventRegimeRun | None:
    return (
        session.execute(
            select(EventRegimeRun)
            .where(EventRegimeRun.playlist_id == playlist_id, EventRegimeRun.status.in_(["pending", "running"]))
            .order_by(EventRegimeRun.created_at.desc(), EventRegimeRun.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def pending_event_regime_job(session: Session, playlist_id: uuid.UUID) -> Job | None:
    return (
        session.execute(
            select(Job)
            .where(
                Job.type == "playlist.build_event_regime_snapshot",
                Job.status.in_(["pending", "running"]),
                Job.params["playlist_id"].as_string() == str(playlist_id),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def request_event_regime_rebuild(session: Session, playlist_id: uuid.UUID, *, priority: int = 0) -> EventRegimeRun:
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        raise ValueError("playlist not found")
    spec = embedding_spec()
    state = ensure_event_regime_state(session, playlist_id)
    run = active_event_regime_run(session, playlist_id)
    if run is None:
        run = EventRegimeRun(
            playlist_id=playlist_id,
            status="pending",
            analysis_clock="day",
            embedding_model=spec.model,
            embedding_dim=spec.dim,
        )
        session.add(run)
        session.flush([run])
    state.analysis_dirty = True
    state.last_requested_at = utcnow()
    state.updated_at = state.last_requested_at
    enqueue_job(
        session,
        type_="playlist.build_event_regime_snapshot",
        params={"playlist_id": str(playlist_id), "regime_run_id": str(run.id)},
        priority=priority,
    )
    return run


def _cosine_distance(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return 1.0 - max(-1.0, min(1.0, dot / (na * nb)))


def _centroid(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    dim = len(vectors[0])
    if dim <= 0:
        return None
    return [sum(vector[i] for vector in vectors) / len(vectors) for i in range(dim)]


def _period_start(value: date, granularity: str) -> date:
    if granularity == "week":
        return value - timedelta(days=value.weekday())
    if granularity == "month":
        return date(value.year, value.month, 1)
    return value


def _event_regime_period_date(event: MarketEvent, granularity: str) -> date | None:
    if not event.available_at:
        return None
    return _period_start(event.available_at.date(), granularity)


def _rolling_z(values: list[float | None], idx: int, window: int) -> tuple[float | None, float | None, float | None]:
    previous = [v for v in values[max(0, idx - window) : idx] if v is not None]
    current = values[idx]
    if current is None or len(previous) < 3:
        return None, None, None
    mean = statistics.fmean(previous)
    std = statistics.pstdev(previous) if len(previous) > 1 else 0.0
    if std <= 1e-9:
        return mean, std, 0.0
    return mean, std, (current - mean) / std


def _pctl(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[idx]


def _event_rows_for_regime(session: Session, playlist_id: uuid.UUID) -> list[tuple[MarketEvent, MarketEventEmbedding]]:
    spec = embedding_spec()
    return (
        session.execute(
            select(MarketEvent, MarketEventEmbedding)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .join(
                MarketEventEmbedding,
                and_(
                    MarketEventEmbedding.event_id == MarketEvent.id,
                    MarketEventEmbedding.embedding_model == spec.model,
                    MarketEventEmbedding.embedding_dim == spec.dim,
                ),
            )
            .where(
                PlaylistMedia.playlist_id == playlist_id,
                MarketEvent.status == "accepted",
                MarketEvent.event_time_start.is_not(None),
                MarketEvent.available_at.is_not(None),
                MarketEventEmbedding.status == "ready",
                MarketEventEmbedding.vector.is_not(None),
            )
            .order_by(MarketEvent.available_at.asc(), MarketEvent.event_time_start.asc(), MarketEvent.id.asc())
        )
        .all()
    )


def build_event_regime_snapshot(session: Session, *, playlist_id: uuid.UUID, job: Job | None = None) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}

    spec = embedding_spec()
    state = ensure_event_regime_state(session, playlist_id)
    run_id = None
    if job and isinstance(job.params, dict) and job.params.get("regime_run_id"):
        try:
            run_id = uuid.UUID(str(job.params["regime_run_id"]))
        except Exception:
            run_id = None
    run = session.get(EventRegimeRun, run_id) if run_id else active_event_regime_run(session, playlist_id)
    if run is None:
        run = EventRegimeRun(
            playlist_id=playlist_id,
            status="pending",
            analysis_clock="day",
            embedding_model=spec.model,
            embedding_dim=spec.dim,
        )
        session.add(run)
        session.flush([run])

    def raise_if_snapshot_cancel_requested() -> None:
        if not job:
            return
        try:
            raise_if_job_cancel_requested(session, job)
        except JobCancelRequested:
            finished_at = utcnow()
            run.status = "canceled"
            run.finished_at = finished_at
            run.updated_at = finished_at
            state.analysis_dirty = True
            state.updated_at = finished_at
            session.flush([run, state])
            raise

    started = utcnow()
    run.status = "running"
    run.started_at = started
    run.embedding_model = spec.model
    run.embedding_dim = spec.dim
    state.last_error = None
    session.flush([run, state])
    raise_if_snapshot_cancel_requested()

    rows = _event_rows_for_regime(session, playlist_id)
    accepted_total = session.execute(
        select(func.count())
        .select_from(MarketEvent)
        .join(Video, Video.id == MarketEvent.source_video_id)
        .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
        .where(PlaylistMedia.playlist_id == playlist_id, MarketEvent.status == "accepted")
    ).scalar_one()
    run.event_total = int(accepted_total or 0)
    run.event_embedded = len(rows)
    run.event_skipped = max(0, run.event_total - run.event_embedded)

    session.execute(delete(EventRegimeSignal).where(EventRegimeSignal.regime_run_id == run.id))
    session.execute(delete(EventRegimeCandidate).where(EventRegimeCandidate.regime_run_id == run.id))
    if job:
        set_job_progress(job_id=job.id, current=0, total=max(1, len(rows)))

    signals_by_key: dict[tuple[str, date], EventRegimeSignal] = {}
    for granularity in EVENT_REGIME_GRANULARITIES:
        raise_if_snapshot_cancel_requested()
        grouped: dict[date, list[tuple[MarketEvent, list[float]]]] = defaultdict(list)
        for event, embedding in rows:
            vector = [float(value) for value in (embedding.vector or [])]
            period_date = _event_regime_period_date(event, granularity)
            if not vector or period_date is None:
                continue
            grouped[period_date].append((event, vector))
        period_dates = sorted(grouped)
        centroids: list[list[float] | None] = []
        drifts: list[float | None] = []
        dispersions: list[float | None] = []
        for idx, period_date in enumerate(period_dates):
            items = grouped[period_date]
            centroid = _centroid([vector for _, vector in items])
            centroids.append(centroid)
            previous = centroids[idx - 1] if idx > 0 else None
            drift = _cosine_distance(centroid, previous) if centroid and previous else None
            drifts.append(drift)
            dispersion_values = [_cosine_distance(vector, centroid) for _, vector in items if centroid]
            dispersions.append(statistics.fmean(dispersion_values) if dispersion_values else None)

        window = EVENT_REGIME_WINDOWS[granularity]
        for idx, period_date in enumerate(period_dates):
            items = grouped[period_date]
            centroid = centroids[idx]
            dispersion_values = [_cosine_distance(vector, centroid) for _, vector in items if centroid]
            mean, std, z = _rolling_z(drifts, idx, window)
            signal = EventRegimeSignal(
                regime_run_id=run.id,
                granularity=granularity,
                period_date=period_date,
                rolling_window=window,
                event_count=len(items),
                ready_embedding_count=len(items),
                centroid_vector=centroid,
                drift_score=drifts[idx],
                drift_rolling_mean=mean,
                drift_rolling_std=std,
                drift_rolling_z=z,
                dispersion_mean=dispersions[idx],
                dispersion_std=statistics.pstdev(dispersion_values) if len(dispersion_values) > 1 else None,
                dispersion_p25=_pctl(dispersion_values, 0.25),
                dispersion_p75=_pctl(dispersion_values, 0.75),
                projection_id=f"{run.id}:timeline",
                projection_method="event_centroid_index_v1",
                projection_x=float(idx),
                projection_y=drifts[idx] if drifts[idx] is not None else 0.0,
                projection_z=dispersions[idx] if dispersions[idx] is not None else 0.0,
                projection_explained_variance_ratio=[],
            )
            session.add(signal)
            signals_by_key[(granularity, period_date)] = signal
        if job:
            set_job_progress(job_id=job.id, current=min(len(rows), len(rows)), total=max(1, len(rows)))

    session.flush()

    candidate_models: list[EventRegimeCandidate] = []
    candidate_by_date: dict[date, EventRegimeCandidate] = {}
    for (granularity, period_date), signal in sorted(signals_by_key.items(), key=lambda item: (item[0][0], item[0][1])):
        raise_if_snapshot_cancel_requested()
        if granularity == "day":
            continue
        z = signal.drift_rolling_z
        if z is None or z < 2.0 or signal.event_count <= 0:
            continue
        existing_candidate = candidate_by_date.get(period_date)
        if existing_candidate:
            evidence = existing_candidate.evidence_json if isinstance(existing_candidate.evidence_json, dict) else {}
            support = {str(item) for item in (evidence.get("supporting_granularities") or []) if str(item).strip()}
            support.add(str(evidence.get("granularity") or "").strip())
            support.add(granularity)
            evidence["supporting_granularities"] = sorted(item for item in support if item)
            existing_candidate.evidence_json = evidence
            if z <= float(existing_candidate.score or 0.0):
                continue
        period_events = [
            event
            for event, _embedding in rows
            if _event_regime_period_date(event, granularity) == period_date
        ]
        event_ids = [str(event.id) for event in period_events[:20]]
        video_ids = sorted({str(event.source_video_id) for event in period_events[:20]})
        summaries = [event.title or event.summary or event.event_type for event in period_events[:5]]
        candidate = EventRegimeCandidate(
            regime_run_id=run.id,
            candidate_date=period_date,
            effective_trade_date=period_date,
            peak_date=period_date,
            event_start=period_date,
            event_end=period_date,
            event_type="event_regime_shift",
            status="draft",
            score=float(z),
            confidence=max(0.0, min(1.0, z / 5.0)),
            uncertainty=max(0.0, 1.0 - min(1.0, z / 5.0)),
            drift_score=signal.drift_score,
            dispersion_score=signal.dispersion_mean,
            drift_rolling_z=z,
            summary="；".join(summaries)[:1500],
            top_terms=[],
            evidence_event_ids=event_ids,
            evidence_video_ids=video_ids,
            evidence_json={"granularity": granularity, "supporting_granularities": [granularity], "event_count": signal.event_count},
            available_at=min([event.available_at for event in period_events if event.available_at] or [None]),
        )
        if existing_candidate:
            existing_candidate.event_type = candidate.event_type
            existing_candidate.score = candidate.score
            existing_candidate.confidence = candidate.confidence
            existing_candidate.uncertainty = candidate.uncertainty
            existing_candidate.drift_score = candidate.drift_score
            existing_candidate.dispersion_score = candidate.dispersion_score
            existing_candidate.drift_rolling_z = candidate.drift_rolling_z
            existing_candidate.summary = candidate.summary
            existing_candidate.evidence_event_ids = candidate.evidence_event_ids
            existing_candidate.evidence_video_ids = candidate.evidence_video_ids
            evidence = candidate.evidence_json if isinstance(candidate.evidence_json, dict) else {}
            previous = existing_candidate.evidence_json if isinstance(existing_candidate.evidence_json, dict) else {}
            support = {str(item) for item in (previous.get("supporting_granularities") or []) if str(item).strip()}
            support.add(granularity)
            evidence["supporting_granularities"] = sorted(item for item in support if item)
            existing_candidate.evidence_json = evidence
            existing_candidate.available_at = candidate.available_at
            continue
        session.add(candidate)
        candidate_models.append(candidate)
        candidate_by_date[period_date] = candidate
    session.flush(candidate_models)
    for candidate in candidate_models:
        raise_if_snapshot_cancel_requested()
        for granularity in ("week", "month"):
            signal = signals_by_key.get((granularity, candidate.candidate_date))
            if signal:
                signal.linked_candidate_id = candidate.id

    raise_if_snapshot_cancel_requested()
    finished = utcnow()
    run.status = "ready"
    run.finished_at = finished
    state.analysis_dirty = False
    state.last_ready_run_id = run.id
    state.last_built_at = finished
    state.updated_at = finished
    session.flush()
    if job:
        set_job_progress(job_id=job.id, current=max(1, len(rows)), total=max(1, len(rows)))
    return {
        "ok": True,
        "playlist_id": str(playlist_id),
        "run_id": str(run.id),
        "event_total": run.event_total,
        "event_embedded": run.event_embedded,
        "signal_count": len(signals_by_key),
        "candidate_count": len(candidate_models),
    }


def update_event_status(session: Session, *, event_id: uuid.UUID, status: str) -> MarketEvent:
    event = session.get(MarketEvent, event_id)
    if not event:
        raise ValueError("event not found")
    normalized = str(status or "").strip().lower()
    if normalized not in {"accepted", "draft", "rejected"}:
        raise ValueError("status must be one of: accepted, draft, rejected")
    previous = event.status
    event.status = normalized
    event.updated_at = utcnow()
    if previous != normalized:
        schedule_playlists_event_regime_dirty_for_video(
            session,
            video_id=event.source_video_id,
            reason="event_status_changed",
        )
        if normalized == "accepted":
            enqueue_job(session, type_="event.embed", params={"event_id": str(event.id)}, priority=0)
    return event


def playlist_event_coverage(
    session: Session,
    playlist_id: uuid.UUID,
    *,
    available_start: Any | None = None,
    available_end: Any | None = None,
) -> dict[str, Any]:
    video_clauses: list[Any] = [PlaylistMedia.playlist_id == playlist_id]
    event_clauses: list[Any] = [PlaylistMedia.playlist_id == playlist_id]
    if available_start is not None:
        video_clauses.append(func.coalesce(Video.published_at, Video.created_at) >= available_start)
        event_clauses.append(MarketEvent.available_at >= available_start)
    if available_end is not None:
        video_clauses.append(func.coalesce(Video.published_at, Video.created_at) < available_end)
        event_clauses.append(MarketEvent.available_at < available_end)

    video_total = int(
        session.execute(
            select(func.count())
            .select_from(Video)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(*video_clauses)
        ).scalar_one()
        or 0
    )
    video_with_event = int(
        session.execute(
            select(func.count(func.distinct(MarketEvent.source_video_id)))
            .select_from(MarketEvent)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(*event_clauses)
        ).scalar_one()
        or 0
    )
    status_rows = (
        session.execute(
            select(MarketEvent.status, func.count())
            .select_from(MarketEvent)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(*event_clauses)
            .group_by(MarketEvent.status)
        )
        .all()
    )
    by_status = {str(status): int(count or 0) for status, count in status_rows}
    return {
        "video_total": video_total,
        "video_with_events": video_with_event,
        "event_total": sum(by_status.values()),
        "accepted": by_status.get("accepted", 0),
        "draft": by_status.get("draft", 0),
        "rejected": by_status.get("rejected", 0),
        "coverage_ratio": (video_with_event / video_total) if video_total else 0.0,
    }


def event_filter_clause(
    *,
    playlist_id: uuid.UUID,
    status: str | None = None,
    event_type: str | None = None,
    entity: str | None = None,
    min_confidence: float | None = None,
    available_start: Any | None = None,
    available_end: Any | None = None,
) -> list[Any]:
    clauses: list[Any] = [PlaylistMedia.playlist_id == playlist_id]
    if status:
        clauses.append(MarketEvent.status == status)
    if event_type:
        clauses.append(MarketEvent.event_type == event_type)
    if min_confidence is not None:
        clauses.append(MarketEvent.confidence >= min_confidence)
    if available_start is not None:
        clauses.append(MarketEvent.available_at >= available_start)
    if available_end is not None:
        clauses.append(MarketEvent.available_at < available_end)
    if entity:
        normalized = f"%{_normalize_key(entity)}%"
        clauses.append(
            MarketEvent.id.in_(
                select(MarketEventEntity.event_id).where(
                    or_(
                        MarketEventEntity.normalized_key.ilike(normalized),
                        MarketEventEntity.name.ilike(f"%{entity}%"),
                    )
                )
            )
        )
    return clauses
