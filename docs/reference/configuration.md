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
  - 应与 `asset.s3_bucket` 中的实际 bucket 保持一致；`/api/stats` 会在检测到配置 bucket 与唯一实际 bucket 不一致时回退统计实际 bucket，并返回 mismatch 标记。
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

### 高优先级代理规则

- `YTDLP_PROXY` 只用于 YouTube 的 `yt-dlp` 同步 / 下载请求。
- 非 `yt-dlp` 的资料抓取 / 头像缓存、B 站请求、ASR / LLM / Embedding / 健康检查都不使用 `YTDLP_PROXY`。
- 应用默认不隐式读取进程环境中的 `HTTP_PROXY` / `HTTPS_PROXY`；这些变量存在于 shell 或 systemd 环境里，不代表本应用会把外部请求送进代理。
- 若 YouTube 同步/下载需要代理，必须配置 `YTDLP_PROXY`，不要依赖 `HTTP_PROXY` / `HTTPS_PROXY` 的副作用。
- 若运行环境本身设置了 `HTTP_PROXY` / `HTTPS_PROXY`，需要让 `NO_PROXY` / `no_proxy` 包含 `127.0.0.1`、`localhost`、`::1` 和 `host.docker.internal`，避免 bgutil provider、MinIO 等本机服务被环境代理劫持。

### 工具与下载

- `FFMPEG_BIN`
- `AUDIO_CODEC`
- `AUDIO_BITRATE`
- `AUDIO_SAMPLE_RATE_HZ`
- `AUDIO_CHANNELS`
- `YTDLP_PROXY`
  - 仅 YouTube 的 `yt-dlp` 同步 / 下载请求会显式使用该代理；完整边界见上方“高优先级代理规则”。
- `NO_PROXY` / `no_proxy`
  - 推荐包含 `127.0.0.1,localhost,::1,host.docker.internal`。
  - `scripts/dev/load-env.sh` 和 Docker entrypoint 会自动补齐这些本机地址。
- `YTDLP_REMOTE_COMPONENTS`
  - 默认 `ejs:github`
  - 只用于 YouTube EJS / JS challenge 组件，不等同于 PO Token Provider。
- `YTDLP_POT_BGUTIL_BASE_URL`
  - 可选；bgutil PO Token Provider HTTP server 地址。空值表示不启用。
  - 本机运行常用 `http://127.0.0.1:4416`；Docker Compose 的 app 容器内使用 `http://host.docker.internal:4416`。
- `YTDLP_YOUTUBE_IMPERSONATE`
  - 默认 `chrome`，仅用于 YouTube 的 `yt-dlp` 同步 / 下载请求。
  - 依赖 `curl_cffi`；空值表示不启用浏览器 impersonation。
- `YTDLP_FORMAT`
  - 作为默认格式选择器；若运行时配置 `ytdlp_format.text` 存在，会优先使用运行时配置。
  - YouTube 下载默认优先 combined MP4/HLS，再回退 DASH video-only；这是为了避开 2026-05-18 实测中 Bloomberg 样本 360p+ DASH video-only GVS URL 返回 `HTTP Error 403` 的路径。

### 同步与并发

- `SYNC_INTERVAL_MINUTES`
- `SYNC_INTERVAL_JITTER_MINUTES`
  - 频道自动同步在基础间隔之后，按媒体与上次同步时间稳定增加 `0..N` 分钟抖动，避免大量频道同一分钟集中请求。
- `SYNC_BATCH_SIZE`
  - `scheduler` 每分钟扫描到期媒体时，单次最多投递的 `media.sync_videos` 数量；`SYNC_INTERVAL_MINUTES` 约束的是单个媒体的同步间隔，不表示全站每小时只投递一个同步任务。
  - 当前推荐值为 `2`，用于降低 YouTube / B 站同步请求波峰；媒体数量较多时，追赶积压会更慢，但更不容易触发平台风控。
- `SYNC_MAX_ENTRIES`
- `AUTO_DOWNLOAD_NEW_VIDEOS`
- `AUTO_GENERATE_BRIEFS`
  - 是否在 transcript 就绪、媒体变更或媒体删除后自动投递 `brief.generate_period`，默认 `false`。
  - 关闭时不会删除已有 `brief` / `daily_brief` 数据，也不影响手动 `POST /api/briefs/generate` 和 `POST /api/briefs/generate_range`。
- `STATS_CACHE_TTL_SECONDS`
  - `/api/stats` 的进程内缓存 TTL，默认 `60` 秒；设置为 `0` 可关闭缓存。
- `YOUTUBE_SYNC_CONCURRENCY`
- `BILIBILI_SYNC_CONCURRENCY`
- `YOUTUBE_DOWNLOAD_CONCURRENCY`
  - 表示 YouTube 的真实最大下载并发数，不只是内部 provider 槽位数。
  - 当值为 `N` 且 `N > 1` 时，运行层默认应启动至少 `N` 个 `download_youtube` worker 进程。
  - handler 内仍会使用 provider advisory lock 做最终上限保护；该锁是内部实现细节，不改变本配置的公开语义。
