# 后端模块

本文只记录当前仓库已经落地的模块职责，不重复展开 job-system skill 中的通用原则。

## 媒体管理（Media）

当前能力：

- 添加单个媒体、导入文本列表、导出全部媒体 URL。
- 媒体新增时只投递 `media.sync_profile`，默认不启用监控。
- 支持显式开启 / 关闭 `monitor_enabled`。
- 关闭监控时会删除该媒体尚未执行的下载任务。
- YouTube 频道返回 404 / entity not found 时，`media.sync_videos` 会自动关闭该媒体监控，并在 `sync_cursor.auto_disabled` 记录原因。
- 支持手动同步单个媒体或全部已启用监控媒体，范围可选 `recent` 或 `all`。

实现要点：

- provider 与 `provider_media_id` 通过 URL 解析得到，保证幂等。
- 手动同步由 [backend/raelyn/services/media_actions.py](../../backend/raelyn/services/media_actions.py) 统一调度。
- `scheduler` 只处理 `monitor_enabled=true` 的媒体。
- B 站 `media.sync_profile` 通过 `curl_cffi` 浏览器模拟优先读取 `/x/web-interface/card`，获取名称、简介、粉丝数、视频数与有效 `/bfs/face/` 头像；端点不可用时回退公开空间页，不再调用受限的旧 `/x/space/acc/info` API，也不为缺失头像继续回退到 yt-dlp。
- YouTube `media.sync_profile` 会从 yt-dlp 频道 `thumbnails` 中区分横幅与方形头像，优先使用 `avatar_uncropped`；头像字节沿用 YouTube yt-dlp 的 Cookies、浏览器模拟和显式 `YTDLP_PROXY`，其余头像抓取保持直连。

## 视频同步与下载

当前任务链：

- `media.sync_videos`：轻量发现新视频，用平台 flat 列表信息幂等写入 `video`，并按配置决定是否自动下载；YouTube flat 条目缺少发布时间时只投递异步补全任务。
- `video.enrich_metadata.youtube`：补全单个 YouTube 视频的 `raw_info/published_at`，并持续监测 `members_only → public/unlisted` 状态变化，不阻塞媒体同步。
- `video.download.youtube` / `video.download.bilibili`：下载视频、缩略图、字幕等原始产物。
- `video.backfill_subtitles.youtube` / `video.backfill_subtitles.bilibili`：对已采集视频执行 subtitle-only 回补，只用 yt-dlp `skip_download` 抓取字幕 / 自动字幕并落 raw subtitle asset。
- `video.extract_audio`：提取音频资产，供移动播放和 ASR 使用。
- `video.normalize_subtitle`：将字幕标准化为 transcript。
- `video.asr_transcribe`：在无可用字幕 transcript 时调用 ASR；默认不传 `language`，按 ASR 返回的实际语言保存 `plain` / `segments`。
- `video.polish_transcript`：可选的 LLM 文字稿润色。

当前边界：

