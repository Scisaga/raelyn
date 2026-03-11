from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import uuid
from typing import Any

from pydantic import BaseModel


def serialize_for_mcp(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return serialize_for_mcp(value.model_dump())
    if isinstance(value, dict):
        return {str(k): serialize_for_mcp(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_for_mcp(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return serialize_for_mcp(value.value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
