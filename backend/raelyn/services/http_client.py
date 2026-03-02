from __future__ import annotations

from typing import Any

import httpx


def httpx_client(
    *,
    proxy: str | None = None,
    timeout: httpx.Timeout | float | None = None,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": follow_redirects}
    if headers:
        kwargs["headers"] = headers

    proxy_url = (proxy or "").strip()
    if proxy_url:
        # httpx version differences:
        # - some versions use `proxy=...`
        # - others use `proxies=...`
        try:
            return httpx.Client(proxy=proxy_url, **kwargs)
        except TypeError:
            return httpx.Client(proxies=proxy_url, **kwargs)

    return httpx.Client(**kwargs)