- `media.sync_videos` 同一媒体运行时互斥；重复发现同一平台视频时依赖 `video(provider, provider_video_id)` 唯一键执行幂等插入。
- 下载任务按单条视频的发布时间确定最终优先级：无论来自常规同步、宽窗口发现还是全量历史补漏，`published_at` 距当前不足 24 小时的视频至少使用优先级 8；历史任务保持调用方原优先级。YouTube flat 条目起初缺少发布时间时，详情补全确认其属于最近 24 小时后会提升已有 pending 下载任务，而不重复创建任务。
- `media.sync_videos` 不再为 YouTube 缺失发布时间的视频内联调用单视频详情解析；新发现视频和已存在但 `published_at is null` 的视频会投递 `video.enrich_metadata.youtube`。新发现或最近 7 天再次观测到的 `members_only` 视频，无论 flat 条目是否已有发布时间都会立即投递优先级 8 的详情监测；同步任务结果记录 `metadata_enrichment_enqueued`。已建立的监测任务超过 7 天后仍会继续重排，该窗口仅避免历史存量首次上线时形成请求洪峰。
- `video.enrich_metadata.youtube` 归属 `sync` worker role，复用 YouTube provider pause 与 sync provider advisory lock；拿不到锁时延迟 30 秒重排。单次只处理一个 `video_id`，单视频 yt-dlp 详情解析有 45 秒硬超时；metadata-only 提取跳过不需要的 player config / JS challenge，父进程先接收 compact IPC 结果再回收子进程。普通缺失时间补全达到 `max_attempts` 后停止自动重投；会员可用性监测在仍受限时按 10 分钟到 24 小时的分段退避持续重排，并在后续频道观测时确保不会因旧终态任务永久漏检。详情明确变为 `public/unlisted` 后，同一事务恢复视频状态并补投下载。
- YouTube metadata 补全只在缺失时写入 `published_at`、`thumbnail_url`、`duration_sec`，不会覆盖已有标题；若 `published_at` 从空变为有值，会触发播放列表事件时间轴 dirty 标记。
- 正常视频下载链路的字幕下载是否开启由运行时配置 `ytdlp_subtitles` 决定；显式字幕回补任务不依赖该开关，因为任务本身就是人工发起的 subtitle-only 抓取。
- `video.download.*` 只会把 yt-dlp 产出且经 FFprobe 确认同时包含视频、音频 packet 的可播放容器登记为 `video` asset；YouTube 某次格式产物无有效 packet 时会清理该次临时产物并进入下一个格式 selector。`.ytdl` 断点状态、`.info.json`、缩略图、字幕和纯音频片段不会进入 `video.extract_audio` 链路。
- `video.extract_audio` 会校验 FFmpeg 输出 M4A 至少包含一个音频 packet；`video.asr_transcribe` 在调用外部 ASR 前对音频资产执行相同校验。空容器属于确定性坏输入，任务直接终止且不会请求 ASR。普通链路在已有字幕 / transcript 时跳过 ASR，`force=true` 的存量修复链路会强制重新投放 ASR。
- 字幕回补复用平台 cookies、YouTube `YTDLP_PROXY`、PO Token/EJS 与 provider 下载并发门控；默认只请求明确语言码。YouTube 以 `zh-Hant`、`zh-Hans`、`zh-CN`、`zh-TW`、`zh-HK`、`zh`、`en` 为主；B 站会额外请求 yt-dlp 暴露的 `ai-zh` / `ai-en`。
- YouTube 会员视频默认不会下载；只有配置 `ytdlp_members_only.enabled=true` 时才会尝试。
- Cookies 来自 `app_config`，运行时会写入 `tmp/` 下的 provider 专属 `cookies.txt` 文件。
- 新视频 transcript 生成后，若 `AUTO_EXTRACT_NEW_VIDEO_EVENTS=true` 且 LLM 已配置，会自动投递 `video.extract_events`；历史事件回填通过播放列表事件面板显式触发 `playlist.backfill_events`。
- 事件抽取响应无法解析为 JSON 时，同一次任务尝试只追加一次 LLM 语法修复调用；修复提示只允许改动 JSON 标点与转义，不允许重写事件内容。修复结果仍需通过原有 `videos[] / video_id / events[]` 协议校验，修复调用的 token 与 `call_count` 会合并计入该次抽取 usage。

## 资产访问与分发

当前 API 同时支持两种访问模式：

- 代理模式：默认通过 `/api/assets/{asset_id}/content` 或 `/download` 由 API 代理流式读取。
- 直连模式：仅在显式启用 `ASSET_PRESIGN_ENABLED=true` 且对象存储出口可被浏览器访问时，通过 `AssetRef.presigned_url` / `download_presigned_url` 直接访问对象存储。

实现要点：

- API 启动后会结合 `ASSET_DIRECT_PROBE_URL`、`ASSET_PRESIGN_ENABLED` 和 `ASSET_PROXY_BASE_PATH` 形成前端可用的分发策略。
- `ASSET_PRESIGN_ENABLED=false` 时，前端直接进入 proxy-only 模式，不做对象存储直连探测，后端也不生成 presigned URL。
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
- 播放列表主界面提供事件审核 / 证据面板，支持事件确认 / 拒绝；播放列表设置页集中提供补齐事件抽取、全部重新抽取、停止抽取任务与事件地图构建。

实现要点：

