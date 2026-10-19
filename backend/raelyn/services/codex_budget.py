from __future__ import annotations

import logging
import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.db import engine, session_scope
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import AppConfig
from raelyn.services.codex_account import read_codex_rate_limits
from raelyn.services.pg_lock import try_lock, unlock
from raelyn.services.worker_role_pause import WORKER_ROLE_PAUSE_CONFIG_KEY
from raelyn.timeutil import utcnow


CODEX_BUDGET_CONFIG_KEY = "codex_daily_budget"
CODEX_QUOTA_PAUSE_REASON = "codex_daily_quota"
_CALL_LOCK_NAME = "codex:subscription-budget:generate"
_REFRESH_SECONDS = 60
_RESERVE_PERCENT = 0.0
_local_call_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _stamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _next_day(now: datetime, timezone_name: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone_name))
    return datetime.combine(local.date() + timedelta(days=1), time.min, local.tzinfo).astimezone(timezone.utc)


def _project_status(state: dict[str, Any], *, now: datetime, daily_limit_percent: float, timezone_name: str) -> dict[str, Any]:
    result = dict(state)
    day = now.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    limit = float(daily_limit_percent)
    enabled = limit > 0
    effective = max(0.0, limit - _RESERVE_PERCENT)
    if result.get("day") != day:
        result.update(
            day=day, used_percent=0.0, baseline_started_at=None, uncertain_day=None,
            status="quota_unavailable", resume_at=now.isoformat(),
            error="新的自然日尚未读取 Codex 官方额度。",
        )
    result.update(
        date=day, enabled=enabled, daily_limit_percent=limit,
        reserve_percent=_RESERVE_PERCENT, effective_limit_percent=effective, timezone=timezone_name,
        measurement_scope="account", metering_precision_percent=1,
        measurement_note="按账户周额度整数读数的增量累计，包含同账户其他客户端；官方整数且事后记账，不构成单请求的计费硬上限。",
    )
    result.setdefault("used_percent", 0.0)
    result.setdefault("status", "quota_unavailable")
    result.setdefault("resume_at", None)
    result.setdefault("weekly_used_percent", None)
    result.setdefault("weekly_resets_at", None)
    result.setdefault("last_checked_at", None)
    result.setdefault("error", "尚未读取 Codex 官方额度。")
    if not enabled:
        result.update(status="ready", resume_at=None, error=None)
    result["remaining_percent"] = max(0.0, effective - float(result["used_percent"])) if enabled else 0.0
    result["paused"] = enabled and result["status"] != "ready"
    result["pause_reason"] = CODEX_QUOTA_PAUSE_REASON if result["paused"] else None
    return result


