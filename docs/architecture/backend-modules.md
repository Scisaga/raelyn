# 后端模块

本文只记录当前仓库已经落地的模块职责，不重复展开 job-system skill 中的通用原则。

## 媒体管理（Media）

当前能力：

- 添加单个媒体、导入文本列表、导出全部媒体 URL。
- 媒体新增时只投递 `media.sync_profile`，默认不启用监控。
- 支持显式开启 / 关闭 `monitor_enabled`。
- 关闭监控时会删除该媒体尚未执行的下载任务。
- 支持手动同步单个媒体或全部已启用监控媒体，范围可选 `recent` 或 `all`。

实现要点：

- provider 与 `provider_media_id` 通过 URL 解析得到，保证幂等。
- 手动同步由 [backend/raelyn/services/media_actions.py](../../backend/raelyn/services/media_actions.py) 统一调度。
- `scheduler` 只处理 `monitor_enabled=true` 的媒体。

## 视频同步与下载

当前任务链：

- `media.sync_videos`：发现新视频、更新视频元数据，并按配置决定是否自动下载。
- `video.download.youtube` / `video.download.bilibili`：下载视频、缩略图、字幕等原始产物。
- `video.extract_audio`：提取音频资产，供移动播放和 ASR 使用。
- `video.normalize_subtitle`：将字幕标准化为 transcript。
- `video.asr_transcribe`：在无可用中文字幕 transcript 时调用 ASR。
- `video.polish_transcript`：可选的 LLM 文字稿润色。
- `video.generate_note`：按需生成单视频 Markdown 笔记。

当前边界：

- 字幕下载是否开启由运行时配置 `ytdlp_subtitles` 决定。
- YouTube 会员视频默认不会下载；只有配置 `ytdlp_members_only.enabled=true` 时才会尝试。
- Cookies 来自 `app_config`，运行时会写入 `tmp/` 下的 provider 专属 `cookies.txt` 文件。

## 资产访问与分发

当前 API 同时支持两种访问模式：

- 直连模式：通过 `AssetRef.presigned_url` / `download_presigned_url` 直接访问对象存储。
- 代理模式：通过 `/api/assets/{asset_id}/content` 或 `/download` 由 API 代理流式读取。

实现要点：

- API 启动后会结合 `ASSET_DIRECT_PROBE_URL`、`ASSET_PRESIGN_ENABLED` 和 `ASSET_PROXY_BASE_PATH` 形成前端可用的分发策略。
- 若主站页面是 HTTPS，而对象存储直连地址或 presigned URL 是 HTTP，前端会强制回退到代理模式，避免 mixed content 破坏播放与 PWA installability。
- 代理下载支持 `Range`，用于视频播放与断点读取。
- `playlist` 头像 / 背景图也复用同一套 standalone asset 写入逻辑。

## 播放列表与简报

当前能力：

- 播放列表支持创建、删除、重命名、改描述、替换媒体集合。
- 支持上传头像与背景图。
- 支持按 `day / week / month` 设置简报聚合粒度。
- 支持为单个播放列表配置独立的简报提示词。
- 支持获取按日期 / 按周期的视频列表与周期视频计数。
- 支持按单周期生成简报，也支持按区间批量重建。

实现要点：

- 播放列表媒体变更会调用 `schedule_brief_refresh_for_media_change()` 触发相关周期简报刷新。
- 简报调度策略由 `brief_generation_policy` 决定，区分“最新周期冷却时间”和“历史周期每日批处理时间”。
- `brief` 是当前主表，`daily_brief` 仅用于历史兼容读取。

## 系统运维与观测

当前能力：

- `/api/system` 返回系统暂停状态、provider 暂停状态和资产分发策略。
- `/api/workers` 返回 worker 在线情况、角色分布、最后心跳时间，以及 worker 角色暂停状态。
- `/api/workers/roles/{role}/pause|resume` 支持人工暂停 / 恢复具体 worker 角色继续领取新任务。
- `/api/stats` 返回概览页统计、最近媒体 / 视频 / 播放列表，以及 ASR / LLM 使用量。
- `/api/jobs` 与 `/api/ws/*` 提供任务列表、事件、时序统计和实时刷新能力。
- `/api/cleanup/stale-videos` 用于扫描 / 清理“已停用监控、仍处于 discovered、且没有有效下载任务或视频资产”的遗留视频记录。

实现要点：

- worker 启动时会写入心跳，并回收孤儿 `running` 任务。
- worker 角色暂停只影响后续 claim，不影响已经 `running` 的任务，也不代替进程启停。
- 主站 API Bearer Token 开启后，`/api/*` 需要 `Authorization` 或 `raelyn_api_token` cookie，`/api/ws/*` 需要 query `token`。
- 系统 / provider 暂停主要用于 Cookies 失效、平台风控或人工运维时的保护性停机。
