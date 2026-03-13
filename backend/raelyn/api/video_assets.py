from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.db import session_scope
from raelyn.models import Asset, Media, Video
from raelyn.services.downloads import build_download_filename, content_disposition_attachment
from raelyn.services.s3 import s3_presign_get
from raelyn.services.ytdlp import YtdlpCookiesInvalidError, ytdlp_extract_info


router = APIRouter(tags=["videos"])

_CJK_RE = re.compile(r"[\u3400-\u9FFF]")
_LOCALIZE_TITLE_ATTEMPTED: set[str] = set()
_LOCALIZE_TITLE_SOCKET_TIMEOUT_SECONDS = 3


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _maybe_localize_youtube_title(video: Video, *, socket_timeout_seconds: int | None = None) -> None:
    if (video.provider or "") != "youtube":
        return
    if video.title and _contains_cjk(video.title):
        return
    try:
        vid = str(getattr(video, "id", "") or "").strip()
    except Exception:
        vid = ""
    if vid and vid in _LOCALIZE_TITLE_ATTEMPTED:
        return
    url = str(video.url or "").strip()
    if not url:
        return
    if vid:
        _LOCALIZE_TITLE_ATTEMPTED.add(vid)
    try:
        info = ytdlp_extract_info(
            url,
            provider=video.provider,
            flat=False,
            max_entries=1,
            socket_timeout=socket_timeout_seconds,
        )
    except (YtdlpCookiesInvalidError, Exception):
        return
    title = str(info.get("title") or "").strip()
    if title and _contains_cjk(title) and title != (video.title or ""):
        video.title = title


class VideoAssetOut(BaseModel):
    id: uuid.UUID
    type: str
    format: str
    language: str | None = None
    source: str
    variant: str | None = None
    presigned_url: str | None = None
    download_url: str | None = None
    filename: str | None = None


@router.get("/videos/{video_id}/assets", response_model=list[VideoAssetOut])
def list_video_assets(
    video_id: uuid.UUID,
    presign: bool = True,
    download: bool = True,
    localize_title: bool = True,
) -> list[VideoAssetOut]:
    with session_scope() as session:
        row = session.execute(select(Video, Media).join(Media, Media.id == Video.media_id).where(Video.id == video_id)).first()
        if not row:
            raise HTTPException(status_code=404, detail="video not found")
        video, media = row
        if localize_title:
            _maybe_localize_youtube_title(video, socket_timeout_seconds=_LOCALIZE_TITLE_SOCKET_TIMEOUT_SECONDS)
        assets = session.execute(select(Asset).where(Asset.video_id == video_id).order_by(Asset.created_at.asc())).scalars().all()
        out: list[VideoAssetOut] = []
        for a in assets:
            url = s3_presign_get(a.s3_bucket, a.s3_key) if presign else None
            filename = None
            download_url = None
            if presign and download:
                filename = build_download_filename(
                    media_name=media.name if media else None,
                    title=video.title,
                    fallback_id=video.provider_video_id,
                    ext=a.format,
                )
                download_url = s3_presign_get(
                    a.s3_bucket,
                    a.s3_key,
                    response_content_disposition=content_disposition_attachment(filename),
                )
            out.append(
                VideoAssetOut(
                    id=a.id,
                    type=a.type,
                    format=a.format,
                    language=a.language,
                    source=a.source,
                    variant=a.variant,
                    presigned_url=url,
                    download_url=download_url,
                    filename=filename,
                )
            )
        return out
