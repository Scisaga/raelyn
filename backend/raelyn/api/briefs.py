from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.api.orm import OrmModel
from raelyn.db import session_scope
from raelyn.models import Asset, Brief, DailyBrief
from raelyn.services.brief_actions import schedule_brief_generation, schedule_brief_generation_range
from raelyn.services.brief_schedule import schedule_brief_refresh
from raelyn.services.brief_prompt import build_brief_prompt_for_period
from raelyn.services.periods import normalize_granularity, period_end_inclusive, period_start
from raelyn.services.s3 import s3_presign_get


router = APIRouter(tags=["briefs"])


class BriefGenerate(BaseModel):
    playlist_id: uuid.UUID
    date: date
    granularity: str = "day"  # day | week | month

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
        try:
            return schedule_brief_generation(
                session,
                playlist_id=payload.playlist_id,
                granularity=payload.granularity,
                date_in_period=payload.date,
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


class BriefGenerateRange(BaseModel):
    playlist_id: uuid.UUID
    granularity: str = "day"  # day | week | month
    from_date: date
    to_date: date


@router.post("/briefs/generate_range")
def generate_brief_range(payload: BriefGenerateRange) -> dict:
    with session_scope() as session:
        try:
            return schedule_brief_generation_range(
                session,
                playlist_id=payload.playlist_id,
                granularity=payload.granularity,
                from_date=payload.from_date,
                to_date=payload.to_date,
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/briefs/by_date", response_model=DailyBriefOut)
def get_brief_by_date(playlist_id: uuid.UUID, date: date) -> DailyBriefOut:
    with session_scope() as session:
        row = session.execute(
            select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == "day", Brief.period_start == date)
        ).scalar_one_or_none()
        if row:
            out = DailyBriefOut(
                id=row.id,
                playlist_id=row.playlist_id,
                brief_date=row.period_start,
                status=row.status,
                markdown_asset_id=row.markdown_asset_id,
                error_message=row.error_message,
                created_at=row.created_at,
                updated_at=row.updated_at,
                markdown_url=None,
            )
            if out.markdown_asset_id:
                asset = session.get(Asset, out.markdown_asset_id)
                if asset:
                    try:
                        out.markdown_url = s3_presign_get(asset.s3_bucket, asset.s3_key)
                    except Exception:
                        out.markdown_url = None
            return out

        brief = session.execute(
            select(DailyBrief).where(DailyBrief.playlist_id == playlist_id, DailyBrief.brief_date == date)
        ).scalar_one_or_none()
        if not brief:
            raise HTTPException(status_code=404, detail="brief not found")
        out2 = DailyBriefOut.model_validate(brief)
        if out2.markdown_asset_id:
            asset2 = session.get(Asset, out2.markdown_asset_id)
            if asset2:
                try:
                    out2.markdown_url = s3_presign_get(asset2.s3_bucket, asset2.s3_key)
                except Exception:
                    out2.markdown_url = None
        return out2


class BriefOut(OrmModel):
    id: uuid.UUID
    playlist_id: uuid.UUID
    granularity: str
    period_start: date
    period_end: date | None = None
    status: str
    markdown_asset_id: uuid.UUID | None = None
    markdown_url: str | None = None
    error_message: str | None = None
    created_at: Any
    updated_at: Any


@router.get("/briefs/by_period", response_model=BriefOut)
def get_brief_by_period(playlist_id: uuid.UUID, granularity: str, date: date) -> BriefOut:
    try:
        g = normalize_granularity(granularity)
        pstart = period_start(date, g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    with session_scope() as session:
        brief = session.execute(
            select(Brief).where(Brief.playlist_id == playlist_id, Brief.granularity == g, Brief.period_start == pstart)
        ).scalar_one_or_none()
        if not brief:
            raise HTTPException(status_code=404, detail="brief not found")
        out = BriefOut.model_validate(brief)
        out.period_end = period_end_inclusive(pstart, g)
        if out.markdown_asset_id:
            asset = session.get(Asset, out.markdown_asset_id)
            if asset:
                try:
                    out.markdown_url = s3_presign_get(asset.s3_bucket, asset.s3_key)
                except Exception:
                    out.markdown_url = None
        return out


@router.get("/briefs", response_model=list[BriefOut])
def list_briefs(
    playlist_id: uuid.UUID | None = None,
    granularity: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[BriefOut]:
    with session_scope() as session:
        stmt = select(Brief)
        if playlist_id:
            stmt = stmt.where(Brief.playlist_id == playlist_id)
        if granularity:
            try:
                g = normalize_granularity(granularity)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            stmt = stmt.where(Brief.granularity == g)
        stmt = stmt.order_by(Brief.period_start.desc()).limit(limit).offset(offset)
        items = session.execute(stmt).scalars().all()
        out: list[BriefOut] = []
        for b in items:
            row = BriefOut.model_validate(b)
            row.period_end = period_end_inclusive(row.period_start, row.granularity)
            if row.markdown_asset_id:
                asset = session.get(Asset, row.markdown_asset_id)
                if asset:
                    try:
                        row.markdown_url = s3_presign_get(asset.s3_bucket, asset.s3_key)
                    except Exception:
                        row.markdown_url = None
            out.append(row)
        return out


class BriefPromptByPeriodOut(BaseModel):
    playlist_id: uuid.UUID
    granularity: str
    period_start: date
    period_end: date
    prompt: str
    video_urls: list[str] = []


@router.get("/briefs/prompt_by_period", response_model=BriefPromptByPeriodOut)
def get_brief_prompt_by_period(playlist_id: uuid.UUID, granularity: str, date: date) -> BriefPromptByPeriodOut:
    try:
        g = normalize_granularity(granularity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    with session_scope() as session:
        try:
            out = build_brief_prompt_for_period(session, playlist_id=playlist_id, granularity=g, date_in_period=date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except LookupError as e:
            msg = str(e) or "not found"
            code = 404
            detail = msg
            if msg == "playlist not found":
                detail = "播放列表不存在"
            elif msg == "empty playlist":
                code = 400
                detail = "播放列表为空"
            elif msg == "no videos":
                detail = "本周期暂无视频"
            elif msg == "no transcript":
                detail = "本周期无可用文本（字幕/文字稿缺失）"
            raise HTTPException(status_code=code, detail=detail) from e
        return BriefPromptByPeriodOut(
            playlist_id=out.playlist_id,
            granularity=out.granularity,
            period_start=out.period_start,
            period_end=out.period_end,
            prompt=out.prompt,
            video_urls=out.video_urls,
        )


@router.get("/briefs/{brief_id}", response_model=BriefOut)
def get_brief(brief_id: uuid.UUID) -> BriefOut:
    with session_scope() as session:
        brief = session.get(Brief, brief_id)
        if not brief:
            raise HTTPException(status_code=404, detail="brief not found")
        out = BriefOut.model_validate(brief)
        out.period_end = period_end_inclusive(out.period_start, out.granularity)
        if out.markdown_asset_id:
            asset = session.get(Asset, out.markdown_asset_id)
            if asset:
                try:
                    out.markdown_url = s3_presign_get(asset.s3_bucket, asset.s3_key)
                except Exception:
                    out.markdown_url = None
        return out
