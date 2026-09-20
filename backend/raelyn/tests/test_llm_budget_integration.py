"""复用上游真实输入，串行验证有限预算下的完整响应；不写业务库，不创建 domain 数据。

先使用 raelyn.tools.capture_llm_budget_cases 采集失败任务，再显式执行：
RAELYN_LLM_BUDGET_CASES_DIR=/tmp/llm-budget-cases RAELYN_RUN_LLM_BUDGET_LIVE=1 \
    PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_llm_budget_integration
默认只读取已保存的真实响应；缺少输入/响应时 skip。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import unittest
from datetime import date
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.services import brief_prompt as bp, event_analysis as ea
from raelyn.services.inference import get_effective_llm_config
from raelyn.services.llm import llm_generate


class LlmBudgetIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = os.getenv("RAELYN_LLM_BUDGET_CASES_DIR", "")
        if not directory:
            self.skipTest("请先用 raelyn.tools.capture_llm_budget_cases 采集真实失败任务并设置 RAELYN_LLM_BUDGET_CASES_DIR")
        self.directory = Path(directory)
        self.live = os.getenv("RAELYN_RUN_LLM_BUDGET_LIVE") == "1"
        self.cfg = get_effective_llm_config()
        if not self.cfg.configured or "/api/generate" not in self.cfg.url:
            self.skipTest("需要已配置的真实 Ollama /api/generate 服务")

    def generate(self, prompt: str, *, schema: dict, limit: int, stream: bool, label: str) -> dict:
        kwargs: dict[str, Any] = {
            "prompt": prompt, "response_format": schema, "think": False,
            "options": {"temperature": 0, "num_predict": limit}, "stream": stream,
        }
        signature = {"request": kwargs, "model": self.cfg.model, "context": settings.llm_ollama_num_ctx}
        digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self.directory / f"verified-{label}-{digest[:16]}.json"
        if path.exists():
            response = json.loads(path.read_text(encoding="utf-8"))
        else:
            if not self.live:
                self.skipTest("缺少已记录的真实响应；设置 RAELYN_RUN_LLM_BUDGET_LIVE=1 执行定向集成验证")
            started = time.monotonic()
            response = llm_generate(**kwargs)
            response["experiment"] = {"seconds": round(time.monotonic() - started, 3), **signature}
            path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "case": label, "limit": limit, "output_tokens": response["usage"]["output_tokens"],
            "done_reason": response["meta"]["done_reason"], "seconds": response["experiment"]["seconds"],
        }, ensure_ascii=False), flush=True)
        self.assertTrue(response["meta"]["done"])
        self.assertEqual(response["meta"]["done_reason"], "stop")
        self.assertLessEqual(response["usage"]["output_tokens"], limit)
        self.assertTrue(response["text"])
        return response

    def test_failed_event_chunks_complete_with_bounded_schema(self) -> None:
        paths = sorted(self.directory.glob("event-*.json"))
        if not paths:
            self.skipTest("请采集 video.extract_events 任务的原始字幕输入")
        for path in paths:
            case = json.loads(path.read_text(encoding="utf-8"))
            for index, chunk in enumerate(case["prompts"], 1):
                with self.subTest(job_id=case["job_id"], chunk=index):
                    response = self.generate(
                        chunk["prompt"], schema=ea.event_extraction_response_schema(["v1"]),
                        limit=settings.event_extraction_ollama_num_predict, stream=True,
                        label=f"event-{case['job_id'][:8]}-{index}",
                    )
                    raw = json.loads(response["text"])
                    self.assertEqual(len(raw["videos"]), 1)
                    rows, _ = ea.parse_event_extraction_batch_response(response["text"], expected_video_ids=["v1"])
                    self.assertLessEqual(len(rows["v1"]), 4)
                    for event in rows["v1"]:
                        self.assertTrue(event.get("evidence_source_ids"))
                        self.assertTrue(set(event["evidence_source_ids"]).issubset(chunk["source_ids"]))

    def test_failed_briefs_complete_with_sections_and_real_sources(self) -> None:
        paths = sorted(self.directory.glob("brief-*.json"))
        if not paths:
            self.skipTest("请采集 brief.generate_period 任务的真实周期输入")
        for path in paths:
            case = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(job_id=case["job_id"]):
                blocks = case["blocks"]
                input_budget = min(case["input_budget"], settings.llm_ollama_num_ctx - settings.brief_ollama_num_predict)
                def compose() -> str:
                    return bp.compose_guarded_brief_prompt(
                        case["template"], granularity=case["granularity"],
                        period_start=date.fromisoformat(case["period_start"]),
                        period_end=date.fromisoformat(case["period_end"]), blocks=blocks, structured=True,
                    )
                for round_index in range(1, 5):
                    if bp.estimate_brief_tokens(compose()) <= input_budget:
                        break
                    reduced = []
                    batches = bp.build_brief_reduction_batches(blocks, max_input_tokens=input_budget, structured=True)
                    for batch_index, batch in enumerate(batches, 1):
                        urls = bp.extract_brief_urls("\n".join(batch))
                        response = self.generate(
                            bp.compose_brief_reduction_prompt(batch, structured=True),
                            schema=bp.brief_reduction_response_schema(urls), limit=2500, stream=False,
                            label=f"brief-{case['job_id'][:8]}-r{round_index}-{batch_index}",
                        )
                        summary = bp.parse_structured_brief_reduction(response["text"], source_urls=urls)
                        self.assertEqual(bp.validate_brief_reduction(summary, source_urls=urls), [])
                        reduced.append(f"## 分段事实摘要 {round_index}-{batch_index}\n\n{summary}")
                    self.assertLess(bp.estimate_brief_tokens('\n'.join(reduced)), bp.estimate_brief_tokens('\n'.join(blocks)))
                    blocks = reduced
                prompt = compose()
                self.assertLessEqual(bp.estimate_brief_tokens(prompt), input_budget)
                response = self.generate(
                    prompt, schema=bp.brief_response_schema(case["template"], source_urls=bp.extract_brief_urls('\n'.join(blocks))),
                    limit=settings.brief_ollama_num_predict, stream=False,
                    label=f"brief-{case['job_id'][:8]}-final",
                )
                markdown = bp.parse_structured_brief_response(
                    response["text"], template=case["template"], source_urls=bp.extract_brief_urls('\n'.join(blocks)),
                )
                self.assertEqual(bp.validate_generated_brief(markdown, template=case["template"], source_urls=case["urls"]), [])
                self.assertTrue(set(bp.extract_brief_urls(markdown)).issubset(case["urls"]))
                (self.directory / f"verified-brief-{case['job_id'][:8]}.md").write_text(markdown, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
