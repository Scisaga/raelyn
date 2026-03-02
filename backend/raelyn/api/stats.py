from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from raelyn.db import session_scope
from raelyn.models import Job, Media, Video


router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats() -> dict:
    with session_scope() as session:
        media_count = session.execute(select(func.count()).select_from(Media)).scalar_one()
        video_count = session.execute(select(func.count()).select_from(Video)).scalar_one()
        pending_jobs = session.execute(select(func.count()).select_from(Job).where(Job.status == "pending")).scalar_one()
        failed_jobs = session.execute(select(func.count()).select_from(Job).where(Job.status == "failed")).scalar_one()
        return {
            "media_count": media_count,
            "video_count": video_count,
            "pending_jobs": pending_jobs,
            "failed_jobs": failed_jobs,
        }

