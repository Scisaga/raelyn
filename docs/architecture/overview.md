# 架构总览

`raelyn` 当前是一个由 `FastAPI API（含挂载式 MCP） + 静态 SPA + Worker + Scheduler` 组成的单用户媒体采集系统。它支持 YouTube / B 站媒体管理、视频同步与下载、字幕 / 转写处理、播放列表聚合，以及按天 / 周 / 月生成 Markdown 简报。

## 当前约束与决策

- 单用户系统；不做多租户与复杂权限模型。
- 主站 API 可选 Bearer Token，MCP HTTP 必须配置 Bearer Token。
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
- 当前时间线以 `published_at` 为主，缺失时回退到 `created_at`。
- 通过 `Asset` 关联视频文件、音频、字幕、文字稿、笔记等产物。

### Asset

- 统一抽象视频文件、音频、字幕、转写、笔记、简报、播放列表图片等对象。
- 内容落在 MinIO / S3，数据库仅保存引用、格式、来源、语言、变体和元信息。
- API 会根据运行时探测结果选择直连 presign 或代理下载路径。

### Playlist / Brief

- `playlist` 用于聚合媒体，并保存简报粒度、独立提示词、头像、背景图。
- 当前简报主表是 `brief`，支持 `day / week / month` 三种粒度。
- `daily_brief` 仍保留用于兼容历史数据读取。

### Job / WorkerHeartbeat / AppConfig

- `job` 统一承载同步、下载、处理、AI 任务。
- `worker_heartbeat` 用于在线状态展示和孤儿任务回收。
- `app_config` 保存运行时开关，例如 Cookies、字幕下载、会员视频、下载格式、简报策略与转写润色提示词。

## 当前组件划分

### API 进程

- 入口是 [backend/raelyn/main.py](../../backend/raelyn/main.py)。
- 提供 `/api/*`、`/api/ws/*`、`/docs`、`/openapi.json`，以及根路径 SPA / PWA 壳。
- 负责资源查询、任务投递、系统状态、配置写入、资产访问与 WebSocket 推送。

### Worker 进程

- 入口是 [backend/raelyn/worker.py](../../backend/raelyn/worker.py)。
- 支持按 `WORKER_ROLE` 或 `WORKER_TYPES` 拆分角色，例如 `download_youtube`、`download_bilibili`、`audio`、`process`、`asr`、`sync`、`ai`。
- 负责任务领取、心跳、孤儿任务回收、失败退避与实际处理逻辑执行。

### Scheduler 进程

- 入口是 [backend/raelyn/scheduler.py](../../backend/raelyn/scheduler.py)。
- 每分钟扫描已启用监控且超过同步间隔的媒体，投递 `media.sync_videos`。
- 会尊重系统暂停和 provider 暂停状态，避免继续放量。

### MCP HTTP 挂载

- 由 [backend/raelyn/main.py](../../backend/raelyn/main.py) 按 `MCP_BASE_PATH=/mcp` 挂载。
- 只有在配置了 `MCP_BEARER_TOKEN` 时才启用；否则主 API 正常启动但不提供 `/mcp`。
- 复用现有 DB / service / job enqueue 能力，不通过 `/api/*` 再套一层 HTTP。

### 静态 UI / PWA

- SPA 构建产物位于 `static/`，源码与模板位于 `ui/`。
- 主要页面为：概览、媒体、视频、播放列表列表、播放列表详情、任务、MCP Server 指南、设置。
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
- `video.extract_audio` 生成音频资产。
- `video.normalize_subtitle` 生成 transcript，并可继续触发 `video.polish_transcript`。
- 若没有可用中文字幕且配置了 ASR，则进入 `video.asr_transcribe`。
- 需要时可手动触发 `video.generate_note` 生成视频级 Markdown 笔记。

### 4. 播放列表聚合与简报

- 播放列表维护媒体集合、周期粒度和提示词。
- 播放列表变更会触发对应周期的简报刷新计划。
- `brief.generate_period` / `brief.generate_daily` 基于粒度聚合视频文本，生成 Markdown 简报并存为 `asset(type=brief)`。

## 文档导航

- 完整文档入口与分类见 [docs/README.md](../README.md)。
