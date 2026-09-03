from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import logging
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from raelyn.db import session_scope
from raelyn.models import (
    AppConfig,
    Asset,
    ExternalServiceUsageDaily,
    Job,
    JobEvent,
    ResourceUsageDaily,
    Video,
)
from raelyn.timeutil import utcnow


logger = logging.getLogger(__name__)

USAGE_TIMEZONE = "Asia/Shanghai"
_USAGE_TZ = ZoneInfo(USAGE_TIMEZONE)
_ALLOWED_USAGE_WINDOWS = {7, 30, 90}
_ALLOWED_EXTERNAL_SERVICES = {"llm", "asr", "embedding"}
_VIDEO_DOWNLOAD_JOB_TYPES = (
    "video.download",
    "video.download.youtube",
    "video.download.bilibili",
)
LEGACY_USAGE_BACKFILL_VERSION = 2
LEGACY_USAGE_BACKFILL_CONFIG_KEY = "usage.legacy_llm_backfill"
_LEGACY_USAGE_OPERATION_PREFIX = "legacy."
_LEGACY_LLM_JOB_OPERATIONS = {
    "brief.generate_daily": "legacy.brief_generation",
    "brief.generate_period": "legacy.brief_generation",
    "video.extract_events": "legacy.event_extraction",
    "video.extract_events_batch": "legacy.event_extraction",
    "video.polish_transcript": "legacy.transcript_polish",
}


def _utc_datetime(value: datetime | None = None) -> datetime:
    current = value or utcnow()
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _non_negative_int(value: int | None) -> int:
    if value is None:
        return 0
    return max(0, int(value))


def _normalized_dimension(value: str | None, *, lower: bool = False) -> str:
    normalized = str(value or "").strip()
    return normalized.lower() if lower else normalized


def _dialect_insert(session: Session, model: type[Any]):
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        return postgresql_insert(model)
    if dialect_name == "sqlite":
        return sqlite_insert(model)
    raise RuntimeError(f"unsupported usage aggregation dialect: {dialect_name}")


def _record_external_service_usage(
    session: Session,
    *,
    service: str,
    operation: str,
    provider: str | None,
    model: str | None,
    succeeded: bool,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    duration_ms: int | None,
    usage_missing: bool | None,
    called_at: datetime | None,
) -> None:
    service_name = _normalized_dimension(service, lower=True)
    operation_name = _normalized_dimension(operation)
    if not service_name or not operation_name:
        raise ValueError("service and operation are required")
    if service_name not in _ALLOWED_EXTERNAL_SERVICES:
        raise ValueError(f"unsupported external service: {service_name}")

    provider_name = _normalized_dimension(provider, lower=True)
    model_name = _normalized_dimension(model)
    called_at_utc = _utc_datetime(called_at)
    input_count = _non_negative_int(input_tokens)
    output_count = _non_negative_int(output_tokens)
    total_count = (
        _non_negative_int(total_tokens)
        if total_tokens is not None
        else input_count + output_count
    )
    if usage_missing is None:
        usage_missing = service_name == "llm" and all(
            value is None for value in (input_tokens, output_tokens, total_tokens)
        )

    values = {
        "day": called_at_utc.astimezone(_USAGE_TZ).date(),
        "service": service_name,
        "operation": operation_name,
        "provider": provider_name,
        "model": model_name,
        "call_count": 1,
        "success_count": 1 if succeeded else 0,
        "failure_count": 0 if succeeded else 1,
        "input_tokens": input_count,
        "output_tokens": output_count,
        "total_tokens": total_count,
        "duration_ms": _non_negative_int(duration_ms),
        "usage_missing_calls": 1 if usage_missing else 0,
        "last_called_at": called_at_utc,
    }
    statement = _dialect_insert(session, ExternalServiceUsageDaily).values(**values)
    excluded = statement.excluded
    if session.get_bind().dialect.name == "postgresql":
        last_called_at = func.greatest(
            ExternalServiceUsageDaily.last_called_at,
            excluded.last_called_at,
        )
    else:
        last_called_at = func.max(
            ExternalServiceUsageDaily.last_called_at,
            excluded.last_called_at,
        )
    statement = statement.on_conflict_do_update(
        index_elements=["day", "service", "operation", "provider", "model"],
        set_={
            "call_count": ExternalServiceUsageDaily.call_count + excluded.call_count,
            "success_count": ExternalServiceUsageDaily.success_count + excluded.success_count,
            "failure_count": ExternalServiceUsageDaily.failure_count + excluded.failure_count,
            "input_tokens": ExternalServiceUsageDaily.input_tokens + excluded.input_tokens,
            "output_tokens": ExternalServiceUsageDaily.output_tokens + excluded.output_tokens,
            "total_tokens": ExternalServiceUsageDaily.total_tokens + excluded.total_tokens,
            "duration_ms": ExternalServiceUsageDaily.duration_ms + excluded.duration_ms,
            "usage_missing_calls": (
                ExternalServiceUsageDaily.usage_missing_calls + excluded.usage_missing_calls
            ),
            "last_called_at": last_called_at,
        },
    )
    session.execute(statement)


