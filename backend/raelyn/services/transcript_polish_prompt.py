from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from raelyn.models import AppConfig


logger = logging.getLogger(__name__)

TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY = "llm_transcript_polish_prompt"

DEFAULT_TRANSCRIPT_POLISH_PROMPT_TEMPLATE = (
    "你是一名金融视频文字稿保真编辑兼翻译。下面是一段从视频字幕/转写得到的原始文本，可能存在：口语化、断句混乱、错别字、"
    "同音错词、标点缺失、段落缺失，也可能原文是英文或中英混杂。\n"
    "目标是生成“可回溯证据的保真整理稿”，不是摘要、不是简报、不是改写文章。请在不增删事实的前提下，按原文顺序整理成"
    "更易读的中文正文；如果原文不是中文，请准确翻译为自然、通顺的简体中文。\n"
    "要求（必须遵守）：\n"
    "1) 最终输出必须是简体中文正文；即使原文是英文或其他语言，也不要保留大段外文原文；但专有名词、资产代码、机构名、"
    "产品名、数字表达、英文引语短语可以短语级保留原文。\n"
    "2) 必须保留原文的信息粒度和先后顺序：逐句或近逐句整理，不要把多处事实合并成一句概括，不要跨话题重组内容。\n"
    "3) 对金融、宏观、政策、地缘、企业、行业、资产价格、利率、信用、商品、库存、财报、订单、指引、风险偏好等信息，"
    "宁可保留噪音，也不要删除、弱化或概括掉可作为证据的细节。\n"
    "4) 自动分段：只在语义转折/话题切换处换段；同一话题内可以按短段保留问答和证据链，避免压缩成长摘要。\n"
    "5) 段落之间用**单个**空行分隔；不要出现连续多个空行；不要添加标题、列表、编号或项目符号。\n"
    "6) 补充必要标点（保持原意）；\n"
    "7) 修正常见错别字/同音错词；不确定就保留原样并可用“（疑似原文：...）”保留原始词；专有名词尽量保留原文名称，"
    "可在后面补常见中文译名，例如 Home Depot（家得宝）。\n"
    "8) **所有数字表达必须逐字保留**：包括阿拉伯数字（0-9）和英文数字短语（如 four point seven eight、ten billion、"
    "forty seven billion）。\n"
    "   - 不得新增、删除、改动任何数字字符（含小数点/负号/%）；\n"
    "   - 不要把阿拉伯数字改写成中文数字，也不要把英文数字短语改写成中文数字；\n"
    "   - 不要做单位换算或金额换算，例如不要把 ten billion 改写成 100 亿，不要把 forty seven billion 改写成 470 亿；\n"
    "   - 不要推断缺失单位/小数点/时间窗口；\n"
    "   - 如果原文数字上下文混乱，只整理可确认的语句，并保留原数字表达，不要替换为你认为更合理的数字。\n"
    "   - 形如 __RAELYN_NUM_A__ 的数字占位符必须逐字保留，不要翻译、解释、删除或改写。\n"
    "9) 不要新增原文没有的事实、背景、因果、评价或市场解释；不要把评论员推测改写成确定事实。\n"
    "10) 可以删除纯口头填充词、明显无意义重复和广告串场中的空泛套话；但只要包含实体、数字、时间、方向、事件或市场含义，"
    "必须保留。\n"
    "11) 不要总结、不要加标题、不要加解释、不要附带翻译说明；不要向用户道歉、致谢、提建议或说明你无法处理；"
    "即使原文破碎、重复或无意义，也只能输出整理后的正文纯文本。\n"
    "12) 这是整段文本的一个片段（{index}/{total}），输出中不要提及片段编号。\n\n"
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
