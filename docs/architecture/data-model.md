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

- `playlist` 保存观测域元信息、持续观测状态、简报粒度、简报提示词与头图资产引用；`background_asset_id` 仅保留兼容历史数据和接口，当前产品界面不再写入或消费背景图。
- `playlist_media` 保存播放列表与媒体的多对多关系。

关键字段：

- `avatar_asset_id` / `background_asset_id`
- `observation_enabled`：当前域是否继续生成认知分析与域派生产物；默认启用，停用不删除共享来源记录与资产。
- `brief_granularity`：`day | week | month`
- `brief_prompt`：播放列表级提示词

### `brief` / `brief_reference` / `daily_brief`

当前职责：

- `brief` 是当前主表，保存按 `day / week / month` 聚合后的简报记录。
- `brief_reference` 保存正文锚点到 canonical、story 或 evidence 的结构化引用，使简报与星域对象可以双向导航。
- `daily_brief` 保留用于读取历史日级简报记录。

关键字段：

- `playlist_id`
- `granularity`
- `period_start` 或 `brief_date`
- `status`
- `markdown_asset_id`
- `snapshot_id` / `generation_basis`：简报生成时采用的星域快照与口径。
- `error_message`

约束：

- `brief`：`(playlist_id, granularity, period_start)` 唯一。
- `daily_brief`：`(playlist_id, brief_date)` 唯一。

### `market_event`

当前职责：

- 保存视频级 LLM 原子事件，是知识图谱与事件语义分析的事实源。
- `confidence >= 0.8` 且至少有 1 条 verified provenance 的事件自动进入 `accepted`，低置信或无合法 provenance 的事件先进入 `draft` 供人工查看。

关键字段：

- `event_time_start/end`、`time_precision`：事件发生或覆盖时间；地图时间轴按该区间筛选，无法解析时不进入事件地图。
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
- `market_event_relation` 保存事件内部的 `cause / effect / affects / mentions` 等边，第一版以关系表承载知识图谱，不接外部图数据库。抽取载荷中的 `cause` / `effect` 保持为完整自然语言命题；关系端点由独立的 `source_entity_key` / `target_entity_key` 声明。

关键字段：

- `market_event_evidence.video_id`、`evidence_text`、`evidence_json`、`confidence`。
- `market_event_evidence.evidence_json` 的 v2 provenance 结构为 `schema_version=event_provenance_v1`、`source_id`、`source_kind=title|description|transcript`、`source_label`、`char_start`、`char_end`、`source_sha256`、`verified=true`；`evidence_text` 由后端根据 source map 写入原文段，不直接信任 LLM 生成的证据文本。
- `market_event_entity.entity_type`、`name`、`normalized_key`、`role`、`confidence`。
- `market_event_relation.source_entity_id`、`target_entity_id`、`relation_type`、`direction`、`magnitude`、`confidence`、`evidence_text`。
- 写入关系端点时，后端只按 `source_entity_key` / `target_entity_key` 与同一事件 `market_event_entity.normalized_key` 做精确且唯一的匹配；空键、缺失键、不匹配键或事件内重复键均写成空端点，不使用 `cause` / `effect`、名称相似度或跨事件实体做兜底推断。关系原始载荷仍保留命题和模型给出的键，便于审计。

约束：

- `market_event_evidence`：`(event_id, video_id, evidence_key)` 唯一。
- `market_event_entity`：`(event_id, entity_type, normalized_key, role)` 唯一。

协议升级只作用于后续新分析或显式重新抽取；不会自动重抽、回写或猜补已有历史关系端点。

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

- 基于结构化事件文本生成 embedding，供事件地图 canonical、topic 和投影使用。
- 只对 `accepted` 事件生成；`draft` / `rejected` 不进入地图构建。

关键字段：

- `event_id`、`embedding_model`、`embedding_dim`：embedding 口径。
- `status`：`ready`、`failed`、`skipped_over_budget` 等。
- `vector`：ready 状态下的事件向量。
- `text_checksum`：判断事件结构化文本是否需要刷新 embedding。

约束：

- `(event_id, embedding_model, embedding_dim)` 唯一。

### `event_map_*`

事件地图采用不可变快照，物理表按职责拆分：

