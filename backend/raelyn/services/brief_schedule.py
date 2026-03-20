from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from dateutil import tz
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import _brief_normalize_date_and_params
from raelyn.models import AppConfig, Brief, Job, JobEvent, Playlist, PlaylistMedia, Video
from raelyn.services.brief_prompt import brief_period_start
from raelyn.services.llm import llm_enabled
from raelyn.services.video_admission import (
    brief_admitted_video_expr,
    ensure_video_published_at_backfilled,
)
from raelyn.timeutil import utcnow

DEFAULT_BRIEF_GENERATION_POLICY = {
    "latest_cooldown_minutes": 120,
    "historical_daily_run_time": "04:00",
}
BRIEF_GENERATION_POLICY_CONFIG_KEY = "brief_generation_policy"
_RUN_TIME_RE = re.compile(r"^(?P<hour>\d{2}):(?P<minute>\d{2})$")


def normalize_brief_generation_policy(value: Any) -> dict[str, Any]:
    if value is None:
        return dict(DEFAULT_BRIEF_GENERATION_POLICY)
    if not isinstance(value, dict):
        raise ValueError("brief_generation_policy must be an object.")

    raw_minutes = value.get("latest_cooldown_minutes", DEFAULT_BRIEF_GENERATION_POLICY["latest_cooldown_minutes"])
    try:
        latest_cooldown_minutes = int(raw_minutes)
    except Exception as exc:
        raise ValueError("brief_generation_policy.latest_cooldown_minutes must be an integer >= 0.") from exc
    if latest_cooldown_minutes < 0:
        raise ValueError("brief_generation_policy.latest_cooldown_minutes must be an integer >= 0.")

    raw_time = str(
        value.get("historical_daily_run_time", DEFAULT_BRIEF_GENERATION_POLICY["historical_daily_run_time"]) or ""
    ).strip()
    match = _RUN_TIME_RE.fullmatch(raw_time)
    if not match:
        raise ValueError("brief_generation_policy.historical_daily_run_time must be HH:MM (24h).")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise ValueError("brief_generation_policy.historical_daily_run_time must be HH:MM (24h).")

    return {
        "latest_cooldown_minutes": latest_cooldown_minutes,
        "historical_daily_run_time": f"{hour:02d}:{minute:02d}",
    }


def brief_generation_policy_defaults() -> dict[str, dict[str, Any]]:
    return {BRIEF_GENERATION_POLICY_CONFIG_KEY: dict(DEFAULT_BRIEF_GENERATION_POLICY)}


def get_brief_generation_policy(session: Session | None = None) -> dict[str, Any]:
    if session is None:
        return dict(DEFAULT_BRIEF_GENERATION_POLICY)
    item = session.get(AppConfig, BRIEF_GENERATION_POLICY_CONFIG_KEY)
    raw = item.value if item else None
    try:
        return normalize_brief_generation_policy(raw)
    except ValueError:
        return dict(DEFAULT_BRIEF_GENERATION_POLICY)


def _normalize_granularity(value: str | None) -> str:
    granularity = str(value or "day").strip().lower()
    if granularity not in {"day", "week", "month"}:
        return "day"
    return granularity


def _tzinfo():
    return tz.gettz(settings.timezone) or tz.tzlocal()


def _local_date(ts: Any | None) -> date | None:
    if not ts:
        return None
    try:
        return ts.astimezone(_tzinfo()).date()
    except Exception:
        return None


def _parse_daily_run_time(value: str) -> time:
    match = _RUN_TIME_RE.fullmatch(str(value or "").strip())
    if not match:
        return time(4, 0)
    return time(hour=int(match.group("hour")), minute=int(match.group("minute")))


def _next_historical_run_at(*, now: datetime, run_time_value: str) -> datetime:
    tzinfo = _tzinfo()
    local_now = now.astimezone(tzinfo)
    run_time = _parse_daily_run_time(run_time_value)
    candidate = datetime.combine(local_now.date(), run_time).replace(tzinfo=tzinfo)
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(tz.tzutc())


