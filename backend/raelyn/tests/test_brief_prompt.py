from __future__ import annotations

import sys
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Playlist
from raelyn.services.brief_prompt import (
    DEFAULT_BRIEF_PROMPT_TEMPLATE,
    build_brief_prompt_for_period,
    build_brief_reduction_batches,
    compose_brief_reduction_prompt,
    compose_guarded_brief_prompt,
    estimate_brief_tokens,
    validate_brief_reduction,
    validate_generated_brief,
)


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class BriefPromptTests(unittest.TestCase):
    def test_over_budget_sources_are_split_without_losing_source_identity(self) -> None:
        blocks = [
            f"## 来源 {index}\n来源：https://example.com/video-{index}\n\n{'宏观政策与市场变化。' * 600}"
            for index in range(1, 4)
        ]

        batches = build_brief_reduction_batches(blocks, max_input_tokens=1200)

        self.assertGreater(len(batches), 3)
        for batch in batches:
            self.assertLessEqual(estimate_brief_tokens(compose_brief_reduction_prompt(batch)), 1200)
        combined = "\n".join(part for batch in batches for part in batch)
        for index in range(1, 4):
            self.assertIn(f"https://example.com/video-{index}", combined)

    def test_guarded_prompt_places_output_contract_after_source_material(self) -> None:
        prompt = compose_guarded_brief_prompt(
            DEFAULT_BRIEF_PROMPT_TEMPLATE,
            granularity="day",
            period_start=date(2026, 7, 27),
            period_end=date(2026, 7, 27),
            blocks=["## 来源\n来源：https://example.com/video\n\n正文"],
        )

        self.assertGreater(prompt.rfind("最终输出检查"), prompt.rfind("https://example.com/video"))

    def test_output_validation_rejects_generic_answer_and_missing_contract(self) -> None:
        errors = validate_generated_brief(
            "您提供了一批材料，我可为您进一步提供更多分析。",
            template=DEFAULT_BRIEF_PROMPT_TEMPLATE,
            source_urls=["https://example.com/video"],
        )

        self.assertIn("正文退化为通用助手回答", errors)
        self.assertIn("正文没有引用任何真实输入来源", errors)
        self.assertTrue(any(error.startswith("缺少规定章节：") for error in errors))

    def test_reduction_validation_accepts_concise_fact_but_rejects_url_only(self) -> None:
        source = "https://example.com/video/1"
        concise = f"* 日元走强，油价回落。（来源：{source}）"

        self.assertEqual(validate_brief_reduction(concise, source_urls=[source]), [])
        self.assertIn(
            "分段摘要没有足够的事实内容",
            validate_brief_reduction(source, source_urls=[source]),
        )

    def test_reduction_retry_prompt_carries_validation_error_and_exact_url_instruction(self) -> None:
        prompt = compose_brief_reduction_prompt(
            ["## 来源\n来源：https://example.com/video/1\n\n正文"],
            retry_errors=["分段摘要没有保留任何真实来源链接"],
        )

        self.assertIn("上一次输出未通过校验", prompt)
        self.assertIn("分段摘要没有保留任何真实来源链接", prompt)
        self.assertIn("从来源材料中的“来源：”行逐字复制完整 URL", prompt)
        self.assertGreater(prompt.rfind("上一次输出未通过校验"), prompt.rfind("</source_material>"))

    def test_output_validation_accepts_required_sections_and_real_source(self) -> None:
        source = "https://example.com/video"
        markdown = "\n\n".join(
            [
                f"## 今日要点\n\n* 事实要点（来源：{source}）",
                f"## 影响与逻辑链\n\n* 事件 → 影响（来源：{source}）",
                f"## 风险与不确定性\n\n* 口径仍需确认（来源：{source}）",
                f"## 关注清单\n\n* 继续关注数据（来源：{source}）",
                f"## 行动建议\n\n* 若条件成立可考虑跟踪（来源：{source}）",
            ]
        )

        errors = validate_generated_brief(
            markdown,
            template=DEFAULT_BRIEF_PROMPT_TEMPLATE,
            source_urls=[source],
        )

        self.assertEqual(errors, [])

    def test_build_brief_prompt_for_period_reports_no_transcript_when_only_playback_exists(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = Playlist(id=playlist_id, name="示例", brief_granularity="week")
        session.execute.side_effect = [
            _scalars_all([uuid.uuid4()]),
            _scalars_all([]),
            _scalar_one_or_none(uuid.uuid4()),
        ]

        with patch("raelyn.services.brief_prompt.ensure_video_published_at_backfilled"):
            with self.assertRaises(LookupError) as ctx:
                build_brief_prompt_for_period(
                    session,
                    playlist_id=playlist_id,
                    granularity="week",
                    date_in_period=date(2026, 3, 9),
                )

        self.assertEqual(str(ctx.exception), "no transcript")
        transcript_stmt = session.execute.call_args_list[1].args[0]
        playback_stmt = session.execute.call_args_list[2].args[0]
        transcript_sql = str(transcript_stmt.compile(dialect=postgresql.dialect())).lower()
        playback_sql = str(playback_stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("asset.type =", transcript_sql)
        self.assertIn("asset.format =", transcript_sql)
        self.assertIn("video_time_evidence", transcript_sql)
        self.assertIn("coalesce", transcript_sql)
        self.assertIn("asset.type =", playback_sql)
        self.assertIn("video_time_evidence", playback_sql)


if __name__ == "__main__":
    unittest.main()
