from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.api.orm import OrmModel
from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Asset, DailyBrief
from raelyn.services.s3 import s3_presign_get


router = APIRouter(tags=["briefs"])


class BriefGenerate(BaseModel):
    playlist_id: uuid.UUID
    date: date


class DailyBriefOut(OrmModel):
    id: uuid.UUID
    playlist_id: uuid.UUID
    brief_date: date
    status: str
    markdown_asset_id: uuid.UUID | None = None
    markdown_url: str | None = None
    error_message: str | None = None
    created_at: Any
    updated_at: Any


@router.post("/briefs/generate")
def generate_brief(payload: BriefGenerate) -> dict:
    with session_scope() as session:
        enqueue_job(
            session,
            type_="brief.generate_daily",
            params={"playlist_id": str(payload.playlist_id), "date": payload.date.isoformat()},
            priority=5,
        )
    return {"ok": True}


class BriefGenerateRange(BaseModel):
    playlist_id: uuid.UUID
    from_date: date
    to_date: date


@router.post("/briefs/generate_range")
def generate_brief_range(payload: BriefGenerateRange) -> dict:
    if payload.to_date < payload.from_date:
        raise HTTPException(status_code=400, detail="to_date must be >= from_date")
    with session_scope() as session:
        n = 0
        d = payload.from_date
        while d <= payload.to_date:
            enqueue_job(
                session,
                type_="brief.generate_daily",
                params={"playlist_id": str(payload.playlist_id), "date": d.isoformat()},
                priority=3,
            )
            n += 1
            d = d + timedelta(days=1)
    return {"ok": True, "enqueued": n}


@router.get("/briefs/by_date", response_model=DailyBriefOut)
def get_brief_by_date(playlist_id: uuid.UUID, date: date) -> DailyBriefOut:
    with session_scope() as session:
        brief = session.execute(
            select(DailyBrief).where(DailyBrief.playlist_id == playlist_id, DailyBrief.brief_date == date)
        ).scalar_one_or_none()
        if not brief:
            raise HTTPException(status_code=404, detail="brief not found")
        out = DailyBriefOut.model_validate(brief)
        if out.markdown_asset_id:
            asset = session.get(Asset, out.markdown_asset_id)
            if asset:
                try:
                    out.markdown_url = s3_presign_get(asset.s3_bucket, asset.s3_key)
                except Exception:
                    out.markdown_url = None
        return out


@router.get("/briefs", response_model=list[DailyBriefOut])
def list_briefs(playlist_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0) -> list[DailyBriefOut]:
    with session_scope() as session:
        stmt = select(DailyBrief)
        if playlist_id:
            stmt = stmt.where(DailyBrief.playlist_id == playlist_id)
        stmt = stmt.order_by(DailyBrief.brief_date.desc()).limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        out: list[DailyBriefOut] = []
        for b in items:
            row = DailyBriefOut.model_validate(b)
            if row.markdown_asset_id:
                asset = session.get(Asset, row.markdown_asset_id)
                if asset:
                    try:
                        row.markdown_url = s3_presign_get(asset.s3_bucket, asset.s3_key)
                    except Exception:
                        row.markdown_url = None
            out.append(row)
        return out


@router.get("/briefs/{brief_id}", response_model=DailyBriefOut)
def get_brief(brief_id: uuid.UUID) -> DailyBriefOut:
    with session_scope() as session:
        brief = session.get(DailyBrief, brief_id)
        if not brief:
            raise HTTPException(status_code=404, detail="brief not found")
        out = DailyBriefOut.model_validate(brief)
        if out.markdown_asset_id:
            asset = session.get(Asset, out.markdown_asset_id)
            if asset:
                try:
                    out.markdown_url = s3_presign_get(asset.s3_bucket, asset.s3_key)
                except Exception:
                    out.markdown_url = None
        return out
