from __future__ import annotations

from contextlib import contextmanager
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.config_api import ConfigUpsert
from raelyn.api.config_api import _sanitize_config_value_for_response
from raelyn.api.config_api import _validate_brief_generation_policy_value
from raelyn.api.config_api import _validate_inference_mode_value
from raelyn.api.config_api import _validate_llm_transcript_polish_prompt_value
from raelyn.api.config_api import _validate_volcengine_inference_config_value
from raelyn.api.config_api import get_config
from raelyn.api.config_api import put_config
from raelyn.models import AppConfig
from raelyn.services.provider_cookies import looks_like_netscape_cookie_file


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _FakeConfigSession:
    def __init__(self, *, items: list[AppConfig] | None = None, media_ids: list[uuid.UUID] | None = None):
        self.items = {item.key: item for item in items or []}
        self.media_ids = list(media_ids or [])

    def get(self, model, key):
        if model is AppConfig:
            return self.items.get(key)
        return None

    def add(self, item):
        if isinstance(item, AppConfig):
            self.items[item.key] = item

    def flush(self):
        return

    def execute(self, _stmt):
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

        with (
            patch("raelyn.api.config_api.session_scope", lambda: _session_scope(session)),
            patch("raelyn.api.config_api.settings.sync_cookie_recovery_max_entries", 200),
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
                {"media_id": str(media_id), "force": True, "max_entries": 200, "download_priority": 8},
            )
            self.assertEqual(call.kwargs["priority"], 5)

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