def record_external_service_usage(
    *,
    service: str,
    operation: str,
    provider: str | None = None,
    model: str | None = None,
    succeeded: bool,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
    usage_missing: bool | None = None,
    called_at: datetime | None = None,
) -> bool:
    """在独立事务中记录一次外部调用；统计失败不得影响主业务请求。"""

    try:
        with session_scope() as session:
            _record_external_service_usage(
                session,
                service=service,
                operation=operation,
                provider=provider,
                model=model,
                succeeded=succeeded,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                usage_missing=usage_missing,
                called_at=called_at,
            )
        return True
    except Exception as exc:
        logger.warning(
            "记录外部服务用量失败：service=%s operation=%s error_type=%s",
            _normalized_dimension(service, lower=True),
            _normalized_dimension(operation),
            type(exc).__name__,
        )
        return False


def _legacy_llm_usage(job_type: str, result: Any) -> dict[str, int] | None:
    if not isinstance(result, dict):
        return None
    usage_key = "usage" if job_type in {"video.extract_events", "video.extract_events_batch"} else "llm_usage"
    usage = result.get(usage_key)
    if not isinstance(usage, dict):
        return None

    def value(key: str) -> int:
        try:
            return max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    input_tokens = value("input_tokens")
    output_tokens = value("output_tokens")
    total_tokens = value("total_tokens") or input_tokens + output_tokens
    call_count = value("call_count")
    if call_count <= 0 and total_tokens > 0:
        call_count = 1
    if call_count <= 0:
        return None
    return {
        "call_count": call_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_missing_calls": call_count if total_tokens <= 0 else 0,
    }


