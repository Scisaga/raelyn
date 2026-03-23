from __future__ import annotations

from urllib.parse import urlparse

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    base_url: str = "http://127.0.0.1:8000"
    timezone: str = "Asia/Shanghai"
    api_bearer_token: str = ""
    mcp_base_path: str = "/mcp"
    mcp_dns_rebinding_protection_enabled: bool = True
    mcp_allowed_hosts: str = ""
    mcp_allowed_origins: str = ""

    database_url: str = "postgresql+psycopg://raelyn:raelyn@127.0.0.1:5432/raelyn"

    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"
    s3_bucket: str = "raelyn"
    s3_use_ssl: bool = False
    asset_direct_probe_url: str = ""
    asset_direct_probe_timeout_ms: int = 1000
    asset_proxy_base_path: str = "/api/assets"
    asset_presign_enabled: bool = True

    ffmpeg_bin: str = "./bin/ffmpeg"

    # --- Audio extraction (ffmpeg) ---
    # Used by video.extract_audio to generate an audio-only asset for mobile playback and ASR.
    # Defaults bias toward speech (lower bandwidth) while staying broadly compatible (AAC in M4A).
    audio_codec: str = Field(default="aac", validation_alias=AliasChoices("AUDIO_CODEC"))
    audio_bitrate: str = Field(default="64k", validation_alias=AliasChoices("AUDIO_BITRATE"))
    audio_sample_rate_hz: int | None = Field(default=16000, validation_alias=AliasChoices("AUDIO_SAMPLE_RATE_HZ"))
    audio_channels: int | None = Field(default=1, validation_alias=AliasChoices("AUDIO_CHANNELS"))
    ytdlp_proxy: str = ""
    # Allow yt-dlp to fetch trusted remote components required by YouTube's EJS/JS challenge solver.
    # Default: enable GitHub-hosted ejs component; set empty to disable.
    ytdlp_remote_components: str = Field(default="ejs:github", validation_alias=AliasChoices("YTDLP_REMOTE_COMPONENTS"))
    # yt-dlp format selector for downloads (see: https://github.com/yt-dlp/yt-dlp#format-selection)
    # Default: cap at 1080p, prefer MP4+M4A, then fall back to best available.
    ytdlp_format: str = Field(
        default=(
            "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
            "/best[ext=mp4][height<=1080]"
            "/bestvideo[height<=1080]+bestaudio"
            "/best[height<=1080]"
            "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]"
            "/best"
        ),
        validation_alias=AliasChoices("YTDLP_FORMAT"),
    )

    sync_interval_minutes: int = 5
    sync_batch_size: int = 20
    sync_max_entries: int = 10
    auto_download_new_videos: bool = True

    youtube_sync_concurrency: int = 1
    bilibili_sync_concurrency: int = 1
    youtube_download_concurrency: int = 2
    bilibili_download_concurrency: int = 2

    # --- Worker heartbeats / orphan running job recovery ---
    # Worker writes a heartbeat row periodically so other workers can detect crashed peers.
    worker_heartbeat_interval_seconds: int = 5
    # Consider a worker dead if its heartbeat hasn't updated within this window.
    worker_stale_after_seconds: int = 20
    # When requeuing orphaned "running" jobs, bump their priority to the head of the queue.
    orphan_requeue_priority_bump: int = 1000

    # --- ASR (qwen3-asr / OpenAI-compatible servers) ---
    # Back-compat: also accepts SPEACHES_* env vars.
    asr_url: str = Field(default="", validation_alias=AliasChoices("ASR_URL", "SPEACHES_URL"))
    # Optional override for ASR endpoint path (default: "/v1/audio/transcriptions").
    asr_endpoint: str = Field(default="", validation_alias=AliasChoices("ASR_ENDPOINT", "SPEACHES_ENDPOINT"))
    asr_model: str = Field(default="", validation_alias=AliasChoices("ASR_MODEL", "SPEACHES_MODEL"))
    asr_prompt: str = Field(default="", validation_alias=AliasChoices("ASR_PROMPT", "SPEACHES_PROMPT"))
    asr_temperature: float | None = Field(default=None, validation_alias=AliasChoices("ASR_TEMPERATURE"))
    # "verbose_json" returns segments; empty means "verbose_json".
    asr_response_format: str = Field(
        default="",
        validation_alias=AliasChoices("ASR_RESPONSE_FORMAT", "SPEACHES_RESPONSE_FORMAT"),
    )
    asr_timeout_seconds: int = Field(
        default=600,
        validation_alias=AliasChoices("ASR_TIMEOUT_SECONDS", "SPEACHES_TIMEOUT_SECONDS"),
    )

    llm_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_headers_json: str = ""
    llm_timeout_seconds: int = 600

    def mcp_allowed_host_values(self) -> list[str]:
        values = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        values.extend(_split_csv_values(self.mcp_allowed_hosts))

        base_url = str(self.base_url or "").strip()
        if base_url:
            parsed = urlparse(base_url)
            if parsed.netloc:
                values.append(parsed.netloc)
        return _dedupe_keep_order(values)

    def mcp_allowed_origin_values(self) -> list[str]:
        values = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
        values.extend(_split_csv_values(self.mcp_allowed_origins))

        base_url = str(self.base_url or "").strip()
        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme and parsed.netloc:
                values.append(f"{parsed.scheme}://{parsed.netloc}")
        return _dedupe_keep_order(values)


def _split_csv_values(raw: str | None) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [item for item in (part.strip() for part in text.split(",")) if item]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


settings = Settings()
