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

- 返回 worker 在线状态、角色分布、最近心跳时间。

### `GET /api/stats`

- 返回概览页统计数据、最近媒体 / 视频 / 播放列表，以及 ASR / LLM 使用量。

## Media

### `POST /api/media`

- body：`{ "url": "...", "provider": "youtube|bilibili"(optional) }`
- 行为：
  - 从 URL 识别 provider 与 `provider_media_id`
  - upsert `media`
  - 默认 `monitor_enabled=false`
  - 仅投递 `media.sync_profile`

### `GET /api/media`

- query：`provider`、`q`、`limit`、`offset`
- 返回媒体列表与本地视频数。
- 每条媒体额外包含：
  - `deleting`：是否存在活跃 `media.delete` 任务
  - `deletion_job_id`：当前删除任务 ID，便于前端恢复轮询状态

### `GET /api/media/export`

- 导出当前媒体 URL 列表，返回纯文本。

### `POST /api/media/import`

- body：`{ "text": "..." }`
- 按行导入媒体 URL，支持跳过空行与注释行。
- 返回新增、已存在、无效项和输入内重复项统计。

### `GET /api/media/{media_id}`

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

### `POST /api/media/{media_id}/sync`

- query：`scope=recent|all`
- 投递单个媒体的资料同步和视频同步任务。
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

### `GET /api/videos/{video_id}/note`

- query：`max_chars`
- 返回视频级 Markdown 笔记文本；若尚未生成，返回 `ok=false`。

### `POST /api/videos/{video_id}/note`

- 若已配置 LLM，则投递 `video.generate_note`。
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
- 返回指定本地日期的视频列表。

### `GET /api/playlists/{playlist_id}/video_counts_by_period`

- query：`granularity`、`start`、`end`
- 返回周期计数，用于日 / 周 / 月时间轴。

### `GET /api/playlists/{playlist_id}/videos_by_period`

- query：`granularity`、`date`、`limit`
- 返回某个周期内的视频列表。

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
