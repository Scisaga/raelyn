from __future__ import annotations

from typing import Any

from curl_cffi.requests import Session as CurlSession
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


def browser_http_client(
    *,
    timeout: float = 10.0,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> CurlSession:
    return CurlSession(
        timeout=timeout,
        allow_redirects=follow_redirects,
        headers=headers,
        impersonate="chrome",
        trust_env=False,
    )
