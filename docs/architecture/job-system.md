# 任务系统（Job Mgmt）

## 设计原则

`raelyn` 的任务系统遵循这些核心约束：单一事实源、Web/API 与执行面解耦、原子领取、幂等写入、租约回收、外部依赖门控与可观测性优先。

项目内关于任务系统的通用设计原则由 [../../skills/job-system-design/SKILL.md](../../skills/job-system-design/SKILL.md) 及其 references 维护；本文只保留 `raelyn` 的项目化实现说明，不重复展开通用原则全文。

## 任务状态机（建议）

- `pending`：待执行
- `running`：执行中（带租约 lease）
- `succeeded`：成功
- `failed`：失败（可重试或终止）
- `canceled`：人工取消

扩展字段：

- `attempt` / `max_attempts`
- `scheduled_for`：定时执行（分钟级同步依赖它）
- `lease_expires_at`：`running` 的租约到期时间，用于卡死回收
- `progress_current` / `progress_total`
- `cancel_requested_at`：协作式取消请求时间；`running` 任务收到请求后由 handler 在安全检查点退出

## 原子领取（Atomic Claim）

Worker 领取任务必须通过 DB 原子更新完成，以避免重复执行。推荐模式：

- 查询并锁定：`SELECT ... FOR UPDATE SKIP LOCKED`
- 条件更新：`pending -> running`，且 `scheduled_for <= now()`，且 `lease_expires_at is null OR lease_expires_at < now()`

领取成功后写入：

- `started_at`
- `worker_id`
- `lease_expires_at = now() + lease_duration`

## 租约 / 心跳与回收

- Worker 执行长任务时周期性刷新 `lease_expires_at`
- 长任务若在业务函数内部有明显批处理检查点，可以通过进度更新同步刷新 `lease_expires_at`，使 DB 中的任务事实源持续反映真实运行状态；例如播放列表分析快照会在 embedding 批读取、离散度计算、事件检测和写库阶段更新进度。
- 下载任务的 yt-dlp 进度回调会同步刷新 `lease_expires_at`，避免大文件或慢速下载超过初始 1 小时租约后被误回收成 `pending`，但原 worker 仍继续占用 provider 下载锁。
- `worker_heartbeat.updated_at` 是进程心跳，由心跳线程维护，只能证明 worker 进程和心跳线程仍在运行。
- `worker_heartbeat.active_at` 是主执行线程活动心跳，由 worker 主循环、任务领取点和下载进度更新维护；同步 / 下载任务回收必须同时检查它，避免“心跳线程活着”掩盖主执行循环已经卡死。
- `worker_heartbeat.current_job_id` 记录主执行线程最近声明的任务，用于排障时定位哪个任务导致执行心跳停止推进。
- `video.backfill_subtitles.*` 属于 provider-facing 下载角色任务：它只执行 yt-dlp subtitle-only 抓取，不下载媒体文件，但仍复用 provider 下载并发门控、平台暂停、cookies 失效暂停和长执行心跳语义。
- `media.sync_videos` 在平台 flat 列表提取完成后和逐条处理循环中刷新执行活动心跳；YouTube 缺失发布时间的单视频详情解析已拆到 `video.enrich_metadata.youtube`，避免同步任务在批量补 metadata 时长期不推进 `active_at`。
- `video.enrich_metadata.youtube` 是低优先级单视频补全任务，自身通过可终止子进程给 yt-dlp 详情解析设置 45 秒硬超时；子进程只向父进程回传 compact metadata，避免完整 yt-dlp `info` 大对象在进程队列中阻塞；超时只使该补全任务失败或重试，不扩大 `media.sync_videos` 的执行窗口。同一视频达到 `max_attempts` 终止失败后，后续自动同步不会再为同一 `dedupe_key` 反复投递补全任务，避免 best-effort 补全绕过任务重试上限。
- provider-facing worker（同步 / 下载）会在本进程内启动执行 watchdog；当 `current_job_id` 指向同步或下载任务且 `active_at` 超过 `WORKER_EXECUTION_STALE_AFTER_SECONDS` 未推进时，worker 主动退出，让 supervisor 重启并释放 PostgreSQL session 级 advisory lock。
- `scheduler` 或 `worker` 启动时可执行回收扫描：
  - `status=running AND lease_expires_at < now()` 视为失联，转回 `pending` 或标记为 `failed`
  - 对下载等 provider-facing 任务，如果进程心跳新鲜但执行心跳超过 `WORKER_EXECUTION_STALE_AFTER_SECONDS` 未推进，也视为主执行循环卡死并转回 `pending`
  - 对 `media.sync_profile` / `media.sync_videos`，若进程心跳仍新鲜但执行心跳过期，按一次同步尝试失败处理：增加 `attempt`、按既有退避重试，且不使用 orphan priority bump；达到 `max_attempts` 后标记 `failed`
  - `media.sync_videos` 因执行心跳过期达到终止失败时，会推进对应媒体的 `last_video_sync_at` 作为冷却时间，避免同一媒体立即被 scheduler 重新投递并堵塞同步队列
  - 回收动作应记录原因，便于后续排障

## Worker 角色暂停（Claim Gate）

