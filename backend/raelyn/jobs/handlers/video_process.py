from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.config import settings
from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.jobs.log import job_log
from raelyn.jobs.progress import set_job_lease_deadline
from raelyn.jobs.registry import registry
from raelyn.jobs.reschedule import JobReschedule, JobTerminalFailure
from raelyn.models import Asset, Job, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.asr import (
    asr_enabled,
    asr_transcribe,
    inspect_asr_backend_defer,
    resolve_asr_timeout_seconds,
)
from raelyn.services.ffmpeg import extract_audio_to_m4a
from raelyn.services.event_analysis import schedule_video_event_extraction
from raelyn.services.inference import LOCAL_PROVIDER, get_effective_asr_config
from raelyn.services.llm import llm_enabled
from raelyn.services.s3 import s3_download_file
from raelyn.services.subtitles import normalize_subtitle
from raelyn.services.workdir import job_workdir
from raelyn.timeutil import utcnow

from .briefs import _enqueue_brief_for_video_playlists
from .common import _best_language_subtitle


_ASR_TERMINAL_HTTP_STATUS_CODES = {400, 401, 403, 404, 413, 415, 422, 507}
_ASR_TRANSIENT_HTTP_STATUS_CODES = {429, 502, 503, 504}
_LOCAL_ASR_READ_TIMEOUT_MAX_ATTEMPTS = 3
_LOCAL_ASR_LEASE_GRACE_SECONDS = 300
_ASR_AUTO_LANGUAGE_VALUES = {"", "auto", "detect", "mixed", "multilingual", "und", "unknown"}
_SUBTITLE_LANGUAGE_ALIASES = {
    "ai-zh": "zh",
    "ai-en": "en",
}


def _normalize_asr_language(value: object) -> str | None:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in _ASR_AUTO_LANGUAGE_VALUES:
        return None
    aliases = {
        "chinese": "zh",
        "mandarin": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "english": "en",
        "en-us": "en",
        "en-gb": "en",
    }
    text = aliases.get(text, text)
    if "-" in text:
        text = text.split("-", 1)[0]
    return text or None


def _configured_asr_language() -> str | None:
    return _normalize_asr_language(settings.asr_language)


def _normalize_subtitle_language(value: object) -> str:
    text = str(value or "und").strip().lower().replace("_", "-") or "und"
    return _SUBTITLE_LANGUAGE_ALIASES.get(text, text)


def _asr_payload_language(resp: dict) -> str | None:
    for key in ("language", "detected_language", "language_code", "detected_language_code"):
        language = _normalize_asr_language(resp.get(key))
        if language:
            return language

    segments = resp.get("segments")
    if isinstance(segments, list):
        for item in segments:
            if not isinstance(item, dict):
                continue
            for key in ("language", "detected_language", "language_code", "detected_language_code"):
                language = _normalize_asr_language(item.get(key))
                if language:
                    return language

    raw = resp.get("raw")
    if isinstance(raw, dict):
        result = raw.get("result")
        if isinstance(result, dict):
            for key in ("language", "detected_language", "language_code", "detected_language_code"):
                language = _normalize_asr_language(result.get(key))
                if language:
                    return language
    return None


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


