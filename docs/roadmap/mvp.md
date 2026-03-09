# MVP 路线图

## 1. 基础设施与数据模型

- 打通 Postgres + MinIO 连接
- 完成 `media / video / asset / job` 表与基础 CRUD

## 2. 任务系统（最小可用）

- 完成 job 创建、查询、领取、租约、重试
- 让 worker 跑通一个简单任务，例如 `sleep + progress`

## 3. 媒体同步（发现视频）

- 支持添加媒体
- 跑通 `media.sync_videos`
- 通过 upsert 写入 videos

## 4. 下载与产物链路

- `video.download` -> `asset(video)`
- `video.extract_audio` -> `asset(audio)`
- `video.normalize_subtitle` -> `asset(transcript)`
- 视频列表支持筛选与获取内容（presigned 或代理）

## 5. 播放列表与简报

- 完成 playlist CRUD 与 `playlist_media`
- 跑通 `brief.generate_daily`
- 支持简报列表与查看

## 6. UI（App Shell + 核心页面）

- 完成 Media / Videos / Jobs / Playlists / Briefs / Settings