- `event_map_state`：播放列表唯一 current ready 指针、`dirty_generation`、`built_generation` 与活跃任务；
- `event_map_snapshot`：输入指纹、embedding/算法版本、布局连续性、边界、对象计数、跳过原因、RSS/临时磁盘峰值和状态；其中 `entity_count` 是快照内不同 `(entity_type, normalized_key)` 的数量，`job_id + execution_token` 标识本次 claim 的 staging snapshot；
- `event_map_record_revision`：冻结事件记录标题、摘要、发生时间、类型、实体、来源和证据；新快照的 transcript 证据同时冻结 `transcript_asset_id` 与片段 `source_sha256`，用于识别转写版本变化；
- `event_map_canonical_identity` / `event_map_canonical` / `event_map_canonical_member` / `event_map_canonical_lineage`：稳定真实事件身份、快照节点、成员归属与 merge/split 延续；
- `event_map_canonical.has_uncertainty`：供场景协议和待审核列表直接读取的热查询布尔列；完整原因仍保存在 `uncertainty_flags`；
- `event_map_canonical_history_revision` / `event_map_canonical_history_member`：不依赖大快照保留期的 canonical 修订正文，以及来源修订到 canonical 的类型化反向索引；
- `event_map_entity_index`：快照原生的实体—canonical 倒排索引；每行保存实体类型、规范键、展示名、固定 `point_index` 和该 canonical 内的底层记录数，实体筛选不再扫描可变的 `market_event_entity` 或 revision JSON；
- `event_map_topic` / `event_map_topic_member`：确定性的一级星域与二级主题团；保存三维中心、包围半径和每层唯一归属，不保存二维 polygon；
- `event_map_story_identity` / `event_map_story` / `event_map_story_member` / `event_map_story_edge`：稳定故事身份、快照版本、有证据的事件图和有向关系；身份保存首次形成后冻结的 `stable_title`、最近实质变化快照 `last_material_snapshot_id` 与时间 `last_material_changed_at`，故事版本保存 `anchor_key`、`story_type`、`maturity` 与 `quality_score`，边保存 evidence revision 列表及关系判定证据 JSON；
- `event_map_story_history_revision` / `event_map_story_history_evidence` / `story_read_state`：跨快照故事修订、锚点/成熟度/质量历史、故事边到来源修订的反向索引、关注状态与最后阅读位置；
- `domain_observation_cursor` / `event_map_change`：观察窗口和按系统认知时间分页的对象变化集；
- `event_map_projection_anchor`：三维布局继承所需的 canonical anchor、x/y/z 与 float32 centroid。

关键约束：

- state 是 current snapshot 的唯一事实源，不能用“最新创建”隐式解析；
- ready snapshot 不再修改，所有子查询必须 pin `snapshot_id`；
- `(job_id, execution_token)` 唯一；同一 job 重领后的新 execution 使用新的 staging snapshot，`job_attempt` 仅保留为审计字段，不作为 staging 身份或执行所有权；
- 每个 record revision 在一个 snapshot 中恰好属于一个 canonical；
- `(snapshot_id, point_index)` 唯一且连续；
- canonical 与 projection anchor 的 x/y/z 全部非空；快照 bounds 同时保存三轴范围；
- 同一 canonical 的同一规范实体只保留一行；`(snapshot_id, point_index, entity_type, normalized_key)` 唯一，并为 `(snapshot_id, entity_type, normalized_key, point_index)` 建立查询索引；
- 每个 canonical 在一级星域和二级主题团各有且仅有一个成员归属；
- 主题下钻与主题—简报反查使用 `(snapshot_id, topic_id, level)` 复合索引，不扫描整个快照成员集；
- story edge 禁止 self-edge，`(snapshot, source, target, relation_type)` 唯一；
- story 身份只能在相同故事算法版本和相同 `anchor_key` 内延续；算法定义升级不会把旧语义身份误接到新故事；
- `event_map_story_identity.stable_title` 在身份创建时写入，后续快照的生成标题不得覆盖；旧身份从最早历史修订标题回填，缺少历史时取最早可用故事标题，active 身份无法得到非空标题时迁移失败并报告；
- `last_material_snapshot_id` 不外键依赖可能被裁剪的快照。`(playlist_id, status, last_material_changed_at)` 支撑阅读队列；只有成员/顺序、关系/判定依据、支持记录、纠正或成熟度等事实结构变化才推进该游标，文案与质量分不推进；
- occurrence interval 只来自 `event_time_start/end`；`available_at` 不进入地图表；
- 原始 embedding 不通过地图 API 传输。

星域主场景只查询上述类型化热列并流式编码固定宽度二进制；长期修订 JSONB 不进入全量显示路径。完整冷热分层见 [V2 观察与存储架构](v2-observation.md)。

旧 `event_regime_*` 与 `event_graph_projection_point` 不属于现行模型，仅可能在显式旧链删除迁移中被识别。

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
- `worker_id` / `execution_token`：前者标识 worker 进程，后者是每次 claim 新生成的 UUID；两者连同 `job_id + running` 状态构成进度、lease 与终态收尾的 CAS 所有权条件
- `parent_job_id`

requeue / reschedule 与所有终态都会清空旧 `execution_token`。失去该 token 所有权的旧执行不得覆盖新执行的 job、事件地图 snapshot 或 `event_map_state`。

