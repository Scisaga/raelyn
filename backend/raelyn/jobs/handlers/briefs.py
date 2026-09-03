from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.jobs.reschedule import JobTerminalFailure
from raelyn.models import Brief, Job, Playlist, PlaylistMedia, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.media_deletion import mark_brief_empty
from raelyn.services.brief_schedule import schedule_brief_refresh_for_video
from raelyn.services.brief_prompt import (
    DEFAULT_BRIEF_PROMPT_TEMPLATE,
    brief_period_bounds_utc,
    brief_period_end_inclusive,
    brief_period_start,
    build_brief_reduction_batches,
    build_brief_blocks,
    compose_brief_reduction_prompt,
    compose_guarded_brief_prompt,
    estimate_brief_tokens,
    extract_brief_urls,
    validate_brief_reduction,
    validate_generated_brief,
)
from raelyn.services.brief_references import attach_structured_brief_references
from raelyn.services.inference import get_effective_llm_config
from raelyn.services.job_cancellation import raise_if_job_cancel_requested
from raelyn.services.llm import llm_enabled, llm_generate
from raelyn.services.video_admission import (
    brief_admitted_video_expr,
    ensure_video_published_at_backfilled,
    playback_admitted_video_expr,
)
from raelyn.services.video_time import timeline_time_expr
from raelyn.services.workdir import job_workdir


_BRIEF_REDUCTION_MAX_ROUNDS = 4
_BRIEF_REDUCTION_NUM_PREDICT = 2500
_BRIEF_REDUCTION_MAX_ATTEMPTS = 2


class _BriefGenerationInvalid(RuntimeError):
    pass


def _merge_llm_usage(total: dict[str, int], part: object) -> None:
    if not isinstance(part, dict):
        return
    for key in ("input_tokens", "output_tokens", "total_tokens", "call_count"):
        try:
            total[key] = int(total.get(key) or 0) + max(0, int(part.get(key) or 0))
        except (TypeError, ValueError):
            continue


