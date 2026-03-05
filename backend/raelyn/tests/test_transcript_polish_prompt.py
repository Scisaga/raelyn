from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers import _build_transcript_polish_prompt


class TranscriptPolishPromptTests(unittest.TestCase):
    def test_prompt_requires_simplified_chinese_output_for_non_chinese_input(self) -> None:
        prompt = _build_transcript_polish_prompt(
            chunk="We have breaking news today. The President made the announcement.",
            index=1,
            total=1,
        )

        self.assertIn("最终输出必须是简体中文正文", prompt)
        self.assertIn("如果原文不是中文，请准确翻译为自然、通顺的简体中文", prompt)
        self.assertIn("不要保留大段外文原文", prompt)


if __name__ == "__main__":
    unittest.main()