- `AUTO_GENERATE_BRIEFS=true` 时，播放列表媒体变更会调用 `schedule_brief_refresh_for_media_change()` 触发相关周期简报刷新；默认关闭自动简报投递。
- 简报调度策略由 `brief_generation_policy` 决定，区分“最新周期冷却时间”和“历史周期每日批处理时间”；该策略只在自动简报开启或手动简报任务创建时生效。
- `brief` 是当前主表，`daily_brief` 仅用于历史兼容读取。
- 事件抽取由 `playlist.backfill_events` 先按播放列表内容时间轴规划月份范围，再由 `playlist.backfill_events_range` 查询单月视频并投递 `video.extract_events`；accepted 记录通过 `event.embed` 后投递 `playlist.mark_event_map_dirty`。`analysis` worker 按 2 分钟静默 / 15 分钟最大等待微批执行 `playlist.build_event_map_snapshot`；构建期间的新 generation 不会被清掉。构建成功后由 `playlist.prune_event_map_snapshots` 分批删除 current 与上一版之外的终态快照。

## 系统运维与观测

当前能力：

- `/api/system` 返回系统暂停状态、provider 暂停状态和资产分发策略。
- `/api/workers` 返回 worker 在线情况、角色分布、最后心跳时间，以及 worker 角色暂停状态。
- `/api/workers/roles/{role}/pause|resume` 支持人工暂停 / 恢复具体 worker 角色继续领取新任务。
- `/api/stats` 返回概览页统计、最近媒体 / 视频 / 播放列表，以及 ASR / LLM 使用量。
- `/api/usage` 为独立资源用量页返回实时当前摘要、7 / 30 / 90 天日趋势、外部服务与逻辑资产构成，以及采集覆盖状态。
- `/api/jobs` 与 `/api/ws/*` 提供任务列表、事件、时序统计和实时刷新能力。
- `/api/cleanup/stale-videos` 用于扫描 / 清理“已停用监控、仍处于 discovered、且没有有效下载任务或视频资产”的遗留视频记录。

实现要点：

- worker 启动时会写入心跳，并回收孤儿 `running` 任务。
- worker 角色暂停只影响后续 claim，不影响已经 `running` 的任务，也不代替进程启停。
- LLM、ASR 与 Embedding 客户端在真实外部调用边界记录 `ExternalServiceUsageDaily`；成功、失败、耗时和可用 token 以 `Asia/Shanghai` 日桶原子累加，不记录站内 HTTP 请求或敏感请求正文。统计写入使用独立短事务，统计失败不会改变外部调用本身的业务结果。
- Scheduler 在 `usage.legacy_llm_backfill` 标记版本过旧时幂等投递一次 `system.backfill_legacy_usage`，由 `sync` worker 从终态 Job 结果精确重建 `legacy.*` LLM 日聚合，并从带任务尝试号的 ASR 请求事件重建可核验的 `legacy.video_transcription` 日聚合。没有请求事件的旧 ASR 任务不会被推算为调用；事件抽取运行表也不参与迁移，避免与 Job 结果重复。
- Scheduler 每小时检查资源快照是否到期，只幂等投递低优先级 `system.capture_usage_snapshot`，不在 scheduler 进程扫描 `video` / `asset` 或调用 `pg_database_size`。该任务归属 `sync` worker，由 worker 实时计算仍存在的视频数、逻辑资产量和数据库规模，并以当日较新的 `captured_at` 幂等更新 `ResourceUsageDaily`。
- `/api/usage` 保持只读：已下载视频库存按现存 `Asset(type=video)` 的去重 `video_id` 计算，每日实际下载量按成功结束且未跳过、未重排队的 `video.download*` 任务完成时间统计；来源记录发现和批量迁移不进入下载趋势。调用趋势合并实时行与独立的历史迁移行，历史逻辑资产与数据库趋势只读快照。采集启用前或采样缺失的资源日桶返回 `null`，当天数据允许是部分采样；非 PostgreSQL 数据库规模为 `null`。
- 对象存储统计只表达 `Asset.size_bytes` 逻辑量。当前没有跨本机、容器与远端对象存储可靠成立的物理磁盘容量来源，因此不提供磁盘总量、余量或占用率。
- 主站 API Bearer Token 开启后，`/api/*` 需要 `Authorization` 或 `raelyn_api_token` cookie，`/api/ws/*` 需要 query `token`。
- 系统 / provider 暂停主要用于 Cookies 失效、平台风控或人工运维时的保护性停机。
