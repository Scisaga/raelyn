"""只读采集失败任务的真实 LLM 输入，不执行任务、不写业务库。

示例：
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.capture_llm_budget_cases \
    --output-dir /tmp/llm-budget-cases --job-id <失败任务 UUID>
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.models import Asset, Job, Media, Playlist, PlaylistMedia, Video, VideoEventExtractionRun
from raelyn.services import brief_prompt as bp, event_analysis as ea
from raelyn.services.transcripts import pick_transcript_asset, read_text_asset
from raelyn.services.video_admission import brief_admitted_video_expr
from raelyn.services.video_time import resolve_video_timeline, timeline_time_expr


def capture_event_case(session: Session, job: Job) -> dict:
    video = session.get(Video, uuid.UUID(job.params["video_id"]))
    if video is None:
        raise ValueError("视频不存在；请先完成来源同步")
    run = session.scalars(
        select(VideoEventExtractionRun)
        .where(VideoEventExtractionRun.video_id == video.id, VideoEventExtractionRun.status == "failed")
        .order_by(VideoEventExtractionRun.created_at)
    ).first()
    asset = session.get(Asset, run.transcript_asset_id) if run and run.transcript_asset_id else pick_transcript_asset(
        session, video.id, variant="plain",
    )
    if asset is None:
        raise ValueError("缺少原始字幕；请先完成字幕规范化或 ASR 任务")
    transcript, _ = read_text_asset(asset)
    media = session.get(Media, video.media_id) if video.media_id else None
    content_time = resolve_video_timeline(session, video).content_published_at or video.published_at
    spec = ea.event_extraction_spec(session)
    prepared = ea._PreparedEventVideo(
        alias="v1", video=ea._video_snapshot(video), media=ea._media_snapshot(media),
        transcript_asset=ea._transcript_asset_snapshot(asset), transcript_text=transcript,
        content_time=content_time, source_hash="", source_chars=len(transcript),
    )
    chunks = ea._chunk_text_with_offsets(transcript, spec.chunk_max_chars)
    prompts = []
    for index, (chunk, start, _) in enumerate(chunks, 1):
        sources = ea._build_event_sources(
            alias="v1", video=video, transcript_asset_id=asset.id,
            transcript_text=transcript, chunk_text=chunk, chunk_start=start,
        )
        prompts.append({
            "prompt": ea._render_event_batch_prompt(
                spec=spec, videos=[prepared], video_sources={"v1": sources},
                chunk_label=f"{index}/{len(chunks)}", compact=True,
            ),
            "source_ids": [source.source_id for source in sources],
        })
    return {
        "kind": "event", "job_id": str(job.id), "video_id": str(video.id),
        "transcript_asset_id": str(asset.id), "prompt_version": spec.prompt_version,
        "historical_prompt_version": run.prompt_version if run else None, "prompts": prompts,
    }


def capture_brief_case(session: Session, job: Job) -> dict:
    playlist = session.get(Playlist, uuid.UUID(job.params["playlist_id"]))
    if playlist is None:
        raise ValueError("域不存在")
    granularity = job.params.get("granularity", "day")
    start_date = date.fromisoformat(job.params["period_start"])
    start, end = bp.brief_period_bounds_utc(start_date, granularity)
    media_ids = session.scalars(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == playlist.id)).all()
    timeline = timeline_time_expr()
    videos = session.scalars(
        select(Video).where(
            Video.media_id.in_(media_ids), brief_admitted_video_expr(), timeline >= start, timeline < end,
        ).order_by(timeline.asc().nullslast())
    ).all()
    blocks, urls = bp.build_brief_blocks(session, list(videos))
    if not blocks:
        raise ValueError("缺少简报输入；请先完成该周期视频的字幕或转写任务")
    from raelyn.jobs.handlers.briefs import _effective_brief_input_budget

    return {
        "kind": "brief", "job_id": str(job.id), "period_start": str(start_date),
        "period_end": str(bp.brief_period_end_inclusive(start_date, granularity)),
        "granularity": granularity, "blocks": blocks, "urls": urls,
        "template": playlist.brief_prompt or bp.DEFAULT_BRIEF_PROMPT_TEMPLATE,
        "input_budget": _effective_brief_input_budget(session), "historical_error": job.error_message,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--job-id", type=uuid.UUID, action="append", required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, execution_options={"postgresql_readonly": True})
    with Session(engine) as session:
        for job_id in args.job_id:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"任务不存在：{job_id}")
            if job.type == "video.extract_events":
                case = capture_event_case(session, job)
            elif job.type == "brief.generate_period":
                case = capture_brief_case(session, job)
            else:
                raise ValueError(f"不支持采集此任务类型：{job.type}")
            path = args.output_dir / f"{case['kind']}-{job_id}.json"
            path.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"captured {job.type}: {path.name}")


if __name__ == "__main__":
    main()
