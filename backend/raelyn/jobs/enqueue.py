from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.models import Job, JobEvent
from raelyn.services.pg_lock import lock_key
from raelyn.timeutil import utcnow


def _default_max_attempts(type_: str) -> int | None:
    # Keep retries low for provider-facing jobs to avoid hammering platforms when blocked (e.g. 352/412).
    # "1 retry" => max_attempts=2 (first try + one retry).
    if type_ in {
        "media.sync_profile",
        "media.sync_videos",
        "media.delete",
        "video.enrich_metadata.youtube",
        "video.download",
        "video.backfill_subtitles",
        "video.backfill_subtitles.youtube",
        "video.backfill_subtitles.bilibili",
    }:
        return 2
    if type_ in {"playlist.build_event_map_snapshot", "playlist.prune_event_map_snapshots"}:
        return 2
    return None


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        s = s[:10]
        try:
            return date.fromisoformat(s)
        except Exception:
            return None
    return None


def _brief_period_start(d: date, granularity: str) -> date:
    g = (granularity or "day").strip().lower()
    if g == "week":
        return d - timedelta(days=d.weekday())  # Monday
    if g == "month":
        return date(d.year, d.month, 1)
    return d


def _normalize_dedupe_key_and_params(type_: str, params: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    if type_ in {"media.sync_profile", "media.sync_videos"}:
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            media_id = uuid.UUID(str(params2.get("media_id")))
        except Exception:
            return None, params2
        return f"{type_}:{media_id}", params2

    if type_ == "media.delete":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        raw_media_id = params2.get("media_id")
        try:
            media_id = uuid.UUID(str(raw_media_id))
        except Exception:
            return None, params2
        return f"media.delete:{media_id}", params2

    if type_ == "video.enrich_metadata.youtube":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            video_id = uuid.UUID(str(params2.get("video_id")))
        except Exception:
            return None, params2
        params2["video_id"] = str(video_id)
        return f"video.enrich_metadata.youtube:{video_id}", params2

    if type_ == "video.extract_events":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            video_id = uuid.UUID(str(params2.get("video_id")))
        except Exception:
            return None, params2
        force = bool(params2.get("force", False))
        params2["force"] = force
        model = str(settings.llm_model or "").strip() or "default"
        mode = "force" if force else "missing"
        return f"video_event_extract:{video_id}:{model}:{mode}", params2

    if type_ == "video.extract_events_batch":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        raw_video_ids = params2.get("video_ids")
        if not isinstance(raw_video_ids, list):
            return None, params2
        video_ids: list[str] = []
        for raw in raw_video_ids:
            try:
                video_ids.append(str(uuid.UUID(str(raw))))
            except Exception:
                continue
        if not video_ids:
            return None, params2
        video_ids = list(dict.fromkeys(video_ids))
        params2["video_ids"] = video_ids
        force = bool(params2.get("force", False))
        params2["force"] = force
        model = str(settings.llm_model or "").strip() or "default"
        mode = "force" if force else "missing"
        digest = uuid.uuid5(uuid.NAMESPACE_URL, ",".join(video_ids))
        return f"video_event_extract_batch:{digest}:{model}:{mode}", params2

    if type_ in {
        "video.backfill_subtitles",
        "video.backfill_subtitles.youtube",
        "video.backfill_subtitles.bilibili",
    }:
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            video_id = uuid.UUID(str(params2.get("video_id")))
        except Exception:
            return None, params2
        params2["video_id"] = str(video_id)
        target = str(params2.get("language") or params2.get("target_language") or "auto").strip().lower() or "auto"
        params2["target_language"] = target
        params2.pop("language", None)
        force = bool(params2.get("force", False))
        params2["force"] = force
        mode = "force" if force else "missing"
        return f"video_subtitle_backfill:{video_id}:{target}:{mode}", params2

    if type_ == "event.embed":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            event_id = uuid.UUID(str(params2.get("event_id")))
        except Exception:
            return None, params2
        model = str(settings.embedding_model or "").strip() or "Qwen/Qwen3-Embedding-8B"
        dim = max(1, int(settings.embedding_dim or 1024))
        return f"event_embedding:{event_id}:{model}:{dim}", params2

    if type_ == "playlist.build_event_map_snapshot":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            playlist_id = uuid.UUID(str(params2.get("playlist_id")))
        except Exception:
            return None, params2
        model = str(settings.embedding_model or "").strip() or "Qwen/Qwen3-Embedding-8B"
        dim = max(1, int(settings.embedding_dim or 1024))
        params2["playlist_id"] = str(playlist_id)
        params2["embedding_model"] = model
        params2["embedding_dim"] = dim
        return f"playlist_event_map_build:{playlist_id}", params2

    if type_ == "playlist.prune_event_map_snapshots":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            playlist_id = uuid.UUID(str(params2.get("playlist_id")))
        except Exception:
            return None, params2
        params2["playlist_id"] = str(playlist_id)
        return f"playlist_event_map_prune:{playlist_id}", params2

    if type_ == "playlist.mark_event_map_dirty":
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            playlist_id = uuid.UUID(str(params2.get("playlist_id")))
        except Exception:
            return None, params2
        params2["playlist_id"] = str(playlist_id)
        reason = str(params2.get("reason") or "").strip() or "event_map_dirty"
        params2["reason"] = reason
        for key in ("source_video_id", "source_job_id"):
            raw = params2.get(key)
            if not raw:
                params2.pop(key, None)
                continue
            try:
                params2[key] = str(uuid.UUID(str(raw)))
            except Exception:
                params2.pop(key, None)
        return f"playlist_event_map_dirty:{playlist_id}", params2

    if type_ in {"playlist.backfill_events", "playlist.backfill_events_range"}:
        if not isinstance(params, dict):
            return None, params
        params2 = dict(params)
        try:
            playlist_id = uuid.UUID(str(params2.get("playlist_id")))
        except Exception:
            return None, params2
        force = bool(params2.get("force", False))
        params2["force"] = force
        mode = "force" if force else "missing"
        model = str(settings.llm_model or "").strip() or "default"
        if type_ == "playlist.backfill_events_range":
            range_start = _parse_iso_date(params2.get("range_start"))
            range_end = _parse_iso_date(params2.get("range_end"))
            if not range_start or not range_end:
                return None, params2
            params2["range_start"] = range_start.isoformat()
            params2["range_end"] = range_end.isoformat()
            return f"playlist_event_backfill_range:{playlist_id}:{range_start.isoformat()}:{range_end.isoformat()}:{model}:{mode}", params2
        return f"playlist_event_backfill:{playlist_id}:{model}:{mode}", params2

    if type_ not in {"brief.generate_period", "brief.generate_daily"}:
        return None, params
    if not isinstance(params, dict):
        return None, params

    params2 = dict(params)
    playlist_raw = params2.get("playlist_id")
    try:
        playlist_id = uuid.UUID(str(playlist_raw))
    except Exception:
        return None, params2

    if type_ == "brief.generate_daily":
        g = "day"
    else:
        g = str(params2.get("granularity") or "day").strip().lower()
        if g not in {"day", "week", "month"}:
            g = "day"
        params2["granularity"] = g

    raw_date = params2.get("period_start") or params2.get("date")
    d = _parse_iso_date(raw_date)
    if not d:
        return None, params2

    if type_ == "brief.generate_period":
        d = _brief_period_start(d, g)
        params2["period_start"] = d.isoformat()

    # Always write "date" so UI and other callers can reliably display/route by day.
    params2["date"] = d.isoformat()

    dedupe_key = f"brief:{playlist_id}:{d.isoformat()}"
    return dedupe_key, params2


def _brief_normalize_date_and_params(type_: str, params: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    return _normalize_dedupe_key_and_params(type_, params)


def _session_dialect_name(session: Session) -> str:
    try:
        bind = session.get_bind()
    except Exception:
        bind = getattr(session, "bind", None)
    return str(getattr(getattr(bind, "dialect", None), "name", "") or "")


def _lock_pending_dedupe_key(session: Session, dedupe_key: str) -> None:
    if _session_dialect_name(session) != "postgresql":
        return
    key = lock_key(f"job:pending-dedupe:{dedupe_key}")
    session.execute(text("select pg_advisory_xact_lock(:k)").bindparams(k=key))


def _pending_job_for_dedupe(
    session: Session,
    dedupe_key: str,
    *,
    lock: bool = False,
) -> Job | None:
    statement = (
        select(Job)
        .where(Job.dedupe_key == dedupe_key, Job.status == "pending")
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(1)
    )
    if lock:
        # dirty job 同时承担“源数据已经提交”的 outbox 信号。复用 pending
        # 行时必须持有行锁到源事务提交，避免 worker 抢先领取旧信号。
        statement = statement.with_for_update()
    return session.execute(statement).scalar_one_or_none()


def _merge_pending_dirty_job(existing: Job, params: dict[str, Any], priority: int) -> None:
    current = dict(existing.params or {})
    reasons: list[str] = []
    for value in [*(current.get("reasons") or []), current.get("reason"), params.get("reason")]:
        reason = str(value or "").strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    current.update(params)
    current["reasons"] = reasons[-8:]
    existing.params = current
    existing.priority = max(int(existing.priority or 0), int(priority or 0))


def enqueue_job(
    session: Session,
    *,
    type_: str,
    params: dict[str, Any],
    priority: int = 0,
    scheduled_for: Any | None = None,
    parent_job_id: str | None = None,
) -> uuid.UUID:
    max_attempts = _default_max_attempts(type_)
    dedupe_key, params2 = _normalize_dedupe_key_and_params(type_, params)
    job = Job(
        type=type_,
        status="pending",
        priority=priority,
        dedupe_key=dedupe_key,
        params=params2,
        scheduled_for=scheduled_for or utcnow(),
        parent_job_id=uuid.UUID(parent_job_id) if parent_job_id else None,
        **({"max_attempts": max_attempts} if isinstance(max_attempts, int) else {}),
    )
    if dedupe_key:
        _lock_pending_dedupe_key(session, dedupe_key)
        lock_existing = type_ == "playlist.mark_event_map_dirty"
        existing = _pending_job_for_dedupe(session, dedupe_key, lock=lock_existing)
        if existing:
            if lock_existing:
                _merge_pending_dirty_job(existing, params2, priority)
            return existing.id
        try:
            with session.begin_nested():
                session.add(job)
                session.flush([job])
        except IntegrityError:
            try:
                session.expunge(job)
            except Exception:
                pass
            existing = _pending_job_for_dedupe(session, dedupe_key, lock=lock_existing)
            if existing:
                if lock_existing:
                    _merge_pending_dirty_job(existing, params2, priority)
                return existing.id
            raise
    else:
        session.add(job)
        session.flush([job])
    event = JobEvent(job_id=job.id, level="info", message="enqueued", data={"type": type_})
    session.add(event)
    session.flush([event])
    return job.id


def enqueue_in(session: Session, *, seconds: int, type_: str, params: dict[str, Any], priority: int = 0) -> uuid.UUID:
    return enqueue_job(
        session,
        type_=type_,
        params=params,
        priority=priority,
        scheduled_for=utcnow() + timedelta(seconds=seconds),
    )
