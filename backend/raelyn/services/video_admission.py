from __future__ import annotations

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from raelyn.models import Asset, Video
from raelyn.services.transcripts import TRANSCRIPT_VARIANTS
from raelyn.services.video_meta import backfill_video_published_at

_VIDEO_PUBLISHED_AT_BACKFILLED = False


def ensure_video_published_at_backfilled(session: Session) -> None:
    global _VIDEO_PUBLISHED_AT_BACKFILLED  # noqa: PLW0603
    if _VIDEO_PUBLISHED_AT_BACKFILLED:
        return
    backfill_video_published_at(session)
    _VIDEO_PUBLISHED_AT_BACKFILLED = True


def video_has_playback_asset_expr():
    return exists(
        select(1).where(
            Asset.video_id == Video.id,
            Asset.type == "video",
        )
    )


def video_has_transcript_text_expr():
    return exists(
        select(1).where(
            Asset.video_id == Video.id,
            Asset.type == "transcript",
            Asset.format == "txt",
            Asset.variant.in_(list(TRANSCRIPT_VARIANTS)),
        )
    )


def playback_admitted_video_expr():
    return and_(Video.published_at.is_not(None), video_has_playback_asset_expr())


def brief_admitted_video_expr():
    return and_(Video.published_at.is_not(None), video_has_transcript_text_expr())
