from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
import math
from pathlib import Path
import random
import struct
import sys

import httpx


ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from raelyn.config import settings


SCENE_RECORD = struct.Struct("<I16sfffiiBBBBIII")
PREVIEW_HEADER = struct.Struct("<8sHHIii")
PREVIEW_RECORD = struct.Struct("<hhh i BBB B H")
PREVIEW_MAGIC = b"RLYNSTR1"
PREVIEW_VERSION = 1
DEFAULT_COLOR = "#94a3b8"


def _hex_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) != 6:
        raw = DEFAULT_COLOR.lstrip("#")
    return tuple(int(raw[offset : offset + 2], 16) for offset in (0, 2, 4))


def _read_scene(payload: bytes) -> list[dict[str, int | float]]:
    if len(payload) % SCENE_RECORD.size != 0:
        raise RuntimeError(f"scene 长度不是 {SCENE_RECORD.size} 字节记录的整数倍")
    records: list[dict[str, int | float]] = []
    for offset in range(0, len(payload), SCENE_RECORD.size):
        (
            point_index,
            _canonical_id,
            x,
            y,
            z,
            start_day,
            _end_day,
            event_type,
            _time_precision,
            _flags,
            _reserved,
            member_count,
            _macro_topic_index,
            _local_topic_index,
        ) = SCENE_RECORD.unpack_from(payload, offset)
        records.append(
            {
                "point_index": int(point_index),
                "x": float(x),
                "y": float(y),
                "z": float(z),
                "day": int(start_day),
                "event_type": int(event_type),
                "member_count": int(member_count),
            }
        )
    return records


