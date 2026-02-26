from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    base_url: str = "http://127.0.0.1:8000"
    timezone: str = "Asia/Shanghai"

    database_url: str = "postgresql+psycopg://videosync:videosync@127.0.0.1:5432/videosync"

    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"
    s3_bucket: str = "video-sync"
    s3_use_ssl: bool = False

    ytdlp_bin: str = "./bin/yt-dlp"
    ffmpeg_bin: str = "./bin/ffmpeg"
    ytdlp_proxy: str = ""
    # yt-dlp format selector for downloads (see: https://github.com/yt-dlp/yt-dlp#format-selection)
    # Default: cap at 1080p, prefer MP4+M4A, then fall back to best available.
    ytdlp_format: str = Field(
        default=(
            "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
            "/best[ext=mp4][height<=1080]"
            "/best[height<=1080]"
            "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]"
            "/best"
        ),
        validation_alias=AliasChoices("YTDLP_FORMAT"),
    )

    sync_interval_minutes: int = 5
    sync_batch_size: int = 20
    sync_max_entries: int = 50
    auto_download_new_videos: bool = True

    youtube_sync_concurrency: int = 1
    bilibili_sync_concurrency: int = 1
    youtube_download_concurrency: int = 2
    bilibili_download_concurrency: int = 2

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

    ollama_url: str = ""
    ollama_model: str = ""
    ollama_timeout_seconds: int = 600


settings = Settings()