- `BILIBILI_DOWNLOAD_CONCURRENCY`
  - 表示 B 站的真实最大下载并发数，不只是内部 provider 槽位数。
  - 当值为 `N` 且 `N > 1` 时，运行层默认应启动至少 `N` 个 `download_bilibili` worker 进程。
  - handler 内仍会使用 provider advisory lock 做最终上限保护；该锁是内部实现细节，不改变本配置的公开语义。
- `ASR_WORKER_CONCURRENCY`
  - `devctl.sh` / Docker 单容器入口启动 `asr` worker 的进程数，默认 `1`。
  - 每个 `asr` worker 同一时间只执行一个 `video.asr_transcribe`，因此该值决定 ASR 远端转写请求的进程级并发上限。
  - 该配置只增加 worker 进程数，不改变 ASR 请求的认证、连接复用或缓存行为。
- `ASR_BACKEND_CAPACITY_GUARD_ENABLED`
  - 是否在本地 OpenAI-compatible ASR worker 领取任务前探测 `/health` 并根据 qwen3-asr-openai 的 `backend_replicas` / `in_flight` / `backend_queue_waiters` 暂停领取 ASR 任务，默认 `true`。
  - 该门控只影响 `video.asr_transcribe` 的任务调度节奏，不改变 ASR 请求体、认证、连接复用或后端模型参数。
- `ASR_BACKEND_CAPACITY_DEFER_SECONDS`
  - 当 ASR `/health` 没有返回 `backend_queue_timeout_seconds` 时，handler 兜底重排 ASR 任务的默认延后秒数，默认 `30`。
- `EMBEDDING_WORKER_CONCURRENCY`
  - `devctl.sh` / Docker 单容器入口启动 `embedding` worker 的进程数，默认 `1`。
  - 可设为 `0`，表示当前节点不启动 `embedding` worker；事件 embedding 任务会保留在 `pending`，直到有 embedding worker 可领取。
- `ANALYSIS_WORKER_CONCURRENCY`
  - `devctl.sh` / Docker 单容器入口启动 `analysis` worker 的进程数，默认 `1`。
  - 可设为 `0`，表示当前节点不启动 `analysis` worker；事件 Regime 快照任务会保留在 `pending`，直到有 analysis worker 可领取。
- `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES`
  - `playlist.build_event_regime_snapshot` 开始和处理中允许继续执行的最低 `MemAvailable`，默认 `1073741824`。
  - 低于该值时任务直接失败并记录原因，不进入重试队列。
- `ANALYSIS_MAX_RSS_BYTES`
  - `playlist.build_event_regime_snapshot` 允许的最大 worker 进程 RSS，默认 `6442450944`（6 GiB）。
  - 高于该值时任务直接失败并记录原因，不进入重试队列；生产环境仍建议用 systemd / cgroup 设置同等硬上限。
- `ANALYSIS_STREAM_BATCH_SIZE`
  - 事件 Regime 快照构建分批读取 ready event embedding 的批大小，默认 `2000`。
- `EVENT_EXTRACTION_CHUNK_MAX_CHARS`
  - `video.extract_events` 读取 `plain` transcript 后的 LLM 分块字符上限，默认 `12000`。
- `AUTO_EXTRACT_NEW_VIDEO_EVENTS`
  - 是否在新视频 transcript 生成后自动投递 `video.extract_events`，默认 `true`。
  - 关闭时不会影响播放列表页面手动触发的 `playlist.backfill_events` 历史回填。

### Worker 心跳与孤儿任务回收

- `WORKER_RESTART_DELAY_SECONDS`
  - `devctl.sh` 和 Docker 单容器入口用于自动拉起崩溃 worker 子进程的等待秒数，默认 `5`。
  - 该配置只影响 worker 子进程；API 或 scheduler 这类关键进程退出时，Docker 单容器入口仍会退出，让外层进程管理器处理整体故障。
- `WORKER_HEARTBEAT_INTERVAL_SECONDS`
  - worker 进程心跳线程写入 `worker_heartbeat.updated_at` 的间隔，默认 `5` 秒。
- `WORKER_STALE_AFTER_SECONDS`
  - 进程心跳超过该阈值未更新时，其他 worker 可将其 `running` 任务回收到 `pending`，默认 `20` 秒。
- `WORKER_EXECUTION_STALE_AFTER_SECONDS`
  - 下载类任务额外检查主执行线程活动心跳 `worker_heartbeat.active_at`，默认 `120` 秒。
  - 该阈值用于发现“心跳线程仍活着，但主执行循环已经卡死”的情况；下载进度更新会刷新执行心跳。
  - 下载类 worker 超过该阈值未推进时会主动退出，由 supervisor 重启并释放下载并发锁。
- `ORPHAN_REQUEUE_PRIORITY_BUMP`
  - 孤儿 `running` 任务被回收后提升的优先级基数，默认 `1000`，用于让回收任务回到队头。

