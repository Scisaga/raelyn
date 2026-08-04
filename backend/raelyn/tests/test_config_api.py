from __future__ import annotations

from contextlib import contextmanager
import sys
import unittest
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.config_api import ConfigUpsert
from raelyn.api.config_api import _cookie_recovery_scheduled_for
from raelyn.api.config_api import _schedule_cookie_recovery_sync
from raelyn.api.config_api import _sanitize_config_value_for_response
from raelyn.api.config_api import _validate_brief_generation_policy_value
from raelyn.api.config_api import _validate_inference_mode_value
from raelyn.api.config_api import _validate_llm_transcript_polish_prompt_value
from raelyn.api.config_api import _validate_volcengine_inference_config_value
from raelyn.api.config_api import get_config
from raelyn.api.config_api import put_config
from raelyn.models import AppConfig, Job, JobEvent
from raelyn.services.provider_cookies import looks_like_netscape_cookie_file


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        if not self.rows:
            return None
        if len(self.rows) != 1:
            raise AssertionError(f"expected at most one row, got {len(self.rows)}")
        return self.rows[0]


class _FakeConfigSession:
    def __init__(
        self,
        *,
        items: list[AppConfig] | None = None,
        media_ids: list[uuid.UUID] | None = None,
        pending_jobs: list[Job] | None = None,
    ):
        self.items = {item.key: item for item in items or []}
        self.media_ids = list(media_ids or [])
        self.pending_jobs = list(pending_jobs or [])
        self.job_events: list[JobEvent] = []

    def get(self, model, key):
        if model is AppConfig:
            return self.items.get(key)
        return None

    def add(self, item):
        if isinstance(item, AppConfig):
            self.items[item.key] = item
        elif isinstance(item, JobEvent):
            self.job_events.append(item)

    def flush(self):
        return

    def execute(self, stmt):
        entity = (stmt.column_descriptions or [{}])[0].get("entity")
        if entity is Job:
            return _FakeResult(self.pending_jobs)
        return _FakeResult(self.media_ids)


class _FakeReadConfigSession(_FakeConfigSession):
    def execute(self, _stmt):
        return _FakeResult(list(self.items.values()))


@contextmanager
def _session_scope(session):
    yield session


