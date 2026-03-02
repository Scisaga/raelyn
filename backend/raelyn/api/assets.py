from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from raelyn.api.orm import OrmModel

from raelyn.db import session_scope
from raelyn.models import Asset
from raelyn.services.s3 import s3_presign_get


router = APIRouter(tags=["assets"])


class AssetOut(OrmModel):
    id: uuid.UUID
    video_id: uuid.UUID | None = None
    type: str
    format: str
    language: str | None = None
    source: str
    variant: str | None = None
    s3_bucket: str
    s3_key: str
    size_bytes: int | None = None
    presigned_url: str | None = None


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: uuid.UUID, presign: bool = True) -> AssetOut:
    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="asset not found")
        url = s3_presign_get(asset.s3_bucket, asset.s3_key) if presign else None
        payload = AssetOut.model_validate(asset.__dict__ | {"presigned_url": url})
        return payload
