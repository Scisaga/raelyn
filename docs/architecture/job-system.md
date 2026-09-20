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
- `execution_token`：每次成功 claim 生成的新 UUID，用于标识本次逻辑执行；它不是 `attempt` 的别名
- `progress_current` / `progress_total`
- `cancel_requested_at`：协作式取消请求时间；`running` 任务收到请求后由 handler 在安全检查点退出

## 原子领取（Atomic Claim）

Worker 领取任务必须通过 DB 原子更新完成，以避免重复执行。推荐模式：

- 查询并锁定：`SELECT ... FOR UPDATE SKIP LOCKED`
- 条件更新：`pending -> running`，且 `scheduled_for <= now()`，且 `lease_expires_at is null OR lease_expires_at < now()`

领取成功后写入：

- `started_at`
- `worker_id`
- `execution_token = uuid4()`；同一个 job 被回收并再次 claim 时必须换新 token
- `lease_expires_at = now() + lease_duration`

`(job_id, status=running, worker_id, execution_token)` 共同构成本次执行的所有权条件。`worker_id` 只标识进程，无法区分同一 job 被回收、重领前后的两个逻辑执行，因此不能单独作为写入授权依据。

## 租约 / 心跳与回收

- Worker 执行长任务时周期性刷新 `lease_expires_at`。进度和 lease 更新都必须按 `(job_id, status=running, worker_id, execution_token)` 做 CAS；受影响行数为 0 表示本次执行已经失去所有权，handler 必须退出。
- 长任务若在业务函数内部有明显批处理检查点，可以通过同一条 token-aware CAS 同步更新进度和 `lease_expires_at`，使 DB 中的任务事实源持续反映真实运行状态；例如事件地图快照会在 embedding 批读取、离散度计算、事件检测和写库阶段更新进度，`event.backfill_embeddings` 则在每个 64 条向量批次的原子提交中同步 checkpoint。
- 下载任务的 yt-dlp 进度回调会同步刷新 `lease_expires_at`，避免大文件或慢速下载超过初始 1 小时租约后被误回收成 `pending`，但原 worker 仍继续占用 provider 下载锁。
- YouTube 下载的 yt-dlp logger 产生活动日志时会刷新 `worker_heartbeat.active_at`，覆盖连接建立、同一 URL 短重试和重新解析等尚未产生字节进度回调的阶段；真正长时间无日志、无进度的阻塞仍由执行 watchdog 回收。
- 本地 `video.asr_transcribe` 在音频下载完成、发起单次 ASR 请求前，按 `4x realtime + 120s` 计算 timeout，并将 lease 一次性延长到请求窗口后 300 秒；更新带 `status=running + worker_id + execution_token` 所有权条件，避免旧执行覆盖新 owner。超时前两次走 worker 退避，第三次终止，防止确定性慢样本连续占用 5 次推理资源。
- `worker_heartbeat.updated_at` 是进程心跳，由心跳线程维护，只能证明 worker 进程和心跳线程仍在运行。
- `worker_heartbeat.active_at` 是主执行线程活动心跳，由 worker 主循环、任务领取点和下载进度更新维护；同步 / 下载任务回收必须同时检查它，避免“心跳线程活着”掩盖主执行循环已经卡死。
- `worker_heartbeat.current_job_id` 记录主执行线程最近声明的任务，用于排障时定位哪个任务导致执行心跳停止推进。
- `video.backfill_subtitles.*` 属于 provider-facing 下载角色任务：它只执行 yt-dlp subtitle-only 抓取，不下载媒体文件，但仍复用 provider 下载并发门控、平台暂停、cookies 失效暂停和长执行心跳语义。
- `media.sync_videos` 在 yt-dlp flat 列表提取期间复用 yt-dlp 的真实分页 / 条目日志刷新执行活动心跳，并在提取完成后和逐条处理循环中继续刷新；这使万级频道全量枚举不会因单次提取超过 `WORKER_EXECUTION_STALE_AFTER_SECONDS` 被误判卡死，同时真实无日志、无进展的阻塞仍会由 watchdog 回收。YouTube 缺失发布时间的单视频详情解析已拆到 `video.enrich_metadata.youtube`，避免同步任务在批量补 metadata 时长期不推进 `active_at`。
- 同步发现或历史补漏产生下载任务时，以单条视频的 `published_at` 决定最终优先级；最近 24 小时的视频至少为优先级 8，不随全量历史任务降到优先级 5。发布时间稍后由 `video.enrich_metadata.youtube` 补齐时，通过既有 `schedule_video_download` 语义提升 pending 任务并写入优先级提升事件，不绕过原子领取或 provider 下载门控。
- 自动事件抽取在 AI 角色队列中按视频 `published_at` 提升当前时间焦点：过去 24 小时至少为优先级 20，本周内较早视频至少为优先级 15；两档都高于历史抽取、转写润色和自动简报积压。重复投递命中已有 pending 抽取任务时只提升优先级并记录事件，不重置参数、排期或重试预算。该优先级会继续传给事件 embedding 与星域 dirty outbox，使最新事件尽快进入下一版快照。
- `video.enrich_metadata.youtube` 的普通 metadata 补全使用低优先级，自身通过可终止子进程给 yt-dlp 详情解析设置 45 秒硬超时；子进程只向父进程回传 compact metadata，避免完整 yt-dlp `info` 大对象在进程队列中阻塞。`members_only` 可用性监测使用优先级 8；详情仍受限时，同一个 Job 通过 `scheduled_for` 按视频年龄从 10 分钟逐步退避到 24 小时并持续复核，明确变为 `public/unlisted` 后才成功收口并补投下载。普通缺失时间补全达到 `max_attempts` 后仍不自动反复投递。
- provider-facing worker（同步 / 下载）会在本进程内启动执行 watchdog；当 `current_job_id` 指向同步或下载任务且 `active_at` 超过 `WORKER_EXECUTION_STALE_AFTER_SECONDS` 未推进时，worker 主动退出，让 supervisor 重启并释放 PostgreSQL session 级 advisory lock。
- 保存新的平台 cookies 并恢复 provider 时，catch-up `media.sync_videos` 不会集中变为可领取；配置 API 会把它们均匀排在一个正常同步周期内，并将同媒体已有的 pending 同步（包括 public discovery）原地转换为认证恢复任务。恢复调度仍落在 `job.scheduled_for` 单一事实源中，不依赖 API 进程内计时器。
- YouTube 频道/播放列表的 `youtube_auth_check` 可能由一次瞬时网页下载失败触发。worker 在当前任务仍有剩余 attempt 时只按既有退避重试，不持久化 provider pause；仅最终尝试仍返回同一错误时才暂停 YouTube。明确 cookies 无效、`youtube_bot_check` 与其他 provider 风控仍立即暂停，避免重复请求扩大风控。
- `scheduler` 或 `worker` 启动时可执行回收扫描：
  - `status=running AND lease_expires_at < now()` 视为失联，转回 `pending` 或标记为 `failed`
  - 对下载等 provider-facing 任务，如果进程心跳新鲜但执行心跳超过 `WORKER_EXECUTION_STALE_AFTER_SECONDS` 未推进，也视为主执行循环卡死并转回 `pending`
  - 对 `media.sync_profile` / `media.sync_videos`，若进程心跳仍新鲜但执行心跳过期，按一次同步尝试失败处理：增加 `attempt`、按既有退避重试，且不使用 orphan priority bump；达到 `max_attempts` 后标记 `failed`
  - `media.sync_videos` 无论因普通异常还是执行心跳过期达到终止失败，都会推进对应媒体的 `last_video_sync_at` 作为冷却时间，避免同一媒体立即被 scheduler 重新投递并堵塞同步队列
  - 回收动作应记录原因，便于后续排障
