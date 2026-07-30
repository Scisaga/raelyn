from __future__ import annotations

import json
import subprocess
from pathlib import Path

from raelyn.config import settings

_FFMPEG_ERROR_MAX_CHARS = 4000
_MEDIA_STREAM_SPECIFIERS = {
    "audio": ("a:0", "音轨"),
    "video": ("v:0", "视频轨"),
}


class MediaPacketValidationError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    candidate = Path(settings.ffmpeg_bin)
    if candidate.exists():
        return str(candidate)
    return "ffmpeg"


def ffprobe_bin() -> str:
    configured_ffmpeg = Path(settings.ffmpeg_bin)
    candidate = configured_ffmpeg.with_name("ffprobe")
    if candidate.exists():
        return str(candidate)
    return "ffprobe"


def _tail_text(value: str, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def require_media_packets(*, input_path: Path, stream_types: tuple[str, ...]) -> None:
    for stream_type in stream_types:
        specifier, label = _MEDIA_STREAM_SPECIFIERS[stream_type]
        completed = subprocess.run(
            [
                ffprobe_bin(),
                "-v",
                "error",
                "-select_streams",
                specifier,
                "-read_intervals",
                "%+#1",
                "-show_entries",
                "stream=codec_type:packet=stream_index",
                "-of",
                "json",
                str(input_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        if completed.returncode != 0:
            detail = _tail_text(completed.stderr or completed.stdout or "", max_chars=_FFMPEG_ERROR_MAX_CHARS)
            raise MediaPacketValidationError(
                f"ffprobe 无法读取媒体文件中的{label}（exit={completed.returncode}）："
                f"{detail or 'ffprobe 未输出错误详情'}"
            )

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("ffprobe 未返回合法 JSON") from exc

        if not payload.get("streams"):
            raise MediaPacketValidationError(f"媒体文件未发现{label}")
        if not payload.get("packets"):
            raise MediaPacketValidationError(f"媒体文件的{label}不包含可读取数据包")


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
    detail = _tail_text(completed.stderr or completed.stdout or "", max_chars=_FFMPEG_ERROR_MAX_CHARS)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 音频提取失败（exit={completed.returncode}）：{detail or 'ffmpeg 未输出错误详情'}"
        )

    try:
        require_media_packets(input_path=output_path, stream_types=("audio",))
    except MediaPacketValidationError as exc:
        suffix = f"；ffmpeg 输出：{detail}" if detail else ""
        raise MediaPacketValidationError(
            f"ffmpeg 音频提取失败：输出音频无有效数据包（{exc}）{suffix}"
        ) from exc
