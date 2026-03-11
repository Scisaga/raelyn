from __future__ import annotations

from datetime import date
import uuid
from typing import Any

from raelyn.db import session_scope
from raelyn.mcp.serialize import serialize_for_mcp
from raelyn.services.brief_actions import schedule_brief_generation
from raelyn.services.media_actions import schedule_media_sync
from raelyn.services.video_actions import schedule_video_download, schedule_video_retranscribe


def sync_media(media_id: uuid.UUID, *, scope: str = "recent") -> dict[str, Any]:
    with session_scope() as session:
        return serialize_for_mcp(schedule_media_sync(session, media_id, scope=scope))


def download_video(video_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        return serialize_for_mcp(schedule_video_download(session, video_id))


def retranscribe_video(video_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        return serialize_for_mcp(schedule_video_retranscribe(session, video_id))


def generate_brief(playlist_id: uuid.UUID, *, granularity: str, date_in_period: date) -> dict[str, Any]:
    with session_scope() as session:
        return serialize_for_mcp(
            schedule_brief_generation(
                session,
                playlist_id=playlist_id,
                granularity=granularity,
                date_in_period=date_in_period,
            )
        )
