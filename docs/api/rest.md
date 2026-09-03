# REST API 设计

本文记录当前已经实现的 HTTP / WebSocket 接口形态，不再保留 MVP 阶段的候选设计。

## 通用约定

- 绝大多数接口直接返回资源 JSON，不额外包一层统一 envelope。
- 只有少数配置接口返回 `{data: ...}` 或 `{ok: true}` 形式。
- 时间字段使用 ISO8601。
- 当 `API_BEARER_TOKEN` 非空时：
  - `/api/*` 需要 `Authorization: Bearer <token>`，同源浏览器请求也可使用 `raelyn_api_token` cookie。
  - `/api/ws/*` 需要 query `token=<token>`。

## Health / System / Runtime

### `GET /api/health`

- 返回 DB、S3、ASR、Embedding、LLM 的健康状态。
- `deps_ok` 表示运行主链路是否整体可用；ASR / Embedding / LLM 未配置时不计为依赖失败，已配置但健康检查失败时计为依赖失败。

### `GET /api/system`

- 返回系统暂停状态、provider 暂停状态和资产分发策略。
- `asset_delivery` 包含 `strategy`、`direct_probe_url`、`proxy_base_path`、`presign_enabled` 等前端运行参数；`presign_enabled=false` 时 `strategy=proxy`，前端不做对象存储直连探测。

### `POST /api/system/pause`

- body：`{ "reason": "...", "message": "..." }`
- 人工暂停系统，后续调度 / 任务入口可据此拒绝继续执行。

### `POST /api/system/resume`

- 清除系统暂停状态。

### `POST /api/system/require_running`

- 若系统处于暂停状态，返回 `409`。

### `GET /api/workers`

- 返回 worker 在线状态、角色分布、最近心跳时间，以及 worker 角色暂停信息。
- `roles[*]` 额外包含：
  - `paused`
  - `pause_reason`
  - `pause_message`
  - `pause_set_at`
  - `controllable`

### `POST /api/workers/roles/{role}/pause`

- body：`{ "reason"?: "...", "message"?: "..." }`
- 人工暂停某个具体 worker 角色继续领取新任务。
- 只影响新的 claim；已经 `running` 的任务继续执行。
- `role=all` 或未知角色返回 `400`。

### `POST /api/workers/roles/{role}/resume`

- 恢复某个具体 worker 角色继续领取新任务。
- `role=all` 或未知角色返回 `400`。

### `GET /api/stats`

- 返回概览页统计数据、最近媒体 / 视频 / 播放列表，以及 ASR / LLM 使用量。
- 默认使用短 TTL 缓存，避免普通页面刷新反复扫描 `job` / `asset` 等大表。
- query：`refresh=true` 可绕过缓存重新计算。
- `database_size_bytes` 返回 PostgreSQL 当前数据库的总占用，口径为 `pg_database_size(current_database())`，包含表、索引和 TOAST；非 PostgreSQL 数据库返回 `null`。
- 资产容量统计会优先使用配置的 `S3_BUCKET`；若资产表中没有该 bucket 且只存在一个实际 bucket，则返回实际 bucket 的统计，并通过 `s3_configured_bucket_mismatch` 标记配置漂移。

### `GET /api/usage`

- 供独立“资源用量”页面读取全局当前摘要、按日趋势与构成明细；接口只读，不在请求线程创建资源快照或投递任务。
- query：`days=7|30|90`，默认 `30`；其他值返回 `422`。`window` 始终包含今天，所有日桶固定按 `Asia/Shanghai` 切分，当天桶可能仍在采样。
- 响应顶层固定包含：
  - `generated_at`：本次响应生成时间；
  - `timezone="Asia/Shanghai"`；
  - `window`：`days/start/end`；
  - `summary`：当前规模与所选窗口调用 / 新增汇总；
  - `series`：从 `start` 到 `end` 的逐日序列；
  - `service_breakdown`：外部服务维度明细；
  - `asset_breakdown`：当前逻辑资产构成；
  - `collection`：采集起点、最近采样与物理磁盘接入状态。