def advance_codex_budget_state(
    state: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    *,
    now: datetime,
    daily_limit_percent: float,
    timezone_name: str = "Asia/Shanghai",
    error: str | None = None,
) -> dict[str, Any]:
    """按官方快照推进账户额度状态；不访问数据库或外部服务。"""

    previous = dict(state or {})
    result = _project_status(previous, now=now, daily_limit_percent=daily_limit_percent, timezone_name=timezone_name)
    result["last_checked_at"] = now.isoformat()
    if not result["enabled"]:
        return result

    weekly = snapshot.get("weekly") if snapshot is not None else None
    if weekly is None:
        result.update(
            status="quota_unavailable", error=error or "Codex 未返回可用的官方周额度窗口。",
            resume_at=(now + timedelta(seconds=_REFRESH_SECONDS)).isoformat(),
        )
        return _project_status(result, now=now, daily_limit_percent=daily_limit_percent, timezone_name=timezone_name)

    used = float(weekly["used_percent"])
    reset = int(weekly["resets_at"])
    resets_at = datetime.fromtimestamp(reset, timezone.utc)
    result.update(weekly_used_percent=used, weekly_resets_at=resets_at.isoformat())
    if resets_at <= now:
        result.update(
            status="quota_unavailable", error="Codex 周额度窗口已过期，等待官方额度更新。",
            resume_at=(now + timedelta(seconds=_REFRESH_SECONDS)).isoformat(),
        )
        return _project_status(result, now=now, daily_limit_percent=daily_limit_percent, timezone_name=timezone_name)

    same_day = previous.get("day") == result["day"]
    old_window = previous.get("weekly_window_id")
    bucket = str(snapshot.get("limit_id", "codex"))
    window = f"{bucket}:{reset}"
    old_used = previous.get("last_weekly_used_percent")
    same_window = False
    if old_window is not None:
        old_bucket, _separator, _old_reset = old_window.rpartition(":")
        observed_at = _stamp(previous.get("last_weekly_checked_at") or previous.get("last_checked_at"))
        window_start = resets_at - timedelta(minutes=weekly["window_minutes"])
        started_since_observation = observed_at is not None and observed_at < window_start <= now
        # resetsAt 不是稳定 ID。当前官方窗口仍覆盖上次成功观测时，保留首次标记；
        # 即使首次 deadline 已过，也只有新窗口确实在相邻观测间开始才能换周。
        same_window = old_bucket == bucket and not started_since_observation
    if old_window is None or old_used is None or (not same_day and not same_window):
        result.update(baseline_started_at=now.isoformat(), baseline_weekly_used_percent=used)
    elif same_window:
        # 同窗口较低的陈旧读数既不返还额度，也不降低下一次差分的基准。
        result["used_percent"] += max(0.0, used - float(old_used))
        used = max(used, float(old_used))
        if not same_day or result.get("baseline_started_at") is None:
            result.update(baseline_started_at=now.isoformat(), baseline_weekly_used_percent=float(old_used))
    else:
        # 同日跨周无法证明旧窗口尾部消耗，保留已知用量并暂停当日。
        result["used_percent"] += used
        result["uncertain_day"] = result["day"]
    result.update(
        weekly_window_id=old_window if same_window else window,
        last_weekly_used_percent=used,
        last_weekly_checked_at=now.isoformat(),
    )

    if used >= 100:
        result.update(status="weekly_limit", resume_at=resets_at.isoformat(), error="Codex 官方周额度已耗尽，等待实际重置。")
    elif result.get("uncertain_day") == result["day"]:
        result.update(
            status="daily_limit", resume_at=_next_day(now, timezone_name).isoformat(),
            error="周窗口已重置，无法核实旧窗口尾部用量；今日暂停，次日建立新基线。",
        )
    elif float(result["used_percent"]) >= result["effective_limit_percent"]:
        result.update(status="daily_limit", resume_at=_next_day(now, timezone_name).isoformat(), error=None)
    else:
        result.update(status="ready", resume_at=None, error=None)
    return _project_status(result, now=now, daily_limit_percent=daily_limit_percent, timezone_name=timezone_name)


def _load_state(session: Session) -> dict[str, Any]:
    item = session.get(AppConfig, CODEX_BUDGET_CONFIG_KEY)
    return dict(item.value) if item else {}


def get_codex_budget_status(session: Session | None = None) -> dict[str, Any]:
    """只读取已保存状态，不查询账号额度，不改变暂停状态。"""

    from raelyn.services.inference import get_effective_llm_config

    if session is None:
        with session_scope() as current:
            state = _load_state(current)
            active = get_effective_llm_config(current).provider == "codex"
    else:
        state = _load_state(session)
        active = get_effective_llm_config(session).provider == "codex"
    status = _project_status(
        state, now=utcnow(), daily_limit_percent=settings.codex_daily_weekly_limit_percent,
        timezone_name=settings.timezone,
    )
    status["active"] = active
    if not active:
        status.update(status="ready", paused=False, pause_reason=None, resume_at=None, error=None)
    return status


def _sync_worker_pause(session: Session, state: dict[str, Any]) -> None:
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    session.execute(insert(AppConfig).values(key=WORKER_ROLE_PAUSE_CONFIG_KEY, value={}).on_conflict_do_nothing(index_elements=[AppConfig.key]))
    current = session.execute(select(AppConfig.value).where(AppConfig.key == WORKER_ROLE_PAUSE_CONFIG_KEY)).scalar_one()
    pause = current.get("ai") or {}
    if pause.get("paused") and pause.get("reason") != CODEX_QUOTA_PAUSE_REASON:
        return
    updated = dict(current)
    if state["paused"]:
        updated["ai"] = {
            "paused": True, "reason": CODEX_QUOTA_PAUSE_REASON,
            "message": "Codex 订阅额度门控已暂停 AI 任务；等待额度刷新或允许恢复时间。",
            "set_at": pause.get("set_at") or utcnow().isoformat(),
        }
    elif pause.get("reason") == CODEX_QUOTA_PAUSE_REASON:
        updated.pop("ai", None)
    if updated != current:
        # 原子比较整个旧值，期间有人手动暂停时不覆盖其新值。
        session.execute(update(AppConfig).where(
            AppConfig.key == WORKER_ROLE_PAUSE_CONFIG_KEY, AppConfig.value == current,
        ).values(value=updated, updated_at=utcnow()).execution_options(synchronize_session=False))


