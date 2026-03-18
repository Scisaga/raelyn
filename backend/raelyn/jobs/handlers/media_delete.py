from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.jobs.reschedule import JobReschedule
from raelyn.models import Asset, Job, Media
from raelyn.services.job_cancellation import request_job_cancel
from raelyn.services.media_deletion import (
    collect_media_delete_snapshot,
    list_related_jobs,
    refresh_affected_briefs_after_media_delete,
)
from raelyn.services.s3 import s3_clear_bucket


@registry.register("media.delete")
def media_delete(session: Session, job: Job) -> dict | None:
    media_id = uuid.UUID(job.params["media_id"])
    media = session.get(Media, media_id)
    if not media:
        return {"skipped": "media not found"}

    media.monitor_enabled = False
    snapshot = collect_media_delete_snapshot(session, media)

    deleted_pending_jobs = 0
    requested_cancel_jobs = 0
    running_job_ids: list[str] = []
    for related_job in list_related_jobs(session, snapshot, exclude_job_id=job.id):
        if related_job.status == "pending":
            session.delete(related_job)
            deleted_pending_jobs += 1
            continue
        action = request_job_cancel(session, related_job, reason="media_delete")
        if action in {"requested", "already_requested"}:
            running_job_ids.append(str(related_job.id))
        if action != "noop":
            requested_cancel_jobs += 1

    if running_job_ids:
        job_log(
            session,
            job,
            "等待相关运行中任务退出后继续删除媒体",
            level="info",
            data={
                "running_job_ids": running_job_ids,
                "deleted_pending_jobs": deleted_pending_jobs,
                "requested_cancel_jobs": requested_cancel_jobs,
            },
        )
        raise JobReschedule(delay_seconds=5, reason="waiting_related_jobs_to_stop")

    if media.avatar_asset_id:
        avatar_asset = session.get(Asset, media.avatar_asset_id)
        media.avatar_asset_id = None
        media.avatar_s3_key = None
        if avatar_asset:
            session.delete(avatar_asset)

    session.delete(media)
    session.flush()

    brief_result = refresh_affected_briefs_after_media_delete(session, snapshot.affected_periods)

    s3_errors: list[dict[str, str]] = []
    s3_deleted_total = 0
    for bucket in sorted(snapshot.cleanup_buckets):
        for prefix in snapshot.prefixes:
            try:
                res = s3_clear_bucket(bucket=bucket, prefix=prefix)
                if not res.get("ok"):
                    s3_errors.append({"bucket": bucket, "prefix": prefix, "error": str(res.get("error") or "")})
                    continue
                s3_deleted_total += int(res.get("deleted") or 0)
            except Exception as e:
                s3_errors.append({"bucket": bucket, "prefix": prefix, "error": str(e)})

    job_log(
        session,
        job,
        "媒体删除完成",
        level="info",
        data={
            "deleted_pending_jobs": deleted_pending_jobs,
            "requested_cancel_jobs": requested_cancel_jobs,
            "brief_refreshed": brief_result.get("refreshed", 0),
            "brief_emptied": brief_result.get("emptied", 0),
            "s3_deleted": s3_deleted_total,
            "s3_error_count": len(s3_errors),
        },
    )
    return {
        "ok": True,
        "media_id": str(media_id),
        "deleted_pending_jobs": deleted_pending_jobs,
        "requested_cancel_jobs": requested_cancel_jobs,
        "brief_refreshed": int(brief_result.get("refreshed") or 0),
        "brief_emptied": int(brief_result.get("emptied") or 0),
        "s3_deleted": s3_deleted_total,
        "s3_errors": s3_errors,
    }
