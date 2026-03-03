from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from raelyn.models import AppConfig
from raelyn.timeutil import utcnow

PAUSE_CONFIG_KEY = "system_pause"


class SystemPausedError(RuntimeError):
    def __init__(self, message: str, *, pause: dict[str, Any] | None = None) -> None:
        self.pause = pause
        super().__init__(str(message or "").strip() or "system paused")


def get_pause(session: Session) -> dict[str, Any]:
    item = session.get(AppConfig, PAUSE_CONFIG_KEY)
    value = item.value if item else None
    if not isinstance(value, dict):
        return {"paused": False, "reason": None, "message": None, "set_at": None}
    paused = bool(value.get("paused")) if isinstance(value.get("paused"), bool) else False
    reason = value.get("reason")
    message = value.get("message")
    set_at = value.get("set_at")
    return {
        "paused": paused,
        "reason": str(reason) if isinstance(reason, str) and reason else None,
        "message": str(message) if isinstance(message, str) and message else None,
        "set_at": str(set_at) if isinstance(set_at, str) and set_at else None,
    }


def is_paused(session: Session) -> bool:
    return bool(get_pause(session).get("paused"))


def require_running(session: Session) -> None:
    p = get_pause(session)
    if p.get("paused"):
        raise SystemPausedError(p.get("message") or "system paused", pause=p)


def set_paused(session: Session, *, reason: str, message: str) -> dict[str, Any]:
    reason = str(reason or "").strip() or "unknown"
    message = str(message or "").strip() or "paused"
    value = {"paused": True, "reason": reason, "message": message, "set_at": utcnow().isoformat()}
    item = session.get(AppConfig, PAUSE_CONFIG_KEY)
    if item:
        # Avoid unnecessary writes if already paused with the same reason+message.
        if isinstance(item.value, dict) and item.value.get("paused") is True:
            if item.value.get("reason") == reason and item.value.get("message") == message:
                return get_pause(session)
        item.value = value
        item.updated_at = utcnow()
    else:
        session.add(AppConfig(key=PAUSE_CONFIG_KEY, value=value))
    session.flush()
    return get_pause(session)


def clear_pause(session: Session) -> dict[str, Any]:
    item = session.get(AppConfig, PAUSE_CONFIG_KEY)
    if item:
        item.value = {"paused": False, "reason": None, "message": None, "set_at": None}
        item.updated_at = utcnow()
        session.flush()
    return get_pause(session)
