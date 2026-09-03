from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from raelyn.db import session_scope
from raelyn.services.usage import build_usage_payload


router = APIRouter(tags=["usage"])


@router.get("/usage")
def usage(days: int = 30) -> dict[str, Any]:
    if days not in {7, 30, 90}:
        raise HTTPException(status_code=422, detail="days must be one of: 7, 30, 90")
    with session_scope() as session:
        return build_usage_payload(session, days=days)