- requeue / reschedule 必须清空旧 `execution_token`，下次 claim 再生成新 UUID；`succeeded / failed / canceled` 等终态收尾也必须清空 token。
- Worker 在 handler 返回或抛错后的收尾阶段仍需用领取时捕获的 token 锁定并核对 owner。若 job 已被回收或重领，只退出本次执行，不得覆盖新执行的 job 状态、结果或错误。

## 业务终态与可重试失败

- 下载 handler 的 `downloading` 属于当前事务内的中间状态，异常回滚后不能作为终止失败判据。下载 job 耗尽重试时，worker 会重新锁定对应 `Video` 行并查询是否已有 `Asset.type=video`：仅当视频仍为 `discovered/downloading` 且没有视频资产时改为 `failed`；已有视频资产或已进入其它可用状态时保留原状态，只记录最新错误。该收尾只发生在终止失败，不影响中间退避重试。
- `video.download.youtube` 将下载恢复分为三层。单次执行内，代理 `CONNECT ... 502`、`connection closed`、`connection reset`、媒体 URL `HTTP 502` 或解析结果 `formats` 为空时，同一媒体 URL 只执行 `retries=1`（首次请求加一次短重试）；原 selector 最多从视频页解析两轮，媒体传输仍失败时只让一个既有 fallback selector 再解析一轮。每轮使用独立临时产物，并保持 cookies、代理与 impersonation 不变。
- 单次恢复耗尽后抛出带原因码的 `YtdlpTransientDownloadError`。worker 将这类任务扩展为最多 4 次执行，前三次失败分别按 2 分钟、10 分钟、30 分钟加确定性 `±20%` jitter 写回 `job.scheduled_for`，等待期间释放 worker 与 provider 下载锁；其它下载错误仍保持最多 2 次、10 秒起步的通用退避。重复 pending job 合并时继承较大的 `attempt/max_attempts`，不能借 dedupe 合并重置自动重试预算；手动重试仍显式把 attempt 清零。
- 多视频同时出现上述瞬时错误时，worker 把观测写入 `app_config.youtube_download_circuit`。10 分钟内至少 2 个不同 job 累计 4 次失败后打开下载熔断，暂停领取新的 `video.download.youtube`；冷却 5 分钟后只放行一个 half-open 探测，探测失败依次把冷却提升到 15、30 分钟，真实下载成功后关闭熔断。该状态独立于 cookies / bot check 的 provider pause，不改变认证会话或代理配置。
- YouTube 单次格式下载即使 HTTP 层报告完成，也必须经 FFprobe 读到真实视频和音频 packet；无 packet 的临时容器按格式失败处理并进入下一个 selector。音频提取输出和 ASR 输入无音频 packet 时属于确定性坏输入，分别在资产写入和 ASR 请求前以 `JobTerminalFailure` 收口，避免重复请求同一个坏资产。
- 事件抽取把“响应顶层结构不满足协议”视为可重试错误，包括 JSON 无法解析、顶层不是对象、缺少 `videos[]`、缺少或重复预期 `video_id`、对应项缺少 `events[]`。服务会先把当前 `video_event_extraction_run` 持久化为 `failed`，再把异常抛给 worker 进入既有 `attempt/max_attempts` 退避；结构错误不会删除该视频已有事件。Ollama 使用 JSON schema 限制视频数量与每视频最多 4 个事件，防止重复展开同一视频直到触发 4000 token 上限；schema 不替代现有事实、实体与证据校验。
- 对纯 JSON 语法错误，每个抽取批次在当前任务尝试内最多额外调用一次 LLM 修复语法；修复成功后仍执行完整协议校验，修复失败才进入既有 worker 重试。缺少 `videos[]`、缺少预期 `video_id` 等已能解析但违反协议的响应不会触发修复调用，避免模型借“修复”重新生成业务内容。
- 事件抽取响应在 JSON 解析失败时会检查 LLM 结束原因与输出 token 数；`done_reason=length` 或输出达到 `num_predict` 表示内容已被截断，不属于可保真修复的 JSON 语法错误。该情况会持久化 failed run 并将当前 job 收口为终止失败，避免相同生成上限下重复修复和重试。
- `events: []` 是合法的零事件结果，会写入 `succeeded` run；空 `plain` transcript 继续按 `skipped` 成功收口，不强制失败或重试。单条事件字段不合法仍只丢弃该条并记录 warning，不能把内容质量问题扩大成整个响应的结构失败。
- 事件提示词或模型升级只改变后续抽取的复用口径，不在 API、scheduler 或 worker 启动时隐式扫描并重投全部历史视频。需要迁移历史结果时显式创建 `playlist.backfill_events(force=false)`；其子任务只复用精确匹配当前 `source_hash / prompt_version / extraction_model` 的成功运行，因此旧版本结果不会被误算成当前 v5 已完成。
- `brief.generate_period` 会先估算完整提示词输入；超过 `BRIEF_LLM_MAX_INPUT_TOKENS` 时，在当前 Job 内按来源顺序串行生成有来源链接的事实摘要，必要时执行有限轮次归并，再进行最终简报合成。该流程不创建进程内并发，也不改变原有 period 去重锚点。单个分段若偶发返回空白、过短或缺少真实来源的内容，会把上一次的校验错误显式带入原分段的串行重试；连续无效，或最终输出缺少真实来源、缺少模板规定章节、退化成通用助手回答时，当前 Job 以终止失败收口，不写入新的 `ready` 简报。
- Ollama 简报采用 `structured_brief_v2`：分段摘要通过 schema 保留最多 8 条、每条不超过 120 字的关键事实及输入中的真实 URL；最终生成按模板章节返回非空 Markdown 内容，再按模板顺序渲染。两阶段关闭显式思考，分别保留 2500 / 8000 的输出硬上限。每次调用持久化结束原因、token 数和正文/思考字符数，明确截断立即终止；不得把达到上限的部分输出标记为有效摘要或 ready 简报。非 Ollama 接口继续使用原 Markdown 生成流程；所有最终简报均拒绝不属于真实输入的来源 URL。