- `summary` 在每次 GET 中实时读取当前仍存在的来源记录数 `video_count`、具有视频资产的去重视频数 `downloaded_video_count`、资产数量、已知 `Asset.size_bytes` 之和、缺少 `size_bytes` 的资产数和数据库规模；`video_downloaded` 统计窗口内真实成功完成的视频下载次数。数据库规模仅在 PostgreSQL 下使用 `pg_database_size(current_database())`，包含表、索引和 TOAST；非 PostgreSQL 返回 `null`。
- `summary.external_calls` 只汇总 LLM、ASR、Embedding 三类外部服务调用，不统计浏览器、前端刷新或站内 `/api/*` HTTP。`summary.llm_calls`、`summary.asr_calls`、`summary.embedding_calls` 提供三类服务的独立口径；三类服务分别以自己的第一条聚合记录作为采集起点，尚未开始采集的服务返回 `null`，不会因为另一类服务已有数据而伪报为 `0`。`llm_input_tokens/llm_output_tokens/llm_total_tokens` 只统计 LLM。窗口没有 LLM 采集覆盖，或覆盖内存在无法取得 usage、因而无法形成完整 token 总数的调用时，相应 token 汇总返回 `null`，不把已知部分冒充完整值。`series[]` 同样逐日返回 `external_calls`、`llm_calls`、`asr_calls` 与 `embedding_calls`。
- `series[]` 每项包含 `date`、`video_downloaded`、`external_calls`、`llm_calls`、`llm_input_tokens`、`llm_output_tokens`、`llm_total_tokens`、`asset_size_bytes`、`database_size_bytes`、`usage_sampled` 和 `resource_sampled`。`video_downloaded` 只统计 `video.download|video.download.youtube|video.download.bilibili` 中状态为 `succeeded`、具有结果且结果不是 `skipped` 或 `rescheduled` 的任务，并按 `finished_at` 落入 `Asia/Shanghai` 日桶；强制重新下载成功会作为一次真实下载计入。服务调用从 `ExternalServiceUsageDaily` 读取，逻辑资产与数据库历史只从 `ResourceUsageDaily` 快照读取，不以当前值反推过去。
- 未开始采集或缺失采样日桶中的调用字段、逻辑资产和数据库字段以 `null` 返回，不补成 `0`；调用分类字段按 LLM、ASR、Embedding 各自的采集起点独立判断，`usage_sampled` 只表示当日至少有一类外部服务已进入采集覆盖，`resource_sampled` 表示当日存在资源快照。只有确定获得相应服务或资源的采集覆盖且实测为零时才返回数值 `0`。`video_downloaded` 不依赖采样覆盖，没有成功下载时返回 `0`。当天存在快照、下载任务或调用聚合也只代表截至当前采样时刻的部分日数据。
- `service_breakdown[]` 按 `service/operation/provider/model` 聚合所选窗口，返回 `calls`、`successes`、`failures`、`input_tokens`、`output_tokens`、`total_tokens`、`duration_ms`、`usage_missing_calls` 和 `last_called_at`。`service` 当前只允许 `llm|asr|embedding`；实时采集的成功与失败直接来自调用结果，不从最终 Job 状态倒推。一次性历史迁移生成的 `legacy.*` 行是明确例外：LLM 使用旧 Job 终态恢复结果，ASR 使用请求开始 / 成功事件按任务与尝试次数配对；两者都不虚构旧记录未完整保存的耗时。
- `asset_breakdown[]` 按 `Asset.type` 返回 `asset_type/count/size_bytes/missing_size_count`；`size_bytes` 是数据库记录的逻辑资产量，不代表 S3/MinIO 所在主机的文件系统占用。
- `collection` 返回 `usage_started_at`、`last_usage_at`、`resource_started_at`、`last_resource_snapshot_at`、`physical_storage_available` 和 `physical_storage_reason`。当前没有跨 Docker、本机与远端对象存储都可靠的物理磁盘容量来源，因此 `physical_storage_available=false`、原因明确为“未接入部署侧指标”，接口不返回伪造的磁盘总量、余量或百分比。
- 历史用量由一次性任务 `system.backfill_legacy_usage` 恢复。LLM 从旧 `Job.result` 读取：简报与转写润色使用 `llm_usage`，事件抽取使用 `usage`；`VideoEventExtractionRun.usage_json` 与 Job 结果存在重复，因此不作为第二数据源。ASR 只回填带有 `asr request started` 的真实请求事件，并用同一任务、同一 `attempt` 的 `asr request succeeded` 判断成功；事件日志产生前的旧任务无法证明是否实际发起调用，因此不从任务数量推算。迁移行使用独立 `legacy.*` operation，可重复精确重建且不会覆盖实时采集行。

## Media

### `POST /api/media`

- body：`{ "url": "...", "provider": "youtube|bilibili"(optional) }`
- 行为：
  - 从 URL 识别 provider 与 `provider_media_id`
  - upsert `media`
  - 默认 `monitor_enabled=false`
  - 仅投递 `media.sync_profile`

### `GET /api/media`

- query：`provider`、`q`、`limit`、`offset`、`presign`
- 返回媒体列表与本地视频数；视频数只按当前分页返回的媒体 ID 统计，避免列表接口扫描整张视频表。
- `presign=false` 时，`avatar_asset` 只返回资产标识等基础字段，不生成 S3 预签名 URL；前端代理模式会通过 `/api/assets/{asset_id}/content` 读取头像。
- 每条媒体额外包含：
  - `deleting`：是否存在活跃 `media.delete` 任务
  - `deletion_job_id`：当前删除任务 ID，便于前端恢复轮询状态
  - `disabled_reason` / `disabled_message` / `disabled_at`：系统自动停用媒体时的原因说明；例如 YouTube 频道返回 404 时会标记为 `source_unavailable`

### `GET /api/media/options`

- query：`provider`、`q`、`limit`、`offset`
- 返回播放列表创建 / 编辑、视频筛选等选择器需要的轻量媒体选项。
- 不计算本地视频数、不生成头像资产引用，也不扫描活跃删除任务。

### `GET /api/media/export`

- 导出当前媒体 URL 列表，返回纯文本。

### `POST /api/media/import`

- body：`{ "text": "..." }`
- 按行导入媒体 URL，支持跳过空行与注释行。
- 返回新增、已存在、无效项和输入内重复项统计。

### `GET /api/media/{media_id}`

- query：`presign`
- 返回单个媒体详情，字段同媒体列表。

### `PATCH /api/media/{media_id}`

- 当前主要用于更新 `monitor_enabled`。
- 关闭监控时会清理该媒体待执行的下载任务。
- 若媒体正在删除中，返回 `409`。

### `DELETE /api/media/{media_id}`

- 异步提交 `media.delete` 任务，返回 `202 Accepted`。
- 返回：`job_id`、`job_type="media.delete"`、`media_id`、`status="accepted"`、`reused`。
- 删除任务会：
  - 删除相关 `pending` 媒体/视频/摘要任务
  - 对相关 `running` 任务发起协作式取消请求并等待退出
  - 删除媒体及其级联数据
  - best-effort 清理相关 S3 前缀
  - 重新调度受影响摘要周期；若周期已无视频，则将摘要置为 `empty`

### `POST /api/media/sync`

- query：`scope=recent|all`
- 只对已启用监控的媒体批量投递同步任务。
- `scope=recent`：同步近期视频；新发现视频会按配置自动投递下载。
- `scope=all`：全量发现历史视频，并给库里已发现但尚未下载成功的历史视频补投下载。

### `POST /api/media/{media_id}/sync`

- query：`scope=recent|all`
- 投递单个媒体的资料同步和视频同步任务。
- `scope=recent`：只处理近期窗口。
- `scope=all`：除了全量发现历史视频，还会补投该媒体下仍处于待下载状态的历史视频。
- 若媒体正在删除中，返回 `409`。

### `GET /api/cleanup/stale-videos`

- query：`limit`
- 扫描“媒体已关闭监控、视频仍为 `discovered`、且没有活跃下载任务或视频资产”的遗留视频记录。

### `POST /api/cleanup/stale-videos`

- 清理全部符合条件的遗留视频记录。

## Videos / Assets

### `GET /api/videos`

