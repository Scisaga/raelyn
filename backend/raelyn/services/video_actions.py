from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String
from sqlalchemy import cast
from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.jobs.enqueue import enqueue_in, enqueue_job
from raelyn.models import AppConfig, Asset, Job, JobEvent, Video
from raelyn.services.asr import asr_enabled

_DOWNLOAD_JOB_TYPES = {"video.download", "video.download.youtube", "video.download.bilibili"}
_DOWNLOAD_JOB_PRIORITY = 10


def _download_job_type_for_video(video: Video) -> str:
    return (
        "video.download.youtube"
        if video.provider == "youtube"
        else ("video.download.bilibili" if video.provider == "bilibili" else "video.download")
    )


def _job_video_id_expr():
    return Job.params["video_id"].astext


def _find_active_download_job(session: Session, *, video_id: uuid.UUID) -> Job | None:
    return session.execute(
        select(Job)
        .where(
            Job.type.in_(_DOWNLOAD_JOB_TYPES),
            Job.status.in_(("pending", "running")),
            _job_video_id_expr() == cast(video_id, String),
        )
        .order_by(Job.status.asc(), Job.priority.desc(), Job.created_at.asc(), Job.id.asc())
        .limit(1)
    ).scalar_one_or_none()


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

    job_type = _download_job_type_for_video(video)
    active = _find_active_download_job(session, video_id=video.id)
    if active:
        previous_priority = int(active.priority or 0)
        if active.status == "pending" and previous_priority < _DOWNLOAD_JOB_PRIORITY:
            active.priority = _DOWNLOAD_JOB_PRIORITY
            session.add(
                JobEvent(
                    job_id=active.id,
                    level="info",
                    message="download priority promoted",
                    data={
                        "video_id": str(video.id),
                        "priority": _DOWNLOAD_JOB_PRIORITY,
                        "previous_priority": previous_priority,
                    },
                )
            )
            message = "existing download priority promoted"
        elif active.status == "running":
            message = "download already running"
        else:
            message = "existing download already queued"
        return {
            "ok": True,
            "status": "accepted",
            "message": message,
            "video_id": str(video.id),
            "job_id": str(active.id),
            "job_type": str(active.type or job_type),
            "priority": int(active.priority or 0),
            "reused": True,
        }

    job_id = enqueue_job(session, type_=job_type, params={"video_id": str(video.id)}, priority=_DOWNLOAD_JOB_PRIORITY)
    return {
        "ok": True,
        "status": "accepted",
        "message": "video download enqueued",
        "video_id": str(video.id),
        "job_id": str(job_id),
        "job_type": job_type,
        "priority": _DOWNLOAD_JOB_PRIORITY,
        "reused": False,
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