def _effective_brief_input_budget(session: Session) -> int:
    configured = max(2048, int(settings.brief_llm_max_input_tokens or 0))
    cfg = get_effective_llm_config(session)
    if "/api/generate" not in urlparse(str(cfg.url or "")).path.lower():
        return configured
    context_tokens = max(2048, int(settings.llm_ollama_num_ctx or 0))
    return min(configured, max(2048, context_tokens * 7 // 10))


def _reduce_brief_blocks_to_budget(
    session: Session,
    job: Job,
    *,
    template: str,
    granularity: str,
    period_start: date,
    period_end: date,
    blocks: list[str],
    max_input_tokens: int,
) -> tuple[list[str], dict[str, int], int, int]:
    current = list(blocks)
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}
    reduction_calls = 0

    final_prompt = compose_guarded_brief_prompt(
        template,
        granularity=granularity,
        period_start=period_start,
        period_end=period_end,
        blocks=current,
    )
    if estimate_brief_tokens(final_prompt) <= max_input_tokens:
        return current, total_usage, reduction_calls, 0

    for round_index in range(1, _BRIEF_REDUCTION_MAX_ROUNDS + 1):
        try:
            batches = build_brief_reduction_batches(current, max_input_tokens=max_input_tokens)
        except ValueError as exc:
            raise _BriefGenerationInvalid(str(exc)) from exc

        job_log(
            session,
            job,
            "brief source exceeds input budget; reducing in bounded sequential batches",
            data={
                "round": round_index,
                "batch_count": len(batches),
                "max_input_tokens": max_input_tokens,
            },
        )
        reduced: list[str] = []
        for batch_index, batch in enumerate(batches, start=1):
            source_urls = extract_brief_urls("\n".join(batch))
            summary = ""
            errors: list[str] = []
            for attempt in range(1, _BRIEF_REDUCTION_MAX_ATTEMPTS + 1):
                raise_if_job_cancel_requested(session, job)
                prompt = compose_brief_reduction_prompt(
                    batch,
                    retry_errors=errors if attempt > 1 else None,
                )
                response = llm_generate(
                    prompt=prompt,
                    think=False,
                    options={"temperature": 0, "num_predict": _BRIEF_REDUCTION_NUM_PREDICT},
                    usage_operation="brief_reduction",
                )
                _merge_llm_usage(total_usage, response.get("usage"))
                reduction_calls += 1
                summary = str(response.get("text", "")).strip()
                errors = validate_brief_reduction(summary, source_urls=source_urls)
                if not errors:
                    break
                if attempt < _BRIEF_REDUCTION_MAX_ATTEMPTS:
                    job_log(
                        session,
                        job,
                        "brief reduction output invalid; retrying the same batch once",
                        level="warn",
                        data={
                            "round": round_index,
                            "batch": batch_index,
                            "reason": "；".join(errors),
                        },
                    )
            if errors:
                raise _BriefGenerationInvalid(
                    f"第 {round_index} 轮第 {batch_index} 个分段摘要连续无效：{'；'.join(errors)}"
                )
            reduced.append(f"## 分段事实摘要 {round_index}-{batch_index}\n\n{summary}")

        previous_tokens = estimate_brief_tokens("\n\n".join(current))
        reduced_tokens = estimate_brief_tokens("\n\n".join(reduced))
        if reduced_tokens >= previous_tokens:
            raise _BriefGenerationInvalid("分段摘要没有缩小输入，已停止继续生成")
        current = reduced

        final_prompt = compose_guarded_brief_prompt(
            template,
            granularity=granularity,
            period_start=period_start,
            period_end=period_end,
            blocks=current,
        )
        if estimate_brief_tokens(final_prompt) <= max_input_tokens:
            return current, total_usage, reduction_calls, round_index

    raise _BriefGenerationInvalid("分层摘要达到最大轮数后仍超过 LLM 输入预算")


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
    if playlist.observation_enabled is False:
        return {"skipped": "domain observation is disabled"}

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
        max_input_tokens = _effective_brief_input_budget(session)
        source_input_chars = sum(len(block) for block in blocks)
        try:
            final_blocks, total_usage, reduction_calls, reduction_rounds = _reduce_brief_blocks_to_budget(
                session,
                job,
                template=template,
                granularity=value,
                period_start=period_start,
                period_end=period_end,
                blocks=blocks,
                max_input_tokens=max_input_tokens,
            )
            prompt = compose_guarded_brief_prompt(
                template,
                granularity=value,
                period_start=period_start,
                period_end=period_end,
                blocks=final_blocks,
            )
            estimated_final_input_tokens = estimate_brief_tokens(prompt)
            if estimated_final_input_tokens > max_input_tokens:
                raise _BriefGenerationInvalid("最终简报提示词仍超过 LLM 输入预算")

            raise_if_job_cancel_requested(session, job)
            resp = llm_generate(
                prompt=prompt,
                think=True,
                options={"temperature": 0},
                usage_operation="brief_generation",
            )
            _merge_llm_usage(total_usage, resp.get("usage"))
            md = _sanitize_brief_markdown(str(resp.get("text", "")))
            validation_errors = validate_generated_brief(
                md,
                template=template,
                source_urls=video_urls,
            )
            if validation_errors:
                raise _BriefGenerationInvalid(f"简报输出校验失败：{'；'.join(validation_errors)}")
        except _BriefGenerationInvalid as exc:
            message = str(exc)
            brief.status = "failed"
            brief.error_message = message
            brief.markdown_asset_id = None
            job_log(
                session,
                job,
                "brief generation rejected invalid or over-budget output",
                level="error",
                data={
                    "period_start": period_start.isoformat(),
                    "source_video_count": len(video_urls),
                    "source_input_chars": source_input_chars,
                    "max_input_tokens": max_input_tokens,
                    "reason": message,
                },
            )
            raise JobTerminalFailure(message) from exc

        md, reference_count = attach_structured_brief_references(
            session,
            brief=brief,
            videos=videos,
            markdown=md,
        )
        effective_llm = get_effective_llm_config(session)
        generation_basis = dict(brief.generation_basis or {})
        generation_basis.update(
            {
                "generation_strategy": "hierarchical_brief_v1" if reduction_calls else "single_pass_brief_v1",
                "prompt_template_sha256": hashlib.sha256(template.encode("utf-8")).hexdigest(),
                "llm_model": effective_llm.model,
                "max_input_tokens": max_input_tokens,
                "estimated_final_input_tokens": estimated_final_input_tokens,
                "source_input_chars": source_input_chars,
                "source_video_count": len(video_urls),
                "reduction_rounds": reduction_rounds,
                "reduction_calls": reduction_calls,
            }
        )
        brief.generation_basis = generation_basis
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
                "generation_strategy": generation_basis["generation_strategy"],
                "prompt_template_sha256": generation_basis["prompt_template_sha256"],
                "llm_model": generation_basis["llm_model"],
                "max_input_tokens": max_input_tokens,
                "estimated_final_input_tokens": estimated_final_input_tokens,
                "source_input_chars": source_input_chars,
                "reduction_rounds": reduction_rounds,
                "reduction_calls": reduction_calls,
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
            "generation_strategy": generation_basis["generation_strategy"],
            "reduction_rounds": reduction_rounds,
            "reduction_calls": reduction_calls,
            "llm_usage": total_usage,
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
