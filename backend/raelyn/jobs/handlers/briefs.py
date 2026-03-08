from __future__ import annotations

import re
import uuid
from datetime import date

from dateutil import tz
from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_in
from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.models import Brief, Job, Playlist, PlaylistMedia, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.brief_prompt import (
    DEFAULT_BRIEF_PROMPT_TEMPLATE,
    brief_period_bounds_utc,
    brief_period_end_inclusive,
    brief_period_start,
    build_brief_blocks,
    compose_brief_prompt,
)
from raelyn.services.llm import llm_enabled, llm_generate
from raelyn.services.workdir import job_workdir


def _enqueue_brief_for_video_playlists(session: Session, *, video: Video, delay_seconds: int = 90) -> int:
    if not llm_enabled():
        return 0

    ts = video.published_at or video.created_at
    if not ts:
        return 0

    tzinfo = tz.gettz(settings.timezone) or tz.tzlocal()
    try:
        day = ts.astimezone(tzinfo).date()
    except Exception:
        return 0

    playlist_ids = (
        session.execute(select(PlaylistMedia.playlist_id).where(PlaylistMedia.media_id == video.media_id).distinct())
        .scalars()
        .all()
    )
    if not playlist_ids:
        return 0

    rows = session.execute(select(Playlist.id, Playlist.brief_granularity).where(Playlist.id.in_(list(playlist_ids)))).all()
    count = 0
    for playlist_id, granularity in rows:
        value = (granularity or "day").strip().lower()
        if value not in {"day", "week", "month"}:
            value = "day"
        period = brief_period_start(day, value)
        enqueue_in(
            session,
            seconds=delay_seconds,
            type_="brief.generate_period",
            params={"playlist_id": str(playlist_id), "granularity": value, "period_start": period.isoformat()},
            priority=2,
        )
        count += 1
    return count


def _brief_generate_period_impl(
    session: Session,
    job: Job,
    *,
    playlist_id: uuid.UUID,
    granularity: str,
    period_start: date,
) -> dict | None:
    value = (granularity or "day").strip().lower()
    if value not in {"day", "week", "month"}:
        value = "day"
    period_start = brief_period_start(period_start, value)
    period_end = brief_period_end_inclusive(period_start, value)

    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        job_log(session, job, f"brief failed: playlist not found {playlist_id}", level="warn")
        return {"failed": True, "reason": "playlist not found"}

    start_utc, end_utc = brief_period_bounds_utc(period_start, value)
    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
    if not media_ids:
        brief = session.execute(
            select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == value, Brief.period_start == period_start)
        ).scalar_one_or_none()
        if not brief:
            brief = Brief(playlist_id=playlist_id, granularity=value, period_start=period_start, status="running")
            session.add(brief)
            session.flush()
        else:
            brief.status = "running"
        brief.status = "failed"
        brief.error_message = "播放列表为空"
        brief.markdown_asset_id = None
        return {"failed": True, "reason": "empty playlist"}

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
        job_log(
            session,
            job,
            f"skip brief: no videos in period {value} {period_start.isoformat()}",
            level="info",
            data={"granularity": value, "period_start": period_start.isoformat()},
        )
        return {"skipped": True, "reason": "no videos"}

    brief = session.execute(
        select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == value, Brief.period_start == period_start)
    ).scalar_one_or_none()
    if not brief:
        brief = Brief(playlist_id=playlist_id, granularity=value, period_start=period_start, status="running")
        session.add(brief)
        session.flush()
    else:
        brief.status = "running"

    if not llm_enabled():
        brief.status = "failed"
        brief.error_message = "llm 未配置"
        brief.markdown_asset_id = None
        return {"failed": True, "reason": "llm not configured"}

    with job_workdir(job.id) as wd:
        blocks, video_urls = build_brief_blocks(session, videos)
        if not blocks:
            brief.status = "failed"
            brief.error_message = "本周期无可用文本（字幕/文字稿缺失）"
            brief.markdown_asset_id = None
            return {"failed": True, "reason": "no transcript"}

        template = (getattr(playlist, "brief_prompt", None) or "").strip() or DEFAULT_BRIEF_PROMPT_TEMPLATE
        prompt = compose_brief_prompt(template, granularity=value, period_start=period_start, period_end=period_end, blocks=blocks)

        resp = llm_generate(prompt=prompt, think=True)
        md = _sanitize_brief_markdown(str(resp.get("text", "")))
        out = wd / "brief.md"
        out.write_text(md, encoding="utf-8")

        asset = ensure_asset(
            session,
            video_id=None,
            type_="brief",
            format_="md",
            language="zh",
            source="llm",
            variant=None,
            local_path=out,
            s3_key=f"brief/{playlist_id}/{value}/{period_start.isoformat()}.md",
            content_type="text/markdown; charset=utf-8",
            metadata={
                "playlist_id": str(playlist_id),
                "granularity": value,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "job_id": str(job.id),
                "video_urls": video_urls,
            },
            dedupe=False,
        )
        brief.status = "ready"
        brief.markdown_asset_id = asset.id
        brief.error_message = None
        return {"asset_id": str(asset.id), "llm_usage": resp.get("usage")}


@registry.register("brief.generate_period")
def brief_generate_period(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(job.params["playlist_id"])
    granularity = str(job.params.get("granularity") or "day")
    if "period_start" in job.params and job.params.get("period_start"):
        period_start = date.fromisoformat(str(job.params["period_start"]))
    elif "date" in job.params and job.params.get("date"):
        period_start = date.fromisoformat(str(job.params["date"]))
    else:
        period_start = date.today()
    return _brief_generate_period_impl(session, job, playlist_id=playlist_id, granularity=granularity, period_start=period_start)


@registry.register("brief.generate_daily")
def brief_generate_daily(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(job.params["playlist_id"])
    brief_date = date.fromisoformat(job.params["date"])
    return _brief_generate_period_impl(session, job, playlist_id=playlist_id, granularity="day", period_start=brief_date)


def _sanitize_brief_markdown(md: str) -> str:
    src = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    if not src.strip():
        return md

    lines = src.split("\n")
    lines = [line for line in lines if not re.fullmatch(r"\s*-{3,}\s*", line or "")]
    lines = [
        line
        for line in lines
        if not re.fullmatch(r"\s*日期\s*[:：]\s*\d{4}[/-]\d{1,2}[/-]\d{1,2}\s*", line or "")
    ]

    for index, line in enumerate(lines[:6]):
        if not (line or "").strip():
            continue
        if "每日财经简报" in line:
            lines[index] = ""
        break

    while lines and not (lines[0] or "").strip():
        lines.pop(0)
    while lines and not (lines[-1] or "").strip():
        lines.pop()

    return "\n".join(lines).strip()
