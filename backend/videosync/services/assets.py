from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from videosync.config import settings
from videosync.models import Asset
from videosync.services.s3 import s3_upload_file


def ensure_asset(
    session: Session,
    *,
    video_id: uuid.UUID | None,
    type_: str,
    format_: str,
    language: str | None,
    source: str,
    variant: str | None,
    local_path: Path,
    s3_key: str,
    metadata: dict[str, Any] | None = None,
    content_type: str | None = None,
    dedupe: bool = True,
    replace: bool = False,
) -> Asset:
    if dedupe:
        existing = session.execute(
            select(Asset).where(
                Asset.video_id == video_id,
                Asset.type == type_,
                Asset.format == format_,
                Asset.language.is_(None) if language is None else Asset.language == language,
                Asset.source == source,
                Asset.variant.is_(None) if variant is None else Asset.variant == variant,
            )
        ).scalar_one_or_none()
        if existing:
            if not replace:
                return existing

            result = s3_upload_file(local_path=local_path, bucket=settings.s3_bucket, key=s3_key, content_type=content_type)
            existing.s3_bucket = result.bucket
            existing.s3_key = result.key
            existing.size_bytes = result.size_bytes
            if metadata is not None:
                existing.meta = metadata
            session.add(existing)
            session.flush()
            return existing

    result = s3_upload_file(local_path=local_path, bucket=settings.s3_bucket, key=s3_key, content_type=content_type)
    asset = Asset(
        video_id=video_id,
        type=type_,
        format=format_,
        language=language,
        source=source,
        variant=variant,
        s3_bucket=result.bucket,
        s3_key=result.key,
        size_bytes=result.size_bytes,
        meta=metadata,
    )
    session.add(asset)
    session.flush()
    return asset
