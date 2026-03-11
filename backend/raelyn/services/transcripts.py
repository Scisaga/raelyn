from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.models import Asset
from raelyn.services.s3 import s3_get_bytes


TRANSCRIPT_VARIANTS = ("polished", "plain")
TRANSCRIPT_SOURCE_LANGUAGE_ORDER = (
    ("subtitle", "zh"),
    ("qwen3-asr", "zh"),
    ("speaches", "zh"),
)


def pick_transcript_asset(session: Session, video_id: uuid.UUID, *, format_: str = "txt") -> Asset | None:
    for variant in TRANSCRIPT_VARIANTS:
        for source, language in TRANSCRIPT_SOURCE_LANGUAGE_ORDER:
            asset = session.execute(
                select(Asset).where(
                    Asset.video_id == video_id,
                    Asset.type == "transcript",
                    Asset.format == format_,
                    Asset.variant == variant,
                    Asset.source == source,
                    Asset.language == language,
                )
            ).scalar_one_or_none()
            if asset:
                return asset

    for variant in TRANSCRIPT_VARIANTS:
        asset = session.execute(
            select(Asset)
            .where(
                Asset.video_id == video_id,
                Asset.type == "transcript",
                Asset.format == format_,
                Asset.variant == variant,
            )
            .order_by(Asset.created_at.desc())
        ).scalar_one_or_none()
        if asset:
            return asset
    return None


def read_text_asset(asset: Asset, *, max_chars: int | None = None) -> tuple[str, bool]:
    raw = s3_get_bytes(bucket=asset.s3_bucket, key=asset.s3_key)
    text = raw.decode("utf-8", errors="ignore")
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "\n…(truncated)…\n", True
    return text, False


def transcript_polish_method(asset: Asset | None) -> str | None:
    if not asset:
        return None
    try:
        if isinstance(asset.meta, dict):
            value = asset.meta.get("polish_method")
            if value is not None:
                return str(value)
    except Exception:
        pass
    return None


def build_transcript_payload(session: Session, video_id: uuid.UUID, *, max_chars: int = 200_000) -> dict[str, Any]:
    asset = pick_transcript_asset(session, video_id)
    if not asset:
        return {"ok": False, "reason": "no transcript", "text": ""}

    text, truncated = read_text_asset(asset, max_chars=max_chars)
    created_at = getattr(asset, "created_at", None)
    updated_at = getattr(asset, "updated_at", None) or created_at
    return {
        "ok": True,
        "asset_id": str(asset.id),
        "language": asset.language,
        "source": asset.source,
        "variant": asset.variant,
        "polish_method": transcript_polish_method(asset),
        "created_at": created_at,
        "updated_at": updated_at,
        "truncated": truncated,
        "text": text,
    }