class ConfigApiValidationTests(unittest.TestCase):
    def test_config_response_never_returns_youtube_cookies(self) -> None:
        value = {"text": ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret-value"}
        self.assertEqual(
            _sanitize_config_value_for_response("ytdlp_cookies_youtube", value),
            {"configured": True},
        )

    def test_config_response_reports_empty_bilibili_cookies(self) -> None:
        self.assertEqual(
            _sanitize_config_value_for_response("ytdlp_cookies_bilibili", {"text": ""}),
            {"configured": False},
        )

    def test_get_config_does_not_return_cookie_contents(self) -> None:
        session = _FakeReadConfigSession(
            items=[
                AppConfig(
                    key="ytdlp_cookies_youtube",
                    value={"text": ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret-value"},
                )
            ]
        )
        with patch("raelyn.api.config_api.session_scope", lambda: _session_scope(session)):
            result = get_config()

        self.assertEqual(result, {"data": {"ytdlp_cookies_youtube": {"configured": True}}})
        self.assertNotIn("secret-value", str(result))

    def test_llm_transcript_polish_prompt_accepts_string(self) -> None:
        _validate_llm_transcript_polish_prompt_value({"text": "hello"})

    def test_llm_transcript_polish_prompt_accepts_empty_string(self) -> None:
        _validate_llm_transcript_polish_prompt_value({"text": ""})

    def test_llm_transcript_polish_prompt_rejects_non_string(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_llm_transcript_polish_prompt_value({"text": 123})

    def test_brief_generation_policy_accepts_valid_value(self) -> None:
        _validate_brief_generation_policy_value({"latest_cooldown_minutes": 30, "historical_daily_run_time": "05:15"})

    def test_brief_generation_policy_rejects_negative_cooldown(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_brief_generation_policy_value({"latest_cooldown_minutes": -1, "historical_daily_run_time": "04:00"})

    def test_brief_generation_policy_rejects_invalid_time(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_brief_generation_policy_value({"latest_cooldown_minutes": 30, "historical_daily_run_time": "24:00"})

    def test_looks_like_netscape_cookie_file_accepts_valid_line(self) -> None:
        text = ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tabc123"
        self.assertTrue(looks_like_netscape_cookie_file(text))

    def test_looks_like_netscape_cookie_file_rejects_invalid_line(self) -> None:
        text = "SID=abc123; Domain=.youtube.com"
        self.assertFalse(looks_like_netscape_cookie_file(text))

    def test_inference_mode_accepts_local_and_volcengine(self) -> None:
        _validate_inference_mode_value({"value": "local"})
        _validate_inference_mode_value({"value": "volcengine"})

    def test_inference_mode_rejects_unknown_value(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_inference_mode_value({"value": "other"})

    def test_volcengine_inference_config_accepts_strings_and_timeouts(self) -> None:
        _validate_volcengine_inference_config_value(
            {
                "api_key": "ark-key",
                "llm_model": "doubao-seed",
                "asr_model": "bigmodel",
                "asr_app_key": "app",
                "asr_access_key": "access",
                "llm_timeout_seconds": 30,
                "asr_timeout_seconds": "45",
            }
        )

    def test_volcengine_inference_config_rejects_invalid_timeout(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_volcengine_inference_config_value({"llm_timeout_seconds": 0})

    def test_put_youtube_cookies_clears_provider_pause_and_enqueues_recovery_syncs(self) -> None:
        media_ids = [uuid.uuid4(), uuid.uuid4()]
        provider_pause = AppConfig(
            key="provider_pause",
            value={"youtube": {"paused": True, "reason": "ytdlp_cookies_expired", "message": "expired"}},
        )
        session = _FakeConfigSession(items=[provider_pause], media_ids=media_ids)
        payload = ConfigUpsert(value={"text": ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tabc123"})
        now = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)

        with (
            patch("raelyn.api.config_api.session_scope", lambda: _session_scope(session)),
            patch("raelyn.api.config_api.settings.sync_cookie_recovery_max_entries", 200),
            patch("raelyn.api.config_api.settings.sync_interval_minutes", 120),
            patch("raelyn.api.config_api.utcnow", return_value=now),
            patch("raelyn.api.config_api.enqueue_job") as enqueue_job,
        ):
            result = put_config("ytdlp_cookies_youtube", payload)

        self.assertEqual(result, {"ok": True})
        self.assertNotIn("youtube", provider_pause.value)
        self.assertEqual(enqueue_job.call_count, 2)
        for call, media_id in zip(enqueue_job.call_args_list, media_ids):
            self.assertEqual(call.kwargs["type_"], "media.sync_videos")
            self.assertEqual(
                call.kwargs["params"],
                {
                    "media_id": str(media_id),
                    "force": True,
                    "max_entries": 200,
                    "download_priority": 8,
                    "cookie_recovery": True,
                },
            )
            self.assertEqual(call.kwargs["priority"], 5)
            self.assertEqual(call.kwargs["scheduled_for"], now + timedelta(hours=media_ids.index(media_id)))
        self.assertEqual(len(session.job_events), 2)

    def test_cookie_recovery_converts_pending_public_discovery_and_spreads_schedule(self) -> None:
        media_id = uuid.uuid4()
        pending = Job(
            id=uuid.uuid4(),
            type="media.sync_videos",
            status="pending",
            priority=0,
            dedupe_key=f"media.sync_videos:{media_id}",
            params={"media_id": str(media_id), "public_discovery": True, "max_entries": 200},
        )
        session = _FakeConfigSession(pending_jobs=[pending])
        scheduled_for = datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)

        _schedule_cookie_recovery_sync(
            session,
            provider="youtube",
            media_id=media_id,
            max_entries=200,
            scheduled_for=scheduled_for,
        )

        self.assertEqual(
            pending.params,
            {
                "media_id": str(media_id),
                "force": True,
                "max_entries": 200,
                "download_priority": 8,
                "cookie_recovery": True,
            },
        )
        self.assertEqual(pending.priority, 5)
        self.assertEqual(pending.scheduled_for, scheduled_for)
        self.assertEqual(session.job_events[0].message, "pending sync converted to cookie recovery")

    def test_cookie_recovery_schedule_spans_one_normal_sync_interval(self) -> None:
        now = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
        with patch("raelyn.api.config_api.settings.sync_interval_minutes", 120):
            scheduled = [
                _cookie_recovery_scheduled_for(index=index, total=4, now=now)
                for index in range(4)
            ]

        self.assertEqual(
            scheduled,
            [
                now,
                now + timedelta(minutes=30),
                now + timedelta(minutes=60),
                now + timedelta(minutes=90),
            ],
        )

    def test_put_empty_youtube_cookies_does_not_clear_pause_or_enqueue_recovery(self) -> None:
        provider_pause = AppConfig(
            key="provider_pause",
            value={"youtube": {"paused": True, "reason": "ytdlp_cookies_expired", "message": "expired"}},
        )
        session = _FakeConfigSession(items=[provider_pause], media_ids=[uuid.uuid4()])
        payload = ConfigUpsert(value={"text": ""})

        with (
            patch("raelyn.api.config_api.session_scope", lambda: _session_scope(session)),
            patch("raelyn.api.config_api.enqueue_job") as enqueue_job,
        ):
            result = put_config("ytdlp_cookies_youtube", payload)

        self.assertEqual(result, {"ok": True})
        self.assertIn("youtube", provider_pause.value)
        enqueue_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
