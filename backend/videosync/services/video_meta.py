from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_published_at(info: dict[str, Any] | None) -> datetime | None:
    if not info:
        return None

    for key in ("timestamp", "release_timestamp"):
        v = info.get(key)
        if v is None:
            continue
        try:
            return datetime.fromtimestamp(int(v), tz=timezone.utc)
        except Exception:
            continue

    for key in ("upload_date", "release_date"):
        v = info.get(key)
        if not isinstance(v, str):
            continue
        s = v.strip()
        if len(s) != 8 or not s.isdigit():
            continue
        try:
            return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None

