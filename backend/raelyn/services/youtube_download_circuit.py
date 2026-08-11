from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from raelyn.models import AppConfig
from raelyn.services.pg_lock import lock_key
from raelyn.timeutil import utcnow


YOUTUBE_DOWNLOAD_CIRCUIT_CONFIG_KEY = "youtube_download_circuit"
_CIRCUIT_LOCK_NAME = "youtube-download-circuit"
_FAILURE_WINDOW_SECONDS = 600
_FAILURE_THRESHOLD = 4
_DISTINCT_JOB_THRESHOLD = 2
_COOLDOWN_SECONDS = (300, 900, 1800)
_HALF_OPEN_PROBE_TIMEOUT_SECONDS = 900


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _closed_state() -> dict[str, Any]:
    return {
        "state": "closed",
        "observations": [],
        "cooldown_index": 0,
        "opened_at": None,
        "retry_at": None,
        "probe_job_id": None,
        "probe_started_at": None,
        "reason": None,
    }


def _sanitize_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _closed_state()
    state = str(value.get("state") or "closed").strip().lower()
    if state not in {"closed", "open", "half_open"}:
        state = "closed"
    observations: list[dict[str, str]] = []
    for item in value.get("observations") or []:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("job_id") or "").strip()
        observed_at = _parse_datetime(item.get("at"))
        if job_id and observed_at:
            observations.append(
                {
                    "job_id": job_id,
                    "at": observed_at.isoformat(),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
    try:
        cooldown_index = max(0, min(int(value.get("cooldown_index") or 0), len(_COOLDOWN_SECONDS) - 1))
    except (TypeError, ValueError):
        cooldown_index = 0
    return {
        "state": state,
        "observations": observations,
        "cooldown_index": cooldown_index,
        "opened_at": value.get("opened_at") if _parse_datetime(value.get("opened_at")) else None,
        "retry_at": value.get("retry_at") if _parse_datetime(value.get("retry_at")) else None,
        "probe_job_id": str(value.get("probe_job_id") or "").strip() or None,
        "probe_started_at": value.get("probe_started_at") if _parse_datetime(value.get("probe_started_at")) else None,
        "reason": str(value.get("reason") or "").strip() or None,
    }


def _open_state(state: dict[str, Any], *, now: datetime, cooldown_index: int, reason: str) -> dict[str, Any]:
    index = max(0, min(cooldown_index, len(_COOLDOWN_SECONDS) - 1))
    return {
        **state,
        "state": "open",
        "cooldown_index": index,
        "opened_at": now.isoformat(),
        "retry_at": (now + timedelta(seconds=_COOLDOWN_SECONDS[index])).isoformat(),
        "probe_job_id": None,
        "probe_started_at": None,
        "reason": str(reason or "").strip() or "youtube_transient_download",
    }


def _state_after_failure(
    value: Any,
    *,
    job_id: uuid.UUID,
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    state = _sanitize_state(value)
    if state["state"] == "open":
        return state
    if state["state"] == "half_open":
        if state["probe_job_id"] == str(job_id):
            return _open_state(
                state,
                now=now,
                cooldown_index=int(state["cooldown_index"]) + 1,
                reason=reason,
            )
        return state

    window_start = now - timedelta(seconds=_FAILURE_WINDOW_SECONDS)
    observations = [
        item
        for item in state["observations"]
        if (_parse_datetime(item.get("at")) or now) >= window_start
    ]
    observations.append({"job_id": str(job_id), "at": now.isoformat(), "reason": reason})
    state["observations"] = observations
    distinct_jobs = {item["job_id"] for item in observations}
    if len(observations) >= _FAILURE_THRESHOLD and len(distinct_jobs) >= _DISTINCT_JOB_THRESHOLD:
        return _open_state(state, now=now, cooldown_index=0, reason=reason)
    return state


def _claim_decision(
    value: Any,
    *,
    job_id: uuid.UUID,
    now: datetime,
) -> tuple[bool, dict[str, Any]]:
    state = _sanitize_state(value)
    if state["state"] == "closed":
        return True, state
    if state["state"] == "open":
        retry_at = _parse_datetime(state["retry_at"])
        if retry_at and retry_at > now:
            return False, state
        state["state"] = "half_open"
        state["probe_job_id"] = str(job_id)
        state["probe_started_at"] = now.isoformat()
        return True, state

    probe_started_at = _parse_datetime(state["probe_started_at"])
    if probe_started_at and probe_started_at + timedelta(seconds=_HALF_OPEN_PROBE_TIMEOUT_SECONDS) > now:
        return False, state
    state["probe_job_id"] = str(job_id)
    state["probe_started_at"] = now.isoformat()
    return True, state


def _state_blocks_claims(value: Any, *, now: datetime) -> bool:
    state = _sanitize_state(value)
    if state["state"] == "open":
        retry_at = _parse_datetime(state["retry_at"])
        return bool(retry_at and retry_at > now)
    if state["state"] != "half_open":
        return False
    probe_started_at = _parse_datetime(state["probe_started_at"])
    return bool(
        probe_started_at
        and probe_started_at + timedelta(seconds=_HALF_OPEN_PROBE_TIMEOUT_SECONDS) > now
    )


def _state_after_success(_value: Any) -> dict[str, Any]:
    return _closed_state()


def _lock_circuit(session: Session) -> AppConfig | None:
    session.execute(
        text("select pg_advisory_xact_lock(:key)").bindparams(key=lock_key(_CIRCUIT_LOCK_NAME))
    )
    return session.execute(
        select(AppConfig)
        .where(AppConfig.key == YOUTUBE_DOWNLOAD_CIRCUIT_CONFIG_KEY)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def youtube_download_circuit_blocks_claims(session: Session, *, now: datetime | None = None) -> bool:
    item = session.get(AppConfig, YOUTUBE_DOWNLOAD_CIRCUIT_CONFIG_KEY)
    return _state_blocks_claims(item.value if item else None, now=now or utcnow())


def allow_youtube_download_circuit_claim(
    session: Session,
    *,
    job_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    current_time = now or utcnow()
    item = _lock_circuit(session)
    allowed, next_state = _claim_decision(item.value if item else None, job_id=job_id, now=current_time)
    if item and next_state != item.value:
        item.value = next_state
        item.updated_at = current_time
    return allowed


def record_youtube_download_transient_failure(
    session: Session,
    *,
    job_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or utcnow()
    item = _lock_circuit(session)
    next_state = _state_after_failure(
        item.value if item else None,
        job_id=job_id,
        reason=reason,
        now=current_time,
    )
    if item:
        item.value = next_state
        item.updated_at = current_time
    else:
        session.add(AppConfig(key=YOUTUBE_DOWNLOAD_CIRCUIT_CONFIG_KEY, value=next_state))
    return next_state


def record_youtube_download_success(
    session: Session,
    *,
    now: datetime | None = None,
) -> bool:
    current_time = now or utcnow()
    item = session.get(AppConfig, YOUTUBE_DOWNLOAD_CIRCUIT_CONFIG_KEY)
    if not item:
        return False
    state = _sanitize_state(item.value)
    if state == _closed_state():
        return False
    item = _lock_circuit(session)
    if not item:
        return False
    item.value = _state_after_success(item.value)
    item.updated_at = current_time
    return True
