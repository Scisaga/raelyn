from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from raelyn.db import session_scope
from raelyn.models import AppConfig
from raelyn.services.inference import INFERENCE_MODE_CONFIG_KEY
from raelyn.services.inference import INFERENCE_MODES
from raelyn.services.inference import VOLCENGINE_INFERENCE_CONFIG_KEY
from raelyn.services.inference import build_effective_asr_config_from_payload
from raelyn.services.inference import build_effective_llm_config_from_payload
from raelyn.services.inference import build_inference_status
from raelyn.services.inference import check_asr_health
from raelyn.services.inference import check_llm_health
from raelyn.services.inference import mask_secret
from raelyn.services.inference import preserve_existing_volcengine_secrets
from raelyn.services.inference import sanitize_config_value
from raelyn.services.inference import test_asr_connection
from raelyn.services.inference import test_llm_connection
from raelyn.services.provider_cookies import cookie_config_key, looks_like_netscape_cookie_file
from raelyn.services.brief_schedule import (
    brief_generation_policy_defaults,
    normalize_brief_generation_policy,
)
from raelyn.services.provider_pause import clear_provider_pauses
from raelyn.services.transcript_polish_prompt import (
    TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY,
    transcript_polish_prompt_defaults,
)
from raelyn.services.system_pause import clear_pause, get_pause
from raelyn.timeutil import utcnow


router = APIRouter(tags=["config"])


class ConfigUpsert(BaseModel):
    value: dict


class InferenceSettingsPayload(BaseModel):
    mode: str
    volcengine: dict | None = None


def _validate_llm_transcript_polish_prompt_value(value: dict) -> None:
    text = value.get("text") if isinstance(value, dict) else None
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="llm_transcript_polish_prompt.text must be a string.")
    if len(text.encode("utf-8")) > 64 * 1024:
        raise HTTPException(status_code=400, detail="llm_transcript_polish_prompt.text is too large (max 64KB).")


def _validate_brief_generation_policy_value(value: dict) -> None:
    try:
        normalize_brief_generation_policy(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_inference_mode_value(value: dict) -> None:
    raw = value.get("value") if isinstance(value, dict) else None
    mode = str(raw or "").strip().lower()
    if mode not in INFERENCE_MODES:
        raise HTTPException(status_code=400, detail="inference_mode.value must be one of: local, volcengine.")


def _validate_volcengine_inference_config_value(value: dict) -> None:
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="volcengine_inference_config must be an object.")
    string_keys = ["api_key", "llm_model", "asr_model", "asr_app_key", "asr_access_key"]
    for key in string_keys:
        if key in value and not isinstance(value.get(key), str):
            raise HTTPException(status_code=400, detail=f"volcengine_inference_config.{key} must be a string.")
    timeout_keys = ["llm_timeout_seconds", "asr_timeout_seconds"]
    for key in timeout_keys:
        if key not in value:
            continue
        try:
            parsed = int(value.get(key))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"volcengine_inference_config.{key} must be an integer.") from exc
        if parsed <= 0:
            raise HTTPException(status_code=400, detail=f"volcengine_inference_config.{key} must be > 0.")


@router.get("/config")
def get_config() -> dict:
    with session_scope() as session:
        items = session.execute(select(AppConfig)).scalars().all()
        return {"data": {i.key: sanitize_config_value(i.key, i.value) for i in items}}


@router.get("/config/defaults")
def get_config_defaults() -> dict:
    defaults = transcript_polish_prompt_defaults()
    defaults.update(brief_generation_policy_defaults())
    return defaults


@router.get("/config/inference")
def get_inference_config() -> dict:
    with session_scope() as session:
        return build_inference_status(session)


