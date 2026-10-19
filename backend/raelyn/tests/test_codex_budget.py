"""额度状态转移与真实隔离配置库测试；不读取账号、不运行生成。"""
from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from raelyn.services.codex_budget import _release_call_lock
from raelyn.services.codex_budget import advance_codex_budget_state as _advance
from raelyn.tests.test_codex_inference import run_isolated_inference_case


_NOW = datetime(2026, 10, 16, 1, tzinfo=timezone.utc)
_RESET = 1792650852


def snapshot(used: float, reset: int = _RESET) -> dict:
    # 复用真实 CLI 的归一化周窗口形状；额度变化和时间由测试显式给定。
    return {"limit_id": "codex", "weekly": {"used_percent": used, "resets_at": reset, "window_minutes": 10080}}


def advance_codex_budget_state(state, reading, *, now, daily_limit_percent=50):
    return _advance(state, reading, now=now, daily_limit_percent=daily_limit_percent)


class CodexBudgetStateTests(unittest.TestCase):
    def test_first_sample_starts_account_baseline_without_reserving_allowance(self) -> None:
        state = advance_codex_budget_state(None, snapshot(15), now=_NOW)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["used_percent"], 0)
        self.assertEqual(state["reserve_percent"], 0)
        self.assertEqual(state["effective_limit_percent"], 50)
        self.assertEqual(state["remaining_percent"], 50)
        self.assertEqual(state["baseline_started_at"], _NOW.isoformat())
        self.assertEqual(state["measurement_scope"], "account")

    def test_observed_daily_growth_reaches_effective_limit_and_waits_for_local_midnight(self) -> None:
        state = advance_codex_budget_state(None, snapshot(15), now=_NOW)
        state = advance_codex_budget_state(state, snapshot(64), now=_NOW + timedelta(minutes=1))
        self.assertFalse(state["paused"])
        self.assertEqual(state["remaining_percent"], 1)
        state = advance_codex_budget_state(state, snapshot(65), now=_NOW + timedelta(minutes=2))
        self.assertEqual(state["status"], "daily_limit")
        self.assertTrue(state["paused"])
        self.assertEqual(state["remaining_percent"], 0)
        self.assertEqual(state["resume_at"], "2026-10-16T16:00:00+00:00")

    def test_configured_limit_is_used_without_a_reserved_percentage_point(self) -> None:
        for limit in (10, 25, 50):
            with self.subTest(limit=limit):
                state = advance_codex_budget_state(None, snapshot(5), now=_NOW, daily_limit_percent=limit)
                state = advance_codex_budget_state(state, snapshot(5 + limit), now=_NOW + timedelta(minutes=1), daily_limit_percent=limit)
                self.assertEqual(state["daily_limit_percent"], limit)
                self.assertEqual(state["effective_limit_percent"], limit)
                self.assertEqual(state["status"], "daily_limit")

    def test_stale_lower_percent_does_not_refund_or_charge_growth_twice(self) -> None:
        state = advance_codex_budget_state(None, snapshot(15), now=_NOW)
        for used in (17, 16, 18):
            state = advance_codex_budget_state(state, snapshot(used), now=_NOW + timedelta(minutes=used))
        self.assertEqual(state["used_percent"], 3)
        self.assertEqual(state["last_weekly_used_percent"], 18)

    def test_reset_time_correction_is_not_a_new_week_or_a_second_charge(self) -> None:
        for correction in (-60, -1, 1, 60):
            with self.subTest(correction=correction):
                state = advance_codex_budget_state(None, snapshot(16), now=_NOW)
                original_window = state["weekly_window_id"]
                state = advance_codex_budget_state(state, snapshot(16, _RESET + correction), now=_NOW + timedelta(minutes=1))
                self.assertEqual(state["status"], "ready")
                self.assertEqual(state["used_percent"], 0)
                self.assertEqual(state["weekly_window_id"], original_window)
                self.assertIsNone(state.get("uncertain_day"))
                state = advance_codex_budget_state(state, snapshot(17, _RESET), now=_NOW + timedelta(minutes=2))
                self.assertEqual(state["used_percent"], 1)
                self.assertEqual(state["status"], "ready")

    def test_canonical_deadline_passing_does_not_override_current_official_window(self) -> None:
        canonical_reset = int(_NOW.timestamp()) + 60
        state = advance_codex_budget_state(None, snapshot(16, canonical_reset), now=_NOW)
        original_window = state["weekly_window_id"]
        after_canonical = _NOW + timedelta(seconds=61)
        state = advance_codex_budget_state(state, snapshot(16, canonical_reset + 2), now=after_canonical)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["used_percent"], 0)
        self.assertEqual(state["weekly_window_id"], original_window)
        self.assertIsNone(state.get("uncertain_day"))

    def test_next_shanghai_day_resumes_without_reclaiming_previous_daily_use(self) -> None:
        state = advance_codex_budget_state(None, snapshot(15), now=_NOW)
        state = advance_codex_budget_state(state, snapshot(65), now=_NOW + timedelta(minutes=1))
        tomorrow = datetime(2026, 10, 16, 16, 1, tzinfo=timezone.utc)
        state = advance_codex_budget_state(state, snapshot(65), now=tomorrow)
        self.assertEqual(state["day"], "2026-10-17")
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["used_percent"], 0)
        self.assertEqual(state["baseline_started_at"], tomorrow.isoformat())

    def test_generation_delta_observed_after_midnight_counts_toward_new_day(self) -> None:
        before = datetime(2026, 10, 16, 15, 59, tzinfo=timezone.utc)
        state = advance_codex_budget_state(None, snapshot(20), now=before)
        state = advance_codex_budget_state(state, snapshot(22), now=before + timedelta(minutes=2))
        self.assertEqual(state["day"], "2026-10-17")
        self.assertEqual(state["used_percent"], 2)

    def test_unavailable_or_missing_weekly_window_fails_closed_without_zeroing_usage(self) -> None:
        initial = advance_codex_budget_state(None, snapshot(15), now=_NOW)
        initial = advance_codex_budget_state(initial, snapshot(18), now=_NOW + timedelta(minutes=1))
        for reading in (None, {"weekly": None}):
            with self.subTest(reading=reading):
                state = advance_codex_budget_state(initial, reading, now=_NOW + timedelta(minutes=2))
                self.assertEqual(state["status"], "quota_unavailable")
                self.assertTrue(state["paused"])
                self.assertEqual(state["used_percent"], 3)

    def test_weekly_exhaustion_uses_official_reset_without_credits(self) -> None:
        state = advance_codex_budget_state(None, {
            **snapshot(100), "credits": {"has_credits": True, "unlimited": False},
            "reset_credits_available_count": 3,
        }, now=_NOW)
        self.assertEqual(state["status"], "weekly_limit")
        self.assertEqual(state["resume_at"], datetime.fromtimestamp(_RESET, timezone.utc).isoformat())
        self.assertTrue(state["paused"])

    def test_cross_week_unknown_tail_pauses_same_day_then_accepts_next_day_baseline(self) -> None:
        before_reset = datetime.fromtimestamp(_RESET, timezone.utc) - timedelta(minutes=2)
        state = advance_codex_budget_state(None, snapshot(90), now=before_reset)
        state = advance_codex_budget_state(state, snapshot(92), now=before_reset + timedelta(minutes=1))
        state = advance_codex_budget_state(state, snapshot(1, _RESET + 604800), now=before_reset + timedelta(minutes=3))
        self.assertEqual(state["status"], "daily_limit")
        self.assertEqual(state["used_percent"], 3)
        self.assertEqual(state["uncertain_day"], "2026-10-22")
        state = advance_codex_budget_state(state, snapshot(1, _RESET + 604800), now=before_reset + timedelta(days=1))
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["used_percent"], 0)

    def test_early_reset_is_identified_by_window_start_between_successful_observations(self) -> None:
        state = advance_codex_budget_state(None, snapshot(90), now=_NOW)
        state = advance_codex_budget_state(state, snapshot(92), now=_NOW + timedelta(minutes=1))
        # 中间读取失败不能抹掉最后一次成功观测，否则会漏掉其间真实发生的提前重置。
        state = advance_codex_budget_state(state, None, now=_NOW + timedelta(minutes=3))
        next_reset = int((_NOW + timedelta(minutes=2, days=7)).timestamp())
        state = advance_codex_budget_state(state, snapshot(1, next_reset), now=_NOW + timedelta(minutes=4))
        self.assertEqual(state["status"], "daily_limit")
        self.assertEqual(state["used_percent"], 3)
        self.assertEqual(state["weekly_window_id"], f"codex:{next_reset}")
        self.assertEqual(state["last_weekly_checked_at"], (_NOW + timedelta(minutes=4)).isoformat())

    def test_zero_limit_disables_gate_and_expired_window_does_not_create_allowance(self) -> None:
        disabled = advance_codex_budget_state(None, None, now=_NOW, daily_limit_percent=0)
        self.assertFalse(disabled["enabled"])
        self.assertFalse(disabled["paused"])
        expired = advance_codex_budget_state(None, snapshot(1, int(_NOW.timestamp()) - 1), now=_NOW)
        self.assertEqual(expired["status"], "quota_unavailable")
        self.assertTrue(expired["paused"])