- query：
  - `provider`
  - `domain_id`：可选的观测域基础范围；只返回该观测域已关联媒体下的视频。观测域不存在时返回 `404`。
  - `media_id`
  - `media_id_in`：用户显式选择的媒体子集；与 `domain_id` 同时传入时取交集，不替代观测域范围。
  - `status`
  - `q`
  - `published_since`
  - `published_until`
  - `time_basis`：`content | platform`，默认 `content`。`content` 使用 `video_time_evidence` 中已采纳或高置信候选的 `content_published_at`，没有证据时回退到 `video.published_at`；`platform` 仅使用平台原始发布时间。
  - `limit`
  - `offset`
- 返回视频列表、媒体名称、媒体头像、缩略图资产和视频资产引用。
- 时间字段：
  - `published_at`：平台原始发布时间。
  - `content_published_at`：内容真实发布时间证据，可能为空。
  - `timeline_at`：本次查询和排序使用的时间。
  - `time_source` / `time_status` / `time_confidence`：`timeline_at` 的来源、审核状态与置信度；平台回退时 `time_status=platform_fallback`。

### `GET /api/videos/{video_id}`

- 返回单个视频详情。

### `POST /api/videos/{video_id}/download`

- 投递下载任务。
- 若已存在同一视频的活跃下载任务，则不重复创建，而是复用该任务；若其仍为 `pending`，会把优先级提升到 `10`。
- 可能返回 `409`，例如重复下载或当前状态不允许下载。
- 若所属媒体正在删除中，也会返回 `409`。

### `GET /api/videos/{video_id}/transcript`

- query：`max_chars`
- 返回当前视频最佳可用 transcript。

### `POST /api/videos/{video_id}/transcript/retranscribe`

- 重新投递转写 / 处理链路。
- 若所属媒体正在删除中，返回 `409`。

### `GET /api/videos/{video_id}/assets`

- query：`presign`、`download`、`localize_title`
- 返回该视频的全部资产列表，可包含 presigned URL 和下载文件名。
- 当 `ASSET_PRESIGN_ENABLED=false` 时，即使 query 传 `presign=true`，响应也不会包含 presigned URL；前端应使用 `/api/assets/{asset_id}/content` 或 `/download` 代理地址。
- `localize_title` 默认 `false`；只有显式传 `true` 时才会额外调用 yt-dlp 尝试补充 YouTube 本地化标题，避免详情页资产加载被外部视频站请求拖慢。

### `GET /api/assets/{asset_id}`

- 返回单个资产元信息与 `asset_ref`。

### `GET /api/assets/{asset_id}/content`

- API 代理流式读取资产，支持 `Range`。

### `GET /api/assets/{asset_id}/download`

- API 代理下载资产，支持 `Range` 和 `Content-Disposition`。

## Jobs / Realtime

### `GET /api/jobs`

- query：
  - `status` / `status_in`
  - `type`
  - `created_since` / `created_until`
  - `finished_since` / `finished_until`
  - `limit` / `offset`

### `GET /api/jobs/counts`

- query：
  - `status` / `status_in`
  - `type` / `type_in`
  - `created_since` / `created_until`
  - `finished_since` / `finished_until`
- 响应示例：`{ "counts": { "pending": 12, "running": 3 }, "total": 15 }`

### `GET /api/jobs/type_counts`

- query：
  - `status` / `status_in`
  - `type` / `type_in`
  - `created_since` / `created_until`
  - `finished_since` / `finished_until`
- 按任务类型聚合数量与占比；Jobs 活动页使用 `status_in=pending,running` 展示待处理 / 运行中任务构成。
- 响应示例：`{ "counts": { "pending": 12, "running": 3 }, "total": 15, "items": [{ "type": "video.asr_transcribe", "count": 10, "counts": { "pending": 9, "running": 1 }, "percentage": 66.67 }] }`

### `GET /api/jobs/series`

- query：
  - `since`
  - `until`
  - `status_in`
  - `type` / `type_in`
  - `ts_field=created_at|started_at|finished_at|scheduled_for`
  - `bucket=minute|hour|day`

### `GET /api/jobs/{job_id}`

- 返回任务详情，包含 `error_stack`。

### `GET /api/jobs/{job_id}/events`

- 返回任务事件流（最新在前）。

### `POST /api/jobs/{job_id}/cancel`

- 取消单个任务。
- `pending` 任务会立即转为 `canceled`。
- `running` 任务会写入取消请求，由 handler 在安全检查点协作式退出后转为 `canceled`。

### `POST /api/jobs/{job_id}/retry`

- 将已完成 / 失败任务重置为 `pending` 并重新调度。
- 若同一 `dedupe_key` 已存在其他 `pending` 任务，手动重试会先将该 pending 任务标记为 `canceled`，再重置当前任务，避免重复 pending 任务触发唯一约束冲突。

### `POST /api/jobs/cancel_active`

- 批量取消所有 `pending` / `running` 任务。

### `POST /api/jobs/delete_failed`

- 按 `type_in`、`error_contains`、时间范围等条件批量删除失败任务。

### `WS /api/ws/jobs`

- 实时推送任务列表快照。
- 支持 `status_in`、`type_in`、`type`、`finished_since`、`finished_until` 等 query。

### `WS /api/ws/job_stats`

- 实时推送任务统计，例如 `pending`、`running`、`succeeded_24h`、`failed`。

## Playlists / Briefs

### `POST /api/playlists`

- body：`{ "name": "...", "description": "...", "media_ids": [...] }`
- 支持创建时直接附带媒体集合。

### `GET /api/playlists`

- 返回播放列表概览，包括媒体预览、视频数、时间范围、头像 / 背景资产引用。

### `GET /api/playlists/{playlist_id}`

- 返回基础播放列表详情。

### `GET /api/playlists/{playlist_id}/detail`

- 返回完整详情，包括全部媒体列表和 `brief_prompt`。
- `media_preview` 用于首屏头像预览；`media` 保留完整媒体列表的基础信息，不要求为每个媒体都生成头像预签名 URL。

### `PATCH /api/playlists/{playlist_id}`

- 可更新：
  - `name`
  - `description`
  - `brief_granularity`
  - `brief_prompt`

### `DELETE /api/playlists/{playlist_id}`

