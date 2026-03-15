from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config as BotoConfig

from raelyn.config import settings


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        use_ssl=settings.s3_use_ssl,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )


@dataclass(frozen=True)
class UploadResult:
    bucket: str
    key: str
    size_bytes: int | None


@dataclass
class ObjectStreamResult:
    body: Any
    content_length: int | None
    content_type: str | None
    content_range: str | None
    etag: str | None
    last_modified: str | None


def _format_http_last_modified(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return format_datetime(value, usegmt=True)
    except Exception:
        return None


def s3_ensure_bucket(*, bucket: str | None = None) -> dict[str, Any]:
    """
    Best-effort ensure the bucket exists.
    - Returns: {"ok": bool, "bucket": str, "created": bool, "error": str|None}
    - Never raises for common "already exists" cases; may raise for unexpected errors.
    """
    client = _client()
    b = (bucket or settings.s3_bucket).strip()
    if not b:
        return {"ok": False, "bucket": "", "created": False, "error": "bucket not configured"}
    try:
        client.head_bucket(Bucket=b)
        return {"ok": True, "bucket": b, "created": False, "error": None}
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code") or ""
        status = (e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        missing = code in {"NoSuchBucket", "NotFound", "404"} or status == 404
        if not missing:
            return {"ok": False, "bucket": b, "created": False, "error": f"{code or status or 'error'}"}

    # Missing bucket -> try create
    try:
        client.create_bucket(Bucket=b)
        return {"ok": True, "bucket": b, "created": True, "error": None}
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code") or ""
        if code in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
            return {"ok": True, "bucket": b, "created": False, "error": None}
        return {"ok": False, "bucket": b, "created": False, "error": f"{code or 'create_bucket_failed'}"}


def s3_check_bucket(*, bucket: str | None = None) -> dict[str, Any]:
    client = _client()
    b = (bucket or settings.s3_bucket).strip()
    if not b:
        return {"ok": False, "bucket": "", "error": "bucket not configured"}
    try:
        client.head_bucket(Bucket=b)
        return {"ok": True, "bucket": b, "error": None}
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code") or ""
        status = (e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return {"ok": False, "bucket": b, "error": f"{code or status or 'error'}"}


def s3_upload_file(*, local_path: Path, bucket: str, key: str, content_type: str | None = None) -> UploadResult:
    client = _client()
    extra_args: dict[str, Any] = {}
    if content_type:
        extra_args["ContentType"] = content_type
    client.upload_file(str(local_path), bucket, key, ExtraArgs=extra_args or None)
    size = local_path.stat().st_size if local_path.exists() else None
    return UploadResult(bucket=bucket, key=key, size_bytes=size)


def s3_download_file(*, bucket: str, key: str, local_path: Path) -> None:
    client = _client()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, key, str(local_path))


def s3_presign_get(
    bucket: str,
    key: str,
    expires_seconds: int = 3600,
    *,
    response_content_disposition: str | None = None,
    response_content_type: str | None = None,
) -> str:
    client = _client()
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if response_content_disposition:
        params["ResponseContentDisposition"] = response_content_disposition
    if response_content_type:
        params["ResponseContentType"] = response_content_type
    return client.generate_presigned_url(
        ClientMethod="get_object",
        Params=params,
        ExpiresIn=expires_seconds,
    )


def s3_get_bytes(*, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
    client = _client()
    extra: dict[str, Any] = {}
    if max_bytes is not None and max_bytes > 0:
        extra["Range"] = f"bytes=0-{max_bytes - 1}"
    obj = client.get_object(Bucket=bucket, Key=key, **extra)
    body = obj.get("Body")
    if not body:
        return b""
    data = body.read()
    try:
        body.close()
    except Exception:
        pass
    return data or b""


def s3_get_object_stream(*, bucket: str, key: str, byte_range: str | None = None) -> ObjectStreamResult:
    client = _client()
    extra: dict[str, Any] = {}
    if byte_range:
        extra["Range"] = byte_range
    obj = client.get_object(Bucket=bucket, Key=key, **extra)
    last_modified = obj.get("LastModified")
    return ObjectStreamResult(
        body=obj.get("Body"),
        content_length=int(obj.get("ContentLength") or 0) or None,
        content_type=(obj.get("ContentType") or "").strip() or None,
        content_range=(obj.get("ContentRange") or "").strip() or None,
        etag=(obj.get("ETag") or "").strip() or None,
        last_modified=_format_http_last_modified(last_modified),
    )


def iter_s3_body(body: Any, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    try:
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            body.close()
        except Exception:
            pass


def s3_clear_bucket(*, bucket: str | None = None, prefix: str = "") -> dict[str, Any]:
    """
    Delete all objects under bucket/prefix.
    - Returns: {"ok": bool, "bucket": str, "deleted": int, "error": str|None}
    - Best-effort: missing bucket returns ok=False with error.
    """
    client = _client()
    b = (bucket or settings.s3_bucket).strip()
    if not b:
        return {"ok": False, "bucket": "", "deleted": 0, "error": "bucket not configured"}

    deleted = 0
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, Any] = {"Bucket": b, "MaxKeys": 1000}
            if prefix:
                kwargs["Prefix"] = prefix
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            contents = resp.get("Contents") or []
            keys = [{"Key": o.get("Key")} for o in contents if o.get("Key")]
            if keys:
                del_resp = client.delete_objects(Bucket=b, Delete={"Objects": keys, "Quiet": True})
                deleted += len(keys)
                # If some deletes failed, surface a small hint (but keep going).
                if del_resp.get("Errors"):
                    return {"ok": False, "bucket": b, "deleted": deleted, "error": "delete_objects_partial_failure"}
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return {"ok": True, "bucket": b, "deleted": deleted, "error": None}
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code") or ""
        status = (e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return {"ok": False, "bucket": b, "deleted": deleted, "error": f"{code or status or 'error'}"}
