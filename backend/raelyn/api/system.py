from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.services.inference import build_inference_status
from raelyn.services.provider_pause import get_provider_pauses
from raelyn.services.system_pause import clear_pause, get_pause, set_paused


router = APIRouter(tags=["system"])


@router.get("/system")
def system_status() -> dict:
    with session_scope() as session:
        presign_enabled = bool(settings.asset_presign_enabled)
        probe_url = ""
        if presign_enabled:
            probe_url = (settings.asset_direct_probe_url or "").strip() or (settings.s3_endpoint.rstrip("/") + "/")
        return {
            "pause": get_pause(session),
            "provider_pauses": get_provider_pauses(session),
            "inference": build_inference_status(session),
            "asset_delivery": {
                "strategy": "startup_probe" if presign_enabled else "proxy",
                "direct_probe_url": probe_url,
                "direct_probe_timeout_ms": max(0, int(settings.asset_direct_probe_timeout_ms or 0)),
                "proxy_base_path": (settings.asset_proxy_base_path or "/api/assets").rstrip("/"),
                "presign_enabled": presign_enabled,
            },
        }


class PauseRequest(BaseModel):
    reason: str
    message: str


@router.post("/system/pause")
def pause_system(payload: PauseRequest) -> dict:
    with session_scope() as session:
        p = set_paused(session, reason=payload.reason, message=payload.message)
        return {"ok": True, "pause": p}


@router.post("/system/resume")
def resume_system() -> dict:
    with session_scope() as session:
        p = clear_pause(session)
        return {"ok": True, "pause": p}


@router.post("/system/require_running")
def require_running() -> dict:
    # Convenience endpoint for UIs/clients that want a clear error code.
    with session_scope() as session:
        p = get_pause(session)
        if p.get("paused"):
            raise HTTPException(status_code=409, detail=p.get("message") or "system paused")
        return {"ok": True}