class CodexBudgetDatabaseTests(unittest.TestCase):
    def test_guard_serializes_calls_and_rejects_unavailable_quota_for_a_codex_draft(self) -> None:
        run_isolated_inference_case(self, """
from raelyn.jobs.reschedule import JobReschedule
from raelyn.services.codex_budget import (
    CODEX_BUDGET_CONFIG_KEY, CODEX_QUOTA_PAUSE_REASON, _call_lock, codex_budget_guard,
)

with _call_lock() as acquired:
    assert acquired
    try:
        with codex_budget_guard():
            raise AssertionError("已有 Codex 请求持锁时不能进入生成")
    except JobReschedule as exc:
        assert exc.reason == "codex_budget_call_busy" and exc.delay_seconds == 15
try:
    with codex_budget_guard():
        raise AssertionError("即使保存的是本地服务，Codex 草稿也必须先读取额度")
except JobReschedule as exc:
    assert exc.reason == "codex_budget_quota_unavailable"
    assert 1 <= exc.delay_seconds <= 60
with SessionLocal() as session:
    assert session.get(AppConfig, CODEX_BUDGET_CONFIG_KEY).value["status"] == "quota_unavailable"
    assert session.get(AppConfig, "worker_role_pause").value["ai"]["reason"] == CODEX_QUOTA_PAUSE_REASON
""", environment={
            "CODEX_DAILY_WEEKLY_LIMIT_PERCENT": "50", "LOCAL_LLM_PROVIDER": "local",
            "CODEX_COMMAND": "/not/installed/codex",
        })

    def test_real_isolated_config_database_persists_budget_without_overriding_manual_pause(self) -> None:
        run_isolated_inference_case(self, """
from datetime import datetime, timedelta, timezone
from raelyn.services.codex_budget import (
    CODEX_BUDGET_CONFIG_KEY, CODEX_QUOTA_PAUSE_REASON,
    _persist_state, advance_codex_budget_state, get_codex_budget_status,
)
from raelyn.timeutil import utcnow

now = utcnow()
reading = {"limit_id": "codex", "weekly": {
    "used_percent": 15, "resets_at": int((now + timedelta(days=7)).timestamp()), "window_minutes": 10080,
}}
ready = advance_codex_budget_state(None, reading, now=now, daily_limit_percent=50)
limited = advance_codex_budget_state(ready, {**reading, "weekly": {**reading["weekly"], "used_percent": 65}}, now=now, daily_limit_percent=50)
with SessionLocal.begin() as session:
    _persist_state(session, limited)
with SessionLocal() as session:
    status = get_codex_budget_status(session)
    assert status["status"] == "daily_limit"
    assert session.get(AppConfig, "worker_role_pause").value["ai"]["reason"] == CODEX_QUOTA_PAUSE_REASON
with SessionLocal.begin() as session:
    _persist_state(session, ready)
with SessionLocal() as session:
    assert "ai" not in session.get(AppConfig, "worker_role_pause").value
with SessionLocal.begin() as session:
    pause = session.get(AppConfig, "worker_role_pause")
    pause.value = {"ai": {"paused": True, "reason": "manual_worker_role_pause", "message": "人工暂停"}}
with SessionLocal.begin() as session:
    _persist_state(session, limited)
with SessionLocal.begin() as session:
    _persist_state(session, ready)
with SessionLocal() as session:
    assert session.get(AppConfig, "worker_role_pause").value["ai"]["reason"] == "manual_worker_role_pause"
    before = session.get(AppConfig, CODEX_BUDGET_CONFIG_KEY).updated_at
    get_codex_budget_status(session)
    assert session.get(AppConfig, CODEX_BUDGET_CONFIG_KEY).updated_at == before
""", environment={"CODEX_DAILY_WEEKLY_LIMIT_PERCENT": "50", "LOCAL_LLM_PROVIDER": "codex"})

    def test_local_provider_does_not_query_codex_and_only_clears_its_own_pause(self) -> None:
        run_isolated_inference_case(self, """
from datetime import timedelta
from raelyn.services.codex_budget import (
    CODEX_BUDGET_CONFIG_KEY, CODEX_QUOTA_PAUSE_REASON,
    _persist_state, advance_codex_budget_state, get_codex_budget_status, refresh_codex_budget,
)
from raelyn.timeutil import utcnow

now = utcnow()
reading = {"limit_id": "codex", "weekly": {
    "used_percent": 15, "resets_at": int((now + timedelta(days=7)).timestamp()), "window_minutes": 10080,
}}
ready = advance_codex_budget_state(None, reading, now=now, daily_limit_percent=50)
limited = advance_codex_budget_state(ready, {**reading, "weekly": {**reading["weekly"], "used_percent": 65}}, now=now, daily_limit_percent=50)
with SessionLocal.begin() as session:
    _persist_state(session, limited)
with SessionLocal() as session:
    before = dict(session.get(AppConfig, CODEX_BUDGET_CONFIG_KEY).value)
    assert session.get(AppConfig, "worker_role_pause").value["ai"]["reason"] == CODEX_QUOTA_PAUSE_REASON
status = refresh_codex_budget(force=True)
assert status["active"] is False and status["paused"] is False
assert status["status"] == "ready"
with SessionLocal() as session:
    assert session.get(AppConfig, CODEX_BUDGET_CONFIG_KEY).value == before
    assert "ai" not in session.get(AppConfig, "worker_role_pause").value
with SessionLocal.begin() as session:
    pause = session.get(AppConfig, "worker_role_pause")
    pause.value = {"ai": {"paused": True, "reason": "manual_worker_role_pause", "message": "人工暂停"}}
refresh_codex_budget(force=True)
with SessionLocal() as session:
    assert session.get(AppConfig, "worker_role_pause").value["ai"]["reason"] == "manual_worker_role_pause"
    assert get_codex_budget_status(session)["paused"] is False
""", environment={
            "CODEX_DAILY_WEEKLY_LIMIT_PERCENT": "50", "LOCAL_LLM_PROVIDER": "local",
            "CODEX_COMMAND": "/not/installed/codex",
        })


class CodexBudgetLockReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        # 使用真实 DBAPI 连接；SQLite 缺少 PG unlock 函数，实际执行会触发 SQLAlchemyError。
        self.engine = create_engine("sqlite://", poolclass=QueuePool, pool_size=1, max_overflow=0)
        self.addCleanup(self.engine.dispose)

    def test_failed_unlock_discards_physical_connection_without_raising(self) -> None:
        with self.engine.connect() as connection:
            original = connection.connection.driver_connection
            with self.assertLogs("raelyn.services.codex_budget", level="WARNING") as logs:
                _release_call_lock(connection)
            self.assertTrue(connection.invalidated)
            with self.assertRaises(sqlite3.ProgrammingError):
                original.execute("select 1")
            self.assertIn("error_type=OperationalError", logs.output[0])
            self.assertNotIn("pg_advisory_unlock", logs.output[0])
        with self.engine.connect() as replacement:
            self.assertIsNot(replacement.connection.driver_connection, original)
            self.assertEqual(replacement.execute(text("select 1")).scalar_one(), 1)

    def test_failed_unlock_does_not_replace_original_business_error(self) -> None:
        with self.engine.connect() as connection:
            with self.assertLogs("raelyn.services.codex_budget", level="WARNING"):
                with self.assertRaisesRegex(ValueError, "原始业务错误"):
                    try:
                        raise ValueError("原始业务错误")
                    finally:
                        _release_call_lock(connection)
            self.assertTrue(connection.invalidated)


if __name__ == "__main__":
    unittest.main()
