from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.models import AppConfig, Asset, Video
from raelyn.services.asr import asr_enabled


def schedule_video_download(session: Session, video_id: uuid.UUID) -> dict[str, Any]:
    video = session.get(Video, video_id)
    if not video:
        raise LookupError("video not found")

    if (video.status or "") == "members_only":
        item = session.get(AppConfig, "ytdlp_members_only")
        value = item.value if item else None
        enabled = bool(isinstance(value, dict) and value.get("enabled") is True)
        if not enabled:
            raise RuntimeError("members-only video; download not enqueued")

    job_type = (
        "video.download.youtube"
        if video.provider == "youtube"
        else ("video.download.bilibili" if video.provider == "bilibili" else "video.download")
    )
    job_id = enqueue_job(session, type_=job_type, params={"video_id": str(video.id)}, priority=10)
    return {
        "ok": True,
        "status": "accepted",
        "message": "video download enqueued",
        "video_id": str(video.id),
        "job_id": str(job_id),
        "job_type": job_type,
    }


def schedule_video_retranscribe(session: Session, video_id: uuid.UUID) -> dict[str, Any]:
    video = session.get(Video, video_id)
    if not video:
        raise LookupError("video not found")

    has_zh_subtitle = session.execute(
        select(Asset.id).where(
            Asset.video_id == video_id,
            Asset.type == "subtitle",
            Asset.language.is_not(None),
            Asset.language.ilike("zh%"),
        )
    ).scalar_one_or_none()
    has_audio = session.execute(select(Asset.id).where(Asset.video_id == video_id, Asset.type == "audio")).scalar_one_or_none()

    if has_zh_subtitle:
        job_id = enqueue_job(
            session,
            type_="video.normalize_subtitle",
            params={"video_id": str(video_id), "force": True},
            priority=4,
        )
        return {
            "ok": True,
            "status": "accepted",
            "message": "subtitle normalization enqueued",
            "video_id": str(video_id),
            "mode": "subtitle",
            "job_id": str(job_id),
            "job_type": "video.normalize_subtitle",
            "job_ids": [str(job_id)],
            "job_types": ["video.normalize_subtitle"],
        }

    if not asr_enabled():
        raise ValueError("asr not configured and no zh subtitle available")

    if not has_audio:
        extract_job_id = enqueue_job(session, type_="video.extract_audio", params={"video_id": str(video_id)}, priority=5)
        asr_job_id = enqueue_in(
            session,
            seconds=20,
            type_="video.asr_transcribe",
            params={"video_id": str(video_id), "force": True},
            priority=5,
        )
        return {
            "ok": True,
            "status": "accepted",
            "message": "audio extraction and asr enqueued",
            "video_id": str(video_id),
            "mode": "asr",
            "job_id": str(asr_job_id),
            "job_type": "video.asr_transcribe",
            "job_ids": [str(extract_job_id), str(asr_job_id)],
            "job_types": ["video.extract_audio", "video.asr_transcribe"],
            "note": "audio not ready; scheduled extract_audio first",
        }

    job_id = enqueue_job(
        session,
        type_="video.asr_transcribe",
        params={"video_id": str(video_id), "force": True},
        priority=5,
    )
    return {
        "ok": True,
        "status": "accepted",
        "message": "asr transcription enqueued",
        "video_id": str(video_id),
        "mode": "asr",
        "job_id": str(job_id),
        "job_type": "video.asr_transcribe",
        "job_ids": [str(job_id)],
        "job_types": ["video.asr_transcribe"],
    }
