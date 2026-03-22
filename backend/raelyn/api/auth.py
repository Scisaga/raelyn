from __future__ import annotations

import secrets
from urllib.parse import unquote

from fastapi import WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

API_AUTH_COOKIE_NAME = "raelyn_api_token"


def _safe_compare_token(provided: str, expected: str) -> bool:
    try:
        return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:
        return False


def normalize_bearer_token(value: str | None) -> str:
    return str(value or "").strip()


def _normalize_prefix(value: str | None, *, default: str = "/api") -> str:
    raw = str(value or default).strip()
    if not raw:
        return default
    path = raw if raw.startswith("/") else f"/{raw}"
    path = path.rstrip("/")
    return path or "/"


def is_api_auth_enabled(token: str | None) -> bool:
    return bool(normalize_bearer_token(token))


def _provided_bearer_token(authorization_header: str | None) -> str:
    header = str(authorization_header or "").strip()
    scheme, _, provided = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return provided.strip()


def _provided_cookie_token(cookie_value: str | None) -> str:
    raw = str(cookie_value or "").strip()
    if not raw:
        return ""
    try:
        return unquote(raw).strip()
    except Exception:
        return raw


def is_valid_bearer_token(provided: str | None, expected: str | None) -> bool:
    required = normalize_bearer_token(expected)
    if not required:
        return True
    candidate = str(provided or "").strip()
    if not candidate:
        return False
    return _safe_compare_token(candidate, required)


class OptionalBearerTokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, token: str | None, protected_prefix: str = "/api") -> None:
        super().__init__(app)
        self._token = normalize_bearer_token(token)
        self._protected_prefix = _normalize_prefix(protected_prefix, default="/api")

    async def dispatch(self, request, call_next):
        if not self._token:
            return await call_next(request)

        path = request.url.path
        protected = path == self._protected_prefix or path.startswith(f"{self._protected_prefix}/")
        if not protected:
            return await call_next(request)

        provided = _provided_bearer_token(request.headers.get("authorization"))
        cookie_token = _provided_cookie_token(request.cookies.get(API_AUTH_COOKIE_NAME, ""))
        if not is_valid_bearer_token(provided, self._token) and not is_valid_bearer_token(cookie_token, self._token):
            return JSONResponse(
                status_code=401,
                content={"ok": False, "detail": "unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


def api_ws_authorized(ws: WebSocket, *, token: str | None) -> bool:
    required = normalize_bearer_token(token)
    if not required:
        return True
    provided = str(ws.query_params.get("token", "") or "").strip()
    return is_valid_bearer_token(provided, required)


async def close_api_ws_unauthorized(ws: WebSocket) -> None:
    await ws.accept()
    await ws.close(code=4401, reason="unauthorized")
