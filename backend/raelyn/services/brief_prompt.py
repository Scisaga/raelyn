from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from dateutil import tz
from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.models import Asset, Playlist, PlaylistMedia, Video
from raelyn.services.s3 import s3_get_bytes


DEFAULT_BRIEF_PROMPT_TEMPLATE = "\n".join(
    [
        "## 提示词",
        "",
        "你是一个**财经内容分析助手**。请基于下面提供的多条视频文字内容（可能含转写、字幕、摘要、片段拼接），生成一份可直接发布的**Markdown**财经简报。",
        "",
        "### 核心约束（必须遵守）",
        "",
        "1. **只使用文本中明确出现的信息**：",
        "",
        "   * 不要补充常识性“背景”来充当事实。",
        "   * 任何无法从文本直接验证的内容，一律写：**“文本未提及”** 或 **“文本表述不充分，无法确认”**。",
        "2. **每条要点必须附 1–3 个来源链接**：",
        "",
        "   * 来源链接必须来自文本中的视频链接/来源字段。",
        "   * 统一写法：句末用 `（来源：https://...，https://...）`（可用纯 URL 或 Markdown 链接）。",
        "3. 输出语言：**中文**。",
        "4. 输出必须是 **Markdown**，段落清晰，便于直接发布。",
        "5. 不需要：**主体归类**、**今日视频清单**。",
        "6. **不要输出“总标题/日期/分割线”**：",
        "",
        "   * 不要输出 H1（例如 `# ...`）或任何“总标题”。",
        "   * 不要输出“每日财经简报”字样。",
        "   * 不要输出“日期：...”或任何单独的日期行。",
        "   * 不要输出 Markdown 水平分割线（例如 `---`）。",
        "",
        "### 输出结构",
        "",
        "你必须严格按以下结构输出，并且**标题行必须用 Markdown 标题语法，单独成行**（不要写成正文里的小标题）：",
        "",
        "## 今日要点",
        "",
        "* 用 **bullet** 列出关键信息点（只写要点，不要写散文段落）。",
        "* 每条要点：",
        "",
        "  * 句式尽量短，先给“结论/信息”，再给“条件/范围/时间”。",
        "  * 必须在句末附 **1–3 个来源链接**，格式：`（来源：...）`。",
        "  * 若文本出现具体数值（涨跌幅、利率、通胀、盈利、库存、产量等），必须原样保留，并注明它属于谁/哪个时间窗口；若时间窗口不清楚，写“文本未提及”。",
        "",
        "示例格式（示例仅展示格式，不要复用示例内容）：",
        "",
        "* 美债收益率在文本中被描述为____，并被归因于____（来源：[视频标题](https://...)）",
        "* 某公司业绩/指引被提到____，但对同比/环比口径未说明（文本未提及）（来源：[视频标题](https://...)）",
        "",
        "## 影响与逻辑链",
        "",
        "* 写清楚“**因 → 果**”或“**事件 → 资产影响**”的链条。",
        "* 每条链条必须满足：链条中的每个关键节点都能在文本中找到依据；找不到就标注“文本未提及”。",
        "* 仍然要在句末附来源链接。",
        "",
        "## 风险与不确定性",
        "",
        "* 重点写：口径不一致、数据缺失、时间不明、推断过度、样本偏差、叙述互相矛盾之处。",
        "* 如不同视频说法冲突：明确写出“视频 A 说…；视频 B 说…；无法判定”（并分别给来源）。",
        "",
        "## 关注清单",
        "",
        "* 给出“接下来应继续跟踪”的观察项（不是预测结论），如：",
        "",
        "  * 关键数据发布、会议/财报、政策口径、价格/利差/汇率阈值、行业库存、地缘事件进展等。",
        "* 每一条都要说明：为什么要跟踪（依据文本哪个说法），并附来源链接。",
        "* 如果文本没有足够依据，写“文本未提及”。",
        "",
        "## 行动建议",
        "",
        "* 给“**条件触发式**”建议，形式如：",
        "",
        "  * “若文本中提到的 A 指标继续…，可考虑…；否则…（文本未提及具体阈值）”",
        "* **不允许直接给确定性买卖指令**；只能写“可考虑/可关注/需验证”。",
        "* 每条建议仍需来源链接；若建议的关键条件缺失，标注“文本未提及”。",
        "",
        "简报日期（仅供你理解上下文，不要在输出中出现）：{{date}}",
        "",
        "周期起止（可选占位符）：{{period_start}} ~ {{period_end}}",
        "",
        "以下是视频文本（多条，可能包含标题与链接；若未包含链接，请在输出中把来源写为“文本未提供链接”）：",
        "{{blocks}}",
    ]
).strip()


def brief_period_start(d: date, granularity: str) -> date:
    g = (granularity or "day").strip().lower()
    if g == "day":
        return d
    if g == "week":
        return d - timedelta(days=d.weekday())  # Monday
    if g == "month":
        return date(d.year, d.month, 1)
    return d


