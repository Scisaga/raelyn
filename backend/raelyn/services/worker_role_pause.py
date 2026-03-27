from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from raelyn.models import AppConfig
from raelyn.services.worker_roles import is_controllable_worker_role, normalize_worker_role
from raelyn.timeutil import utcnow


WORKER_ROLE_PAUSE_CONFIG_KEY = "worker_role_pause"


def _default_pause() -> dict[str, Any]:
    return {"paused": False, "reason": None, "message": None, "set_at": None}


def _sanitize_pause(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _default_pause()
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


def _require_controllable_worker_role(role: str) -> str:
    normalized = normalize_worker_role(role)
    if not is_controllable_worker_role(normalized):
        raise ValueError(f"invalid controllable worker role: {role}")
    return normalized


def _load_worker_role_pause_map(session: Session) -> tuple[AppConfig | None, dict[str, Any]]:
    item = session.get(AppConfig, WORKER_ROLE_PAUSE_CONFIG_KEY)
    value = item.value if item else None
    return item, value if isinstance(value, dict) else {}


def get_worker_role_pauses(session: Session) -> dict[str, dict[str, Any]]:
    _item, value = _load_worker_role_pause_map(session)
    out: dict[str, dict[str, Any]] = {}
    for raw_role, raw_pause in value.items():
        role = normalize_worker_role(raw_role)
        if not is_controllable_worker_role(role):
            continue
        pause = _sanitize_pause(raw_pause)
        if pause["paused"]:
            out[role] = pause
    return out


def get_worker_role_pause(session: Session, role: str) -> dict[str, Any]:
    normalized = normalize_worker_role(role)
    if not is_controllable_worker_role(normalized):
        return _default_pause()
    return get_worker_role_pauses(session).get(normalized, _default_pause())


def is_worker_role_paused(session: Session, role: str | None) -> bool:
    if not role:
        return False
    return bool(get_worker_role_pause(session, str(role)).get("paused"))


def set_worker_role_paused(session: Session, *, role: str, reason: str, message: str) -> dict[str, Any]:
    normalized = _require_controllable_worker_role(role)
    pause = {
        "paused": True,
        "reason": str(reason or "").strip() or "worker_role_pause_requested",
        "message": str(message or "").strip() or f"{normalized} worker paused",
        "set_at": utcnow().isoformat(),
    }
    item, value = _load_worker_role_pause_map(session)
    current = _sanitize_pause(value.get(normalized))
    if current["paused"] and current["reason"] == pause["reason"] and current["message"] == pause["message"]:
        return current

    next_value = dict(value)
    next_value[normalized] = pause
    if item:
        item.value = next_value
        item.updated_at = utcnow()
    else:
        session.add(AppConfig(key=WORKER_ROLE_PAUSE_CONFIG_KEY, value=next_value))
    session.flush()
    return get_worker_role_pause(session, normalized)


def clear_worker_role_pause(session: Session, *, role: str) -> dict[str, Any]:
    normalized = _require_controllable_worker_role(role)
    item, value = _load_worker_role_pause_map(session)
    if not item:
        return _default_pause()

    next_value = {k: v for k, v in value.items() if normalize_worker_role(k) != normalized}
    if next_value != value:
        item.value = next_value
        item.updated_at = utcnow()
        session.flush()
    return get_worker_role_pause(session, normalized)
