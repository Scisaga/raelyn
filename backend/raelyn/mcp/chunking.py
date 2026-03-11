from __future__ import annotations

from typing import Any


DEFAULT_TRANSCRIPT_CHUNK_SIZE = 12_000
MAX_TRANSCRIPT_CHUNK_SIZE = 50_000


def normalize_chunk_size(chunk_size: int | None) -> int:
    if chunk_size is None:
        return DEFAULT_TRANSCRIPT_CHUNK_SIZE
    try:
        value = int(chunk_size)
    except Exception as exc:
        raise ValueError("chunk_size must be an integer between 1 and 50000") from exc
    if value <= 0 or value > MAX_TRANSCRIPT_CHUNK_SIZE:
        raise ValueError("chunk_size must be an integer between 1 and 50000")
    return value


def build_chunk_bounds(text: str, chunk_size: int) -> list[tuple[int, int]]:
    if text == "":
        return [(0, 0)]

    bounds: list[tuple[int, int]] = []
    start = 0
    total = len(text)
    while start < total:
        end = min(start + chunk_size, total)
        if end < total:
            near_end = max(start, start + int(chunk_size * 0.7))
            split_at = text.rfind("\n", near_end, end)
            if split_at <= start:
                split_at = text.rfind("\n", start, end)
            if split_at > start:
                end = split_at + 1
        if end <= start:
            end = min(start + chunk_size, total)
        bounds.append((start, end))
        start = end
    return bounds


def get_text_chunk(text: str, *, chunk_index: int, chunk_size: int) -> dict[str, Any]:
    size = normalize_chunk_size(chunk_size)
    bounds = build_chunk_bounds(text, size)
    if chunk_index < 0 or chunk_index >= len(bounds):
        raise IndexError("chunk_out_of_range")

    start, end = bounds[chunk_index]
    next_chunk_index = chunk_index + 1 if chunk_index + 1 < len(bounds) else None
    return {
        "text": text[start:end],
        "chunk_index": chunk_index,
        "chunk_count": len(bounds),
        "has_more": next_chunk_index is not None,
        "next_chunk_index": next_chunk_index,
        "chunk_size": size,
        "total_chars": len(text),
    }