### ASR / LLM

本地模式：

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

事件抽取 / 事件 Regime：

- `EVENT_EXTRACTION_CHUNK_MAX_CHARS`
- `AUTO_EXTRACT_NEW_VIDEO_EVENTS`
- `EMBEDDING_URL`
- `EMBEDDING_ENDPOINT`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIM`
- `EMBEDDING_TIMEOUT_SECONDS`
- `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES`
- `ANALYSIS_MAX_RSS_BYTES`
- `ANALYSIS_STREAM_BATCH_SIZE`
- `AUTO_GENERATE_BRIEFS`

火山模式默认值：

- `VOLCENGINE_LLM_URL`
- `VOLCENGINE_LLM_MODEL`
- `VOLCENGINE_LLM_API_KEY`
- `VOLCENGINE_LLM_TIMEOUT_SECONDS`
- `VOLCENGINE_ASR_URL`
- `VOLCENGINE_ASR_MODEL`
- `VOLCENGINE_ASR_APP_KEY`
- `VOLCENGINE_ASR_ACCESS_KEY`
- `VOLCENGINE_ASR_RESOURCE_ID`
- `VOLCENGINE_ASR_TIMEOUT_SECONDS`

说明：

- `local` 模式只读取本地 `.env` 与自托管推理服务配置。
- `volcengine` 模式优先读取运行时配置 `app_config`；若某些字段未配置，则回退到对应的 `VOLCENGINE_*` 环境变量默认值。
- ASR / LLM / Embedding 的健康检查、容量门控、实际请求与配置测试连接都会忽略进程环境中的 `HTTP_PROXY` / `HTTPS_PROXY`；对应 URL 应直接指向可达服务地址。
- UI 不直接修改 `.env`；保存设置后只影响新任务，不会中断正在运行的任务。

### Worker 进程选择

- `WORKER_ROLE`
  - 支持 `download_youtube`、`download_bilibili`、`audio`、`process`、`asr`、`sync`、`embedding`、`analysis`、`ai`、`all`
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
- YouTube cookies / bot check / PO Token 的运行策略见 [YouTube yt-dlp 同步与 Cookies 策略](youtube-ytdlp-strategy.md)。

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
- YouTube 自定义格式若优先选择 `bestvideo+bestaudio` 这类 DASH video-only 组合，可能重新触发媒体 URL `HTTP Error 403`。排障时优先尝试 `best[ext=mp4][height<=1080]` 或 `best[protocol^=m3u8][height<=1080]`。
- 格式类型、selector 顺序和 403 排障步骤见 [yt-dlp 视频 / 音频格式选择策略](ytdlp-format-selection.md)。

### 转写润色提示词

- `llm_transcript_polish_prompt`

值结构：

```json
{ "text": "<prompt template>" }
```

说明：

- 用于 `video.polish_transcript`。
- 建议保留 `{chunk}` 占位符；`{index}` / `{total}` 可选。
- 默认提示词定位为“可回溯证据的保真整理稿”：按原文顺序近逐句整理，保留金融、宏观、政策、地缘、企业、行业、资产价格、利率、信用、商品、库存、财报、订单、指引、风险偏好等证据粒度；不得把长转写压缩成摘要；数字表达按原文保留，不做中文数字改写或单位换算。
- `video.polish_transcript` 默认按约 `1500` 字符切分原始转写后逐段调用 LLM；若单段调用超时，会继续二分该段，直到最小重试粒度仍失败时再让任务失败，降低保真输出过长导致单次调用超时的概率。
- 调用 LLM 前会对阿拉伯数字和英文数字短语做临时占位保护，返回后还原原文数字表达，避免模型把 `four point seven eight percent`、`ten billion` 等证据改写成中文数字或换算金额。

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

### 推理模式

- `inference_mode`
- `volcengine_inference_config`

值结构：

```json
{ "value": "local" }
```

```json
{
  "api_key": "<ark api key>",
  "llm_model": "doubao-seed-1-6-thinking-250715",
  "asr_model": "bigmodel",
  "asr_app_key": "<app key>",
  "asr_access_key": "<access key>",
  "llm_timeout_seconds": 600,
  "asr_timeout_seconds": 600
}
```

说明：

- 通过 `GET /api/config/inference`、`PUT /api/config/inference`、`POST /api/config/inference/test` 管理。
- 运行时优先级固定为 `app_config > .env`。
- 返回给前端的密钥字段会被脱敏，前端留空保存时会保留已存密钥。
- v1 只公开 `local` / `volcengine` 两种模式；底层仍保持 ASR / LLM 适配层解耦。

## 当前配置边界

- 环境变量偏“进程级 / 基础设施级”。
- `app_config` 偏“运行时行为开关”。
- 并不是所有环境变量都有 UI 页面；也不是所有 `app_config` 都有独立 UI 标签。
- 平台 Cookies、字幕、会员视频、下载格式、转写润色提示词目前都有现成 UI；简报调度策略目前主要通过 API 管理。
