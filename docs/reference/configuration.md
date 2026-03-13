# 配置项说明

这份文档只负责记录运行配置项，不承载架构设计或 API 约定。

## 核心运行配置

- `APP_ENV`
- `BASE_URL`
- `TIMEZONE`
- `DATABASE_URL`
- `S3_ENDPOINT`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
- `S3_REGION`
- `S3_BUCKET`
- `S3_USE_SSL`

## 工具与处理链路

- `FFMPEG_BIN`
- `AUDIO_CODEC`
- `AUDIO_BITRATE`
- `AUDIO_SAMPLE_RATE_HZ`
- `AUDIO_CHANNELS`
- `YTDLP_PROXY`
- `YTDLP_REMOTE_COMPONENTS`：默认 `ejs:github`；用于允许 yt-dlp 在 YouTube EJS/JS challenge 场景下拉取远程组件。可留空禁用，多个值可用逗号或空格分隔。
- `YTDLP_FORMAT`

## 同步与并发

- `SYNC_INTERVAL_MINUTES`：默认 `5`
- `SYNC_BATCH_SIZE`
- `SYNC_MAX_ENTRIES`
- `AUTO_DOWNLOAD_NEW_VIDEOS`
- `YOUTUBE_SYNC_CONCURRENCY`
- `BILIBILI_SYNC_CONCURRENCY`
- `YOUTUBE_DOWNLOAD_CONCURRENCY`
- `BILIBILI_DOWNLOAD_CONCURRENCY`

## ASR / LLM

- `ASR_URL`
- `ASR_TIMEOUT_SECONDS`
- `LLM_URL`
- `LLM_MODEL`
- `LLM_API_KEY`
- `LLM_HEADERS_JSON`
- `LLM_TIMEOUT_SECONDS`

## Worker

- `WORKER_ROLE`
- `WORKER_TYPES`

## MCP HTTP

- `MCP_HOST`：默认 `0.0.0.0`
- `MCP_PORT`：默认 `8001`
- `MCP_BASE_PATH`：默认 `/mcp`
- `MCP_BEARER_TOKEN`：MCP HTTP 服务必填；为空时 `python -m raelyn.mcp_server` 会直接启动失败，`scripts/dev/devctl.sh start` 也会跳过 MCP 进程

MCP 运行边界：

- `GET /health` 允许匿名访问
- `MCP_BASE_PATH` 下的 MCP 请求全部要求 `Authorization: Bearer <MCP_BEARER_TOKEN>`
- 默认监听 `0.0.0.0` 是为了受信任局域网访问，不按公网暴露方案设计
- 如果只希望本机访问，可把 `MCP_HOST` 改成 `127.0.0.1`
