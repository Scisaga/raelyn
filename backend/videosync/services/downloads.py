from __future__ import annotations

import re
from urllib.parse import quote


def sanitize_filename_component(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    # Remove characters that commonly break filenames across OSes.
    s = re.sub(r"""[\\/:*?"<>|]""", " ", s)
    s = s.strip(" ._")
    return s


def build_download_filename(*, media_name: str | None, title: str | None, fallback_id: str, ext: str) -> str:
    m = sanitize_filename_component(media_name or "")
    t = sanitize_filename_component(title or "")
    base = "_".join([p for p in [m, t] if p])
    if not base:
        base = sanitize_filename_component(fallback_id) or "video"
    base = base.replace(" ", "_")
    base = re.sub(r"_+", "_", base).strip("._")
    # Clamp to keep URLs short and avoid OS limits; keep extension.
    max_base = 140
    if len(base) > max_base:
        base = base[:max_base].rstrip(" ._")
    ext = (ext or "bin").lstrip(".").lower()
    return f"{base}.{ext}"


def content_disposition_attachment(filename: str) -> str:
    # Use both `filename` (ASCII-ish) and RFC 5987 `filename*` for UTF-8.
    safe = re.sub(r'["\\\\]', "_", filename)
    safe = re.sub(r"[\r\n]+", " ", safe).strip() or "download.bin"
    return f'attachment; filename="{safe}"; filename*=UTF-8\'\'{quote(filename)}'

