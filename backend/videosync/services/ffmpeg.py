from __future__ import annotations

import subprocess
from pathlib import Path

from videosync.config import settings


def ffmpeg_bin() -> str:
    candidate = Path(settings.ffmpeg_bin)
    if candidate.exists():
        return str(candidate)
    return "ffmpeg"


def extract_audio_to_m4a(*, input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