def _brief_month_add_one(d: date) -> date:
    y = int(d.year)
    m = int(d.month)
    if m >= 12:
        return date(y + 1, 1, 1)
    return date(y, m + 1, 1)


def brief_period_end_inclusive(period_start: date, granularity: str) -> date:
    g = (granularity or "day").strip().lower()
    if g == "day":
        return period_start
    if g == "week":
        return period_start + timedelta(days=6)
    if g == "month":
        return _brief_month_add_one(period_start) - timedelta(days=1)
    return period_start


def brief_period_bounds_utc(period_start: date, granularity: str) -> tuple[datetime, datetime]:
    tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
    start = datetime.combine(period_start, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
    g = (granularity or "day").strip().lower()
    if g == "day":
        return start, start + timedelta(days=1)
    if g == "week":
        return start, start + timedelta(days=7)
    if g == "month":
        end_date = _brief_month_add_one(period_start)
        end = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
        return start, end
    return start, start + timedelta(days=1)


def load_plain_transcript_asset(session: Session, video_id: uuid.UUID) -> tuple[str | None, str | None]:
    variants = ["polished", "plain"]
    order = [
        ("subtitle", "zh"),
        ("qwen3-asr", "zh"),
        ("speaches", "zh"),
    ]
    for variant in variants:
        for source, lang in order:
            a = session.execute(
                select(Asset).where(
                    Asset.video_id == video_id,
                    Asset.type == "transcript",
                    Asset.format == "txt",
                    Asset.variant == variant,
                    Asset.source == source,
                    Asset.language == lang,
                )
            ).scalar_one_or_none()
            if a:
                return a.s3_bucket, a.s3_key

    for variant in variants:
        a = session.execute(
            select(Asset)
            .where(
                Asset.video_id == video_id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.variant == variant,
            )
            .order_by(Asset.created_at.desc())
        ).scalar_one_or_none()
        if a:
            return a.s3_bucket, a.s3_key
    return None, None


def build_brief_blocks(session: Session, videos: list[Video]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    video_urls: list[str] = []
    for v in videos:
        bucket, key = load_plain_transcript_asset(session, v.id)
        if not bucket or not key:
            continue
        data = s3_get_bytes(bucket=bucket, key=key)
        text = data.decode("utf-8", errors="ignore").strip()
        if not text:
            continue
        video_urls.append(v.url)
        blocks.append(f"## {v.title or v.provider_video_id}\n来源：{v.url}\n\n{text}\n")
    return blocks, video_urls


def compose_brief_prompt(
    tpl: str,
    *,
    granularity: str,
    period_start: date,
    period_end: date,
    blocks: list[str],
) -> str:
    blocks_text = "\n\n\n".join(blocks)
    g = (granularity or "day").strip().lower()
    date_label = period_start.isoformat() if g == "day" else f"{period_start.isoformat()} ~ {period_end.isoformat()}"
    prompt = (
        (tpl or "")
        .replace("{{date}}", date_label)
        .replace("{{period_start}}", period_start.isoformat())
        .replace("{{period_end}}", period_end.isoformat())
        .replace("{{blocks}}", blocks_text)
    )
    if "{{blocks}}" not in (tpl or ""):
        prompt = (prompt.rstrip() + "\n\n\n" + blocks_text).strip()
    return prompt


@dataclass(frozen=True)
class BriefPromptOut:
    playlist_id: uuid.UUID
    granularity: str
    period_start: date
    period_end: date
    prompt: str
    video_urls: list[str]


def build_brief_prompt_for_period(
    session: Session,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    date_in_period: date,
) -> BriefPromptOut:
    g = (granularity or "day").strip().lower()
    if g not in {"day", "week", "month"}:
        raise ValueError("invalid granularity (day/week/month)")
    period_start = brief_period_start(date_in_period, g)
    period_end = brief_period_end_inclusive(period_start, g)
    start_utc, end_utc = brief_period_bounds_utc(period_start, g)

    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        raise LookupError("playlist not found")

    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
    if not media_ids:
        raise LookupError("empty playlist")

    videos = (
        session.execute(
            select(Video)
            .where(Video.media_id.in_(list(media_ids)), Video.published_at >= start_utc, Video.published_at < end_utc)
            .order_by(Video.published_at.asc().nullslast())
        )
        .scalars()
        .all()
    )
    if not videos:
        raise LookupError("no videos")

    blocks, video_urls = build_brief_blocks(session, videos)
    if not blocks:
        raise LookupError("no transcript")

    tpl = (getattr(playlist, "brief_prompt", None) or "").strip() or DEFAULT_BRIEF_PROMPT_TEMPLATE
    prompt = compose_brief_prompt(tpl, granularity=g, period_start=period_start, period_end=period_end, blocks=blocks)
    return BriefPromptOut(
        playlist_id=playlist_id,
        granularity=g,
        period_start=period_start,
        period_end=period_end,
        prompt=prompt,
        video_urls=video_urls,
    )

