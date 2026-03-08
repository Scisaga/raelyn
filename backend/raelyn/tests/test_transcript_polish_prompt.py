from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.handlers import _build_transcript_polish_prompt
from raelyn.services.transcript_polish_prompt import (
    DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE,
    render_transcript_polish_prompt,
)


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

    def test_custom_template_uses_chunk_and_indices(self) -> None:
        prompt = render_transcript_polish_prompt(template="片段 {index}/{total}\n内容:\n{chunk}\n", chunk="abc", index=2, total=5)
        self.assertEqual(prompt, "片段 2/5\n内容:\nabc")

    def test_custom_template_without_chunk_appends_original_text(self) -> None:
        prompt = render_transcript_polish_prompt(template="请润色以下内容", chunk="abc", index=1, total=1)
        self.assertIn("请润色以下内容", prompt)
        self.assertIn("原始文本：\nabc\n", prompt)

    def test_invalid_placeholder_falls_back_to_default_prompt(self) -> None:
        prompt = render_transcript_polish_prompt(template="bad {unknown}", chunk="abc", index=1, total=1)
        self.assertEqual(prompt, DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE.format(chunk="abc", index=1, total=1))

    def test_build_prompt_uses_custom_template_from_config_loader(self) -> None:
        with patch("raelyn.jobs.handlers.load_transcript_polish_prompt_template", return_value="x {chunk} {index}/{total}"):
            prompt = _build_transcript_polish_prompt(session=object(), chunk="abc", index=3, total=4)
        self.assertEqual(prompt, "x abc 3/4")


if __name__ == "__main__":
    unittest.main()
