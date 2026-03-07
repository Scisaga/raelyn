from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from raelyn.models import AppConfig


logger = logging.getLogger(__name__)

TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY = "llm_transcript_polish_prompt"

DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE = (
    "你是一名中文文字稿编辑兼翻译。下面是一段从视频字幕/转写得到的原始文本，可能存在：口语化、断句混乱、错别字、同音错词、"
    "标点缺失、段落缺失，也可能原文是英文或中英混杂。\n"
    "请在不增删事实的前提下，把文本整理成更易读的中文正文；如果原文不是中文，请准确翻译为自然、通顺的简体中文。\n"
    "要求（必须遵守）：\n"
    "1) 最终输出必须是简体中文正文；即使原文是英文或其他语言，也不要保留大段外文原文。\n"
    "2) 自动分段：只在语义转折/话题切换处换段；不要逐句换段；段落宁可更长一些，避免出现大量短段。\n"
    "3) 段落之间用**单个**空行分隔；不要出现连续多个空行；不要把每一句都写成单独一行。\n"
    "4) 补充必要标点（保持原意）；\n"
    "5) 修正常见错别字/同音错词；不确定就保留原样；专有名词优先使用常见中文译名，不确定时保留原文名称；\n"
    "6) **所有阿拉伯数字（0-9）必须逐字保留**：不得新增、删除、改动任何数字字符（含小数点/负号/%）。\n"
    "   - 不要把阿拉伯数字改写成中文数字，也不要把中文数字改写成阿拉伯数字；\n"
    "   - 不要推断缺失单位/小数点/时间窗口；\n"
    "7) 不要总结、不要加标题、不要加解释、不要附带翻译说明；只输出整理/翻译后的正文纯文本。\n"
    "8) 这是整段文本的一个片段（{index}/{total}），输出中不要提及片段编号。\n\n"
    "原始文本：\n"
    "{chunk}\n"
)


def transcript_polish_prompt_defaults() -> dict[str, dict[str, str]]:
    return {TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY: {"text": DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE}}


def load_transcript_polish_prompt_template(session: Session | None) -> str:
    if session is None:
        return DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE
    try:
        item = session.get(AppConfig, TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY)
        value = item.value if item else None
    except Exception:
        return DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE
    if not isinstance(value, dict):
        return DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        return DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE
    return text


def render_transcript_polish_prompt(*, template: str, chunk: str, index: int, total: int) -> str:
    src = str(template or "").strip() or DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE
    if "{chunk}" not in src:
        src = src.rstrip() + "\n\n原始文本：\n{chunk}\n"
    try:
        return src.format(chunk=chunk, index=index, total=total)
    except Exception as e:
        logger.warning("invalid transcript polish prompt template; fallback to default: %s", e)
        return DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE.format(chunk=chunk, index=index, total=total)
