from __future__ import annotations

import subprocess
from pathlib import Path

from raelyn.config import settings

_FFMPEG_ERROR_MAX_CHARS = 4000


def ffmpeg_bin() -> str:
    candidate = Path(settings.ffmpeg_bin)
    if candidate.exists():
        return str(candidate)
    return "ffmpeg"


def _tail_text(value: str, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def extract_audio_to_m4a(*, input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    codec = str(settings.audio_codec or "").strip() or "aac"
    bitrate = str(settings.audio_bitrate or "").strip() or "64k"
    sample_rate_hz = settings.audio_sample_rate_hz
    channels = settings.audio_channels
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(input_path),
        "-vn",
    ]
    if isinstance(channels, int) and channels > 0:
        cmd.extend(["-ac", str(int(channels))])
    if isinstance(sample_rate_hz, int) and sample_rate_hz > 0:
        cmd.extend(["-ar", str(int(sample_rate_hz))])
    cmd.extend(
        [
            "-c:a",
            codec,
            "-b:a",
            bitrate,
            str(output_path),
        ]
    )
    completed = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    if completed.returncode == 0:
        return

    detail = _tail_text(completed.stderr or completed.stdout or "", max_chars=_FFMPEG_ERROR_MAX_CHARS)
    if not detail:
        detail = "ffmpeg 未输出错误详情"
    raise RuntimeError(f"ffmpeg 音频提取失败（exit={completed.returncode}）：{detail}")
