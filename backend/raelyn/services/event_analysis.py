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

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_job
from raelyn.jobs.progress import set_job_progress
from raelyn.models import (
    AppConfig,
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
from raelyn.services.job_cancellation import raise_if_job_cancel_requested
from raelyn.services.llm import llm_enabled, llm_generate
from raelyn.services.transcripts import pick_transcript_asset, read_text_asset
from raelyn.services.video_time import resolve_video_timeline
from raelyn.timeutil import utcnow


EVENT_EXTRACTION_PROMPT_CONFIG_KEY = "llm_event_extraction_prompt"
EVENT_EXTRACTION_PROMPT_BASE_VERSION = "llm_event_v1"
EVENT_ACCEPT_CONFIDENCE = 0.8
EVENT_REGIME_GRANULARITIES = ("day", "week", "month")
EVENT_REGIME_WINDOWS = {"day": 20, "week": 12, "month": 12}


DEFAULT_EVENT_EXTRACTION_PROMPT = """
你是金融市场事件抽取器。请只输出 JSON，不要 Markdown，不要解释。

任务：从一段财经视频转写中抽取会影响市场 regime 分析的原子事件。忽略寒暄、主持人串场、重复免责声明、泛泛评论和没有明确事实支撑的预测。

输出格式：
{
  "events": [
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
      "summary": "1-3 句话，保留可验证事实粒度",
      "entities": [{"type": "company|person|country|institution|indicator|asset|sector|other", "name": "...", "role": "actor|affected|indicator|source|other", "confidence": 0.0}],
      "assets": [{"name": "...", "role": "affected|signal|other", "confidence": 0.0}],
      "sectors": [{"name": "...", "role": "affected|signal|other", "confidence": 0.0}],
      "macro_variables": [{"name": "...", "role": "indicator|affected|cause|other", "confidence": 0.0}],
      "direction": "positive|negative|mixed|neutral|unknown",
      "magnitude": {"value": null, "unit": "", "description": ""},
      "surprise_or_delta": {"value": null, "unit": "", "description": ""},
      "cause_effect_chain": [{"cause": "...", "effect": "...", "relation_type": "cause|effect|affects|mentions", "direction": "positive|negative|mixed|neutral|unknown", "magnitude": {"description": ""}, "confidence": 0.0, "evidence_text": "..."}],
      "evidence_quotes": [{"text": "转写中的短证据原文", "confidence": 0.0}],
      "confidence": 0.0
    }
  ]
}

约束：
- 相对时间必须基于输入中的“视频内容时间”解析；无法解析时 time_precision 必须为 "unknown"。
- evidence_quotes 必须来自输入 transcript chunk，不能编造。
- 单个事件只表达一个事实变化；同一事实的原因、影响可以放入 cause_effect_chain。
- confidence 表示该事件是否由当前视频证据充分支持。
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
        return datetime(int(text), 1, 1, tzinfo=timezone.utc)
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = [int(part) for part in text.split("-", 1)]
        return datetime(year, month, 1, tzinfo=timezone.utc)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.combine(date.fromisoformat(text), time.min, tzinfo=timezone.utc)
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


def _event_source_hash(*, video: Video, media: Media | None, transcript_asset_id: uuid.UUID, transcript_text: str) -> str:
    timeline = resolve_video_timeline_static(video)
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
    video: Video,
    media: Media | None,
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


def _insert_event_children(session: Session, *, event: MarketEvent, raw: dict[str, Any], video: Video, transcript_asset_id: uuid.UUID) -> None:
    quote_rows = raw.get("evidence_quotes")
    if not isinstance(quote_rows, list):
        quote_rows = []
    if not quote_rows:
        text = _short_text(raw.get("evidence_text") or raw.get("summary"), max_len=1200)
        quote_rows = [{"text": text, "confidence": raw.get("confidence")}] if text else []
    for idx, quote in enumerate(quote_rows[:12]):
        if isinstance(quote, dict):
            evidence_text = _short_text(quote.get("text") or quote.get("quote"), max_len=1200)
            confidence = _safe_float(quote.get("confidence"))
            evidence_json = quote
        else:
            evidence_text = _short_text(quote, max_len=1200)
            confidence = _safe_float(raw.get("confidence"))
            evidence_json = {"text": evidence_text}
        if not evidence_text:
            continue
        session.add(
            MarketEventEvidence(
                event_id=event.id,
                video_id=video.id,
                transcript_asset_id=transcript_asset_id,
                evidence_key=_sha256_text(f"{idx}:{evidence_text}")[:24],
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


def extract_video_events(session: Session, *, video_id: uuid.UUID, force: bool = False, job: Job | None = None) -> dict[str, Any]:
    if not llm_enabled():
        return {"skipped": "llm not configured"}

    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}
    media = session.get(Media, video.media_id) if video.media_id else None
    transcript_asset = pick_transcript_asset(session, video_id, variant="plain")
    if not transcript_asset:
        return {"skipped": "plain transcript not found"}

    transcript_text, _ = read_text_asset(transcript_asset)
    if not transcript_text.strip():
        return {"skipped": "empty transcript"}

    spec = event_extraction_spec(session)
    source_hash = _event_source_hash(video=video, media=media, transcript_asset_id=transcript_asset.id, transcript_text=transcript_text)
    if not force:
        exists = session.execute(
            select(MarketEvent.id)
            .where(
                MarketEvent.source_video_id == video_id,
                MarketEvent.source_hash == source_hash,
                MarketEvent.prompt_version == spec.prompt_version,
                MarketEvent.extraction_model == spec.model,
            )
            .limit(1)
        ).scalar_one_or_none()
        if exists:
            return {"ok": True, "cached": True, "source_hash": source_hash}
    _delete_video_events(session, video_id=video_id)

    timeline = resolve_video_timeline(session, video)
    content_time = timeline.content_published_at or video.published_at
    chunks = _chunk_text(transcript_text, spec.chunk_max_chars)
    if not chunks:
        return {"skipped": "empty transcript"}

    accepted = 0
    draft = 0
    inserted = 0
    duplicate = 0
    warnings: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}
    seen_keys: set[str] = set()
    accepted_event_ids: list[str] = []
    for idx, chunk in enumerate(chunks):
        if job:
            raise_if_job_cancel_requested(session, job)
            set_job_progress(job_id=job.id, current=idx, total=len(chunks))
        prompt = _render_event_prompt(
            spec=spec,
            video=video,
            media=media,
            content_time=content_time,
            chunk=chunk,
            chunk_index=idx,
            chunk_total=len(chunks),
        )
        result = llm_generate(prompt=prompt, think=False)
        for key, value in (result.get("usage") or {}).items():
            if key in usage:
                try:
                    usage[key] += int(value or 0)
                except Exception:
                    pass
        parsed_events, parse_warnings = parse_event_extraction_response(str(result.get("text") or ""))
        for message in parse_warnings:
            warnings.append(message)
            _job_warning(session, job, message, data={"chunk": idx + 1})

        for raw in parsed_events:
            start, end, precision = _event_time(raw)
            event_key = _event_key(raw, start)
            if event_key in seen_keys:
                duplicate += 1
                continue
            seen_keys.add(event_key)
            confidence = float(raw["confidence"])
            status = "accepted" if confidence >= EVENT_ACCEPT_CONFIDENCE else "draft"
            event = MarketEvent(
                event_time_start=start,
                event_time_end=end,
                time_precision=precision,
                available_at=video.published_at or video.created_at,
                event_type=_normalize_key(raw.get("event_type")) or "other",
                title=_short_text(raw.get("title"), max_len=220) or None,
                summary=_short_text(raw.get("summary"), max_len=2400) or None,
                direction=_short_text(raw.get("direction"), max_len=80) or None,
                magnitude=_jsonable(raw.get("magnitude")),
                surprise_or_delta=_jsonable(raw.get("surprise_or_delta")),
                confidence=confidence,
                status=status,
                source_video_id=video.id,
                transcript_asset_id=transcript_asset.id,
                extraction_model=spec.model,
                prompt_version=spec.prompt_version,
                source_hash=source_hash,
                event_key=event_key,
                raw_payload=raw,
            )
            try:
                with session.begin_nested():
                    session.add(event)
                    session.flush([event])
                    _insert_event_children(session, event=event, raw=raw, video=video, transcript_asset_id=transcript_asset.id)
            except IntegrityError:
                duplicate += 1
                continue
            inserted += 1
            if status == "accepted":
                accepted += 1
                accepted_event_ids.append(str(event.id))
            else:
                draft += 1

    if job:
        set_job_progress(job_id=job.id, current=len(chunks), total=len(chunks))
    mark_playlists_event_regime_dirty_for_video(session, video_id)
    for event_id in accepted_event_ids:
        enqueue_job(session, type_="event.embed", params={"event_id": event_id}, priority=getattr(job, "priority", 0) if job else 0)
    return {
        "ok": True,
        "source_hash": source_hash,
        "chunks": len(chunks),
        "inserted": inserted,
        "accepted": accepted,
        "draft": draft,
        "duplicate": duplicate,
        "warnings": warnings[:50],
        "usage": usage,
    }


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


def request_playlist_event_backfill(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    force: bool = False,
    priority: int = 0,
) -> Job:
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


def backfill_playlist_events(session: Session, *, playlist_id: uuid.UUID, force: bool = False, job: Job | None = None) -> dict[str, Any]:
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}

    rows = (
        session.execute(
            select(Video.id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(PlaylistMedia.playlist_id == playlist_id)
            .order_by(Video.published_at.asc().nullslast(), Video.created_at.asc(), Video.id.asc())
        )
        .scalars()
        .all()
    )
    total = len(rows)
    enqueued = 0
    skipped = 0
    spec = event_extraction_spec(session)
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
        source_hash = _event_source_hash(
            video=video,
            media=media,
            transcript_asset_id=transcript_asset.id,
            transcript_text=transcript_text,
        )
        current_exists = session.execute(
            select(MarketEvent.id)
            .where(
                MarketEvent.source_video_id == video_id,
                MarketEvent.source_hash == source_hash,
                MarketEvent.prompt_version == spec.prompt_version,
                MarketEvent.extraction_model == spec.model,
            )
            .limit(1)
        ).scalar_one_or_none()
        if current_exists and not force:
            skipped += 1
            continue
        enqueue_job(
            session,
            type_="video.extract_events",
            params={"video_id": str(video_id), "force": bool(force)},
            priority=getattr(job, "priority", 0) if job else 0,
            parent_job_id=str(job.id) if job else None,
        )
        enqueued += 1
    if job:
        set_job_progress(job_id=job.id, current=total, total=total)
    return {"ok": True, "playlist_id": str(playlist_id), "scanned": total, "enqueued": enqueued, "skipped": skipped}


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
            mark_playlists_event_regime_dirty_for_video(session, event.source_video_id)
        raise

    session.flush([existing])
    if previous_status != existing.status or previous_checksum != checksum:
        mark_playlists_event_regime_dirty_for_video(session, event.source_video_id)
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


def mark_playlists_event_regime_dirty_for_video(session: Session, video_id: uuid.UUID, *, changed_at: datetime | None = None) -> None:
    video = session.get(Video, video_id)
    if not video:
        return
    playlist_ids = session.execute(select(PlaylistMedia.playlist_id).where(PlaylistMedia.media_id == video.media_id)).scalars().all()
    for playlist_id in playlist_ids:
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
                MarketEventEmbedding.status == "ready",
                MarketEventEmbedding.vector.is_not(None),
            )
            .order_by(MarketEvent.event_time_start.asc(), MarketEvent.id.asc())
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

    started = utcnow()
    run.status = "running"
    run.started_at = started
    run.embedding_model = spec.model
    run.embedding_dim = spec.dim
    state.last_error = None
    session.flush([run, state])

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
        grouped: dict[date, list[tuple[MarketEvent, list[float]]]] = defaultdict(list)
        for event, embedding in rows:
            vector = [float(value) for value in (embedding.vector or [])]
            if not vector or not event.event_time_start:
                continue
            grouped[_period_start(event.event_time_start.date(), granularity)].append((event, vector))
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
            if event.event_time_start and _period_start(event.event_time_start.date(), granularity) == period_date
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
        for granularity in ("week", "month"):
            signal = signals_by_key.get((granularity, candidate.candidate_date))
            if signal:
                signal.linked_candidate_id = candidate.id

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
        mark_playlists_event_regime_dirty_for_video(session, event.source_video_id)
        if normalized == "accepted":
            enqueue_job(session, type_="event.embed", params={"event_id": str(event.id)}, priority=0)
    return event


def playlist_event_coverage(session: Session, playlist_id: uuid.UUID) -> dict[str, Any]:
    video_total = int(
        session.execute(
            select(func.count())
            .select_from(Video)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(PlaylistMedia.playlist_id == playlist_id)
        ).scalar_one()
        or 0
    )
    video_with_event = int(
        session.execute(
            select(func.count(func.distinct(MarketEvent.source_video_id)))
            .select_from(MarketEvent)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(PlaylistMedia.playlist_id == playlist_id)
        ).scalar_one()
        or 0
    )
    status_rows = (
        session.execute(
            select(MarketEvent.status, func.count())
            .select_from(MarketEvent)
            .join(Video, Video.id == MarketEvent.source_video_id)
            .join(PlaylistMedia, PlaylistMedia.media_id == Video.media_id)
            .where(PlaylistMedia.playlist_id == playlist_id)
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
) -> list[Any]:
    clauses: list[Any] = [PlaylistMedia.playlist_id == playlist_id]
    if status:
        clauses.append(MarketEvent.status == status)
    if event_type:
        clauses.append(MarketEvent.event_type == event_type)
    if min_confidence is not None:
        clauses.append(MarketEvent.confidence >= min_confidence)
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
