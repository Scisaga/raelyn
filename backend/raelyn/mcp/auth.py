from __future__ import annotations

import secrets
from collections.abc import Iterable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


def _safe_compare_token(provided: str, expected: str) -> bool:
    try:
        return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:
        return False


def normalize_mount_path(value: str | None, *, default: str = "/mcp") -> str:
    raw = str(value or default).strip()
    if not raw:
        return default
    path = raw if raw.startswith("/") else f"/{raw}"
    path = path.rstrip("/")
    return path or "/"


def require_mcp_token(token: str | None) -> str:
    value = str(token or "").strip()
    if not value:
        raise RuntimeError("MCP_BEARER_TOKEN is required for MCP HTTP server")
    return value


class BearerTokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        token: str,
        protected_prefix: str,
        public_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._token = require_mcp_token(token)
        self._protected_prefix = normalize_mount_path(protected_prefix)
        self._public_paths = {
            normalize_mount_path(path, default="/")
            for path in (public_paths or ())
        }

    async def dispatch(self, request, call_next):
        path = request.url.path
        protected = self._protected_prefix == "/" or path == self._protected_prefix or path.startswith(f"{self._protected_prefix}/")
        if path in self._public_paths:
            protected = False
        if not protected:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, provided = header.partition(" ")
        if scheme.lower() != "bearer" or not provided or not _safe_compare_token(provided.strip(), self._token):
            return JSONResponse(
                status_code=401,
                content={"ok": False, "detail": "unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
