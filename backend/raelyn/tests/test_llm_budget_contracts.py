from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import Settings
from raelyn.services import brief_prompt as bp, event_analysis as ea


class LlmBudgetContractTests(unittest.TestCase):
    def test_output_limits_cannot_disable_the_hard_budget(self) -> None:
        for field in ("EVENT_EXTRACTION_OLLAMA_NUM_PREDICT", "brief_ollama_num_predict"):
            for value in (0, -1):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    Settings(_env_file=None, **{field: value})

    def read_recording(self, pattern: str) -> dict:
        directory = os.getenv("RAELYN_LLM_BUDGET_CASES_DIR")
        if not directory:
            self.skipTest("需要有限预算实验的真实响应目录 RAELYN_LLM_BUDGET_CASES_DIR；参见实验记录")
        path = next(Path(directory).glob(pattern), None)
        if path is None:
            self.skipTest(f"缺少历史对照响应 {pattern}；先按实验记录复现对应失败样本")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_completed_real_response_with_duplicate_video_is_rejected(self) -> None:
        response = self.read_recording("response-1ac40fba-presence15-*.json")
        self.assertEqual(response["done_reason"], "stop")
        with self.assertRaisesRegex(ValueError, "repeats video_id"):
            ea.parse_event_extraction_batch_response(response["response"], expected_video_ids=["v1"])

    def test_real_brief_with_one_mistyped_source_is_rejected(self) -> None:
        case = self.read_recording("brief-6c9e5ddd.json")
        response = self.read_recording("response-12952be0-final-baseline-*.json")
        # 正常停止也不能证明正文结构完整，使用原始模型输出检查章节。
        self.assertEqual(response["done_reason"], "stop")
        self.assertTrue(any(
            error.startswith("缺少规定章节") for error in bp.validate_generated_brief(
                response["response"], template=case["template"], source_urls=[],
            )
        ))
        directory = Path(os.environ["RAELYN_LLM_BUDGET_CASES_DIR"])
        for path in directory.glob("verified-brief-6c9e5ddd-final-*.json"):
            payload = json.loads(json.loads(path.read_text(encoding="utf-8"))["text"])
            if all(isinstance(value, str) for value in payload.values()):
                markdown = "\n\n".join(f"## {heading}\n\n{content}" for heading, content in payload.items())
                self.assertIn(
                    "正文引用了不属于真实输入的来源",
                    bp.validate_generated_brief(markdown, template=case["template"], source_urls=case["urls"]),
                )
                return
        self.skipTest("缺少章节字符串 schema 的真实 URL 拼写失败响应")


if __name__ == "__main__":
    unittest.main()
