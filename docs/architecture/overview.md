# 架构总览

`raelyn` 是一个基于 `yt-dlp + ffmpeg` 的单用户媒体订阅与视频采集系统，支持 YouTube / B 站媒体同步、批量 / 增量下载、字幕与转写（可选）、播放列表聚合，以及按天生成 Markdown 简报。

约束与决策（来自需求澄清）：

- 单用户（暂不做鉴权 / 审计）
- 媒体数量 `< 1000`
- MVP：不处理需要登录 / 付费内容
- “实时同步”目标：分钟级
- 简报产物：Markdown
- 播放列表：MVP 仅支持“添加媒体”，其余后续扩展
- 任务管理：遵循项目内 skill `skills/job-system-design/` 定义的通用设计原则，包括统一任务存储、Web / Worker 解耦、原子领取、幂等、回收与可观测性

## 基本概念（Domain Model）

### Provider

- `provider`: `youtube` | `bilibili`
- `provider_media_id`: 平台侧频道 / UP 主唯一 ID（不是名称）
- `provider_video_id`: 平台侧视频唯一 ID（YouTube video id / B 站 BV/AV）

### Media（媒体）

表示一个内容源（YouTube 频道 / B 站 UP 主）：

- 可被“同步”以更新头像、简介、订阅量等元数据
- 可被“抓取 / 同步视频列表”以发现新视频

### Video（视频）

表示一个可下载、可播放、可分析的视频条目：

- 只存元数据与处理状态；大文件内容不落地在 DB
- 产物统一通过 Asset 关联

### Asset（产物 / 资源）

统一抽象：视频文件、音频文件、字幕文件、标准化字幕文本、ASR 转写文本、简报等都属于 Asset：

- 内容本体存 MinIO（S3）
- DB 存引用与索引（`s3_key`、类型、格式、语言、校验和等）

建议的 `asset.type`：

- `video`：下载后的视频文件
- `audio`：从视频分离的音频
- `subtitle`：原始字幕文件
- `transcript`：标准化文本，建议同时产出 `json` 分段 + 时间戳 与 `txt` 纯文本
- `brief`：按天简报 Markdown

### Playlist（播放列表）

MVP 中，播放列表仅是“媒体集合”：

- `playlist`：列表本身
- `playlist_media`：列表包含哪些媒体

后续扩展点包括自动同步规则、已播放记录、自动播放最新等。

### Job（任务）

所有重任务（同步媒体、拉取新视频、下载、转写、简报）都以 Job 表示：

- API 只负责“创建 / 查询 / 运维”，不在请求线程执行重活
- Worker 通过 DB 原子领取任务执行
- 任务可拆分父子任务（批量下载为父任务，单视频下载为子任务）

## 总体架构与数据流

### 组件划分

- `api`：推荐使用 FastAPI，提供 HTTP API、静态 UI（`/static`）以及任务创建 / 查询
- `worker`：执行下载、处理、转写、总结等任务，可多进程 / 多实例运行
- `scheduler`：分钟级触发媒体增量同步任务的投递，可与 `api` 合并或独立运行
- 外部依赖：
  - `yt-dlp`（`default + curl_cffi`）负责抓取与下载
  - `ffmpeg` 负责分离音频与转码
  - `PostgreSQL` 保存元数据与任务状态
  - `MinIO` 保存对象内容
  - `qwen3-asr` 作为可选 ASR 服务
  - `LLM` 用于简报 / 笔记，可对接 Ollama 或在线推理

### 关键数据流（MVP）

1. 添加媒体

- UI 通过 API 创建 media
- API 投递 `media.sync_profile` 与 `media.sync_videos`

2. 分钟级增量同步

- `scheduler` 每 N 分钟为需要同步的媒体投递 `media.sync_videos`
- 发现新视频后为每个视频创建或更新 `video` 记录，并可按配置自动投递下载任务

3. 下载与处理

- `video.download`：使用 `yt-dlp` 下载视频与字幕到临时目录
- `video.extract_audio`：使用 `ffmpeg` 分离音频并上传 S3
- `video.normalize_subtitle`：字幕转标准文本并上传 S3
- 若无中文字幕且已配置 ASR，继续投递 `video.asr_transcribe`

4. 简报（按天）

- `brief.generate_daily` 按播放列表和日期聚合当日已就绪的 transcript 或字幕文本
- 调用 LLM 生成 Markdown，上传 S3 并落库

## 文档导航

- [任务系统](job-system.md)
- [数据模型与存储布局](data-model.md)
- [后端模块](backend-modules.md)
- [MCP 集成设计](mcp.md)
- [REST API 设计](../api/rest.md)
- [UI 设计总览](../ui/overview.md)
- [MVP 路线图](../roadmap/mvp.md)
- [配置项说明](../reference/configuration.md)
- [风险与处理](risks.md)
- `docs/adr/` 预留给未来的架构决策记录