def _persist_state(session: Session, state: dict[str, Any]) -> None:
    item = session.execute(select(AppConfig).where(AppConfig.key == CODEX_BUDGET_CONFIG_KEY).with_for_update()).scalar_one_or_none()
    if item is None:
        session.add(AppConfig(key=CODEX_BUDGET_CONFIG_KEY, value=state))
    else:
        item.value = state
        item.updated_at = utcnow()
    _sync_worker_pause(session, state)


def _release_call_lock(connection: Connection) -> None:
    try:
        unlock(connection, _CALL_LOCK_NAME)
    except SQLAlchemyError as exc:
        # PG 会话锁随物理连接结束释放；普通 close 只归池，不能保留可能仍持锁的连接。
        # invalidate 立即丢弃该 DBAPI 连接，避免释放失败覆盖已经生成的正文或原始业务异常。
        connection.invalidate()
        logger.warning("Codex 额度锁释放失败，已丢弃持锁连接；error_type=%s", type(exc).__name__)


@contextmanager
def _call_lock() -> Iterator[bool]:
    if not _local_call_lock.acquire(blocking=False):
        yield False
        return
    try:
        if engine.dialect.name != "postgresql":
            # 隔离 SQLite 集成测试使用进程内互斥；生产多进程门控使用 PostgreSQL。
            yield True
            return
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = try_lock(connection, _CALL_LOCK_NAME)
            try:
                yield acquired
            finally:
                if acquired:
                    _release_call_lock(connection)
    finally:
        _local_call_lock.release()


def _read_and_record() -> dict[str, Any]:
    with session_scope() as session:
        previous = _load_state(session)
    try:
        snapshot = read_codex_rate_limits()
        error = None
    except (RuntimeError, httpx.TimeoutException) as exc:
        snapshot = None
        error = "Codex 官方订阅额度暂时无法读取，已暂停后续生成。"
        logger.warning("Codex 官方额度读取失败；error_type=%s", type(exc).__name__)
    state = advance_codex_budget_state(
        previous, snapshot, now=utcnow(),
        daily_limit_percent=settings.codex_daily_weekly_limit_percent,
        timezone_name=settings.timezone, error=error,
    )
    with session_scope() as session:
        _persist_state(session, state)
    return state


def refresh_codex_budget(*, force: bool = False) -> dict[str, Any]:
    """刷新暂停中的额度状态；跨日与强制刷新不受一分钟节流限制。"""

    status = get_codex_budget_status()
    if not status["enabled"] or not status["active"]:
        with session_scope() as session:
            _sync_worker_pause(session, status)
        return status
    checked = _stamp(status.get("last_checked_at"))
    if not force and checked and checked.astimezone(ZoneInfo(settings.timezone)).date() == utcnow().astimezone(ZoneInfo(settings.timezone)).date() and (utcnow() - checked).total_seconds() < _REFRESH_SECONDS:
        return status
    with _call_lock() as acquired:
        return _read_and_record() if acquired else status


def _defer(state: dict[str, Any]) -> NoReturn:
    resume = _stamp(state.get("resume_at"))
    delay = max(1, math.ceil((resume - utcnow()).total_seconds())) if resume else _REFRESH_SECONDS
    raise JobReschedule(delay_seconds=delay, reason=f"codex_budget_{state['status']}")


@contextmanager
def codex_budget_guard() -> Iterator[dict[str, Any]]:
    if settings.codex_daily_weekly_limit_percent <= 0:
        yield get_codex_budget_status()
        return
    with _call_lock() as acquired:
        if not acquired:
            raise JobReschedule(delay_seconds=15, reason="codex_budget_call_busy")
        try:
            state = _read_and_record()
        except SQLAlchemyError as exc:
            logger.warning("Codex 额度前置记账失败；error_type=%s", type(exc).__name__)
            _defer({"status": "quota_unavailable", "resume_at": None})
        if state["paused"]:
            _defer(state)
        try:
            yield state
        finally:
            try:
                _read_and_record()
            except Exception as exc:
                # 已生成的正文必须交给上层保存；下一次生成仍须重新读取并成功落账。
                logger.warning("Codex 额度后置记账失败，后续请求必须重新核验；error_type=%s", type(exc).__name__)
