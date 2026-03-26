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
- `scheduler` 或 `worker` 启动时可执行回收扫描：
  - `status=running AND lease_expires_at < now()` 视为失联，转回 `pending` 或标记为 `failed`
  - 回收动作应记录原因，便于后续排障

## 幂等策略（At-least-once 友好）

- 数据写入侧通过唯一键 + upsert 保证重复任务不会重复产出
  - `videos(provider, provider_video_id)` 唯一
  - `assets(video_id, type, format, language, source, variant)` 可按业务定义唯一
- Job 层可设置 `idempotency_key`
  - 例如 `media.sync_videos:{media_id}:{window}`

## 并发与外部依赖门控（Guardrails）

外部依赖容易触发风控或互相争抢资源，MVP 建议至少控制两层并发：

- Provider 级并发：如 YouTube 同时下载数、B 站同时下载数
- Media 级并发：同一媒体同一时刻只允许 1 个同步 / 下载任务

当前项目对下载并发采用“强绑定语义”：

- `YOUTUBE_DOWNLOAD_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY` 的公开语义是对应 provider 的真实最大下载并发数
- 当 `*_DOWNLOAD_CONCURRENCY = N` 时，运行层默认会拉起至少 `N` 个对应的 `download_*` worker 进程
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

## 当前协作式取消语义

- `POST /api/jobs/{job_id}/cancel` 不再把 `running` 任务直接硬改成 `canceled`
- `pending + cancel_requested_at`：worker 会在领取前直接落为 `canceled`
- `running + cancel_requested_at`：handler 在安全检查点退出，worker 统一收口为 `canceled`
- `media.delete` 复用这套机制，用于等待相关下载、转写、摘要任务安全退出后再执行级联删除
