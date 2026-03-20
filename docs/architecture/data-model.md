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

- `published_at`：视频发布时间；只负责时间归属，不代表视频产物或文本已就绪。
- `status`：当前下载 / 处理状态，例如 `discovered`、`members_only` 等。
- `raw_info`：保留 provider 返回的原始元数据。

播放列表 / 简报准入口径：

- 播放列表时间轴：`published_at` 非空，且存在 `asset(type=video)`。
- 简报周期聚合：`published_at` 非空，且存在可用于 transcript 读取的文本资产。

约束：

- `(provider, provider_video_id)` 唯一。

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
- `job` 依赖 `status + scheduled_for + priority` 和 `lease_expires_at` 路径支撑任务领取与回收。

## 与代码的对应关系

- ORM 定义见 [backend/raelyn/models.py](../../backend/raelyn/models.py)。
- 初始化与兼容迁移见 [backend/raelyn/db.py](../../backend/raelyn/db.py)。
- 对象存储写入与 presign 见 [backend/raelyn/services/s3.py](../../backend/raelyn/services/s3.py)。