## Worker 角色暂停（Claim Gate）

- worker 角色暂停的事实源落在 `app_config.worker_role_pause`
- 该开关只阻止对应角色继续 claim 新任务，不会直接停止 OS 进程
- 已经 `running` 的任务保持原样，继续执行到正常结束或走现有协作式取消语义
- `ALL` worker 也必须遵守角色暂停门控，不能绕过已暂停的具体角色
- 专属 role worker 在对应角色已暂停时，应在查询 `job` 热表前直接跳过 claim，避免大量 pending 积压时“暂停但空转扫描队列”。

## 观测域持续观测开关

- `playlist.observation_enabled` 是观测域级业务事实，不是 worker role pause，也不改变 worker、连接或已认证会话生命周期。
- 停用域继续执行来源同步、视频/字幕下载、字幕规范化与 ASR 转写归档；新的转写润色、事件抽取、事件向量、自动简报和星域构建在投递点与 handler 入口共同检查状态。
- 视频级任务按所有 `PlaylistMedia` 关联判断：至少一个关联域启用时共享任务仍执行一次；简报与星域等域级任务只面向启用域。
- 已经 `running` 的任务不被强制取消；排队后才遇到停用状态的认知任务以成功 `skipped` 收口。重新启用时投递 `playlist.backfill_events` 和 `playlist.mark_event_map_dirty`，沿用既有去重与租约语义。