- 旧兼容删除入口；与观测域安全删除复用同一底层删除服务。
- 若观测域仍有 `pending / running / cancel_requested` 任务则返回 `409`，不会自动取消任务。
- V2 UI 使用 `DELETE /api/domains/{domain_id}`，要求显式提交完整域名确认。

### `POST /api/playlists/{playlist_id}/media`

- body：`{ "media_id": "..." }`
- 旧兼容关联入口；向播放列表追加一个媒体。V2 UI 使用观测域信源接口。

### `DELETE /api/playlists/{playlist_id}/media/{media_id}`

- 旧兼容解除关联入口；只移除播放列表内的媒体关联，不删除全局媒体、来源记录或资产。V2 UI 使用观测域信源接口。
- 该变更会触发受影响周期的摘要刷新；若某周期已无视频，则对应摘要状态会变为 `empty`。

### `PUT /api/playlists/{playlist_id}/media`

- body：`{ "media_ids": [...] }`
- 原子替换播放列表媒体集合。

### `POST /api/playlists/{playlist_id}/avatar`

- multipart 上传头像，支持 `jpg/png/webp`。

### `POST /api/playlists/{playlist_id}/background`

- multipart 上传背景图，支持 `jpg/png/webp`。

### `DELETE /api/playlists/{playlist_id}/background`

- 清除背景图。

### `GET /api/playlists/{playlist_id}/videos_by_date`

- query：`date=YYYY-MM-DD`、`time_basis=content|platform`（默认 `content`）、`limit`（最大 500）
- 返回指定本地日期的可播放视频列表；默认按内容时间轴归属，缺少内容时间证据时回退到平台发布时间，且仅包含已落视频资产的视频。

### `GET /api/playlists/{playlist_id}/video_counts_by_period`

- query：`granularity`、`start`、`end`、`time_basis=content|platform`（默认 `content`）
- 返回可播放视频的周期计数，用于日 / 周 / 月时间轴；默认按内容时间轴聚合。

### `GET /api/playlists/{playlist_id}/videos_by_period`

- query：`granularity`、`date`、`limit`、`time_basis=content|platform`（默认 `content`）
- 返回某个周期内的可播放视频列表；默认按内容时间轴归属，且仅包含已落视频资产的视频。
- V2 播放列表固定传入 `granularity=day&time_basis=content&limit=500`，不受简报日 / 周 / 月粒度影响；达到上限时由界面显示截断提示。

### `POST /api/playlists/{playlist_id}/events/extract`

- 手动创建 `playlist.backfill_events` 父任务；父任务按播放列表内容时间轴拆分月份范围并投递 `playlist.backfill_events_range` 子任务，范围任务运行时再查询该月内已有 `plain` transcript 的视频并投递 `video.extract_events_batch` 或 `video.extract_events` 子任务。批量抽取任务执行时每次 LLM 请求只包含 1 个视频，避免多个视频共用一个大 JSON 生成导致本地模型长时间无返回。
- 事件抽取在 Ollama `/api/generate` 模式下使用 JSON 输出约束、低温度采样、流式读取、全局统一的 `LLM_OLLAMA_NUM_CTX`、事件专用 `num_predict` 上限和 per-model advisory lock，降低标题、实体和证据之间的结构化抽取漂移，并避免同一 Ollama 大模型被多个事件抽取请求同时压满。当前 v4 口径要求 LLM 只返回 `evidence_source_ids`，后端用 source map 写入可验证证据，并显式声明关系端点。
- 月份范围任务的优先级低于它投递的视频事件抽取任务；同一批回填中，一旦 `video.extract_events_batch` 或 `video.extract_events` 入队，worker 会优先消费事件抽取，再继续领取后续月份范围任务。
- body：`{ "force": false }`；`force=false` 只补齐缺失当前 transcript / prompt / model 口径事件的视频，`force=true` 会先取消当前播放列表相关的活跃事件抽取、事件 embedding 与事件地图构建任务，再重新抽取同一 prompt / model 口径下的视频事件。
- `force=true` 的任务清理只取消 `pending/running` 任务并保留历史记录；地图 state 保持 dirty，现有 ready 快照不因重抽被提前切走。
- 返回任务 ID、任务状态与进度。新 transcript 生成后也可由 `AUTO_EXTRACT_NEW_VIDEO_EVENTS=true` 自动投递单视频抽取。

### `GET /api/playlists/{playlist_id}/events/summary`

- 返回事件抽取覆盖率与状态计数：`video_total`、`video_with_events`、`event_total`、`accepted`、`draft`、`rejected`、`coverage_ratio`。
- query：可选 `period_start=YYYY-MM-DD`、`granularity=day|week|month`；传入后按事件 `available_at` 过滤到对应可观察周期。未传时返回播放列表全量。
- 返回当前活跃的历史抽取任务 `backfill_job`，便于 UI 展示扫描 / 投递进度；其中 `range_finished`、`range_pending`、`range_running`、`range_failed`、`range_total` 用于展示月份范围任务进度，`scanned`、`enqueued`、`skipped` 是已执行月份范围任务累计扫描 / 投递 / 跳过的视频数，`video_extracted`、`video_pending`、`video_running`、`video_failed`、`video_total` 用于展示已投递视频抽取子任务进度，`elapsed_seconds` 与 `estimated_total_seconds` 用于展示已运行时间和预估总时长。若还有月份范围未完成，`estimated_total_seconds` 会按已完成月份的平均投递视频数估算未扫描月份的未来视频抽取工作；样本不足时返回 `null`，前端显示估算中。

### `GET /api/playlists/{playlist_id}/events`

- query：可选 `status=accepted|draft|rejected`、`event_type`、`entity`、`min_confidence`、`period_start=YYYY-MM-DD`、`granularity=day|week|month`、`limit`、`offset`。
- 返回播放列表内的视频级 LLM 原子事件，默认按 `available_at` 倒序排列，同一可观察时间内再按 `event_time_start` 倒序排列。
- 关键字段：`event_time_start/end`、`time_precision`、`available_at`、`event_type`、`title`、`summary`、`direction`、`magnitude`、`surprise_or_delta`、`confidence`、`status`、`source_video_id`、`evidence_count`、`provenance_status`、`source_video_title`、`source_media_name`、`entities`。