@router.put("/config/{key}")
def put_config(key: str, payload: ConfigUpsert) -> dict:
    with session_scope() as session:
        value = payload.value
        cookie_keys = {
            cookie_config_key("youtube"),
            cookie_config_key("bilibili"),
        }
        if key in cookie_keys:
            try:
                text = value.get("text") if isinstance(value, dict) else ""
            except Exception:
                text = ""
            if isinstance(text, str) and text.strip():
                if not looks_like_netscape_cookie_file(text):
                    raise HTTPException(
                        status_code=400,
                        detail=f"{key} must be Netscape cookies.txt format (tab-separated).",
                    )
        if key == TRANSCRIPT_POLISH_PROMPT_CONFIG_KEY:
            _validate_llm_transcript_polish_prompt_value(value)
        if key == "brief_generation_policy":
            _validate_brief_generation_policy_value(value)
            value = normalize_brief_generation_policy(value)
        if key == INFERENCE_MODE_CONFIG_KEY:
            _validate_inference_mode_value(value)
        if key == VOLCENGINE_INFERENCE_CONFIG_KEY:
            _validate_volcengine_inference_config_value(value)

        existing = session.get(AppConfig, key)
        if existing:
            existing.value = value
            existing.updated_at = utcnow()
        else:
            session.add(AppConfig(key=key, value=value))

        # If the system was paused due to invalid/expired cookies, resume automatically after cookies update.
        if key in cookie_keys:
            p = get_pause(session)
            reason = str(p.get("reason") or "")
            if p.get("paused") and reason.startswith("ytdlp_cookies_"):
                clear_pause(session)
            if key == cookie_config_key("youtube"):
                clear_provider_pauses(session, providers=["youtube"])
            elif key == cookie_config_key("bilibili"):
                clear_provider_pauses(session, providers=["bilibili"])
    return {"ok": True}


@router.put("/config/inference")
def put_inference_config(payload: InferenceSettingsPayload) -> dict:
    body = {"mode": payload.mode, "volcengine": payload.volcengine or {}}
    _validate_inference_mode_value({"value": body["mode"]})
    _validate_volcengine_inference_config_value(body["volcengine"])

    with session_scope() as session:
        mode_value = {"value": str(body["mode"]).strip().lower()}
        existing_mode = session.get(AppConfig, INFERENCE_MODE_CONFIG_KEY)
        if existing_mode:
            existing_mode.value = mode_value
            existing_mode.updated_at = utcnow()
        else:
            session.add(AppConfig(key=INFERENCE_MODE_CONFIG_KEY, value=mode_value))

        merged_volc = preserve_existing_volcengine_secrets(body["volcengine"], session=session)
        existing_volc = session.get(AppConfig, VOLCENGINE_INFERENCE_CONFIG_KEY)
        if existing_volc:
            existing_volc.value = merged_volc
            existing_volc.updated_at = utcnow()
        else:
            session.add(AppConfig(key=VOLCENGINE_INFERENCE_CONFIG_KEY, value=merged_volc))

        return {"ok": True, "data": build_inference_status(session)}


@router.post("/config/inference/test")
def test_inference_config(payload: InferenceSettingsPayload) -> dict:
    body = {"mode": payload.mode, "volcengine": payload.volcengine or {}}
    _validate_inference_mode_value({"value": body["mode"]})
    _validate_volcengine_inference_config_value(body["volcengine"])

    with session_scope() as session:
        merged_volc = preserve_existing_volcengine_secrets(body["volcengine"], session=session)
        test_payload = {"mode": body["mode"], "volcengine": merged_volc}
        llm_cfg = build_effective_llm_config_from_payload(test_payload)
        asr_cfg = build_effective_asr_config_from_payload(test_payload)
        llm_test = test_llm_connection(llm_cfg) if llm_cfg.configured else check_llm_health(llm_cfg)
        asr_test = test_asr_connection(asr_cfg) if asr_cfg.configured else check_asr_health(asr_cfg)
        return {
            "ok": bool(llm_test.get("ok") and asr_test.get("ok")),
            "mode": str(body["mode"]).strip().lower(),
            "volcengine": {
                "api_key_masked": mask_secret(merged_volc.get("api_key")),
                "api_key_present": bool(str(merged_volc.get("api_key") or "").strip()),
                "asr_app_key_masked": mask_secret(merged_volc.get("asr_app_key")),
                "asr_app_key_present": bool(str(merged_volc.get("asr_app_key") or "").strip()),
                "asr_access_key_masked": mask_secret(merged_volc.get("asr_access_key")),
                "asr_access_key_present": bool(str(merged_volc.get("asr_access_key") or "").strip()),
            },
            "llm": llm_test,
            "asr": asr_test,
        }