def _desired_schedule_for_auto(
    *,
    now: datetime,
    granularity: str,
    period_start: date,
    last_ready_at: datetime | None,
    policy: dict[str, Any],
) -> tuple[datetime, str]:
    granularity = _normalize_granularity(granularity)
    today_period = brief_period_start(now.astimezone(_tzinfo()).date(), granularity)
    if period_start == today_period:
        cooldown_minutes = int(policy.get("latest_cooldown_minutes") or 0)
        if last_ready_at is None:
            return now, "latest_cooldown"
        scheduled_for = max(now, last_ready_at + timedelta(minutes=cooldown_minutes))
        return scheduled_for, "latest_cooldown"
    return _next_historical_run_at(
        now=now,
        run_time_value=str(policy.get("historical_daily_run_time") or DEFAULT_BRIEF_GENERATION_POLICY["historical_daily_run_time"]),
    ), "historical_batch"


def _merge_trigger_metadata(existing: dict[str, Any] | None, *, trigger_mode: str, reason: str, scheduled_by_policy: str) -> dict[str, Any]:
    params = dict(existing or {})
    params["trigger_mode"] = str(trigger_mode or "auto").strip() or "auto"
    params["trigger_reason"] = str(reason or "").strip() or "unknown"
    params["scheduled_by_policy"] = str(scheduled_by_policy or "").strip() or "manual_immediate"
    return params


def _brief_job_priority(trigger_mode: str) -> int:
    return 5 if str(trigger_mode or "").strip().lower() == "manual" else 2


