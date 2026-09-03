# 架构总览

`raelyn` 当前是一个由 `FastAPI API（含挂载式 MCP） + 静态 SPA + Worker + Scheduler` 组成的单用户语义观测系统。它以 YouTube / B 站信源、来源记录和转写为感知底座，把结构化事件组织成可回放、可追踪故事、可回到证据的事件语义星域。

## 当前约束与决策

- 单用户系统；不做多租户与复杂权限模型。
- 主站 API 与 MCP HTTP 共用同一个可选 Bearer Token；未配置时 `/api/*` 公开，且不挂载 `/mcp`。
- 媒体新增后默认 `monitor_enabled=false`，只有启用监控后才会进入分钟级自动同步。
- 播放列表不只是“媒体集合”，还承载周期聚合粒度、简报提示词、封面与背景图。
- 任务系统沿用项目内 [skills/job-system-design/SKILL.md](../../skills/job-system-design/SKILL.md) 的通用原则，项目文档只记录本仓库的实现形态。

## 当前领域对象

### Media

- 表示一个内容源（YouTube 频道 / B 站 UP 主）。
- 维护 provider id、URL、资料信息、监控开关与同步游标。
- 新增后只投递 `media.sync_profile`；视频同步由显式同步或 `scheduler` 触发。

### Video

- 表示一个平台视频条目，保存元数据、状态与错误信息。
- `published_at` 可能在“发现视频”阶段就写入；它表示平台原始发布时间，不覆盖历史回填出的内容真实日期。
- 播放列表周期归属使用内容时间轴 `coalesce(content_published_at, published_at)` + 视频资产就绪。
- 简报周期归属使用内容时间轴 `coalesce(content_published_at, published_at)` + transcript 文本就绪。
- 通过 `Asset` 关联视频文件、音频、字幕、文字稿、简报图片等产物。

### Asset

- 统一抽象视频文件、音频、字幕、转写、笔记、简报、播放列表图片等对象。
- 内容落在 MinIO / S3，数据库仅保存引用、格式、来源、语言、变体和元信息。
- API 默认通过 `/api/assets` 代理读取资产；只有显式启用 presign 且浏览器可访问对象存储出口时，才会探测并使用直连路径。

### Playlist / Brief

- `playlist` 是当前内部名称，对外产品语义为“观测域”；它聚合信源，并保存简报粒度、独立提示词、头像和背景图。
- `brief` 支持 `day / week / month` 三种粒度，并通过 `brief_reference` 固化生成快照、语义对象与证据引用。
- `daily_brief` 仍保留用于兼容历史数据读取。

### MarketEvent / EventGraph

- `market_event` 保存从视频 transcript 抽取出的结构化事件记录；`market_event_entity/evidence/relation` 保留记录级实体和证据。
- `market_event_embedding` 提供记录级语义向量。
- `event_map_*` 把记录保守归并为 canonical 真实事件，并在不可变快照中保存两级 topic、story、anchor 与固定三维语义坐标。
- `domain_observation_cursor`、长期 canonical/story 修订、变化集和阅读状态组成 V2 连续观察层；快照裁剪不删除这些长期语义历史。
- 星域主场景从类型化关系列流式编码为固定宽度二进制，不通过全量 JSONB 渲染。冷热存储边界见 [V2 观察与存储架构](v2-observation.md)。

### Job / WorkerHeartbeat / AppConfig

- `job` 统一承载同步、下载、处理、AI 任务。
- `worker_heartbeat` 用于在线状态展示和孤儿任务回收。
- `app_config` 保存运行时开关，例如 Cookies、字幕下载、会员视频、下载格式、简报策略、转写润色提示词与事件抽取提示词。

## 当前组件划分

### API 进程

- 入口是 [backend/raelyn/main.py](../../backend/raelyn/main.py)。
- 提供 `/api/*`、`/api/ws/*`、`/docs`、`/openapi.json`，以及根路径 SPA / PWA 壳。
- 负责资源查询、任务投递、系统状态、配置写入、资产访问与 WebSocket 推送。

### Worker 进程

