from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.config_api import _validate_llm_transcript_polish_prompt_value


class ConfigApiValidationTests(unittest.TestCase):
    def test_llm_transcript_polish_prompt_accepts_string(self) -> None:
        _validate_llm_transcript_polish_prompt_value({"text": "hello"})

    def test_llm_transcript_polish_prompt_accepts_empty_string(self) -> None:
        _validate_llm_transcript_polish_prompt_value({"text": ""})

    def test_llm_transcript_polish_prompt_rejects_non_string(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_llm_transcript_polish_prompt_value({"text": 123})


if __name__ == "__main__":
    unittest.main()