def _create_brief_job(
    session: Session,
    *,
    type_: str,
    dedupe_key: str,
    params: dict[str, Any],
    scheduled_for: datetime,
    priority: int,
) -> uuid.UUID:
    job = Job(
        type=type_,
        status="pending",
        priority=priority,
        dedupe_key=dedupe_key,
        params=params,
        scheduled_for=scheduled_for,
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush([job])
    except IntegrityError:
        try:
            session.expunge(job)
        except Exception:
            pass
        pending = session.execute(
            select(Job)
            .where(Job.dedupe_key == dedupe_key, Job.status == "pending")
            .order_by(Job.created_at.asc(), Job.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        if pending:
            return pending.id
        raise

    session.add(JobEvent(job_id=job.id, level="info", message="enqueued", data={"type": type_, "dedupe_key": dedupe_key}))
    return job.id


def schedule_brief_refresh(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    period_start: date,
    trigger_mode: str,
    reason: str,
) -> uuid.UUID | None:
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return None

    granularity = _normalize_granularity(granularity or getattr(playlist, "brief_granularity", None))
    period_start = brief_period_start(period_start, granularity)
    trigger_mode = str(trigger_mode or "auto").strip().lower() or "auto"
    if trigger_mode not in {"auto", "manual"}:
        trigger_mode = "auto"

    dedupe_key, base_params = _brief_normalize_date_and_params(
        "brief.generate_period",
        {"playlist_id": str(playlist_id), "granularity": granularity, "period_start": period_start.isoformat()},
    )
    if not dedupe_key:
        return None

    now = utcnow()
    if trigger_mode == "manual":
        desired_scheduled_for = now
        scheduled_by_policy = "manual_immediate"
    else:
        policy = get_brief_generation_policy(session)
        last_ready_at = session.execute(
            select(Brief.updated_at)
            .where(
                Brief.playlist_id == playlist_id,
                Brief.granularity == granularity,
                Brief.period_start == period_start,
                Brief.status == "ready",
            )
            .order_by(Brief.updated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        desired_scheduled_for, scheduled_by_policy = _desired_schedule_for_auto(
            now=now,
            granularity=granularity,
            period_start=period_start,
            last_ready_at=last_ready_at,
            policy=policy,
        )

    params = _merge_trigger_metadata(
        base_params,
        trigger_mode=trigger_mode,
        reason=reason,
        scheduled_by_policy=scheduled_by_policy,
    )
    desired_priority = _brief_job_priority(trigger_mode)

    pending = session.execute(
        select(Job)
        .where(Job.dedupe_key == dedupe_key, Job.status == "pending")
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if pending:
        previous_schedule = pending.scheduled_for
        previous_priority = int(pending.priority or 0)
        pending.scheduled_for = min(previous_schedule or desired_scheduled_for, desired_scheduled_for)
        pending.priority = max(previous_priority, desired_priority)
        pending.params = _merge_trigger_metadata(
            pending.params,
            trigger_mode=trigger_mode,
            reason=reason,
            scheduled_by_policy=scheduled_by_policy,
        )
        pending.error_message = None
        pending.error_stack = None
        session.add(
            JobEvent(
                job_id=pending.id,
                level="info",
                message="brief job rescheduled",
                data={
                    "dedupe_key": dedupe_key,
                    "trigger_mode": trigger_mode,
                    "trigger_reason": reason,
                    "scheduled_for": pending.scheduled_for.isoformat() if pending.scheduled_for else None,
                    "priority": pending.priority,
                    "previous_scheduled_for": previous_schedule.isoformat() if previous_schedule else None,
                    "previous_priority": previous_priority,
                },
            )
        )
        return pending.id

    running = session.execute(
        select(Job)
        .where(Job.dedupe_key == dedupe_key, Job.status == "running")
        .order_by(Job.started_at.desc().nullslast(), Job.created_at.desc(), Job.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if running and trigger_mode == "manual":
        session.add(
            JobEvent(
                job_id=running.id,
                level="info",
                message="manual brief trigger ignored; job already running",
                data={"dedupe_key": dedupe_key, "trigger_reason": reason},
            )
        )
        return running.id

    job_id = _create_brief_job(
        session,
        type_="brief.generate_period",
        dedupe_key=dedupe_key,
        params=params,
        scheduled_for=desired_scheduled_for,
        priority=desired_priority,
    )
    if running and trigger_mode == "auto":
        session.add(
            JobEvent(
                job_id=job_id,
                level="info",
                message="follow-up brief job enqueued while previous run is active",
                data={"dedupe_key": dedupe_key, "trigger_reason": reason, "scheduled_for": desired_scheduled_for.isoformat()},
            )
        )
    return job_id


def schedule_brief_refresh_for_video(session: Session, *, video: Video, reason: str) -> int:
    if not llm_enabled():
        return 0

    ensure_video_published_at_backfilled(session)
    ts = video.published_at
    day = _local_date(ts)
    if not day:
        return 0
    admitted = session.execute(select(Video.id).where(Video.id == video.id, brief_admitted_video_expr()).limit(1)).scalar_one_or_none()
    if not admitted:
        return 0

    playlist_ids = (
        session.execute(select(PlaylistMedia.playlist_id).where(PlaylistMedia.media_id == video.media_id).distinct())
        .scalars()
        .all()
    )
    if not playlist_ids:
        return 0

    rows = session.execute(select(Playlist.id, Playlist.brief_granularity).where(Playlist.id.in_(list(playlist_ids)))).all()
    count = 0
    for playlist_id, granularity in rows:
        period_start = brief_period_start(day, _normalize_granularity(granularity))
        if schedule_brief_refresh(
            session,
            playlist_id=playlist_id,
            granularity=granularity,
            period_start=period_start,
            trigger_mode="auto",
            reason=reason,
        ):
            count += 1
    return count


def _period_starts_for_timestamps(*, timestamps: list[Any], granularity: str) -> list[date]:
    granularity = _normalize_granularity(granularity)
    period_starts: set[date] = set()
    for ts in timestamps:
        local_day = _local_date(ts)
        if local_day:
            period_starts.add(brief_period_start(local_day, granularity))
    return sorted(period_starts)


def schedule_brief_refresh_for_media_change(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    changed_media_ids: list[uuid.UUID],
    change_type: str,
) -> int:
    if not llm_enabled():
        return 0

    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return 0

    media_ids = list(dict.fromkeys(changed_media_ids or []))
    if not media_ids:
        return 0

    ensure_video_published_at_backfilled(session)
    co_ts = Video.published_at
    timestamps = session.execute(
        select(co_ts).where(Video.media_id.in_(list(media_ids)), brief_admitted_video_expr())
    ).scalars().all()
    period_starts = _period_starts_for_timestamps(
        timestamps=list(timestamps),
        granularity=getattr(playlist, "brief_granularity", None),
    )
    count = 0
    for period_start in period_starts:
        if schedule_brief_refresh(
            session,
            playlist_id=playlist_id,
            granularity=getattr(playlist, "brief_granularity", None),
            period_start=period_start,
            trigger_mode="auto",
            reason=change_type,
        ):
            count += 1
    return count