def backfill_legacy_external_usage(session: Session) -> dict[str, int]:
    """从可核验的历史 Job 结果与调用事件重建 legacy 日聚合。"""

    rows = session.execute(
        select(Job.type, Job.status, Job.finished_at, Job.result).where(
            Job.type.in_(tuple(_LEGACY_LLM_JOB_OPERATIONS)),
            Job.status.in_(("succeeded", "failed")),
            Job.finished_at.is_not(None),
            Job.result.is_not(None),
        )
    )
    totals: dict[tuple[date, str, str, str, str], dict[str, Any]] = {}
    llm_source_job_count = 0
    for job_type, status, finished_at, result in rows:
        usage = _legacy_llm_usage(str(job_type), result)
        if usage is None or finished_at is None:
            continue
        llm_source_job_count += 1
        finished_at_utc = _utc_datetime(finished_at)
        key = (
            finished_at_utc.astimezone(_USAGE_TZ).date(),
            "llm",
            _LEGACY_LLM_JOB_OPERATIONS[str(job_type)],
            "legacy",
            "",
        )
        item = totals.setdefault(
            key,
            {
                "call_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_missing_calls": 0,
                "last_called_at": finished_at_utc,
            },
        )
        call_count = int(usage["call_count"])
        item["call_count"] += call_count
        if status == "succeeded":
            item["success_count"] += call_count
        else:
            item["failure_count"] += call_count
        for field in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "usage_missing_calls",
        ):
            item[field] += int(usage[field])
        if finished_at_utc > item["last_called_at"]:
            item["last_called_at"] = finished_at_utc

    successful_asr_requests = {
        (job_id, int((data or {}).get("attempt") or 0))
        for job_id, data in session.execute(
            select(JobEvent.job_id, JobEvent.data)
            .join(Job, Job.id == JobEvent.job_id)
            .where(
                Job.type == "video.asr_transcribe",
                Job.status.in_(("succeeded", "failed")),
                JobEvent.message == "asr request succeeded",
            )
        )
        if isinstance(data, dict) and int(data.get("attempt") or 0) > 0
    }
    asr_source_event_count = 0
    for job_id, event_at, data in session.execute(
        select(JobEvent.job_id, JobEvent.ts, JobEvent.data)
        .join(Job, Job.id == JobEvent.job_id)
        .where(
            Job.type == "video.asr_transcribe",
            Job.status.in_(("succeeded", "failed")),
            JobEvent.message == "asr request started",
        )
    ):
        if event_at is None or not isinstance(data, dict):
            continue
        attempt = int(data.get("attempt") or 0)
        provider = _normalized_dimension(data.get("provider"), lower=True)
        if attempt <= 0 or not provider:
            continue
        asr_source_event_count += 1
        event_at_utc = _utc_datetime(event_at)
        key = (
            event_at_utc.astimezone(_USAGE_TZ).date(),
            "asr",
            "legacy.video_transcription",
            provider,
            "",
        )
        item = totals.setdefault(
            key,
            {
                "call_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_missing_calls": 0,
                "last_called_at": event_at_utc,
            },
        )
        item["call_count"] += 1
        if (job_id, attempt) in successful_asr_requests:
            item["success_count"] += 1
        else:
            item["failure_count"] += 1
        if event_at_utc > item["last_called_at"]:
            item["last_called_at"] = event_at_utc

    session.execute(
        delete(ExternalServiceUsageDaily).where(
            ExternalServiceUsageDaily.operation.like(f"{_LEGACY_USAGE_OPERATION_PREFIX}%"),
        )
    )
    for (usage_day, service, operation, provider, model), item in totals.items():
        session.add(
            ExternalServiceUsageDaily(
                day=usage_day,
                service=service,
                operation=operation,
                provider=provider,
                model=model,
                call_count=int(item["call_count"]),
                success_count=int(item["success_count"]),
                failure_count=int(item["failure_count"]),
                input_tokens=int(item["input_tokens"]),
                output_tokens=int(item["output_tokens"]),
                total_tokens=int(item["total_tokens"]),
                duration_ms=0,
                usage_missing_calls=int(item["usage_missing_calls"]),
                last_called_at=item["last_called_at"],
            )
        )

    result = {
        "version": LEGACY_USAGE_BACKFILL_VERSION,
        "source_job_count": llm_source_job_count,
        "llm_source_job_count": llm_source_job_count,
        "asr_source_event_count": asr_source_event_count,
        "daily_row_count": len(totals),
        "call_count": sum(int(item["call_count"]) for item in totals.values()),
        "llm_call_count": sum(
            int(item["call_count"])
            for key, item in totals.items()
            if key[1] == "llm"
        ),
        "asr_call_count": sum(
            int(item["call_count"])
            for key, item in totals.items()
            if key[1] == "asr"
        ),
        "input_tokens": sum(int(item["input_tokens"]) for item in totals.values()),
        "output_tokens": sum(int(item["output_tokens"]) for item in totals.values()),
        "total_tokens": sum(int(item["total_tokens"]) for item in totals.values()),
    }
    marker = session.get(AppConfig, LEGACY_USAGE_BACKFILL_CONFIG_KEY)
    marker_value = {**result, "completed_at": _utc_datetime().isoformat()}
    if marker is None:
        session.add(AppConfig(key=LEGACY_USAGE_BACKFILL_CONFIG_KEY, value=marker_value))
    else:
        marker.value = marker_value
    session.flush()
    return result


def _collect_current_resource_values(
    session: Session,
    *,
    asset_breakdown: list[dict[str, Any]] | None = None,
) -> dict[str, int | None]:
    video_count = int(session.execute(select(func.count(Video.id))).scalar_one() or 0)
    assets = asset_breakdown if asset_breakdown is not None else _asset_breakdown(session)

    database_size_bytes = None
    if session.get_bind().dialect.name == "postgresql":
        database_size_bytes = int(
            session.execute(
                select(func.pg_database_size(func.current_database()))
            ).scalar_one()
        )

    return {
        "video_count": video_count,
        "asset_count": sum(int(item["count"]) for item in assets),
        "asset_size_bytes": sum(int(item["size_bytes"]) for item in assets),
        "asset_missing_size_count": sum(int(item["missing_size_count"]) for item in assets),
        "database_size_bytes": database_size_bytes,
    }