def _schedule_auto_video_event_extraction(
    session: Session,
    *,
    video_id: uuid.UUID,
    priority: int = 0,
    parent_job_id: str | None = None,
) -> uuid.UUID | None:
    return schedule_video_event_extraction(
        session,
        video_id=video_id,
        force=False,
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
        has_transcript_source = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type.in_(["subtitle", "transcript"]),
            ).limit(1)
        ).scalar_one_or_none()
        if not has_transcript_source:
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

        lang = _normalize_subtitle_language(sub.language)
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

    _schedule_auto_video_event_extraction(
        session,
        video_id=video.id,
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
    claimed_worker_id = str(job.worker_id or "").strip()
    claimed_execution_token = job.execution_token
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

    asr_config = get_effective_asr_config(session)
    request_timeout_seconds = resolve_asr_timeout_seconds(
        base_timeout_seconds=asr_config.timeout_seconds,
        media_duration_seconds=video.duration_sec,
        provider=asr_config.provider,
    )
    requested_language = _configured_asr_language()
    with job_workdir(job.id) as wd:
        local_audio = wd / f"audio.{audio_asset.format}"
        s3_download_file(bucket=audio_asset.s3_bucket, key=audio_asset.s3_key, local_path=local_audio)
        audio_size_bytes = local_audio.stat().st_size
        lease_expires_at = job.lease_expires_at
        if asr_config.provider == LOCAL_PROVIDER:
            requested_lease_expires_at = utcnow() + timedelta(
                seconds=request_timeout_seconds + _LOCAL_ASR_LEASE_GRACE_SECONDS
            )
            if lease_expires_at is None or lease_expires_at < requested_lease_expires_at:
                if claimed_execution_token is None or not set_job_lease_deadline(
                    job_id=job.id,
                    worker_id=claimed_worker_id,
                    execution_token=claimed_execution_token,
                    lease_expires_at=requested_lease_expires_at,
                ):
                    raise RuntimeError("asr job lease extension rejected because job ownership changed")
                lease_expires_at = requested_lease_expires_at

        attempt_number = int(job.attempt or 0) + 1
        request_event_data = {
            "provider": asr_config.provider,
            "media_duration_seconds": video.duration_sec,
            "audio_size_bytes": audio_size_bytes,
            "read_timeout_seconds": request_timeout_seconds,
            "lease_expires_at": lease_expires_at.isoformat() if lease_expires_at else None,
            "attempt": attempt_number,
        }
        if asr_config.provider == LOCAL_PROVIDER:
            request_event_data["max_read_timeout_attempts"] = _LOCAL_ASR_READ_TIMEOUT_MAX_ATTEMPTS
        job_log(session, job, "asr request started", data=request_event_data)
        session.commit()
        request_started_at = time.monotonic()
        try:
            resp = asr_transcribe(
                audio_path=local_audio,
                language=requested_language,
                media_duration_seconds=video.duration_sec,
                config=asr_config,
                timeout_seconds=request_timeout_seconds,
            )
        except httpx.ReadTimeout as exc:
            request_elapsed_seconds = time.monotonic() - request_started_at
            timeout_event_data = dict(request_event_data)
            timeout_event_data["request_elapsed_seconds"] = round(request_elapsed_seconds, 3)
            job_log(session, job, "asr request read timed out", level="error", data=timeout_event_data)
            session.commit()
            if (
                asr_config.provider == LOCAL_PROVIDER
                and attempt_number >= _LOCAL_ASR_READ_TIMEOUT_MAX_ATTEMPTS
            ):
                reason = (
                    "local asr read timed out "
                    f"after {request_timeout_seconds}s "
                    f"(media_duration_seconds={video.duration_sec}, "
                    f"attempt={attempt_number}/{_LOCAL_ASR_READ_TIMEOUT_MAX_ATTEMPTS})"
                )
                raise JobTerminalFailure(reason) from exc
            raise
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

        request_elapsed_seconds = time.monotonic() - request_started_at
        success_event_data = dict(request_event_data)
        success_event_data["request_elapsed_seconds"] = round(request_elapsed_seconds, 3)
        if isinstance(video.duration_sec, int) and video.duration_sec > 0 and request_elapsed_seconds > 0:
            success_event_data["realtime_factor"] = round(video.duration_sec / request_elapsed_seconds, 3)
        job_log(session, job, "asr request succeeded", data=success_event_data)
        session.commit()

        segments_path = wd / "segments.json"
        plain_path = wd / "plain.txt"
        segments = resp.get("segments")
        text = resp.get("text") or ""
        if segments:
            segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            segments_path.write_text("[]", encoding="utf-8")
        plain_path.write_text(str(text), encoding="utf-8")

        transcript_language = _asr_payload_language(resp) or requested_language
        transcript_language_slug = transcript_language or "und"
        base = f"{video.provider}/{video.media_id}/{video.provider_video_id}/transcript/{transcript_language_slug}"
        force = bool(job.params.get("force"))

        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="json",
            language=transcript_language,
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
            language=transcript_language,
            source="qwen3-asr",
            variant="plain",
            local_path=plain_path,
            s3_key=f"{base}/qwen3-asr-plain.txt",
            content_type="text/plain; charset=utf-8",
            replace=force,
        )

    _schedule_auto_video_event_extraction(
        session,
        video_id=video.id,
        priority=job.priority,
        parent_job_id=str(job.id),
    )
    _enqueue_brief_for_video_playlists(session, video=video)
    if llm_enabled() and transcript_language == "zh":
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
    return {"ok": True, "language": transcript_language, "requested_language": requested_language}
