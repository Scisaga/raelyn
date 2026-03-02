from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.db import session_scope
from raelyn.models import AppConfig
from raelyn.timeutil import utcnow


router = APIRouter(tags=["config"])


class ConfigUpsert(BaseModel):
    value: dict


@router.get("/config")
def get_config() -> dict:
    with session_scope() as session:
        items = session.execute(select(AppConfig)).scalars().all()
        return {"data": {i.key: i.value for i in items}}


@router.put("/config/{key}")
def put_config(key: str, payload: ConfigUpsert) -> dict:
    with session_scope() as session:
        existing = session.get(AppConfig, key)
        if existing:
            existing.value = payload.value
            existing.updated_at = utcnow()
        else:
            session.add(AppConfig(key=key, value=payload.value))
    return {"ok": True}