## 幂等策略（At-least-once 友好）

- 数据写入侧通过唯一键 + upsert 保证重复任务不会重复产出
  - `videos(provider, provider_video_id)` 唯一；`media.sync_videos` 发现新视频时用该唯一键执行 `ON CONFLICT DO NOTHING`
  - `assets(video_id, type, format, language, source, variant)` 可按业务定义唯一
- Job 层可设置 `dedupe_key`
  - 例如 `media.sync_videos:{media_id}`
  - 例如 `video.enrich_metadata.youtube:{video_id}`
  - 事件模型迁移使用 `event_embedding_backfill:{source_model}:{target_model}:{dimension}`；该类型在事务级 advisory lock 内同时查询 pending/running，避免长任务运行时被重复投递。
  - PostgreSQL 下，普通 pending dedupe job 使用既有 `job(dedupe_key) where dedupe_key is not null and status='pending'` 唯一索引执行 `INSERT ... ON CONFLICT ... RETURNING id`。冲突时返回已有任务 ID，保留其参数、排期、优先级与重试预算，且不重复写入 `enqueued` 事件。任务与事件仍和调用方业务数据在同一事务内提交或回滚。
  - `playlist.mark_event_map_dirty` 的 outbox 参数合并，以及 `event.backfill_embeddings` 的 pending/running 去重，继续按 `dedupe_key` 获取事务级 advisory lock。普通任务不再逐 key 持有事务锁或创建入队保存点，避免万级频道全量同步将锁共享内存耗尽。
  - 同一事务需要投递多个 dedupe job 时，调用方必须按稳定 key 顺序投递，避免多个 worker 对同一批 key 反向等待。
  - 手动重试失败任务时，若同一 `dedupe_key` 已有 pending 任务，重试接口会取消那个 pending 任务并复用当前任务，避免提交时撞 pending dedupe 唯一约束
  - 自动重试合并到同一 `dedupe_key` 的 pending 任务时，会同步继承已经消耗的 `attempt` 和当前 `max_attempts`，避免通过重复投递绕过有界重试

事件地图快照额外使用执行级 staging 所有权：

