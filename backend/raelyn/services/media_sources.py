from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Media
from raelyn.services.provider import detect_provider, extract_media_identity


@dataclass(frozen=True)
class MediaResolution:
    media: Media
    created: bool


def resolve_media_url(
    session: Session,
    *,
    url: str,
    provider: str | None = None,
    enqueue_profile_sync: bool = True,
) -> MediaResolution:
    """在调用方事务内按平台身份查重或创建全局信源。"""

    normalized_url = str(url or "").strip()
    if not normalized_url:
        raise ValueError("url is required")

    resolved_provider = str(provider or "").strip().lower() or detect_provider(normalized_url)
    if not resolved_provider:
        raise ValueError("无法从 URL 识别 provider，请显式传 provider")

    try:
        identity = extract_media_identity(provider=resolved_provider, url=normalized_url)
    except Exception as error:
        raise ValueError(f"无法解析媒体：{error}") from error

    media = session.execute(
        select(Media).where(
            Media.provider == resolved_provider,
            Media.provider_media_id == identity.provider_media_id,
        )
    ).scalar_one_or_none()
    created = media is None
    if media is None:
        media = Media(
            provider=resolved_provider,
            provider_media_id=identity.provider_media_id,
            url=normalized_url,
            monitor_enabled=False,
        )
        session.add(media)
    else:
        media.url = normalized_url
    session.flush([media])

    if enqueue_profile_sync:
        enqueue_job(
            session,
            type_="media.sync_profile",
            params={"media_id": str(media.id)},
            priority=10,
        )
    return MediaResolution(media=media, created=created)
