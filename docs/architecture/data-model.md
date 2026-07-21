# 数据模型与存储布局

本文记录当前代码中的主要表结构与对象存储约定，重点说明已经实现的字段与职责边界，不再保留早期“建议草案”。

## 数据库主表

### `media`

当前职责：

- 保存 provider / `provider_media_id` / URL。
- 保存媒体资料、头像资产引用、监控开关与同步游标。

关键字段：

- `monitor_enabled`：是否进入分钟级自动同步。
- `avatar_asset_id`：媒体头像对应的 `asset`。
- `sync_cursor`：provider 专属同步游标。
- `last_profile_sync_at` / `last_video_sync_at`：资料 / 视频同步时间。

约束：

- `(provider, provider_media_id)` 唯一。

### `video`

当前职责：

- 保存平台视频元数据、状态、错误信息和原始 `raw_info`。
- 与 `asset` 建立一对多关系。

关键字段：

- `published_at`：平台原始发布时间；不覆盖历史回填出的内容真实日期，也不代表视频产物或文本已就绪。
- `status`：当前下载 / 处理状态，例如 `discovered`、`members_only` 等。
- `raw_info`：保留 provider 返回的原始元数据。

播放列表 / 简报准入口径：

- 播放列表时间轴：优先使用 `video_time_evidence` 选出的 `content_published_at`，缺失时回退 `published_at`，且存在 `asset(type=video)`。
- 播放列表按日期列视频、日历计数和详情统计使用集合化的已选内容时间子查询；不要在这些大范围查询里重复嵌套逐行 `video_time_evidence` scalar subquery。
- 简报周期聚合：优先使用 `content_published_at`，缺失时回退 `published_at`，且存在可用于 transcript 读取的文本资产。

约束：

- `(provider, provider_video_id)` 唯一，视频发现写入以该约束作为幂等锚点。

### `video_time_evidence`

当前职责：

- 保存视频内容时间、事件时间或平台可用时间的候选证据，不覆盖 `video.published_at`。
- 支持 Bloomberg 历史批量上传这类场景：平台上传时间可信，但不等于内容真实日期。
- 同一视频可以有多条不同来源、不同置信度的时间证据，后续解析流程或人工审核再选择 accepted 记录。

关键字段：

- `video_id`：关联视频。
- `time_role`：时间角色，例如 `content_published_at`、`event_time`、`platform_available_at`。
- `source` / `source_version` / `evidence_key`：证据来源、解析器版本与幂等键。
- `date_year` / `date_month` / `date_day`：可保存完整或部分日期；例如 Bloomberg description 只有 `May 12` 时只写月日。
- `time_start` / `time_end` / `precision`：可保存完整时间点或时间区间，以及 `year | month | day | second | range | unknown` 等精度。
- `confidence` / `status`：置信度与审核状态，`status='accepted'` 表示该 `time_role` 当前采用的时间证据。
- `evidence_text` / `evidence_json` / `reliability_flags`：原始证据片段、结构化证据和可靠性标记，例如 `suspected_bulk_upload`、`year_inferred`。

内容时间选择口径：

- 只把完整年月日、`precision in ('day', 'second')` 且 `time_role='content_published_at'` 的证据纳入主时间轴。
- 优先采用 `status='accepted'` 的记录；同一角色最多一条 accepted 由数据库约束保证。
- 没有 accepted 时，只允许可信来源候选进入主时间轴：`source in ('codex_batch_publish_time_inference', 'external_title_search')` 且 `confidence >= 0.8`。
- 排序规则为：accepted 优先、`confidence` 高优先、`updated_at` 新优先、`id` 大优先。
- 对外保留 `published_at` 作为平台时间；查询与分析使用的有效时间记为 `timeline_at = coalesce(content_published_at, published_at)`。

约束：

- `(video_id, time_role, source, evidence_key)` 唯一，用于解析任务幂等写入。
- 同一视频同一 `time_role` 最多一条 `status='accepted'` 记录。

### `asset`

当前职责：

- 统一保存视频文件、音频、字幕、transcript、笔记、简报、播放列表图片等对象的引用。

关键字段：

- `video_id`：视频关联产物；播放列表图片等独立资产可为空。
- `type` / `format` / `language` / `source` / `variant`：区分同一视频下的不同产物。
- `s3_bucket` / `s3_key` / `size_bytes` / `checksum_sha256` / `metadata`：对象存储定位与校验信息。

约束：

- `(video_id, type, format, language, source, variant)` 唯一，用于保证产物幂等写入。

### `playlist` / `playlist_media`

当前职责：