def capture_resource_usage_snapshot(
    session: Session,
    *,
    captured_at: datetime | None = None,
) -> ResourceUsageDaily:
    captured_at_utc = _utc_datetime(captured_at)
    snapshot_day = captured_at_utc.astimezone(_USAGE_TZ).date()
    values = {
        "day": snapshot_day,
        "captured_at": captured_at_utc,
        **_collect_current_resource_values(session),
    }
    statement = _dialect_insert(session, ResourceUsageDaily).values(**values)
    excluded = statement.excluded
    use_newer = excluded.captured_at >= ResourceUsageDaily.captured_at
    if session.get_bind().dialect.name == "postgresql":
        latest_captured_at = func.greatest(
            ResourceUsageDaily.captured_at,
            excluded.captured_at,
        )
    else:
        latest_captured_at = func.max(
            ResourceUsageDaily.captured_at,
            excluded.captured_at,
        )
    statement = statement.on_conflict_do_update(
        index_elements=["day"],
        set_={
            "captured_at": latest_captured_at,
            "video_count": case(
                (use_newer, excluded.video_count),
                else_=ResourceUsageDaily.video_count,
            ),
            "asset_count": case(
                (use_newer, excluded.asset_count),
                else_=ResourceUsageDaily.asset_count,
            ),
            "asset_size_bytes": case(
                (use_newer, excluded.asset_size_bytes),
                else_=ResourceUsageDaily.asset_size_bytes,
            ),
            "asset_missing_size_count": case(
                (use_newer, excluded.asset_missing_size_count),
                else_=ResourceUsageDaily.asset_missing_size_count,
            ),
            "database_size_bytes": case(
                (use_newer, excluded.database_size_bytes),
                else_=ResourceUsageDaily.database_size_bytes,
            ),
        },
    )
    session.execute(statement)
    session.flush()
    return session.execute(
        select(ResourceUsageDaily)
        .where(ResourceUsageDaily.day == snapshot_day)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _window_bounds(generated_at: datetime, days: int) -> tuple[date, date, datetime, datetime]:
    end_day = generated_at.astimezone(_USAGE_TZ).date()
    start_day = end_day - timedelta(days=days - 1)
    start_utc = datetime.combine(start_day, time.min, tzinfo=_USAGE_TZ).astimezone(timezone.utc)
    until_utc = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=_USAGE_TZ).astimezone(
        timezone.utc
    )
    return start_day, end_day, start_utc, until_utc


def _daily_video_downloads(
    session: Session,
    *,
    start_utc: datetime,
    until_utc: datetime,
) -> dict[date, int]:
    skipped = Job.result["skipped"].as_string()
    rescheduled = Job.result["rescheduled"].as_boolean()
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        day_bucket = func.date(func.timezone(USAGE_TIMEZONE, Job.finished_at))
    elif dialect_name == "sqlite":
        day_bucket = func.date(Job.finished_at, "+8 hours")
    else:
        rows = session.execute(
            select(Job.finished_at, Job.result).where(
                Job.type.in_(_VIDEO_DOWNLOAD_JOB_TYPES),
                Job.status == "succeeded",
                Job.finished_at >= start_utc,
                Job.finished_at < until_utc,
            )
        ).all()
        counts: dict[date, int] = defaultdict(int)
        for finished_at, result in rows:
            if (
                finished_at is None
                or not isinstance(result, dict)
                or result.get("skipped")
                or result.get("rescheduled")
            ):
                continue
            counts[_utc_datetime(finished_at).astimezone(_USAGE_TZ).date()] += 1
        return dict(counts)

    rows = session.execute(
        select(day_bucket, func.count(Job.id))
        .where(
            Job.type.in_(_VIDEO_DOWNLOAD_JOB_TYPES),
            Job.status == "succeeded",
            Job.finished_at >= start_utc,
            Job.finished_at < until_utc,
            Job.result.is_not(None),
            skipped.is_(None),
            or_(rescheduled.is_(None), rescheduled.is_(False)),
        )
        .group_by(day_bucket)
    ).all()
    return {
        (
            day_value
            if isinstance(day_value, date)
            else date.fromisoformat(str(day_value))
        ): int(count)
        for day_value, count in rows
        if day_value is not None
    }


def _downloaded_video_count(session: Session) -> int:
    return int(
        session.execute(
            select(func.count(func.distinct(Asset.video_id))).where(
                Asset.type == "video",
                Asset.video_id.is_not(None),
            )
        ).scalar_one()
        or 0
    )


def _asset_breakdown(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Asset.type,
            func.count(Asset.id),
            func.coalesce(func.sum(func.coalesce(Asset.size_bytes, 0)), 0),
            func.coalesce(
                func.sum(case((Asset.size_bytes.is_(None), 1), else_=0)),
                0,
            ),
        )
        .group_by(Asset.type)
        .order_by(Asset.type.asc())
    ).all()
    return [
        {
            "asset_type": str(asset_type),
            "count": int(count or 0),
            "size_bytes": int(size_bytes or 0),
            "missing_size_count": int(missing_size_count or 0),
        }
        for asset_type, count, size_bytes, missing_size_count in rows
    ]