### `GET /api/playlists/{playlist_id}/events/entities`

- query：可选 `q`、`status=accepted|draft|rejected`、`period_start=YYYY-MM-DD`、`granularity=day|week|month`、`limit`。
- 返回当前播放列表事件实体推荐，用于事件审核页实体过滤自动补全；传入周期参数时按事件 `available_at` 限定到当前可观察周期。
- 返回字段：`entity_type`、`name`、`normalized_key`、`count`，其中 `count` 是命中该实体的事件数。

### `GET /api/playlists/{playlist_id}/events/{event_id}`

- 返回单个事件详情，包含实体、证据视频文本、事件内 cause/effect/affects/mentions 边与原始抽取载荷。新抽取关系的 `cause` / `effect` 自然语言命题保留在原始载荷；边的实体 ID 只由显式 `source_entity_key` / `target_entity_key` 精确解析，不由命题文本推断。
- `evidence[]` 额外返回 `source_id`、`source_kind`、`source_label`、`verified`；v2 证据按标题、描述、转写片段定位，`verified=true` 表示 source id 已命中当前请求 source map 且写入 source sha256。

### `PATCH /api/playlists/{playlist_id}/events/{event_id}`

- body：`{ "status": "accepted|draft|rejected" }`。
- 将低置信或人工审核事件改为 accepted 后，会投递 `event.embed` 并标记播放列表事件地图 dirty；rejected/draft 不进入地图构建。

### `GET /api/playlists/{playlist_id}/events/map/manifest`

- query 可选 `snapshot_id`；省略时解析 `event_map_state.current_snapshot_id`，显式指定时读取仍可用且属于该播放列表的历史 ready 快照，并以 `is_current=false` 标记。无效的显式快照返回 `409`，不会静默回退到 current。
- query 可选 `compact=true`，供构建状态轮询和星域首帧启动使用；该模式跳过主题标签、关键词、锚点等完整元数据，但仍返回 scene 协议、记录长度、坐标边界、稳定语义色映射、快照已物化的 `monthly_distribution[]` 和轻量 `topic_geometry[]`。首帧使用该月度分布直接确定最终的 12 个月窗口；`topic_geometry[]` 只含主题索引、层级、中心、半径和 canonical 数量，用于直接创建最终 WebGL 语义网格，不承担标签展示。
- compact 模式的 `backfill_job` 只返回前端状态展示和轮询所需的 `job_id/status/progress_current/progress_total/created_at/started_at/cancel_requested_at`，不扫描并汇总整个 range/video 子任务树；完整 manifest 仍返回原有的详细 backfill 进度。
- query 可选 `include_coverage=true|false`。省略时完整 manifest 默认计算覆盖率、compact manifest 默认跳过覆盖率；首屏使用 `include_coverage=false` 取得主题与标签元数据，交互开放后再以 `compact=true&include_coverage=true` 后台补取覆盖计数。
- 返回构建/dirty generation、当前 ready snapshot、`dimension=3`、场景协议版本、canonical/record/topic/story/entity 数量、三维固定坐标边界、类别映射、两级主题中心/半径/父子索引、主题代表事件的 `anchor_canonical_id/anchor_point_index/anchor_title`、按事件覆盖区间计算的月度 canonical/record 分布、时间边界和峰值 RSS。
- 完整 manifest 的 `semantic_families[]` 给出稳定语义族的 `code/label/color`；每个 `type_categories[]` 项通过 `semantic_family/semantic_family_label/semantic_color` 归入其中一个语义族。该映射同时适用于既有 ready 快照，无需仅为颜色重建投影。
- `status` 描述当前可浏览快照；`build_status=idle|pending|running|failed` 和 `build_error` 独立描述下一版构建，因此后台失败不会让旧 ready 地图消失。
- 有旧 ready 快照且后台构建新版本时，`status=ready`、`building=true`，浏览仍固定到旧快照。
- 覆盖字段：`event_total`、`event_embedded`、`event_eligible`、`event_skipped`、`event_failed`。事件时间缺失不能进入地图，不能用 `available_at` 补位。

### `GET /api/playlists/{playlist_id}/events/map/scene`

- query：必填 `snapshot_id`，且该快照必须属于播放列表并为 ready。可选 `preview_limit=1..20000`；传入后按全量 `point_index` 的确定性步长均匀抽取最多指定数量的真实事件，并将响应内 point index 重新连续编号，供首帧快速落点。未传入时返回完整场景。
- 返回不可变 `application/octet-stream`，按 `point_index` 递增；协议 v2 每条 56 字节，小端布局 `<I16sfffiiBBBBIII>`。
- 字段依次是 point index、canonical UUID、float32 x/y/z、事件起止 epoch-day、类型/精度/flags/保留位、member 数、一级星域索引、二级主题索引。
- 场景流只读取类型化数值列和物化的 `has_uncertainty` 布尔位，不逐点读取 canonical 修订 JSONB。
- 预览和完整响应中的节点都是真实 canonical 事件，不生成装饰点或虚构位置；预览 URL 和完整 URL 分别按 snapshot 长期缓存。客户端在同一个 WebGL 场景中以预览点启动，再用完整点集原位替换几何，不重建网格或相机。

### `GET /api/playlists/{playlist_id}/events/map/entities`

- query：必填 `snapshot_id`；可选 `start_date`、`end_date`、`q`、`limit`。
- 从该快照的 `event_map_entity_index` 统计 canonical 去重数；时间窗口只使用事件发生区间，不扫描 revision JSON，也不回连实时实体表。
- PostgreSQL 查询使用事务级 `EVENT_MAP_ENTITY_QUERY_TIMEOUT_SECONDS`，并固定为快照段线性扫描 + Hash Join，禁止退化成逐 canonical 随机 Nested Loop；超时返回 `503`，不会留下后台无限查询。

### `GET /api/playlists/{playlist_id}/events/map/entity-indices`

- query：必填 `snapshot_id`、`normalized_key`；可选 `entity_type`、`start_date`、`end_date`。
- 返回按升序排列的小端 uint32 canonical point index。
- 同样受事务级事件地图查询超时保护；实体键索引查询继续允许选择性 Nested Loop，不强制扫描整个快照。

