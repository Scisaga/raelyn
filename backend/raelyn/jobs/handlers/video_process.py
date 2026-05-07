from __future__ import annotations

import json
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.jobs.reschedule import JobReschedule, JobTerminalFailure
from raelyn.models import Asset, Job, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.asr import asr_enabled, asr_transcribe, inspect_asr_backend_defer
from raelyn.services.ffmpeg import extract_audio_to_m4a
from raelyn.services.llm import llm_enabled
from raelyn.services.playlist_analysis import schedule_video_embedding_refresh, transcript_checksum
from raelyn.services.s3 import s3_download_file
from raelyn.services.subtitles import normalize_subtitle
from raelyn.services.workdir import job_workdir

from .briefs import _enqueue_brief_for_video_playlists
from .common import _best_language_subtitle


_ASR_TERMINAL_HTTP_STATUS_CODES = {400, 401, 403, 404, 413, 415, 422, 507}
_ASR_TRANSIENT_HTTP_STATUS_CODES = {429, 502, 503, 504}


def _asr_error_detail(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    try:
        payload = response.json()
    except ValueError:
        detail: object = response.text or response.reason_phrase
    else:
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    return str(detail).strip()[:600]


def _asr_transient_delay_seconds(job: Job, status_code: int) -> int:
    params = dict(job.params or {})
    retries = max(0, int(params.get("asr_transient_defers") or 0))
    base = 120 if status_code == 429 else 60
    return min(900, base * (2 ** min(retries, 4)))


def _reschedule_asr_job(job: Job, *, counter_key: str, delay_seconds: int, reason: str) -> None:
    params = dict(job.params or {})
    params[counter_key] = int(params.get(counter_key) or 0) + 1
    job.params = params
    raise JobReschedule(delay_seconds=delay_seconds, reason=reason)


def _schedule_auto_video_embedding_refresh(
    session: Session,
    *,
    video_id: uuid.UUID,
    text_checksum_value: str,
    priority: int = 0,
    parent_job_id: str | None = None,
) -> uuid.UUID | None:
    if not bool(settings.auto_embed_new_video_transcripts):
        return None
    return schedule_video_embedding_refresh(
        session,
        video_id=video_id,
        text_checksum_value=text_checksum_value,
        priority=priority,
        parent_job_id=parent_job_id,
    )


@registry.register("video.extract_audio")
def video_extract_audio(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    video_asset = session.execute(
        select(Asset).where(Asset.video_id == video.id, Asset.type == "video").order_by(Asset.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if not video_asset:
        return {"skipped": "video asset not found"}

    with job_workdir(job.id) as wd:
        local_video = wd / f"input.{video_asset.format}"
        s3_download_file(bucket=video_asset.s3_bucket, key=video_asset.s3_key, local_path=local_video)
        audio_out = wd / "audio.m4a"
        extract_audio_to_m4a(input_path=local_video, output_path=audio_out)

        ensure_asset(
            session,
            video_id=video.id,
            type_="audio",
            format_="m4a",
            language=None,
            source="ffmpeg",
            variant="raw",
            local_path=audio_out,
            s3_key=f"{video.provider}/{video.media_id}/{video.provider_video_id}/audio/raw.m4a",
        )

    if asr_enabled():
        has_zh_subtitle = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "subtitle",
                Asset.language.is_not(None),
                Asset.language.ilike("zh%"),
            ).limit(1)
        ).scalar_one_or_none()
        has_zh_transcript = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "transcript",
                Asset.language.is_not(None),
                Asset.language.ilike("zh%"),
            ).limit(1)
        ).scalar_one_or_none()
        if not has_zh_subtitle and not has_zh_transcript:
            enqueue_job(
                session,
                type_="video.asr_transcribe",
                params={"video_id": str(video.id)},
                priority=job.priority,
                parent_job_id=str(job.id),
            )

    video.status = "ready"
    return {"ok": True}