### `worker_heartbeat`

当前职责：

- 保存 worker 进程心跳，用于在线状态展示和孤儿任务回收。

关键字段：

- `worker_id`：格式为 `{hostname}:{pid}:{nonce}`
- `role`
- `updated_at`

### `external_service_usage_daily`

ORM 模型为 `ExternalServiceUsageDaily`，用于保存资源用量页所需的外部服务日聚合；它不是站内 HTTP access log。

关键字段：

- `day`：调用发生时间换算到 `Asia/Shanghai` 后的日桶；
- `service`：`llm | asr | embedding`；
- `operation` / `provider` / `model`：调用用途与服务维度；provider 或 model 不适用时保存空字符串，使复合键保持非空；
- `call_count` / `success_count` / `failure_count`：调用总数与成功、失败列聚合；
- `input_tokens` / `output_tokens` / `total_tokens`：LLM usage 聚合，非 LLM 服务保持 `0`；
- `duration_ms`：调用累计耗时；
- `usage_missing_calls`：调用成功或失败但未取得完整 usage 的次数；缺失调用按 0 参与已记录 Token 累计，API 同时返回缺失数供界面说明，不再将包含缺失调用的整个日桶或累计清空；
- `last_called_at`：该维度最近一次实际调用时间。

约束与写入语义：

- `(day, service, operation, provider, model)` 为复合主键；
- 每次真实外部调用完成或失败后，以独立短事务对对应日桶原子累加；成功与失败直接来自调用结果，不从 Job 终态反推；
- 只聚合计数、token 与耗时，不保存请求正文、响应正文、鉴权头、URL 查询参数或站内 `/api/*` 请求；
- 实时采集使用正常 operation；一次性 `system.backfill_legacy_usage` 从终态 `Job.result` 恢复旧 LLM 用量，并从终态 ASR 任务的 `asr request started / succeeded` 事件按 `job_id + attempt` 恢复真实请求。两类历史行都写入 `legacy.*` operation；没有请求事件的旧 ASR 任务不做数量推算。简报与转写润色读取 `llm_usage`，事件抽取读取 `usage`，不再叠加 `VideoEventExtractionRun.usage_json`，避免同一次事件抽取重复计数；
- 历史迁移以 `Asia/Shanghai` 日桶聚合，调用成功 / 失败来自旧 Job 终态，provider 固定为 `legacy`，未持久化的耗时保持 `0` 并由 UI 标明“历史未记录”。每次迁移只删除并重建 `legacy.*` 行，不影响实时行，因此重试结果一致且不会与实时采集冲突。

### `resource_usage_daily`

ORM 模型为 `ResourceUsageDaily`，用于保存每天最后一次资源规模快照。

关键字段：

- `day`：`Asia/Shanghai` 日桶，也是主键；
- `captured_at`：快照实际完成时间；
- `video_count`：快照时仍存在的 `video` 行数；
- `asset_count`：快照时仍存在的 `asset` 行数；
- `asset_size_bytes`：已知 `Asset.size_bytes` 的逻辑总量；
- `asset_missing_size_count`：`size_bytes IS NULL` 的资产数；
- `database_size_bytes`：PostgreSQL `pg_database_size(current_database())`，非 PostgreSQL 为 `null`。

同一天的 `system.capture_usage_snapshot` 可以重复执行；写入按 `day` upsert，只有 `captured_at` 更新的结果才能覆盖当日旧值，因此重试、重复投递或乱序完成不会让快照倒退。Scheduler 每小时只投递任务，实际全库计数与快照写入由 `sync` worker 执行；该表不保存物理磁盘总量或余量。

资源用量接口的当前摘要直接实时查询 `video`、`asset` 和数据库，不依赖该表是否已有当天记录；实测逻辑资产与数据库趋势只读本表。首次快照之前允许通过现存 `Asset.created_at` 与已知 `size_bytes` 按上海日期累计生成逻辑资产估算，单列在 API 的 `asset_size_estimate_bytes` 中；它不是历史实测，不写入本表，也不改变 `resource_sampled`。已删除资产、被替换前的大小与数据库历史占用仍无法还原。

### `app_config`

当前职责：

- 保存运行时可修改配置，不要求重启即可生效。

当前已使用的 key 包括：

- `ytdlp_cookies_youtube`
- `usage.legacy_llm_backfill`：旧 Job LLM 用量与可核验 ASR 请求事件完成一次性回填后的版本、完成时间与分服务汇总；Scheduler 据此停止重复投递迁移任务。
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
- 外部调用日聚合、资源快照与用量查询组装见 [backend/raelyn/services/usage.py](../../backend/raelyn/services/usage.py)。