### `GET /api/playlists/{playlist_id}/events/map/canonical/{canonical_id}`

- query：必填 `snapshot_id`。
- 返回真实事件摘要、时间、topic、最多 100 条 member 记录、关联 `videos[]`、实体角色、冻结的实体关系、证据及相邻 story edge。每条 member 同时返回冻结 revision 对应的 `source_context` 与精确播放位置；视频缩略图使用 AssetRef。记录、关系和证据来自快照 revision，不回查可变事件子表，也不读写另一版快照。

### `GET /api/domains/{domain_id}/event-highlights`

- query：必填 `event_date_start`、`event_date_end`（按快照冻结的事件日历日解释、含首尾，最长 7 天）；可选 `window_start`、`window_end`、`snapshot_id`、`event_type_code`、`normalized_key`、`entity_type`、`topic_id` 和 `limit`（1–10，默认 10）。日期范围超过七天返回 `400`。接口按事件发生日期筛选 canonical，视频发布时间不参与事件入选。
- 只把 `time_precision in ('second', 'day')` 的事件作为“今日 / 本周”精确焦点；只有月、年或未知精度的事件不会在整月、整年每天冒充当日事件。固定快照、观察窗口、类型、实体和主题条件在事件排序前共同生效，主题内空结果不会回退到全域。
- 返回 `matched_event_total`、具有可播放关联视频的 `total`、实际展示的 `shown_total`、所选 `video_total`、单媒体视频上限 `max_cards_per_media`（固定为 2）、被精度规则排除的 `excluded_imprecise_total`，以及全部精确匹配事件的 `point_indices[]`。星图用完整 `point_indices[]` 突出当前日期焦点；`items[]` 承载最多十个入选事件，且 `canonical_id` 唯一。每项包含事件标题、摘要、发生时间、证据强度 `ranking_factors`、一级主题，以及一个 `primary_video`（`media_id`、媒体名、媒体头像 AssetRef `media_avatar_asset`、标题、缩略图 AssetRef、精确播放位置、时长和来源时间）。事件先按跨媒体/跨视频佐证、成员数、时间可靠性和稳定 tie-break 排序，再按原顺序选择尚未达到媒体上限的不同视频；某事件有多个真实关联视频时，优先选择当前占位更少的媒体。接口仍保持事件级事实结构；前端按 `primary_video.video_id` 将多个事件合成一张空间卡片并保留全部事件连线，因此卡片数可以少于 `shown_total`。该顺序衡量可审计的证据强度，不声称是尚未物化的市场影响重要度。媒体上限只影响视频卡，全部精确事件仍保留在 `point_indices[]` 中参与星图高亮。

### `GET /api/playlists/{playlist_id}/events/map/topic/{topic_id}`

- query：必填 `snapshot_id`；可选 `start_date`、`end_date`、`event_type_code` 和 `limit`（1–24，前端使用 20）。日期与类型条件按 canonical 的事件时间区间和类型过滤。
- 返回主题层级（`level`、`parent_topic_id`）、标签、关键词、快照全量计数及过滤后的代表 canonical；每个代表项包含 point index、标题、事件时间区间、类型和成员记录数。该接口供右侧“星域 → 主题 → 当前窗口代表事件”下钻使用。

### `GET /api/playlists/{playlist_id}/events/map/story/{story_id}`

- query：必填 `snapshot_id`。
- 返回故事事件图，以及 `anchor_key`、`story_type`、`maturity`、`quality_score` 和有证据的有向 edge。每条 edge 返回 `evidence_revision_ids` 与关系判定证据；普通三维近邻或仅有高语义相似度不会自动成为 story。

### `GET /api/playlists/{playlist_id}/events/map/search`

- query：必填 `snapshot_id`、`q`；可选 `limit`。
- 在 canonical、topic 和 entity 中搜索；topic 通过快照内 anchor canonical 返回可定位 `point_index`。

### `POST /api/playlists/{playlist_id}/events/map/rebuild`

- 递增 dirty generation 并立即投递 `playlist.build_event_map_snapshot`；相同播放列表已有 pending/running build 时复用任务。
- 构建只消费 accepted、事件时间可解析且当前 embedding 口径 ready 的记录。
- 构建失败、取消或超出 12 GiB 进程树 RSS 时保留现有 ready 指针。

### `GET /api/playlists/{playlist_id}/events/export`

- query：`level=canonical|record`，可选 `snapshot_id`；省略时使用 current ready。
- canonical 口径用于消费去重后的真实事件；record 口径保留每条来源记录与所属 canonical。
- 返回 `time_basis=event_time`，不把 `available_at` 导出为发生时间。

## V2 连续观察

### `GET /api/domains`

- 返回观测域目录、`avatar_asset`、`background_asset`、`observation_enabled`、`brief_granularity`、current ready 快照、`media_count`、最多 5 条 `media_preview`、`video_count`、未读变化数、最后观察位置，以及从类型化坐标列确定性抽样的 `preview_points`。观测域与媒体头像复用统一 AssetRef 交付链路并批量读取，目录响应不逐项生成 S3 预签名 URL，前端通过 `/api/assets/{asset_id}/content` 读取。
- query 可选 `compact=true`，仅返回首屏恢复当前观测域所需的身份字段、观测开关、简报粒度、current ready 快照的 `id/status/observed_at` 和 `web_url`；不读取快照边界与月份分布，也不读取媒体预览、视频计数、未读变化或星点。星域入口先用该响应确定域并启动 WebGL，完整目录在首图之后再补取。

### `PATCH /api/domains/{domain_id}`

- body：`{ "name": "观测域名称", "description": "观测范围" }`。
- 仅更新设置页的轻量身份字段并返回域摘要，不执行播放列表视频计数或详情聚合。

### `POST /api/domains/{domain_id}/sources`

- body 只能提供 `{ "media_id": "..." }` 或 `{ "url": "..." }` 之一，同时提供或同时缺少返回 `422`。
- `media_id` 用于把全部资料中的已有信源加入当前观测域；重复加入是幂等操作。
- `url` 在同一数据库事务中完成 URL 规范化查重、必要时创建全局信源以及加入当前观测域；任一步失败都会整体回滚。
- 返回 `source`、`created` 和 `attached`，分别说明信源、是否新建全局信源、是否新建域关联。

