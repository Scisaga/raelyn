from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import srt as srtlib
import webvtt


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str


def _to_ms(hours: int, minutes: int, seconds: int, milliseconds: int) -> int:
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + milliseconds


def parse_srt(path: Path) -> list[Segment]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    subs = list(srtlib.parse(content))
    segments: list[Segment] = []
    for s in subs:
        start_ms = int(s.start.total_seconds() * 1000)
        end_ms = int(s.end.total_seconds() * 1000)
        text = " ".join(s.content.splitlines()).strip()
        if text:
            segments.append(Segment(start_ms=start_ms, end_ms=end_ms, text=text))
    return segments


def parse_vtt(path: Path) -> list[Segment]:
    v = webvtt.read(str(path))
    segments: list[Segment] = []
    for c in v:
        text = " ".join((c.text or "").splitlines()).strip()
        if not text:
            continue
        start = c.start_time
        end = c.end_time
        # 格式：HH:MM:SS.mmm 或 MM:SS.mmm
        def parse_ts(ts: object) -> int:
            # webvtt-py 可能返回 Timestamp 对象，而不是字符串
            if not isinstance(ts, str):
                if all(hasattr(ts, k) for k in ("hours", "minutes", "seconds", "milliseconds")):
                    return _to_ms(
                        int(getattr(ts, "hours")),
                        int(getattr(ts, "minutes")),
                        int(getattr(ts, "seconds")),
                        int(getattr(ts, "milliseconds")),
                    )
                ts = str(ts)

            parts = ts.replace(",", ".").split(":")
            if len(parts) == 3:
                hh, mm, rest = parts
            else:
                hh = "0"
                mm, rest = parts
            ss, ms = rest.split(".")
            return _to_ms(int(hh), int(mm), int(ss), int(ms))

        segments.append(Segment(start_ms=parse_ts(start), end_ms=parse_ts(end), text=text))
    return segments


def normalize_subtitle(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower().lstrip(".")
    if ext == "srt":
        segments = parse_srt(path)
    elif ext == "vtt":
        segments = parse_vtt(path)
    else:
        raise ValueError(f"unsupported subtitle format: {ext}")

    segments_json = json.dumps([s.__dict__ for s in segments], ensure_ascii=False, indent=2)
    plain = "\n".join(s.text for s in segments)
    return segments_json, plain
