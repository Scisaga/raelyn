from __future__ import annotations

import uuid

from pydantic import BaseModel

from raelyn.config import settings
from raelyn.models import Asset
from raelyn.services.s3 import s3_presign_get


class AssetRef(BaseModel):
    id: uuid.UUID
    type: str
    format: str
    presigned_url: str | None = None
    download_presigned_url: str | None = None
    filename: str | None = None


def build_asset_ref(
    asset: Asset | None,
    *,
    filename: str | None = None,
    response_content_disposition: str | None = None,
    expires_seconds: int = 3600,
    include_presigned: bool = True,
) -> AssetRef | None:
    if not asset:
        return None

    presigned_url = None
    download_presigned_url = None
    should_presign = bool(include_presigned and settings.asset_presign_enabled)
    if should_presign:
        try:
            presigned_url = s3_presign_get(asset.s3_bucket, asset.s3_key, expires_seconds=expires_seconds)
        except Exception:
            presigned_url = None

    if should_presign and response_content_disposition:
        try:
            download_presigned_url = s3_presign_get(
                asset.s3_bucket,
                asset.s3_key,
                expires_seconds=expires_seconds,
                response_content_disposition=response_content_disposition,
            )
        except Exception:
            download_presigned_url = None

    return AssetRef(
        id=asset.id,
        type=asset.type,
        format=asset.format,
        presigned_url=presigned_url,
        download_presigned_url=download_presigned_url,
        filename=filename,
    )
