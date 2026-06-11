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
    # 可选的 bgutil PO Token Provider HTTP server；空值表示不启用。
    ytdlp_pot_bgutil_base_url: str = Field(default="", validation_alias=AliasChoices("YTDLP_POT_BGUTIL_BASE_URL"))
    # YouTube 请求默认启用浏览器 TLS 指纹模拟；空值表示不启用。
    ytdlp_youtube_impersonate: str = Field(default="chrome", validation_alias=AliasChoices("YTDLP_YOUTUBE_IMPERSONATE"))
    # yt-dlp format selector for downloads (see: https://github.com/yt-dlp/yt-dlp#format-selection)
    # Default: cap at 1080p, prefer combined MP4/HLS before DASH video-only formats.
    ytdlp_format: str = Field(
        default=(
            "best[ext=mp4][height<=1080]"
            "/best[height<=1080]"
            "/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
            "/bestvideo[height<=1080]+bestaudio"
            "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]"
            "/best"
        ),
        validation_alias=AliasChoices("YTDLP_FORMAT"),
    )

    sync_interval_minutes: int = 60
    sync_interval_jitter_minutes: int = 15
    sync_batch_size: int = 20
    sync_max_entries: int = 10
    auto_download_new_videos: bool = True
    stats_cache_ttl_seconds: int = Field(default=60, validation_alias=AliasChoices("STATS_CACHE_TTL_SECONDS"))
    auto_generate_briefs: bool = Field(default=False, validation_alias=AliasChoices("AUTO_GENERATE_BRIEFS"))

    youtube_sync_concurrency: int = 1
    bilibili_sync_concurrency: int = 1
    youtube_download_concurrency: int = 2
    bilibili_download_concurrency: int = 2

    # --- Worker heartbeats / orphan running job recovery ---
    # Worker 周期性写入进程心跳，用于发现进程崩溃或退出。
    worker_heartbeat_interval_seconds: int = 5
    # 超过该时间未更新进程心跳，则认为 worker 已失联。
    worker_stale_after_seconds: int = 20
    # 进程心跳仍新鲜、但主执行线程活动时间超过该阈值未推进时，认为执行循环卡死。
    worker_execution_stale_after_seconds: int = 120
    # 回收孤儿 running 任务时提升优先级，让它们回到队头。
    orphan_requeue_priority_bump: int = 1000

    # --- ASR (qwen3-asr / OpenAI-compatible servers) ---
    # Back-compat: also accepts SPEACHES_* env vars.
    asr_url: str = Field(default="", validation_alias=AliasChoices("ASR_URL", "SPEACHES_URL"))
    # Optional override for ASR endpoint path (default: "/v1/audio/transcriptions").
    asr_endpoint: str = Field(default="", validation_alias=AliasChoices("ASR_ENDPOINT", "SPEACHES_ENDPOINT"))
    asr_model: str = Field(default="", validation_alias=AliasChoices("ASR_MODEL", "SPEACHES_MODEL"))
    asr_prompt: str = Field(default="", validation_alias=AliasChoices("ASR_PROMPT", "SPEACHES_PROMPT"))
    asr_language: str = Field(default="", validation_alias=AliasChoices("ASR_LANGUAGE"))
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
    asr_worker_concurrency: int = Field(default=1, validation_alias=AliasChoices("ASR_WORKER_CONCURRENCY"))
    asr_backend_capacity_guard_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("ASR_BACKEND_CAPACITY_GUARD_ENABLED"),
    )
    asr_backend_capacity_defer_seconds: int = Field(
        default=30,
        validation_alias=AliasChoices("ASR_BACKEND_CAPACITY_DEFER_SECONDS"),
    )

    llm_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_headers_json: str = ""
    llm_timeout_seconds: int = 600

    # --- Event extraction / event regime analysis ---
    event_extraction_chunk_max_chars: int = Field(default=12000, validation_alias=AliasChoices("EVENT_EXTRACTION_CHUNK_MAX_CHARS"))
    auto_extract_new_video_events: bool = Field(default=True, validation_alias=AliasChoices("AUTO_EXTRACT_NEW_VIDEO_EVENTS"))

    # --- Event embeddings (OpenAI-compatible embedding servers) ---
    embedding_url: str = "http://10.6.0.10:12302"
    embedding_endpoint: str = "/v1/embeddings"
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_dim: int = 1024
    embedding_timeout_seconds: int = 120
    embedding_worker_concurrency: int = 1
    analysis_worker_concurrency: int = 1
    ai_worker_concurrency: int = Field(default=1, validation_alias=AliasChoices("AI_WORKER_CONCURRENCY"))
    analysis_min_available_memory_bytes: int = 1024 * 1024 * 1024
    analysis_max_rss_bytes: int = 6 * 1024 * 1024 * 1024
    analysis_stream_batch_size: int = 2000

    # --- Volcengine managed inference defaults (used when inference_mode=volcengine) ---
    volcengine_llm_url: str = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    volcengine_llm_model: str = ""
    volcengine_llm_api_key: str = ""
    volcengine_llm_timeout_seconds: int = 600

    # Doubao Speech 极速版固定接口；高级用户可通过 .env 隐式覆盖，但 UI 不暴露这些细节。
    volcengine_asr_url: str = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
    volcengine_asr_model: str = "bigmodel"
    volcengine_asr_app_key: str = ""
    volcengine_asr_access_key: str = ""
    volcengine_asr_resource_id: str = "volc.bigasr.auc_turbo"
    volcengine_asr_timeout_seconds: int = 600

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
