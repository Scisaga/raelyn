from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.models import Brief, Job, Playlist, PlaylistMedia, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.media_deletion import mark_brief_empty
from raelyn.services.brief_schedule import schedule_brief_refresh_for_video
from raelyn.services.brief_prompt import (
    DEFAULT_BRIEF_PROMPT_TEMPLATE,
    brief_period_bounds_utc,
    brief_period_end_inclusive,
    brief_period_start,
    build_brief_blocks,
    compose_brief_prompt,
)
from raelyn.services.brief_references import attach_structured_brief_references
from raelyn.services.llm import llm_enabled, llm_generate
from raelyn.services.video_admission import (
    brief_admitted_video_expr,
    ensure_video_published_at_backfilled,
    playback_admitted_video_expr,
)
from raelyn.services.video_time import timeline_time_expr
from raelyn.services.workdir import job_workdir


def _enqueue_brief_for_video_playlists(session: Session, *, video: Video, delay_seconds: int = 90) -> int:
    return schedule_brief_refresh_for_video(session, video=video, reason="transcript_ready")


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

    ensure_video_published_at_backfilled(session)
    start_utc, end_utc = brief_period_bounds_utc(period_start, value)
    media_ids = session.execute(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist_id)).scalars().all()
    if not media_ids:
        mark_brief_empty(session, playlist_id=playlist_id, granularity=value, period_start=period_start)
        return {"empty": True, "reason": "empty playlist"}

    co_ts = timeline_time_expr()
    brief_admitted = brief_admitted_video_expr()
    videos = (
        session.execute(
            select(Video)
            .where(Video.media_id.in_(list(media_ids)), brief_admitted, co_ts >= start_utc, co_ts < end_utc)
            .order_by(co_ts.asc().nullslast())
        )
        .scalars()
        .all()
    )
    if not videos:
        has_playback_videos = (
            session.execute(
                select(Video.id)
                .where(Video.media_id.in_(list(media_ids)), playback_admitted_video_expr(), co_ts >= start_utc, co_ts < end_utc)
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )
        if not has_playback_videos:
            mark_brief_empty(session, playlist_id=playlist_id, granularity=value, period_start=period_start)
            job_log(
                session,
                job,
                f"brief empty: no videos in period {value} {period_start.isoformat()}",
                level="info",
                data={"granularity": value, "period_start": period_start.isoformat()},
            )
            return {"empty": True, "reason": "no videos"}

        brief = session.execute(
            select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == value, Brief.period_start == period_start)
        ).scalar_one_or_none()
        if not brief:
            brief = Brief(playlist_id=playlist_id, granularity=value, period_start=period_start, status="failed")
            session.add(brief)
            session.flush()
        else:
            brief.status = "failed"
        brief.error_message = "本周期无可用文本（字幕/文字稿缺失）"
        brief.markdown_asset_id = None
        return {"failed": True, "reason": "no transcript"}

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
        md, reference_count = attach_structured_brief_references(
            session,
            brief=brief,
            videos=videos,
            markdown=md,
        )
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
        return {
            "asset_id": str(asset.id),
            "reference_count": reference_count,
            "snapshot_id": str(brief.snapshot_id) if brief.snapshot_id else None,
            "llm_usage": resp.get("usage"),
        }


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