@registry.register("video.normalize_subtitle")
def video_normalize_subtitle(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    subs = session.execute(select(Asset).where(Asset.video_id == video.id, Asset.type == "subtitle")).scalars().all()
    sub = _best_language_subtitle(list(subs))
    if not sub:
        return {"skipped": "no_subtitles"}

    with job_workdir(job.id) as wd:
        local_sub = wd / f"subtitle.{sub.format}"
        s3_download_file(bucket=sub.s3_bucket, key=sub.s3_key, local_path=local_sub)
        segments_json, plain = normalize_subtitle(local_sub)

        seg_path = wd / "segments.json"
        txt_path = wd / "plain.txt"
        seg_path.write_text(segments_json, encoding="utf-8")
        txt_path.write_text(plain, encoding="utf-8")

        lang = (sub.language or "und").lower()
        base = f"{video.provider}/{video.media_id}/{video.provider_video_id}/transcript/{lang}"
        force = bool(job.params.get("force"))

        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="json",
            language=lang,
            source="subtitle",
            variant="segments",
            local_path=seg_path,
            s3_key=f"{base}/segments.json",
            content_type="application/json",
            replace=force,
        )
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="txt",
            language=lang,
            source="subtitle",
            variant="plain",
            local_path=txt_path,
            s3_key=f"{base}/plain.txt",
            content_type="text/plain; charset=utf-8",
            replace=force,
        )

    _schedule_auto_video_embedding_refresh(
        session,
        video_id=video.id,
        text_checksum_value=transcript_checksum(plain),
        priority=job.priority,
        parent_job_id=str(job.id),
    )
    _enqueue_brief_for_video_playlists(session, video=video)
    if llm_enabled():
        enqueue_job(
            session,
            type_="video.polish_transcript",
            params={
                "video_id": str(video.id),
                "language": lang,
                "source": "subtitle",
                "force": bool(job.params.get("force")),
            },
            priority=job.priority,
            parent_job_id=str(job.id),
        )

    video.status = "ready"
    return {"ok": True}


@registry.register("video.asr_transcribe")
def video_asr_transcribe(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    audio_asset = session.execute(
        select(Asset).where(Asset.video_id == video.id, Asset.type == "audio").order_by(Asset.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if not audio_asset:
        force = bool(job.params.get("force"))
        if force:
            retries = int(job.params.get("retries") or 0)
            if retries < 10:
                params = dict(job.params)
                params["retries"] = retries + 1
                job_log(session, job, f"audio not ready; reschedule asr_transcribe (retry {retries + 1}/10)", level="warn")
                enqueue_in(session, seconds=30, type_=job.type, params=params, priority=job.priority)
                return {"rescheduled": True, "reason": "audio asset not found"}
        return {"skipped": "audio asset not found"}

    defer = inspect_asr_backend_defer()
    if defer is not None:
        _reschedule_asr_job(
            job,
            counter_key="asr_capacity_defers",
            delay_seconds=defer.delay_seconds,
            reason=defer.reason,
        )

    with job_workdir(job.id) as wd:
        local_audio = wd / f"audio.{audio_asset.format}"
        s3_download_file(bucket=audio_asset.s3_bucket, key=audio_asset.s3_key, local_path=local_audio)
        try:
            resp = asr_transcribe(audio_path=local_audio, language="zh", media_duration_seconds=video.duration_sec)
        except httpx.HTTPStatusError as exc:
            status_code = int(exc.response.status_code)
            detail = _asr_error_detail(exc)
            if status_code in _ASR_TERMINAL_HTTP_STATUS_CODES:
                raise JobTerminalFailure(f"asr request rejected with http {status_code}: {detail}") from exc
            if status_code in _ASR_TRANSIENT_HTTP_STATUS_CODES:
                _reschedule_asr_job(
                    job,
                    counter_key="asr_transient_defers",
                    delay_seconds=_asr_transient_delay_seconds(job, status_code),
                    reason=f"asr backend returned transient http {status_code}: {detail}",
                )
            raise

        segments_path = wd / "segments.json"
        plain_path = wd / "plain.txt"
        segments = resp.get("segments")
        text = resp.get("text") or ""
        if segments:
            segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            segments_path.write_text("[]", encoding="utf-8")
        plain_path.write_text(str(text), encoding="utf-8")

        base = f"{video.provider}/{video.media_id}/{video.provider_video_id}/transcript/zh"
        force = bool(job.params.get("force"))

        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="json",
            language="zh",
            source="qwen3-asr",
            variant="segments",
            local_path=segments_path,
            s3_key=f"{base}/qwen3-asr-segments.json",
            content_type="application/json",
            replace=force,
        )
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="txt",
            language="zh",
            source="qwen3-asr",
            variant="plain",
            local_path=plain_path,
            s3_key=f"{base}/qwen3-asr-plain.txt",
            content_type="text/plain; charset=utf-8",
            replace=force,
        )

    _schedule_auto_video_embedding_refresh(
        session,
        video_id=video.id,
        text_checksum_value=transcript_checksum(str(text)),
        priority=job.priority,
        parent_job_id=str(job.id),
    )
    _enqueue_brief_for_video_playlists(session, video=video)
    if llm_enabled():
        enqueue_job(
            session,
            type_="video.polish_transcript",
            params={
                "video_id": str(video.id),
                "language": "zh",
                "source": "qwen3-asr",
                "force": bool(job.params.get("force")),
            },
            priority=job.priority,
            parent_job_id=str(job.id),
        )

    video.status = "ready"
    return {"ok": True}