### `DELETE /api/domains/{domain_id}/sources/{media_id}`

- 只解除 `PlaylistMedia` 域关联；重复解除保持幂等。
- 不删除全局 `Media`、`Video`、`Asset` 或该信源与其他观测域的关系。

### `GET /api/domains/{domain_id}/deletion-impact`

- 返回信源关联、简报、星域快照、变化、故事、观察状态和活跃任务计数。
- `retained_shared_data` 明确说明全局信源、来源记录和资产不会随观测域删除。

### `DELETE /api/domains/{domain_id}`

- body：`{ "confirm_name": "完整域名" }`。
- 名称不完全匹配返回 `400`；存在 `pending / running / cancel_requested` 的域任务或其后代任务时返回 `409` 并列出活跃任务，不自动取消任务。
- 删除域内关联和派生观测数据，但保留共享的 `Media`、`Video` 与 `Asset`。

### `GET /api/domains/{domain_id}/observation`

- 返回当前域的 `observation_enabled`、`brief_granularity`、`brief_prompt`，以及当前快照、观察游标和四类独立覆盖：可分析转写、当前口径抽取、星域收录、证据核验。它们属于处理覆盖或产出覆盖，各自使用独立分母，不表示同一条流水线的四阶段总进度。观测域设置页复用该响应，不额外读取包含完整媒体清单的播放列表详情。
- `coverage.event_extraction` 的分母是已有纯文本转写的视频，分子是存在与当前 `prompt_version + extraction_model` 一致的成功抽取记录的视频；`details` 进一步区分有事件、成功但零事件、仅旧口径成功、当前口径失败、尚无成功记录和缺少转写。`event_count=0` 是成功结果，不计为失败。
- 提示词基础版本、提示词正文哈希或模型变化不会在服务启动时自动重投全部历史视频；历史迁移通过 `POST /api/playlists/{playlist_id}/events/extract` 的 `force=false` 回填显式触发，并复用精确命中当前 `source_hash / prompt_version / extraction_model` 的成功结果。

### `PUT /api/domains/{domain_id}/observation`

- body：`{ "enabled": true|false }`，持久化当前域的持续观测状态。
- 停用只阻止新的认知分析与域派生任务，不停止匹配来源的同步、视频/字幕下载和转写归档，也不强制取消已经 `running` 的任务。
- 视频属于多个域时，只要其中一个域仍启用，视频级转写润色、事件抽取与事件向量仍可执行一次；域级简报和星域任务只为启用域投递。
- 从停用切回启用时投递一次事件补齐和星域 dirty 信号，复用 Job 去重语义。

### `GET|PUT /api/domains/{domain_id}/observation/cursor`

- 保存或读取快照、系统认知时间、事件时间窗、`now|replay|story|verify` 模式、相机、筛选和最后选择对象。
- 星域首屏使用该轻量接口恢复观察位置，不读取 `/observation` 中的覆盖率聚合；相机状态记录三维镜头位置、观察目标、快照、时间窗口、取景版本和视口比例，不再记录平面/三维模式，不兼容时按当前窗口重新取景。
- `last_page` 支持 `field|stories|briefs|playlist|library|operations`；`playlist` 表示来源播放工作台。
- 相同状态重复提交为幂等更新，不创建新的游标记录。

### `GET /api/domains/{domain_id}/changes`

- query 可选 `after_snapshot_id`、`object_type`、`change_type`、`cursor`、`limit`。
- 按 `(observed_at desc, id desc)` 稳定分页；每项同时声明事件发生时间和系统认知时间口径。
- 每项返回 `before_revision` / `after_revision`；retire 变化的 Web 深链接固定到对象最后仍存在的 `from_snapshot_id`，其余变化固定到 `to_snapshot_id`。
- 新增且带 evidence revision 的显式 `corrects` 故事关系会额外生成 `story_correction_added`。故事首次形成、成员或顺序变化、关系或判定依据变化、支持记录变化、事实纠正和 `emerging / established` 成熟度变化属于实质变化；仅标题/摘要措辞、质量分波动和无变化重建不刷新故事未读状态。

### `GET /api/domains/{domain_id}/observation/feed`

- 分开返回 `newly_occurred`、`newly_mapped`、`story_updates`、`needs_review`，并在 `definitions` 中返回每组口径。

### `GET /api/domains/{domain_id}/canonicals`

- query 可选 `event_time_start`、`event_time_end`、`event_type`、`normalized_key`、`entity_type`、`limit`、`offset`。
- 返回 current ready 快照的线性真实事件列表，供 3D 星域的无障碍/低性能替代视图使用；时间、事件类型和实体条件与星域一致，查询只读 canonical 热列与实体索引，不解析 JSONB。

### `GET /api/domains/{domain_id}/canonicals/{canonical_id}/history`

- 返回稳定 canonical 身份、跨快照修订、merge/split 谱系和变化记录。

### `GET /api/domains/{domain_id}/topics/{topic_id}`

- query 可选 `limit`、`snapshot_id`；省略快照时读取 current ready，显式指定时固定到该历史 ready 快照。
- 返回所选快照中的主题层级、父主题、关键词、成员计数和类型化代表 canonical，并附与 Web 星域共享的深链接。

### `GET /api/domains/{domain_id}/stories`

- query 可选 `scope=all|attention|followed|established|emerging`、`q`、`limit`（默认 `80`，最大 `200`）和 `offset`；API 默认 `scope=all`，故事页显式请求 `attention`。
- `attention` 包含已关注或读过故事中尚未阅读的实质更新，以及域观察游标之后发生任一实质变化的成熟故事；后者包含首次形成、成员、关系、支持记录、事实纠正和成熟度变化，不要求用户预先打开过该故事。没有任何观察或阅读基线时，只返回最近一页成熟故事并逐项标记推荐，不把全库伪装成未读；只保存阅读位置或关注状态不等于已经读过，关注但未读的故事仍单独进入队列。
- 搜索同时匹配不会随快照改写的 `stable_title` 和最新真实事件标题。结果按 `last_material_changed_at desc, story_identity_id asc` 稳定排序。
- 每项返回稳定标题、`latest_progress`、成熟度、关系与支持记录数量、最近实质更新时间、实质变化类型、关注状态和准确的 `unread`；同时提供 `total`、`offset`、`limit`、`has_more`，调用方应渐进读取。