- `playlist` 保存播放列表元信息、简报粒度、简报提示词、头像 / 背景图资产引用。
- `playlist_media` 保存播放列表与媒体的多对多关系。

关键字段：

- `avatar_asset_id` / `background_asset_id`
- `brief_granularity`：`day | week | month`
- `brief_prompt`：播放列表级提示词

### `brief` / `daily_brief`

当前职责：

- `brief` 是当前主表，保存按 `day / week / month` 聚合后的简报记录。
- `daily_brief` 保留用于读取历史日级简报记录。

关键字段：

- `playlist_id`
- `granularity`
- `period_start` 或 `brief_date`
- `status`
- `markdown_asset_id`
- `error_message`

约束：

- `brief`：`(playlist_id, granularity, period_start)` 唯一。
- `daily_brief`：`(playlist_id, brief_date)` 唯一。

### `market_event`

当前职责：

- 保存视频级 LLM 原子事件，是知识图谱与事件语义分析的事实源。
- `confidence >= 0.8` 且至少有 1 条 verified provenance 的事件自动进入 `accepted`，低置信或无合法 provenance 的事件先进入 `draft` 供人工查看。

关键字段：

- `event_time_start/end`、`time_precision`：事件目标时间；语义时间轴按 `event_time_start` 聚合，无法解析时不进入语义快照。
- `available_at`：事件在系统中最早可被观察到的时间，仅用于溯源与导出审计，不作为事件发生时间或语义信号周期。
- `event_type`、`title`、`summary`、`direction`、`magnitude`、`surprise_or_delta`：事件语义与强度。
- `title` / `summary` 应保留具体市场对象；例如房地产、住房、楼市语境不能只写“市场”，必须与实体、资产、行业标签保持同一对象口径。
- `status`：`accepted | draft | rejected`。
- `source_video_id`、`transcript_asset_id`、`extraction_model`、`prompt_version`、`source_hash`、`raw_payload`：抽取依据与复算口径。

约束：

- `(source_video_id, source_hash, prompt_version, event_key)` 唯一。

### `market_event_evidence` / `market_event_entity` / `market_event_relation`

当前职责：

- `market_event_evidence` 保存事件到视频标题、描述或 transcript source 的可验证证据引用与证据文本。
- `market_event_entity` 保存实体、资产、行业与宏观变量节点。
- `market_event_relation` 保存事件内部的 `cause / effect / affects / mentions` 等边，第一版以关系表承载知识图谱，不接外部图数据库。

关键字段：

- `market_event_evidence.video_id`、`evidence_text`、`evidence_json`、`confidence`。
- `market_event_evidence.evidence_json` 的 v2 provenance 结构为 `schema_version=event_provenance_v1`、`source_id`、`source_kind=title|description|transcript`、`source_label`、`char_start`、`char_end`、`source_sha256`、`verified=true`；`evidence_text` 由后端根据 source map 写入原文段，不直接信任 LLM 生成的证据文本。
- `market_event_entity.entity_type`、`name`、`normalized_key`、`role`、`confidence`。
- `market_event_relation.source_entity_id`、`target_entity_id`、`relation_type`、`direction`、`magnitude`、`confidence`、`evidence_text`。

约束：

- `market_event_evidence`：`(event_id, video_id, evidence_key)` 唯一。
- `market_event_entity`：`(event_id, entity_type, normalized_key, role)` 唯一。

### `video_event_extraction_run`

当前职责：

- 记录单个视频在某个 `source_hash / prompt_version / extraction_model` 口径下的事件抽取运行结果。
- `force=false` 命中 `status=succeeded` 时跳过重复抽取；即使 `event_count=0` 也表示该视频已经按当前口径完成抽取。

关键字段：

- `video_id`、`transcript_asset_id`：运行对应的视频和 transcript 资产。
- `source_hash`：视频标题、描述、媒体名、发布时间、内容时间、transcript asset 与 transcript sha256 的复算口径。
- `prompt_version`、`extraction_model`：抽取提示词和模型口径。
- `status`：`succeeded | failed`。
- `event_count`、`warning_count`、`usage_json`、`error_message`：抽取结果与排障信息。

约束：

- `(video_id, source_hash, prompt_version, extraction_model)` 唯一。

### `market_event_embedding`

当前职责：

- 基于结构化事件文本生成 embedding，供事件图谱与语义分析使用。
- 只对 `accepted` 事件生成；`draft` / `rejected` 不进入自动分析。

关键字段：

- `event_id`、`embedding_model`、`embedding_dim`：embedding 口径。
- `status`：`ready`、`failed`、`skipped_over_budget` 等。
- `vector`：ready 状态下的事件向量。
- `text_checksum`：判断事件结构化文本是否需要刷新 embedding。