- worker 角色暂停的事实源落在 `app_config.worker_role_pause`
- 该开关只阻止对应角色继续 claim 新任务，不会直接停止 OS 进程
- 已经 `running` 的任务保持原样，继续执行到正常结束或走现有协作式取消语义
- `ALL` worker 也必须遵守角色暂停门控，不能绕过已暂停的具体角色
- 专属 role worker 在对应角色已暂停时，应在查询 `job` 热表前直接跳过 claim，避免大量 pending 积压时“暂停但空转扫描队列”。

## 幂等策略（At-least-once 友好）

- 数据写入侧通过唯一键 + upsert 保证重复任务不会重复产出
  - `videos(provider, provider_video_id)` 唯一；`media.sync_videos` 发现新视频时用该唯一键执行 `ON CONFLICT DO NOTHING`
  - `assets(video_id, type, format, language, source, variant)` 可按业务定义唯一
- Job 层可设置 `dedupe_key`
  - 例如 `media.sync_videos:{media_id}`
  - 例如 `video.enrich_metadata.youtube:{video_id}`
  - PostgreSQL 下，创建 pending dedupe job 时先按 `dedupe_key` 获取事务级 advisory lock，再查询已有 `pending`，最后才插入新 job；`job(dedupe_key) where status='pending'` 唯一索引只作为兜底约束，不作为常规并发控制机制。
  - 同一事务需要投递多个 dedupe job 时，调用方必须按稳定 key 顺序投递，避免多个 worker 对同一批 key 反向等待。
  - 手动重试失败任务时，若同一 `dedupe_key` 已有 pending 任务，重试接口会取消那个 pending 任务并复用当前任务，避免提交时撞 pending dedupe 唯一约束

## 并发与外部依赖门控（Guardrails）

外部依赖容易触发风控或互相争抢资源，MVP 建议至少控制两层并发：

- Provider 级并发：如 YouTube 同时下载数、B 站同时下载数
- Media 级并发：同一媒体同一时刻只允许 1 个同步 / 下载任务；`media.sync_videos` 运行时会持有事务级媒体 advisory lock，拿不到锁时重排队
- ASR 后端容量：当 qwen3-asr-openai `/health` 显示 replica 推理槽位已满或已有等待队列时，worker claim 会跳过 `video.asr_transcribe`，让任务继续留在 DB 的 `pending` 队列中等待后端释放容量

当前项目对下载并发采用“强绑定语义”：

- `YOUTUBE_DOWNLOAD_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY` 的公开语义是对应 provider 的真实最大下载并发数
- 当 `*_DOWNLOAD_CONCURRENCY = N` 时，运行层默认会拉起至少 `N` 个对应的 `download_*` worker 进程
- `video.download.*` 与 `video.backfill_subtitles.*` 共享对应 provider 的下载角色和 advisory lock；字幕回补不会绕过平台并发上限
- `video.enrich_metadata.youtube` 归属 `sync` worker role，受 YouTube provider pause 与 sync provider advisory lock 控制；sync 锁繁忙时重排当前补全任务，不占用下载并发。
- provider advisory lock / guard slot 只负责做最终上限保护和防风控，不作为对外配置语义

实现方式可从易到难演进：

1. 进程内信号量（适用于单 worker）
2. PostgreSQL advisory lock（适用于跨 worker 实例）

示例：

- `pg_try_advisory_lock(hashtext('youtube:download'))`
- `pg_try_advisory_lock(hashtext('media:{media_id}:sync'))`

## 可观测性（Visibility by Design）

- 每个 job 记录 `type / status / params / result / error / attempt / worker_id`
- 结构化日志带 `job_id` 与 `video_id / media_id`
- `job_events` 保存关键事件，便于 UI 展示与排障

## Job 查询索引

`job` 是 API、WebSocket、worker claim 和租约回收共享的热表。当前兼容迁移会补充以下查询索引：

- `job_active_list_order_idx`：支撑任务页和 `/api/ws/jobs` 的 `pending/running` 实时列表，按运行中优先、开始时间、计划时间和创建时间取前 N 条。
- `job_pending_claim_order_idx`：支撑 worker 领取 `pending` 任务，按优先级、处理阶段等级、计划时间和创建时间取候选任务。
- `job_status_type_idx`：支撑 `/api/jobs/counts` 与 `/api/jobs/type_counts` 的状态 / 类型聚合。
- `job_finished_status_finished_at_idx`：支撑已完成任务列表和 24 小时成功 / 失败统计。
- `job_running_lease_idx` / `job_running_worker_idx`：支撑租约过期回收和孤儿 `running` 任务回收。

生产库补建大索引时优先使用 `CREATE INDEX CONCURRENTLY` 在维护窗口外执行；常规启动迁移只保证缺失索引能被补齐，不承担大表在线建索引调度。

## 当前协作式取消语义

- `POST /api/jobs/{job_id}/cancel` 不再把 `running` 任务直接硬改成 `canceled`
- `pending + cancel_requested_at`：worker 会在领取前直接落为 `canceled`
- `running + cancel_requested_at`：handler 在安全检查点退出，worker 统一收口为 `canceled`
- `media.delete` 复用这套机制，用于等待相关下载、转写、摘要任务安全退出后再执行级联删除