- 入口是 [backend/raelyn/worker.py](../../backend/raelyn/worker.py)。
- 支持按 `WORKER_ROLE` 或 `WORKER_TYPES` 拆分角色，例如 `download_youtube`、`download_bilibili`、`audio`、`process`、`asr`、`sync`、`embedding`、`analysis`、`ai`。
- `download_youtube` / `download_bilibili` 是 provider 专属下载执行面；其 worker 进程数默认与 `YOUTUBE_DOWNLOAD_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY` 强绑定，用来兑现真实下载并发语义。
- `asr` 负责 `video.asr_transcribe`；其 worker 进程数默认与 `ASR_WORKER_CONCURRENCY` 绑定，每个进程同一时间执行一个远端 ASR 请求。对 qwen3-asr-openai 这类会在 `/health` 暴露后端 replica 与队列状态的服务，worker 会在领取 ASR 任务前做容量门控，后端已满时不从 DB claim 新 ASR 任务，避免继续把请求打进 502/503。
- `ai` 负责 `video.extract_events`、`playlist.backfill_events`、`playlist.backfill_events_range`、转写润色与简报生成；`embedding` 负责 `event.embed`；`analysis` 负责 `playlist.mark_event_map_dirty`、`playlist.build_event_map_snapshot` 与 `playlist.prune_event_map_snapshots`。三类执行面可独立暂停和扩容。
- provider 下载 handler 内仍保留 advisory lock 作为最终并发上限保护；它是内部实现，不是对外配置语义。
- 负责任务领取、心跳、孤儿任务回收、失败退避与实际处理逻辑执行。

### Scheduler 进程

- 入口是 [backend/raelyn/scheduler.py](../../backend/raelyn/scheduler.py)。
- 每分钟扫描已启用监控且超过同步间隔的媒体；到期时间会按媒体与上次同步时间增加稳定随机抖动，再投递 `media.sync_videos`。
- 会尊重系统暂停和 provider 暂停状态，避免继续放量。

### MCP HTTP 挂载

- 由 [backend/raelyn/main.py](../../backend/raelyn/main.py) 按 `MCP_BASE_PATH=/mcp` 挂载。
- 只有在配置了 `API_BEARER_TOKEN` 时才启用；否则主 API 正常启动但不提供 `/mcp`。
- 复用现有 DB / service / job enqueue 能力，不通过 `/api/*` 再套一层 HTTP。

### 静态 UI / PWA

- SPA 构建产物位于 `static/`，源码与模板位于 `ui/`。
- 主要认知页面为：星域、故事、简报、资料库；系统页面依次为：运行中心、资源用量、智能体接入和设置。旧媒体、视频、播放列表与任务路由继续保留兼容，不再占据一级认知导航。
- 启动时会先探测 API 鉴权与资产分发策略，再进入应用主界面。

## 当前关键数据流

### 1. 添加媒体与启用监控

- `POST /api/media` 新增媒体，默认 `monitor_enabled=false`。
- API 立即投递 `media.sync_profile`，以补齐名称、头像、描述等资料。
- 用户在媒体页显式开启监控后，媒体才会进入调度循环。

### 2. 媒体同步

- 手动同步支持 `scope=recent|all`。
- `scheduler` 只对已启用监控媒体投递增量 `media.sync_videos`。
- 同步逻辑会根据 provider、Cookies、会员视频开关和平台暂停状态决定是否继续发现与自动下载。

### 3. 下载与文本处理

- `video.download.*` 下载视频、缩略图、字幕等原始产物。
- `video.backfill_subtitles.*` 可对已采集视频只回补字幕 / 自动字幕，成功后写入 raw subtitle asset。
- `video.extract_audio` 生成音频资产。
- `video.normalize_subtitle` 生成 transcript，并可继续触发 `video.polish_transcript`。
- 若没有可用字幕 transcript 且配置了 ASR，则进入 `video.asr_transcribe`。
- ASR 默认不传 `language`，由模型自动识别；`qwen3-asr` 的 `plain` / `segments` transcript 按响应里的实际语言保存。现有 polish 仍保持中文整理口径，只在 ASR transcript 语言为 `zh` 时自动投递。

### 4. 播放列表聚合与简报

- 播放列表维护媒体集合、周期粒度和提示词。
- 播放列表变更会触发对应周期的简报刷新计划。
- `brief.generate_period` / `brief.generate_daily` 基于粒度聚合视频文本，生成 Markdown 简报并存为 `asset(type=brief)`。

## 文档导航

- 完整文档入口与分类见 [docs/README.md](../README.md)。
