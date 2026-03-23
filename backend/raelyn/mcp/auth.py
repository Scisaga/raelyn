from __future__ import annotations

from collections.abc import Iterable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from raelyn.api.auth import is_valid_bearer_token, normalize_bearer_token


def normalize_mount_path(value: str | None, *, default: str = "/mcp") -> str:
    raw = str(value or default).strip()
    if not raw:
        return default
    path = raw if raw.startswith("/") else f"/{raw}"
    path = path.rstrip("/")
    return path or "/"


def require_bearer_token(token: str | None, *, label: str = "API_BEARER_TOKEN") -> str:
    value = normalize_bearer_token(token)
    if not value:
        raise RuntimeError(f"{label} is required for MCP HTTP server")
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
        self._token = require_bearer_token(token)
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
        if scheme.lower() != "bearer" or not is_valid_bearer_token(provided.strip(), self._token):
            return JSONResponse(
                status_code=401,
                content={"ok": False, "detail": "unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