- `event_map_snapshot(job_id, execution_token)` 唯一；同一个 job 被重领后使用新 token 创建新的 staging snapshot。
- `job_attempt` 仅用于审计和排障，不参与 staging 身份或写入授权，因为 lease 回收和重领未必能靠 attempt 唯一区分。
- checkpoint、ready finalize 和失败收尾都必须校验领取时捕获的 worker/token。失权执行不得写 snapshot，也不得更新 `event_map_state.last_error` 或 current snapshot 指针。
- ready 切换后由独立的 `playlist.prune_event_map_snapshots` analysis job 做保留清理；它按播放列表去重、每次只删除一个旧快照并再次投递自己，current、上一版 ready 与所有运行中 staging 始终受保护。清理不进入 API 请求线程，也不扩大 ready 原子切换事务。
- 快照构建发现同域仍有 `playlist.mark_event_map_dirty` 处于 `pending/running` 时，以 `superseded_by_active_dirty` 收口且不再投递新的构建任务；必须先让 dirty outbox 任务推进 generation 并负责后续构建，避免过期的构建计划时间反复抢占 analysis worker。
- 应用启动迁移会先查询目录并跳过已经存在的索引和已经生效的表级分析参数；没有旧分析任务时也不会执行空 `UPDATE job`。这样启动进程不会在持有 job 表锁时等待事件地图大表 DDL 锁，避免与正在清理/构建的 analysis worker 形成锁顺序死锁。
- 启动迁移的 schema 变更与 V2 历史回填使用两个连续事务：schema 先提交并释放关系锁，再从仍保留的 ready 快照幂等写入新增历史表。初始化 advisory lock 在两个事务期间保持，用于阻止多个启动进程重复迁移，但不能以长事务阻塞运行中 worker 对业务表的写入。

事件 embedding 全量迁移同样使用执行级所有权：每批先在无写事务状态调用 embedding 服务，再以 `status=running + worker_id + execution_token` 锁回 Job，复核事件仍为 accepted 且文本 checksum 未变化后，单事务更新向量行与 Job checkpoint。失权、取消或批次写入异常都不会提交当前批；此前批次已经独立提交，重试从“不存在目标模型 ready 向量”的事件继续扫描。迁移完成前不逐事件投递地图 dirty，完成后才按播放列表各投递一次全量地图重建信号。

## 并发与外部依赖门控（Guardrails）

外部依赖容易触发风控或互相争抢资源，MVP 建议至少控制两层并发：

- Provider 级并发：如 YouTube 同时下载数、B 站同时下载数
- Media 级并发：同一媒体同一时刻只允许 1 个同步 / 下载任务；`media.sync_videos` 运行时会持有事务级媒体 advisory lock，拿不到锁时重排队
- ASR 后端容量：当 qwen3-asr-openai `/health` 显示 replica 推理槽位已满或已有等待队列时，worker claim 会跳过 `video.asr_transcribe`，让任务继续留在 DB 的 `pending` 队列中等待后端释放容量
- ASR 长视频超时：本地 `video.asr_transcribe` 会在 `ASR_TIMEOUT_SECONDS` 基础上按媒体时长动态放大请求 timeout，并同步延长该 job 的 lease；火山 ASR 保持 provider 自身的既有超时行为

当前项目对下载并发采用“强绑定语义”：

- `YOUTUBE_DOWNLOAD_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY` 的公开语义是对应 provider 的真实最大下载并发数
- 当 `*_DOWNLOAD_CONCURRENCY = N` 时，运行层默认会拉起至少 `N` 个对应的 `download_*` worker 进程
- `video.download.*` 与 `video.backfill_subtitles.*` 共享对应 provider 的下载角色和 advisory lock；字幕回补不会绕过平台并发上限
- `video.enrich_metadata.youtube` 归属 `sync` worker role，受 YouTube provider pause 与 sync provider advisory lock 控制；sync 锁繁忙时重排当前补全任务，不占用下载并发。
- provider advisory lock / guard slot 只负责做最终上限保护和防风控，不作为对外配置语义

同步、下载与事件抽取的 session-level advisory lock 使用从现有 Engine 连接池独立借出的物理连接，领取和释放始终在同一连接上进行；业务 Session 的提交、回滚或 SQL 异常不会释放门控锁，也不会使解锁进入 aborted 事务而覆盖首个异常。离开门控范围后先解锁，再将连接归还原池。该连接只负责 PostgreSQL 并发门控，不创建或重置 YouTube cookies、yt-dlp client 或其他上游认证会话；事务级媒体锁与特殊入队锁仍随业务事务结束释放。每个持有此类门控的 worker 会额外占用一条池内连接，运行容量见 [配置说明](../reference/configuration.md)。

锁生命周期与原子入队依据：[PostgreSQL 16 advisory locks](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS)、[SQLAlchemy PostgreSQL ON CONFLICT](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert)。

实现方式可从易到难演进：

1. 进程内信号量（适用于单 worker）
2. PostgreSQL advisory lock（适用于跨 worker 实例）

示例：

- `pg_try_advisory_lock(hashtext('youtube:download'))`
- `pg_try_advisory_lock(hashtext('media:{media_id}:sync'))`

## 可观测性（Visibility by Design）

- 每个 job 记录 `type / status / params / result / error / attempt / worker_id / execution_token`
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
