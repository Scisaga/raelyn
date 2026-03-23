# 配置项说明

这份文档只记录当前运行配置，不承载架构设计或接口说明。配置分为两类：

- 环境变量：进程启动时读取，主要来自 `.env`。
- 运行时配置：保存在 `app_config`，通过 `/api/config/*` 修改，通常无需重启即可生效。

## 环境变量

### 核心运行

- `APP_ENV`
- `BASE_URL`
- `TIMEZONE`
- `API_BEARER_TOKEN`
  - 为空时不启用主站 API 鉴权。
  - 非空时 `/api/*` 需要 `Authorization: Bearer <token>` 或 `raelyn_api_token` cookie。
  - `/api/ws/*` 需要 query `token=<token>`。
  - 非空时主 API 进程也会额外挂载 `/mcp`，MCP HTTP 复用同一个 Bearer Token。

### 数据与对象存储

- `DATABASE_URL`
- `S3_ENDPOINT`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
- `S3_REGION`
- `S3_BUCKET`
- `S3_USE_SSL`

### 资产分发策略

- `ASSET_DIRECT_PROBE_URL`
  - 前端启动时用于探测是否能直接访问对象存储。
  - 若页面本身运行在 `https://` 下，而该地址是 `http://`，前端会直接判定为 mixed content 风险并回退到代理模式，不再发起直连探测。
- `ASSET_DIRECT_PROBE_TIMEOUT_MS`
- `ASSET_PROXY_BASE_PATH`
  - 默认 `/api/assets`
- `ASSET_PRESIGN_ENABLED`
  - 控制是否为资产生成 presigned URL。

说明：

- HTTPS 主站下，若对象存储或 presigned URL 仍是 `http://`，前端不会使用直连资源，而会统一退回 `ASSET_PROXY_BASE_PATH` 对应的 API 代理路径。
- 若希望在 HTTPS 主站下继续使用直连 / presigned URL，需要让对象存储出口本身也提供 HTTPS。

### 工具与下载

- `FFMPEG_BIN`
- `AUDIO_CODEC`
- `AUDIO_BITRATE`
- `AUDIO_SAMPLE_RATE_HZ`
- `AUDIO_CHANNELS`
- `YTDLP_PROXY`
- `YTDLP_REMOTE_COMPONENTS`
  - 默认 `ejs:github`
- `YTDLP_FORMAT`
  - 作为默认格式选择器；若运行时配置 `ytdlp_format.text` 存在，会优先使用运行时配置。

### 同步与并发

- `SYNC_INTERVAL_MINUTES`
- `SYNC_BATCH_SIZE`
- `SYNC_MAX_ENTRIES`
- `AUTO_DOWNLOAD_NEW_VIDEOS`
- `YOUTUBE_SYNC_CONCURRENCY`
- `BILIBILI_SYNC_CONCURRENCY`
- `YOUTUBE_DOWNLOAD_CONCURRENCY`
- `BILIBILI_DOWNLOAD_CONCURRENCY`

### Worker 心跳与孤儿任务回收

- `WORKER_HEARTBEAT_INTERVAL_SECONDS`
- `WORKER_STALE_AFTER_SECONDS`
- `ORPHAN_REQUEUE_PRIORITY_BUMP`

### ASR / LLM

- `ASR_URL`
- `ASR_ENDPOINT`
- `ASR_MODEL`
- `ASR_PROMPT`
- `ASR_TEMPERATURE`
- `ASR_RESPONSE_FORMAT`
- `ASR_TIMEOUT_SECONDS`
- `LLM_URL`
- `LLM_MODEL`
- `LLM_API_KEY`
- `LLM_HEADERS_JSON`
- `LLM_TIMEOUT_SECONDS`

### Worker 进程选择

- `WORKER_ROLE`
  - 支持 `download_youtube`、`download_bilibili`、`audio`、`process`、`asr`、`sync`、`ai`、`all`
- `WORKER_TYPES`
  - 逗号分隔的 job type 列表，优先级高于 `WORKER_ROLE`

### MCP HTTP

- `MCP_BASE_PATH`
- `MCP_ALLOWED_HOSTS`
  - 逗号分隔的 Host 白名单，用于 MCP SDK 的 DNS rebinding 防护。
  - 反向代理公网访问时，需要把外部 Host 加进去，例如 `scisaga.cc:234`。
- `MCP_ALLOWED_ORIGINS`
  - 逗号分隔的 Origin 白名单。
  - 反向代理公网访问时，通常与 `MCP_ALLOWED_HOSTS` 对应，例如 `https://scisaga.cc:234`。

## 运行时配置（`app_config`）

### 平台 Cookies

- `ytdlp_cookies_youtube`
- `ytdlp_cookies_bilibili`

值结构：

```json
{ "text": "<netscape cookies.txt>" }
```

说明：

- 通过 `/api/config/{key}` 写入。
- 服务运行时会把内容写到 `tmp/ytdlp_cookies_*.txt` 供 yt-dlp / profile fetch 使用。
- 更新后若系统是因为 Cookies 失效被自动暂停，会尝试自动恢复。

### 字幕与会员视频

- `ytdlp_subtitles`
- `ytdlp_members_only`

值结构：

```json
{ "enabled": true }
```

说明：

- `ytdlp_subtitles` 控制是否下载字幕 / 自动字幕。
- `ytdlp_members_only` 控制是否尝试下载 YouTube 会员专享视频。

### 下载格式

- `ytdlp_format`

值结构：

```json
{ "preset": "1080|720|custom", "text": "<yt-dlp format selector>" }
```

说明：

- 若存在此项，下载时优先于环境变量 `YTDLP_FORMAT`。

### 转写润色提示词

- `llm_transcript_polish_prompt`

值结构：

```json
{ "text": "<prompt template>" }
```

说明：

- 用于 `video.polish_transcript`。
- 建议保留 `{chunk}` 占位符；`{index}` / `{total}` 可选。

### 简报调度策略

- `brief_generation_policy`

值结构：

```json
{
  "latest_cooldown_minutes": 120,
  "historical_daily_run_time": "04:00"
}
```

说明：

- 控制最新周期的冷却时间与历史周期批处理时间。
- 默认值可通过 `GET /api/config/defaults` 获取。

## 当前配置边界

- 环境变量偏“进程级 / 基础设施级”。
- `app_config` 偏“运行时行为开关”。
- 并不是所有环境变量都有 UI 页面；也不是所有 `app_config` 都有独立 UI 标签。
- 平台 Cookies、字幕、会员视频、下载格式、转写润色提示词目前都有现成 UI；简报调度策略目前主要通过 API 管理。
