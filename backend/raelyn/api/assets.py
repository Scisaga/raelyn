from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from raelyn.api.asset_refs import AssetRef, build_asset_ref
from raelyn.api.orm import OrmModel
from raelyn.db import session_scope
from raelyn.models import Asset, Media, Video
from raelyn.services.downloads import build_download_filename, content_disposition_attachment
from raelyn.services.s3 import ClientError, iter_s3_body, s3_get_object_stream


router = APIRouter(tags=["assets"])


class AssetOut(OrmModel):
    id: uuid.UUID
    video_id: uuid.UUID | None = None
    type: str
    format: str
    language: str | None = None
    source: str
    variant: str | None = None
    size_bytes: int | None = None
    asset_ref: AssetRef | None = None


def _asset_download_filename(session, asset: Asset) -> str:
    ext = (asset.format or "bin").lstrip(".").lower() or "bin"
    if asset.video_id:
        row = session.execute(select(Video, Media).join(Media, Media.id == Video.media_id).where(Video.id == asset.video_id)).first()
        if row:
            video, media = row
            return build_download_filename(
                media_name=media.name if media else None,
                title=video.title,
                fallback_id=video.provider_video_id,
                ext=ext,
            )
    base = (asset.variant or asset.type or "asset").strip() or "asset"
    return f"{base}.{ext}"


def _stream_asset(asset: Asset, *, byte_range: str | None, as_attachment: bool, filename: str | None = None) -> StreamingResponse:
    try:
        stream = s3_get_object_stream(bucket=asset.s3_bucket, key=asset.s3_key, byte_range=byte_range)
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code") or ""
        status = int((e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 500)
        if code in {"NoSuchKey", "404"} or status == 404:
            raise HTTPException(status_code=404, detail="asset object not found") from e
        if code in {"InvalidRange"} or status == 416:
            raise HTTPException(status_code=416, detail="invalid range") from e
        raise HTTPException(status_code=502, detail=f"s3 read failed: {code or status}") from e

    headers: dict[str, str] = {"Accept-Ranges": "bytes"}
    if stream.content_length is not None:
        headers["Content-Length"] = str(stream.content_length)
    if stream.content_range:
        headers["Content-Range"] = stream.content_range
    if stream.etag:
        headers["ETag"] = stream.etag
    if stream.last_modified:
        headers["Last-Modified"] = stream.last_modified
    if as_attachment:
        headers["Content-Disposition"] = content_disposition_attachment(filename or "download.bin")
    status_code = 206 if byte_range else 200
    media_type = stream.content_type or "application/octet-stream"
    return StreamingResponse(iter_s3_body(stream.body), status_code=status_code, headers=headers, media_type=media_type)


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: uuid.UUID) -> AssetOut:
    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="asset not found")
        filename = _asset_download_filename(session, asset)
        payload = AssetOut.model_validate(
            asset.__dict__ | {"asset_ref": build_asset_ref(asset, filename=filename, response_content_disposition=content_disposition_attachment(filename))}
        )
        return payload


@router.get("/assets/{asset_id}/content")
def get_asset_content(asset_id: uuid.UUID, range_header: str | None = Header(default=None, alias="Range")) -> StreamingResponse:
    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="asset not found")
        return _stream_asset(asset, byte_range=range_header, as_attachment=False)


@router.get("/assets/{asset_id}/download")
def download_asset(asset_id: uuid.UUID, range_header: str | None = Header(default=None, alias="Range")) -> StreamingResponse:
    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="asset not found")
        filename = _asset_download_filename(session, asset)
        return _stream_asset(asset, byte_range=range_header, as_attachment=True, filename=filename)