约束：

- `(event_id, embedding_model, embedding_dim)` 唯一。

### `event_regime_run` / `event_regime_signal` / `event_regime_candidate`（兼容命名）

当前职责：

- `event_regime_run` 保存一次播放列表语义快照的状态、embedding 口径与覆盖率。
- `event_regime_signal` 保存 day / week / month 多尺度事件 embedding 语义信号。
- `event_regime_candidate` 保存语义变化点和人工状态。
- `event_regime_state` 保存播放列表级 dirty 状态与最新 ready run。
- 这些表名属于历史兼容标识；产品与 UI 不使用 Regime 概念。

关键字段：

- `event_regime_signal.granularity`：`day | week | month`。
- `event_regime_signal.period_date`：按事件 `event_time_start` 与 `time_precision` 归属后的周期起点；年精度事件不伪装成 1 月 1 日的日/月事件。
- `event_count`、`ready_embedding_count`：当前周期事件数和 ready embedding 数。
- `drift_score`、`drift_rolling_mean/std/z`：事件语义中心漂移及其 rolling z。
- `dispersion_mean/std/p25/p75`：同一 period 内部事件 embedding 分散度。
- `linked_candidate_id`：该 signal period 命中的语义变化点。
- `event_regime_candidate.candidate_date` / `event_start` / `event_end` / `peak_date`：候选日期与区间。
- `evidence_event_ids` / `evidence_video_ids` / `evidence_json`：候选解释与证据事件。
- `available_at`：候选窗口中最早可观察证据时间。

约束：

- `event_regime_signal`：`(regime_run_id, granularity, period_date, rolling_window)` 唯一。
- `event_regime_candidate`：`(regime_run_id, candidate_date)` 唯一。

### `job` / `job_event`

当前职责：

- `job` 保存异步任务状态、参数、结果、租约和重试信息。
- `job_event` 保存任务过程事件，供 UI / 排障使用。

关键字段：

- `dedupe_key`：任务幂等与去重。
- `params` / `result`
- `progress_current` / `progress_total`
- `attempt` / `max_attempts`
- `scheduled_for` / `started_at` / `finished_at` / `lease_expires_at`
- `parent_job_id`

### `worker_heartbeat`

当前职责：

- 保存 worker 进程心跳，用于在线状态展示和孤儿任务回收。

关键字段：

- `worker_id`：格式为 `{hostname}:{pid}:{nonce}`
- `role`
- `updated_at`

### `app_config`

当前职责：

- 保存运行时可修改配置，不要求重启即可生效。

当前已使用的 key 包括：

- `ytdlp_cookies_youtube`
- `ytdlp_cookies_bilibili`
- `ytdlp_subtitles`
- `ytdlp_members_only`
- `ytdlp_format`
- `llm_transcript_polish_prompt`
- `brief_generation_policy`

## 当前对象存储布局

### Bucket

- 默认统一使用一个 bucket，例如 `raelyn`。
- 启动时会自动确保 bucket 存在。

### 典型 Key 布局

当前实现没有把所有 key 路径完全抽象成单一模板，但实际会收敛到以下几类前缀：

```text
{provider}/{media_id}/{video_id}/...
media/{media_id}/...
playlist/{playlist_id}/avatar.{ext}
playlist/{playlist_id}/background.{ext}
```

说明：

- 视频、音频、字幕、transcript、缩略图等视频级产物围绕 `video_id` 写入。
- 媒体删除时会尝试清理 `{provider}/{media.id}/` 和 `media/{media.id}/` 前缀。
- 播放列表图片使用 `playlist/{playlist_id}/...` 前缀。

## 当前索引与约束重点

- `media(provider, provider_media_id)` 唯一：媒体创建幂等。
- `video(provider, provider_video_id)` 唯一：视频发现幂等。
- `asset(...)` 唯一：产物写入幂等。
- `brief(...)` / `daily_brief(...)` 唯一：避免重复简报记录。
- `job` 依赖状态 / 类型聚合、活动列表排序、`pending` claim 顺序、`finished_at` 时间窗、`running` 租约 / worker 回收等索引支撑任务页实时刷新和 worker 执行面。

## 与代码的对应关系

- ORM 定义见 [backend/raelyn/models.py](../../backend/raelyn/models.py)。
- 初始化与兼容迁移见 [backend/raelyn/db.py](../../backend/raelyn/db.py)。
- 对象存储写入与 presign 见 [backend/raelyn/services/s3.py](../../backend/raelyn/services/s3.py)。