def _empty_usage_totals() -> dict[str, int]:
    return {
        "external_calls": 0,
        "llm_calls": 0,
        "asr_calls": 0,
        "embedding_calls": 0,
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "llm_total_tokens": 0,
        "llm_usage_missing_calls": 0,
    }


def build_usage_payload(session: Session, *, days: int) -> dict[str, Any]:
    if days not in _ALLOWED_USAGE_WINDOWS:
        raise ValueError("days must be one of: 7, 30, 90")

    generated_at = utcnow()
    start_day, end_day, start_utc, until_utc = _window_bounds(generated_at, days)
    usage_rows = session.execute(
        select(ExternalServiceUsageDaily).where(
            ExternalServiceUsageDaily.day >= start_day,
            ExternalServiceUsageDaily.day <= end_day,
        )
    ).scalars().all()
    resource_rows = session.execute(
        select(ResourceUsageDaily).where(
            ResourceUsageDaily.day >= start_day,
            ResourceUsageDaily.day <= end_day,
        )
    ).scalars().all()
    service_started_at = {
        str(service): first_day
        for service, first_day in session.execute(
            select(
                ExternalServiceUsageDaily.service,
                func.min(ExternalServiceUsageDaily.day),
            )
            .where(ExternalServiceUsageDaily.service.in_(tuple(_ALLOWED_EXTERNAL_SERVICES)))
            .group_by(ExternalServiceUsageDaily.service)
        ).all()
    }

    usage_by_day: dict[date, list[ExternalServiceUsageDaily]] = defaultdict(list)
    for row in usage_rows:
        usage_by_day[row.day].append(row)
    resource_by_day = {row.day: row for row in resource_rows}
    asset_breakdown = _asset_breakdown(session)
    current_resource = _collect_current_resource_values(
        session,
        asset_breakdown=asset_breakdown,
    )
    video_downloaded_by_day = _daily_video_downloads(
        session,
        start_utc=start_utc,
        until_utc=until_utc,
    )
    downloaded_video_count = _downloaded_video_count(session)

    breakdown: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in usage_rows:
        key = (row.service, row.operation, row.provider, row.model)
        item = breakdown.setdefault(
            key,
            {
                "service": row.service,
                "operation": row.operation,
                "provider": row.provider,
                "model": row.model,
                "calls": 0,
                "successes": 0,
                "failures": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "duration_ms": 0,
                "usage_missing_calls": 0,
                "last_called_at": None,
            },
        )
        item["calls"] += int(row.call_count or 0)
        item["successes"] += int(row.success_count or 0)
        item["failures"] += int(row.failure_count or 0)
        item["input_tokens"] += int(row.input_tokens or 0)
        item["output_tokens"] += int(row.output_tokens or 0)
        item["total_tokens"] += int(row.total_tokens or 0)
        item["duration_ms"] += int(row.duration_ms or 0)
        item["usage_missing_calls"] += int(row.usage_missing_calls or 0)
        called_at = _utc_datetime(row.last_called_at)
        previous = item["last_called_at"]
        if previous is None or called_at > previous:
            item["last_called_at"] = called_at
    series: list[dict[str, Any]] = []
    summary_usage = _empty_usage_totals()
    summary_service_sampled = {
        service: first_day <= end_day
        for service, first_day in service_started_at.items()
    }
    summary_usage_sampled = any(summary_service_sampled.values())
    for offset in range(days):
        current_day = start_day + timedelta(days=offset)
        daily_rows = usage_by_day.get(current_day, [])
        resource = resource_by_day.get(current_day)
        service_sampled = {
            service: first_day <= current_day
            for service, first_day in service_started_at.items()
        }
        usage_sampled = any(service_sampled.values())
        daily = _empty_usage_totals()
        for row in daily_rows:
            daily["external_calls"] += int(row.call_count or 0)
            if row.service == "llm":
                daily["llm_calls"] += int(row.call_count or 0)
                daily["llm_input_tokens"] += int(row.input_tokens or 0)
                daily["llm_output_tokens"] += int(row.output_tokens or 0)
                daily["llm_total_tokens"] += int(row.total_tokens or 0)
                daily["llm_usage_missing_calls"] += int(row.usage_missing_calls or 0)
            elif row.service == "asr":
                daily["asr_calls"] += int(row.call_count or 0)
            elif row.service == "embedding":
                daily["embedding_calls"] += int(row.call_count or 0)

        llm_sampled = service_sampled.get("llm", False)
        llm_tokens_known = llm_sampled and daily["llm_usage_missing_calls"] == 0
        series.append(
            {
                "date": current_day,
                "video_downloaded": int(video_downloaded_by_day.get(current_day, 0)),
                "external_calls": daily["external_calls"] if usage_sampled else None,
                "llm_calls": (
                    daily["llm_calls"] if service_sampled.get("llm", False) else None
                ),
                "asr_calls": (
                    daily["asr_calls"] if service_sampled.get("asr", False) else None
                ),
                "embedding_calls": (
                    daily["embedding_calls"]
                    if service_sampled.get("embedding", False)
                    else None
                ),
                "llm_input_tokens": (
                    daily["llm_input_tokens"] if llm_tokens_known else None
                ),
                "llm_output_tokens": (
                    daily["llm_output_tokens"] if llm_tokens_known else None
                ),
                "llm_total_tokens": (
                    daily["llm_total_tokens"] if llm_tokens_known else None
                ),
                "asset_size_bytes": int(resource.asset_size_bytes) if resource is not None else None,
                "database_size_bytes": (
                    int(resource.database_size_bytes)
                    if resource is not None and resource.database_size_bytes is not None
                    else None
                ),
                "usage_sampled": usage_sampled,
                "resource_sampled": resource is not None,
            }
        )
        for key in summary_usage:
            summary_usage[key] += daily[key]

    summary_llm_sampled = summary_service_sampled.get("llm", False)
    summary_llm_tokens_known = (
        summary_llm_sampled and summary_usage["llm_usage_missing_calls"] == 0
    )
    usage_started_at, last_usage_at = session.execute(
        select(
            func.min(ExternalServiceUsageDaily.day),
            func.max(ExternalServiceUsageDaily.last_called_at),
        )
    ).one()
    all_resource_bounds = session.execute(
        select(
            func.min(ResourceUsageDaily.day),
            func.max(ResourceUsageDaily.captured_at),
        )
    ).one()
    resource_started_at, last_resource_snapshot_at = all_resource_bounds

    return {
        "generated_at": generated_at,
        "timezone": USAGE_TIMEZONE,
        "window": {"days": days, "start": start_day, "end": end_day},
        "summary": {
            "video_count": int(current_resource["video_count"] or 0),
            "downloaded_video_count": downloaded_video_count,
            "video_downloaded": sum(video_downloaded_by_day.values()),
            "external_calls": (
                summary_usage["external_calls"] if summary_usage_sampled else None
            ),
            "llm_calls": (
                summary_usage["llm_calls"] if summary_llm_sampled else None
            ),
            "asr_calls": (
                summary_usage["asr_calls"]
                if summary_service_sampled.get("asr", False)
                else None
            ),
            "embedding_calls": (
                summary_usage["embedding_calls"]
                if summary_service_sampled.get("embedding", False)
                else None
            ),
            "llm_input_tokens": (
                summary_usage["llm_input_tokens"]
                if summary_llm_tokens_known
                else None
            ),
            "llm_output_tokens": (
                summary_usage["llm_output_tokens"]
                if summary_llm_tokens_known
                else None
            ),
            "llm_total_tokens": (
                summary_usage["llm_total_tokens"]
                if summary_llm_tokens_known
                else None
            ),
            "asset_count": int(current_resource["asset_count"] or 0),
            "asset_size_bytes": int(current_resource["asset_size_bytes"] or 0),
            "asset_missing_size_count": int(current_resource["asset_missing_size_count"] or 0),
            "database_size_bytes": (
                int(current_resource["database_size_bytes"])
                if current_resource["database_size_bytes"] is not None
                else None
            ),
        },
        "series": series,
        "service_breakdown": sorted(breakdown.values(), key=lambda item: (
            item["service"],
            item["operation"],
            item["provider"],
            item["model"],
        )),
        "asset_breakdown": asset_breakdown,
        "collection": {
            "usage_started_at": usage_started_at,
            "last_usage_at": (
                _utc_datetime(last_usage_at) if last_usage_at is not None else None
            ),
            "resource_started_at": resource_started_at,
            "last_resource_snapshot_at": (
                _utc_datetime(last_resource_snapshot_at)
                if last_resource_snapshot_at is not None
                else None
            ),
            "physical_storage_available": False,
            "physical_storage_reason": "未接入部署侧指标",
        },
    }