### `GET /api/domains/{domain_id}/stories/{story_identity_id}`

- query 可选 `snapshot_id`；省略时读取 current ready，显式指定时航迹固定到该历史 ready 快照且不回退。
- 返回 `stable_title`、所选 `selected_revision`、可读的 `trajectory.nodes/edges`、相对最后阅读修订的具名 `reading_update`、仅含实质变化的 `material_history`、合并后的 `audit_summary`、阅读状态与相关简报。若期间发生过实质变化但当前净结构已恢复，`reading_update.returned_to_previous_state=true` 并返回期间变化次数与类型，不伪造净增减。兼容期继续返回 `current_trajectory` 别名和旧历史字段。
- 节点返回标题、摘要、起止时间、时间精度、事件类型、成员数和不确定性；同一发生时间的节点保留所选 story revision 的成员顺序。边返回中文关系、两端事件标题、支持记录数，以及已有两端命题、摘录和时间间隔。它们用于解释系统为何建立关系，不代表每一段摘录已经完成严格的逐段关系归因。
- 历史深链接中的标题、进展、节点、关系和简报上下文全部来自所选 revision，不能混入 current 数据。

### `GET /api/domains/{domain_id}/stories/{story_identity_id}/edges/{edge_id}/support`

- query 必填 `snapshot_id`，并校验观测域、故事身份、故事修订和关系边属于同一所选快照。
- 返回具名来源事件、目标事件、中文关系类型、两端 claim、已有摘录、时间间隔，以及去重后的来源支持记录。
- 支持记录包含来源标题、信源、原文片段、`verified`、转写版本状态与精确播放位置（若存在）；`support_granularity=record_revision` 明确当前证明粒度。详情按选中关系懒加载，故事主响应不逐记录扩张。

### `PATCH /api/domains/{domain_id}/stories/{story_identity_id}/read-state`

- body 可包含 `followed`、`snapshot_id`、`position`、`mark_read`。打开故事不写已读；位置更新也不隐式清除未读。只有显式 `mark_read=true` 才把 current 故事实质更新标为已读，历史快照不能清除 current 未读状态。

### `GET /api/briefs/{brief_id}/structured`

- 返回简报生成快照、生成口径、正文地址和按正文锚点排序的结构化引用。

### `GET /api/domains/{domain_id}/objects/{object_type}/{object_id}/brief-references`

- query 对 topic 可选 `snapshot_id`；反查引用某个 canonical、story、evidence 或 source 的简报位置，topic 按所选快照的成员 canonical 聚合解释该主题的简报段落。

### `GET /api/domains/{domain_id}/evidence/{revision_id}`

- 返回来源记录、证据文本、校验信息、字符区间和播放定位。
- 只有来源提供精确分段时间时才返回播放秒数；否则保留字符区间，不做比例估算。
- transcript 证据返回冻结的 `transcript_asset_id`、当前 plain transcript asset、`source_version_status=current|superseded|unavailable|unknown` 与片段 `source_sha256`；旧转写版本不会被当前版本静默替换。

### `GET /api/domains/{domain_id}/source-records/{video_id}/semantic-references`

- 通过正规化历史成员与故事证据索引反查 canonical、故事关系与简报，不扫描长期修订 JSONB。

### `GET /api/library/sources` / `GET /api/library/source-records`

- query：`scope=domain|global`；域内口径要求 `domain_id`。两者分别对应全局/域内信源管理和来源记录浏览。
- `GET /api/library/sources` 另支持 `q`、`limit`、`offset`；传入 `domain_id` 时，每项返回 `in_current_domain`，可用于从全部资料搜索并加入尚未关联的信源。

### `GET /api/search/semantic`

- query：`q`，可选 `domain_id`、`limit`。
- 按观测域、主题、真实事件、故事、实体、简报、信源和来源记录分组返回，并附可恢复上下文的 Web 深链接。

### `POST /api/briefs/generate`

- body：`{ "playlist_id": "...", "date": "YYYY-MM-DD", "granularity": "day|week|month" }`

### `POST /api/briefs/generate_range`

- body：`{ "playlist_id": "...", "granularity": "...", "from_date": "...", "to_date": "..." }`

### `GET /api/briefs/by_date`

- query：`playlist_id`、`date`
- 优先从新 `brief` 表查日级简报，查不到再回退 `daily_brief`。

### `GET /api/briefs/by_period`

- query：`playlist_id`、`granularity`、`date`

### `GET /api/briefs`

- query：`playlist_id`、`granularity`、`limit`、`offset`

### `GET /api/briefs/prompt_by_period`

- query：`playlist_id`、`granularity`、`date`
- 返回该周期实际用于生成简报的 prompt 和视频 URL 列表。

### `GET /api/briefs/{brief_id}`

- 返回单条简报记录。

## Config

### `GET /api/config`

- 返回全部 `app_config` 键值。
- `ytdlp_cookies_youtube` 与 `ytdlp_cookies_bilibili` 只返回 `{ "configured": true|false }`，绝不回显 Cookie 内容。
- 火山推理配置中的密钥字段继续只返回掩码。

### `GET /api/config/defaults`

- 返回默认配置模板，目前包括：
  - `llm_transcript_polish_prompt`
  - `llm_event_extraction_prompt`
  - `brief_generation_policy`

### `PUT /api/config/{key}`

- 写入单个运行时配置项。
- 当前已校验的 key 包括：
  - `ytdlp_cookies_youtube`
  - `ytdlp_cookies_bilibili`
  - `llm_transcript_polish_prompt`
  - `llm_event_extraction_prompt`
  - `brief_generation_policy`

## 非 API 根路径

- `GET /`：返回 SPA 壳。
- `GET /manifest.webmanifest`、`GET /sw.js`：PWA 入口。
- `GET /docs`、`GET /openapi.json`：FastAPI 文档与 OpenAPI schema。
