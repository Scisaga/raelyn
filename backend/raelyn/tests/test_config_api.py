from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.config_api import _validate_llm_transcript_polish_prompt_value
from raelyn.api.config_api import _validate_brief_generation_policy_value
from raelyn.services.provider_cookies import looks_like_netscape_cookie_file


class ConfigApiValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