def _stratified_sample(
    records: list[dict[str, int | float]],
    *,
    limit: int,
    colors: dict[int, tuple[int, int, int]],
    seed: int,
) -> list[dict[str, int | float]]:
    if len(records) <= limit:
        return list(records)

    min_day = min(int(record["day"]) for record in records)
    max_day = max(int(record["day"]) for record in records)
    span = max(1, max_day - min_day + 1)
    groups: dict[tuple[int, tuple[int, int, int]], list[dict[str, int | float]]] = defaultdict(list)
    for record in records:
        time_bucket = min(23, ((int(record["day"]) - min_day) * 24) // span)
        color = colors.get(int(record["event_type"]), _hex_rgb(DEFAULT_COLOR))
        groups[(time_bucket, color)].append(record)

    quotas = {key: 1 for key in groups}
    remaining = limit - len(quotas)
    capacities = {key: max(0, len(items) - 1) for key, items in groups.items()}
    capacity_total = sum(capacities.values())
    residuals: list[tuple[float, tuple[int, tuple[int, int, int]]]] = []
    if remaining > 0 and capacity_total > 0:
        for key, capacity in capacities.items():
            raw = remaining * capacity / capacity_total
            extra = min(capacity, math.floor(raw))
            quotas[key] += extra
            residuals.append((raw - extra, key))
        left = limit - sum(quotas.values())
        for _residual, key in sorted(residuals, reverse=True):
            if left <= 0:
                break
            if quotas[key] >= len(groups[key]):
                continue
            quotas[key] += 1
            left -= 1

    sampled: list[dict[str, int | float]] = []
    for (time_bucket, color), items in sorted(groups.items()):
        count = min(quotas[(time_bucket, color)], len(items))
        group_seed = seed ^ (time_bucket * 0x9E37) ^ (color[0] << 16) ^ (color[1] << 8) ^ color[2]
        sampled.extend(random.Random(group_seed).sample(items, count))
    sampled.sort(key=lambda record: int(record["point_index"]))
    return sampled


def _build_preview(
    records: list[dict[str, int | float]],
    *,
    colors: dict[int, tuple[int, int, int]],
) -> bytes:
    min_day = min(int(record["day"]) for record in records)
    max_day = max(int(record["day"]) for record in records)
    bounds = [
        (min(float(record[axis]) for record in records), max(float(record[axis]) for record in records))
        for axis in ("x", "y", "z")
    ]
    centers = [(minimum + maximum) * 0.5 for minimum, maximum in bounds]
    scale = max(maximum - minimum for minimum, maximum in bounds) * 0.5
    if scale <= 0:
        raise RuntimeError("scene 坐标范围为空")

    log_members = sorted(math.log1p(max(1, int(record["member_count"]))) for record in records)
    weight_scale = log_members[min(len(log_members) - 1, math.floor(len(log_members) * 0.99))]
    payload = bytearray(PREVIEW_HEADER.pack(PREVIEW_MAGIC, PREVIEW_VERSION, PREVIEW_RECORD.size, len(records), min_day, max_day))
    for record in records:
        quantized = [
            max(-32767, min(32767, round((float(record[axis]) - center) / scale * 32767)))
            for axis, center in zip(("x", "y", "z"), centers, strict=True)
        ]
        color = colors.get(int(record["event_type"]), _hex_rgb(DEFAULT_COLOR))
        weight = round(
            24
            + 231
            * min(1.0, math.log1p(max(1, int(record["member_count"]))) / max(weight_scale, 1e-6))
        )
        phase = ((int(record["point_index"]) * 2654435761) ^ 0xA53C) & 0xFFFF
        payload.extend(
            PREVIEW_RECORD.pack(
                quantized[0],
                quantized[1],
                quantized[2],
                int(record["day"]),
                color[0],
                color[1],
                color[2],
                weight,
                phase,
            )
        )
    return bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="从 ready 事件星图生成登录门面的匿名星域样本")
    parser.add_argument("--playlist-id", required=True)
    parser.add_argument("--limit", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--base-url", default=str(settings.base_url or "http://127.0.0.1:8000"))
    parser.add_argument("--output", type=Path, default=ROOT / "ui/assets/startup-event-field.bin")
    args = parser.parse_args()

    headers = {}
    token = str(settings.api_bearer_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base_url = str(args.base_url).rstrip("/")
    playlist_id = str(args.playlist_id).strip()
    with httpx.Client(headers=headers, timeout=60, trust_env=False) as client:
        manifest_response = client.get(f"{base_url}/api/playlists/{playlist_id}/events/map/manifest")
        manifest_response.raise_for_status()
        manifest = manifest_response.json()
        snapshot_id = str(manifest.get("snapshot_id") or "").strip()
        if manifest.get("status") != "ready" or not snapshot_id:
            raise RuntimeError("播放列表没有 ready 事件星图快照")
        scene_response = client.get(
            f"{base_url}/api/playlists/{playlist_id}/events/map/scene",
            params={"snapshot_id": snapshot_id},
        )
        scene_response.raise_for_status()

    records = _read_scene(scene_response.content)
    epoch = date(1970, 1, 1)
    if args.start_date:
        start_day = (args.start_date - epoch).days
        records = [record for record in records if int(record["day"]) >= start_day]
    if args.end_date:
        end_day = (args.end_date - epoch).days
        records = [record for record in records if int(record["day"]) <= end_day]
    if not records:
        raise RuntimeError("指定时间范围内没有事件")
    colors = {
        int(item["code"]): _hex_rgb(str(item.get("semantic_color") or DEFAULT_COLOR))
        for item in manifest.get("type_categories") or []
        if isinstance(item, dict) and item.get("code") is not None
    }
    sampled = _stratified_sample(
        records,
        limit=max(1, min(int(args.limit), len(records))),
        colors=colors,
        seed=int(args.seed),
    )
    preview = _build_preview(sampled, colors=colors)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(preview)

    min_day = min(int(record["day"]) for record in sampled)
    max_day = max(int(record["day"]) for record in sampled)
    print(
        f"[startup-starfield] source={len(records)} sample={len(sampled)} "
        f"range={epoch + timedelta(days=min_day)}..{epoch + timedelta(days=max_day)} "
        f"bytes={len(preview)} output={args.output}"
    )


if __name__ == "__main__":
    main()
