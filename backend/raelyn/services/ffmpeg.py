from __future__ import annotations

import subprocess
from pathlib import Path

from raelyn.config import settings


def ffmpeg_bin() -> str:
    candidate = Path(settings.ffmpeg_bin)
    if candidate.exists():
        return str(candidate)
    return "ffmpeg"


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
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
