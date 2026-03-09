# REST API 设计

约定：

- JSON 使用 `camelCase` 或 `snake_case` 均可，但全局要保持一致，建议使用 `snake_case`
- 返回格式可采用统一包裹结构，或直接返回资源 JSON；MVP 可以从简单方案起步
- 所有列表接口支持分页：`limit / offset` 或 `cursor`
- 时间字段使用 ISO8601

## Health

- `GET /api/health`
  - 返回服务状态，以及可选的 DB / S3 可用性检查结果

## Media

- `POST /api/media`
  - body：`{ "provider": "youtube", "url": "..." }`
  - 行为：解析 URL，提取 `provider_media_id`，再 upsert media
  - side effects：投递 `media.sync_profile` + `media.sync_videos`
- `GET /api/media?provider=&q=&limit=&offset=`
- `GET /api/media/{media_id}`
- `DELETE /api/media/{media_id}`
  - 行为：删除媒体及其视频（cascade）或仅取消订阅
- `POST /api/media/{media_id}/sync`
  - 行为：投递一次 `media.sync_profile` + `media.sync_videos`

## Videos

- `GET /api/videos?provider=&media_id=&status=&from=&to=&limit=&offset=`
  - 支持按时间与媒体筛选
- `GET /api/videos/{video_id}`
- `POST /api/videos/{video_id}/download`
  - 行为：投递 `video.download`，以及后续 extract / normalize / asr 的编排链路

## Assets / Content

目标：支持通过 HTTP 获取视频、音频和文字内容。

推荐做法（MinIO 可直连时）：

- `GET /api/assets/{asset_id}`
  - 返回 asset 元信息与短时有效的 `presigned_url`

若 MinIO 不对外暴露，则 API 代理：

- `GET /api/assets/{asset_id}/content`
  - 需支持 `Range`，返回流式响应

## Jobs

- `GET /api/jobs?status=&type=&limit=&offset=`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/cancel`
- `POST /api/jobs/{job_id}/retry`
- `GET /api/jobs/{job_id}/events`

## Playlists

MVP 中，播放列表先作为“媒体集合”使用：

- `POST /api/playlists`
  - body：`{ "name": "...", "description": "..." }`
- `GET /api/playlists`
- `GET /api/playlists/{playlist_id}`
- `DELETE /api/playlists/{playlist_id}`
- `POST /api/playlists/{playlist_id}/media`
  - body：`{ "media_id": "..." }`
- `DELETE /api/playlists/{playlist_id}/media/{media_id}`

## Briefs

- `POST /api/briefs/generate`
  - body：`{ "playlist_id": "...", "date": "YYYY-MM-DD" }`
  - 行为：投递 `brief.generate_daily`
- `GET /api/briefs?playlist_id=&from=&to=`
- `GET /api/briefs/{brief_id}`
  - 返回简报元信息，以及 Markdown asset 的获取方式（presigned 或 content）

## Settings

- `GET /api/config`
- `PUT /api/config`
- 保存到 `app_config`，例如：
  - `asr_url`
  - `llm_url`
  - 并发限制
  - 同步间隔
  - 默认自动下载开关
