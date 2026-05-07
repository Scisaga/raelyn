from __future__ import annotations

from typing import Any

import httpx


def httpx_client(
    *,
    timeout: httpx.Timeout | float | None = None,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": follow_redirects, "trust_env": False}
    if headers:
        kwargs["headers"] = headers

    return httpx.Client(**kwargs)
