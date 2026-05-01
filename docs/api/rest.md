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

- 返回 DB、S3、ASR、LLM 的健康状态。
- `deps_ok` 表示运行主链路是否整体可用。

### `GET /api/system`

- 返回系统暂停状态、provider 暂停状态和资产分发策略。
- `asset_delivery` 包含 `direct_probe_url`、`proxy_base_path`、`presign_enabled` 等前端运行参数。

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
- 资产容量统计会优先使用配置的 `S3_BUCKET`；若资产表中没有该 bucket 且只存在一个实际 bucket，则返回实际 bucket 的统计，并通过 `s3_configured_bucket_mismatch` 标记配置漂移。

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
  - `media_id`
  - `media_id_in`
  - `status`
  - `q`
  - `published_since`
  - `published_until`
  - `limit`
  - `offset`
- 返回视频列表、媒体名称、媒体头像、缩略图资产和视频资产引用。

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

- 删除播放列表。

### `POST /api/playlists/{playlist_id}/media`

- body：`{ "media_id": "..." }`
- 向播放列表追加一个媒体。

### `DELETE /api/playlists/{playlist_id}/media/{media_id}`

- 移除播放列表内的媒体。
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

- query：`date=YYYY-MM-DD`
- 返回指定本地日期的可播放视频列表；仅包含已发布时间且已落视频资产的视频。

### `GET /api/playlists/{playlist_id}/video_counts_by_period`

- query：`granularity`、`start`、`end`
- 返回可播放视频的周期计数，用于日 / 周 / 月时间轴。

### `GET /api/playlists/{playlist_id}/videos_by_period`

- query：`granularity`、`date`、`limit`
- 返回某个周期内的可播放视频列表；仅包含已发布时间且已落视频资产的视频。

### `GET /api/playlists/{playlist_id}/analysis/summary`

- 返回播放列表分析覆盖率、快照状态与候选数量。
- 若已有 ready 快照，覆盖率计数直接来自该 ready run 的快照统计，避免首屏为了展示 summary 重新扫描大播放列表的视频与 embedding。
- 若存在 ready 快照，返回 `signal_start_date` / `signal_end_date`，表示 day 级分析信号的全量日期边界，供 UI 初始化时间轴与默认查询范围。
- 若当前播放列表存在 `pending/running` 的 `playlist.backfill_embeddings`，返回 `backfill_job`，包含任务 ID、状态、扫描/写入/跳过数量与取消请求时间。
- 该接口只读取状态并展示 `analysis_dirty`，不会自动创建分析任务。

### `POST /api/playlists/{playlist_id}/analysis/backfill_embeddings`

- 手动创建 `playlist.backfill_embeddings` 任务。
- 同一播放列表已有 `pending/running` 的历史 embedding 补算时，不创建新任务；返回现有任务的 `job_id`、`created=false` 与 `backfill_job`。需要等待现有任务结束，或通过任务取消接口停止现有任务后再创建新任务。

### `POST /api/playlists/{playlist_id}/analysis/rebuild`

- 手动创建 `playlist.build_analysis_snapshot` 任务；只有显式调用该接口才会投递分析重建。
- 分析 worker 会分批流式读取 ready embedding；可用内存低于 `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES` 或进程 RSS 高于 `ANALYSIS_MAX_RSS_BYTES` 时直接失败并记录原因。
- 新快照成功后会自动清理同播放列表旧的非运行中 analysis run，只保留当前可读取的 `last_ready_run` 和仍在 `pending/running` 的 run。

### `GET /api/playlists/{playlist_id}/analysis/signals`

- query：可选 `granularity=day|week|month`、`since=YYYY-MM-DD`、`until=YYYY-MM-DD`。
- 返回当前 ready 快照的连续多尺度信号面板。
- 关键字段：`granularity`、`period_date`、`rolling_window`、`video_count`、`ready_embedding_count`、`drift_score`、`drift_rolling_mean/std/z`、`dispersion_mean/std/p25/p75`、`projection_id`、`projection_method`、`projection_x/y/z`、`projection_explained_variance_ratio`、`linked_event_id`。
- `projection_*` 仅用于解释与 UI 可视化，不作为事件分数来源。

### `GET /api/playlists/{playlist_id}/analysis/events`

- 返回当前 ready 快照的事件序列，等价于候选事件的新主口径。
- 关键字段：`event_id`、`event_date`、`peak_date`、`event_start`、`event_end`、`effective_trade_date`、`event_type`、`status`、`score`、`confidence`、`uncertainty`、`summary`、`top_terms`、`evidence_video_ids`、`available_at`。
- 新口径事件会额外返回 `breakpoint_date`、`detection_method`、`detection_granularity`、`boundary_score`、`boundary_z`、`before_start`、`before_end`、`after_start`、`after_end`、`supporting_granularities`；旧 ready run 没有 detection 元数据时这些字段为 `null` 或空数组。
- `score` 使用 `boundary_z`，`drift_score` 使用 `boundary_score`；`drift_rolling_z` 仅作为兼容字段保留，不再作为主事件分数解释。
- 不包含 `train_start / train_end / valid_start / valid_end / test_start / test_end`；训练窗口、验证窗口与回测 horizon 由 quant-lab 按实验目标自行决定。

### `GET /api/playlists/{playlist_id}/analysis/evidence`

- query：可选 `event_id`。
- 返回事件证据视频列表，包含 `event_id`、`video_id`、`media_id`、标题、媒体名、发布时间、到 period centroid 的距离与 shift score。

### `GET /api/playlists/{playlist_id}/analysis/export/explicit-event-windows`

- legacy 接口，返回 `{ "legacy": true, "window_mode": "explicit_event", "explicit_event_windows": [...] }`。
- 仅用于兼容旧 quant-lab train planner 请求；新的主接口应使用 `analysis/events` 与 `analysis/signals`。

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

### `GET /api/config/defaults`

- 返回默认配置模板，目前包括：
  - `llm_transcript_polish_prompt`
  - `brief_generation_policy`

### `PUT /api/config/{key}`

- 写入单个运行时配置项。
- 当前已校验的 key 包括：
  - `ytdlp_cookies_youtube`
  - `ytdlp_cookies_bilibili`
  - `llm_transcript_polish_prompt`
  - `brief_generation_policy`

## 非 API 根路径

- `GET /`：返回 SPA 壳。
- `GET /manifest.webmanifest`、`GET /sw.js`：PWA 入口。
- `GET /docs`、`GET /openapi.json`：FastAPI 文档与 OpenAPI schema。
