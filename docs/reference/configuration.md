# 配置项说明

这份文档只记录当前运行配置，不承载架构设计或接口说明。配置分为两类：

- 环境变量：进程启动时读取，主要来自 `.env`。
- 运行时配置：保存在 `app_config`，通过 `/api/config/*` 修改，通常无需重启即可生效。

## 环境变量

### 核心运行

- `APP_ENV`
- `BASE_URL`
- `TIMEZONE`
- `API_BEARER_TOKEN`
  - 为空时不启用主站 API 鉴权。
  - 非空时 `/api/*` 需要 `Authorization: Bearer <token>` 或 `raelyn_api_token` cookie。
  - `/api/ws/*` 需要 query `token=<token>`。
  - 非空时主 API 进程也会额外挂载 `/mcp`，MCP HTTP 复用同一个 Bearer Token。
  - Docker Compose 部署要求显式设置非空随机值，不提供可公开复用的默认 token。

### 数据与对象存储

- `DATABASE_URL`
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`
  - Docker Compose 用这三项初始化 PostgreSQL，并据此构造 app 容器内的 `DATABASE_URL`。
  - `POSTGRES_PASSWORD` 必须通过环境变量提供，且应使用可直接放入 URL 的 URI 安全字符串。
- `DATABASE_POOL_SIZE`
  - 每个 API / worker / scheduler 进程保留在 SQLAlchemy 连接池中的常驻 DB 连接数，默认 `2`。
- `DATABASE_MAX_OVERFLOW`
  - 单进程在连接池已满时允许临时额外打开的 DB 连接数，默认 `2`；空闲后不会作为常驻连接保留。
- `DATABASE_POOL_TIMEOUT_SECONDS`
  - 获取 DB 连接的等待超时，默认 `30` 秒。
- `EVENT_MAP_ENTITY_QUERY_TIMEOUT_SECONDS`
  - 事件地图实体排行和实体索引查询的事务级 PostgreSQL 超时，默认 `10` 秒；超时只终止当前请求，不污染连接池后续事务。
- `EVENT_MAP_READY_SNAPSHOT_RETENTION`
  - 每个播放列表保留的 ready 事件地图快照数，默认 `2`（current 与上一版）；更旧快照由 analysis worker 的保留任务逐个清理。
- `S3_ENDPOINT`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
  - Docker Compose 使用这两项初始化 MinIO root 凭据并传给 app，不提供弱默认凭据。
- `S3_REGION`
- `S3_BUCKET`
  - 应与 `asset.s3_bucket` 中的实际 bucket 保持一致；`/api/stats` 会在检测到配置 bucket 与唯一实际 bucket 不一致时回退统计实际 bucket，并返回 mismatch 标记。
- `S3_USE_SSL`
- `S3_TRANSFER_MAX_CONCURRENCY`
  - 单个 `upload_file` / `download_file` 传输允许的分片并发数，默认 `2`。该值是“每个文件、每个进程”的上限，不是所有 worker 的全局上限。
- `S3_TRANSFER_MULTIPART_THRESHOLD_BYTES`
  - 文件达到该大小后使用 multipart 传输，默认 `67108864`（64 MiB）。
- `S3_TRANSFER_MULTIPART_CHUNKSIZE_BYTES`
  - multipart 分片大小，默认 `67108864`（64 MiB），不得小于 5 MiB。

说明：

- 连接池配置只影响非 SQLite 数据库；SQLite 会继续使用 SQLAlchemy 对应 URL 的默认池实现。
- 当前运行形态会启动 API、scheduler 和多个角色 worker，DB 连接上限约为 `进程数 * (DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW)`。默认值用于单用户部署，避免每个进程沿用 SQLAlchemy 默认连接池后保留过多 PostgreSQL backend。
- S3 传输配置只作用于落盘文件的 `upload_file` / `download_file`；代理流式读取和小对象 `get_object` 不启用 multipart 线程。
- 当前 4 块 HDD 的生产默认值采用单文件并发 `2` 与 64 MiB 分片，避免一个大文件独占过多磁盘寻道和连接。若同时运行多个 download/process worker，总并发仍会按活跃传输数叠加，调大前应同时观察磁盘等待与 MinIO 负载。
- S3 client 在调用它的 API 或 worker 进程内按操作创建，不放入模块级全局缓存，也不会跨 fork/进程共享已建立的连接或认证状态。修改上述环境变量后需要重启对应进程才会生效。

### 资产分发策略

- `ASSET_DIRECT_PROBE_URL`
  - 前端启动时用于探测是否能直接访问对象存储。
  - 若页面本身运行在 `https://` 下，而该地址是 `http://`，前端会直接判定为 mixed content 风险并回退到代理模式，不再发起直连探测。
- `ASSET_DIRECT_PROBE_TIMEOUT_MS`
- `ASSET_PROXY_BASE_PATH`
  - 默认 `/api/assets`
- `ASSET_PRESIGN_ENABLED`
  - 默认 `false`，生产环境默认使用 `ASSET_PROXY_BASE_PATH` 对应的同源 Python 代理读取资产。
  - 只有显式设为 `true` 时，后端才会生成 presigned URL，前端才会探测并尝试对象存储直连。

说明：

- 默认 proxy-only 模式下，视频、头像、播放列表图片和 Markdown 等资产都通过 `/api/assets/{asset_id}/content` 或 `/download` 访问；代理接口支持 `Range`，可用于视频播放和断点读取。
- HTTPS 主站下，若对象存储或 presigned URL 仍是 `http://`，前端不会使用直连资源，而会统一退回 `ASSET_PROXY_BASE_PATH` 对应的 API 代理路径。
- 若希望启用直连 / presigned URL，需要让对象存储出口可被浏览器访问；HTTPS 主站下对象存储出口本身也必须提供 HTTPS。

### 高优先级代理规则

- `YTDLP_PROXY` 只用于 YouTube 的 `yt-dlp` 资料同步、频道头像下载和视频同步 / 下载请求。
- 非 `yt-dlp` 的资料 / 头像抓取、B 站请求、ASR / LLM / Embedding / 健康检查都不使用 `YTDLP_PROXY`。
- 应用默认不隐式读取进程环境中的 `HTTP_PROXY` / `HTTPS_PROXY`；这些变量存在于 shell 或 systemd 环境里，不代表本应用会把外部请求送进代理。
- 若 YouTube 同步/下载需要代理，必须配置 `YTDLP_PROXY`，不要依赖 `HTTP_PROXY` / `HTTPS_PROXY` 的副作用。
- 若运行环境本身设置了 `HTTP_PROXY` / `HTTPS_PROXY`，需要让 `NO_PROXY` / `no_proxy` 包含 `127.0.0.1`、`localhost`、`::1` 和 `host.docker.internal`，避免 bgutil provider、MinIO 等本机服务被环境代理劫持。

### 工具与下载

- `FFMPEG_BIN`
- `AUDIO_CODEC`
- `AUDIO_BITRATE`
- `AUDIO_SAMPLE_RATE_HZ`
- `AUDIO_CHANNELS`
- `YTDLP_PROXY`
  - 仅 YouTube 的 `yt-dlp` 同步 / 下载请求会显式使用该代理；完整边界见上方“高优先级代理规则”。
- `NO_PROXY` / `no_proxy`
  - 推荐包含 `127.0.0.1,localhost,::1,host.docker.internal`。
  - `scripts/dev/load-env.sh` 和 Docker entrypoint 会自动补齐这些本机地址。
- `YTDLP_REMOTE_COMPONENTS`
  - 默认 `ejs:github`
  - 只用于 YouTube EJS / JS challenge 组件，不等同于 PO Token Provider。
- `YTDLP_POT_BGUTIL_BASE_URL`
  - 可选；bgutil PO Token Provider HTTP server 地址。空值表示不启用。
  - 本机运行常用 `http://127.0.0.1:4416`；Docker Compose 的 app 容器内使用 `http://host.docker.internal:4416`。
- `YTDLP_YOUTUBE_IMPERSONATE`
  - 默认 `chrome`，仅用于 YouTube 的 `yt-dlp` 同步 / 下载请求。
  - 依赖 `curl_cffi`；空值表示不启用浏览器 impersonation。
- `YTDLP_FORMAT`
  - 作为默认格式选择器；若运行时配置 `ytdlp_format.text` 存在，会优先使用运行时配置。
  - YouTube 下载默认优先 combined MP4/HLS，再回退 DASH video-only；这是为了避开 2026-05-18 实测中 Bloomberg 样本 360p+ DASH video-only GVS URL 返回 `HTTP Error 403` 的路径。

### 同步与并发

- `SYNC_INTERVAL_MINUTES`
- `SYNC_INTERVAL_JITTER_MINUTES`
  - 频道自动同步在基础间隔之后，按媒体与上次同步时间稳定增加 `0..N` 分钟抖动，避免大量频道同一分钟集中请求。
- `SYNC_BATCH_SIZE`
  - `scheduler` 每分钟扫描到期媒体时，单次最多投递的 `media.sync_videos` 数量；`SYNC_INTERVAL_MINUTES` 约束的是单个媒体的同步间隔，不表示全站每小时只投递一个同步任务。
  - 当前推荐值为 `2`，用于降低 YouTube / B 站同步请求波峰；媒体数量较多时，追赶积压会更慢，但更不容易触发平台风控。
- `SYNC_MAX_ENTRIES`
- `SYNC_PUBLIC_DISCOVERY_ENABLED`
  - 默认 `true`。当 provider 因 `ytdlp_cookies_*`、`youtube_bot_check` 或 `youtube_auth_check` 暂停时，允许 `media.sync_videos` 以 `public_discovery=true` 显式无 cookies 抓公开视频 flat 列表。
  - 该降级只用于视频发现；下载、字幕回补和 YouTube metadata 补全仍受 provider pause 阻断。
- `SYNC_PUBLIC_DISCOVERY_MAX_ENTRIES`
  - public discovery 单次抓取的 flat 列表上限，默认 `200`。
- `SYNC_COOKIE_RECOVERY_MAX_ENTRIES`
  - 保存有效非空平台 cookies 并清除 provider pause 后，自动为该 provider 受监控媒体补投 catch-up 同步的 `max_entries`，默认 `200`。
  - 恢复任务不会同时变为可领取：系统按媒体稳定顺序把它们均匀排在一个 `SYNC_INTERVAL_MINUTES` 周期内，避免新 cookies 保存后立刻形成全量扫描波峰。
  - 若同一媒体已有 pending 同步（包括 public discovery），同一任务会原地转换为认证恢复任务并按恢复窗口重新排期，不会额外保留一条立即执行的扫描。
- `AUTO_DOWNLOAD_NEW_VIDEOS`
- `AUTO_GENERATE_BRIEFS`
  - 是否在 transcript 就绪、媒体变更或媒体删除后自动投递 `brief.generate_period`，默认 `false`。
  - 关闭时不会删除已有 `brief` / `daily_brief` 数据，也不影响手动 `POST /api/briefs/generate` 和 `POST /api/briefs/generate_range`。
- `BRIEF_LLM_MAX_INPUT_TOKENS`
  - 简报最终合成与每次分段摘要的输入预算，默认 `24000`。输入超过预算时，`brief.generate_period` 会在同一个 Job 内按来源顺序执行有界、串行的事实摘要，再合成最终简报；不在单个任务内增加 LLM 并发。
  - 使用 Ollama `/api/generate` 时，实际预算还会限制为 `LLM_OLLAMA_NUM_CTX` 的 70%，为最终输出预留上下文。分段摘要或最终正文缺少真实来源、缺少提示词规定的章节，或退化成通用助手回答时，任务会明确失败，不再把内容标记为 `ready`。
- `STATS_CACHE_TTL_SECONDS`
  - `/api/stats` 的进程内缓存 TTL，默认 `60` 秒；设置为 `0` 可关闭缓存。
- `YOUTUBE_SYNC_CONCURRENCY`
- `BILIBILI_SYNC_CONCURRENCY`
- `YOUTUBE_DOWNLOAD_CONCURRENCY`
  - 表示 YouTube 的真实最大下载并发数，不只是内部 provider 槽位数。
  - 当值为 `N` 且 `N > 1` 时，运行层默认应启动至少 `N` 个 `download_youtube` worker 进程。
  - handler 内仍会使用 provider advisory lock 做最终上限保护；该锁是内部实现细节，不改变本配置的公开语义。
- `BILIBILI_DOWNLOAD_CONCURRENCY`
  - 表示 B 站的真实最大下载并发数，不只是内部 provider 槽位数。
  - 当值为 `N` 且 `N > 1` 时，运行层默认应启动至少 `N` 个 `download_bilibili` worker 进程。
  - handler 内仍会使用 provider advisory lock 做最终上限保护；该锁是内部实现细节，不改变本配置的公开语义。
- `ASR_WORKER_CONCURRENCY`
  - `devctl.sh` / Docker 单容器入口启动 `asr` worker 的进程数，默认 `1`。
  - 每个 `asr` worker 同一时间只执行一个 `video.asr_transcribe`，因此该值决定 ASR 远端转写请求的进程级并发上限。
  - 该配置只增加 worker 进程数，不改变 ASR 请求的认证、连接复用或缓存行为。
- `ASR_BACKEND_CAPACITY_GUARD_ENABLED`
  - 是否在本地 OpenAI-compatible ASR worker 领取任务前探测 `/health` 并根据 qwen3-asr-openai 的 `backend_replicas` / `in_flight` / `backend_queue_waiters` 暂停领取 ASR 任务，默认 `true`。
  - 该门控只影响 `video.asr_transcribe` 的任务调度节奏，不改变 ASR 请求体、认证、连接复用或后端模型参数。
- `ASR_BACKEND_CAPACITY_DEFER_SECONDS`
  - 当 ASR `/health` 没有返回 `backend_queue_timeout_seconds` 时，handler 兜底重排 ASR 任务的默认延后秒数，默认 `30`。
- `ASR_TIMEOUT_SECONDS`
  - 本地 ASR 请求的短音频基础超时，默认 `600` 秒。
  - `video.asr_transcribe` 处理本地 ASR 长视频时会按媒体时长动态放大 HTTP timeout：约按 `4x realtime + 120s` 估算，不设固定 3300 秒上限；发起请求前会把 job lease 延长到 timeout 后 300 秒，避免超长请求尚未完成就被回收。
  - 本地 ASR `ReadTimeout` 最多执行 3 次；第三次仍超时则终止并保留人工重试。火山 ASR 继续使用原有 provider 超时行为。
- `EMBEDDING_WORKER_CONCURRENCY`
  - `devctl.sh` / Docker 单容器入口启动 `embedding` worker 的进程数，默认 `1`。
  - 可设为 `0`，表示当前节点不启动 `embedding` worker；`event.embed` 与 `event.backfill_embeddings` 会保留在 `pending`，直到有 embedding worker 可领取。
- `ANALYSIS_WORKER_CONCURRENCY`
  - `devctl.sh` / Docker 单容器入口启动 `analysis` worker 的进程数，默认 `1`。
  - 可设为 `0`，表示当前节点不启动 `analysis` worker；`playlist.mark_event_map_dirty`、`playlist.build_event_map_snapshot` 与 `playlist.prune_event_map_snapshots` 会保留在 `pending`。
  - 新记录按 2 分钟静默、15 分钟最大等待合并为地图快照；需要事件地图自动更新时至少保留 1 个。
- `ANALYSIS_CPU_THREADS`
  - 每个 `analysis` worker 的 CPU 线程预算，默认 `2`。
  - Python worker 入口会在导入 NumPy / sklearn 前，把该值统一设置给 `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS` 和 `MKL_NUM_THREADS`；开发脚本、Docker 与直接执行 `python -m raelyn.worker` 的行为一致。
  - 该配置作用于 `analysis`、all-types 及 `WORKER_TYPES` 显式包含 analysis 任务的 worker，不改变 API、scheduler 或其他专职 worker 的线程环境；修改后必须重启对应 worker 才会生效。
- `AI_WORKER_CONCURRENCY`
  - `devctl.sh` / Docker 单容器入口启动 `ai` worker 的进程数，默认 `1`。
  - 每个 `ai` worker 同一时间执行一个 LLM 任务，例如 `video.extract_events`、播放列表事件回填范围扫描、转写润色或简报生成。
  - 大型同播放列表事件回填可适当提高该值；AI worker 只竞争单个视频自己的事件写入，播放列表 dirty 由 `analysis` worker 的 `playlist.mark_event_map_dirty` 合并。
  - 该配置只增加 worker 进程数，不改变 LLM 请求认证、连接复用或模型参数；提升前应确认 LLM 服务可承受对应并发。Ollama `/api/generate` 事件抽取还会额外使用 per-model advisory lock，忙时重排任务，避免多个事件抽取请求同时压到同一个本地大模型。
- `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES`
  - 事件地图快照开始和每批处理中允许继续执行的最低 `/proc/meminfo` `MemAvailable`，默认 `1073741824`（1 GiB）。
  - 低于该值时任务直接失败并记录原因，不进入重试队列。
- `ANALYSIS_MAX_RSS_BYTES`
  - 事件地图快照允许的最大进程树 RSS，代码默认值和 `.env.example` 都是 `12884901888`（12 GiB）。流式处理每批检查 worker RSS；UMAP 阶段检查父任务与投影子进程的合计 RSS。
  - 高于该值时任务直接失败并记录原因，不进入重试队列；生产环境若使用 systemd / cgroup，应设置相同或略高的硬上限。
  - 该值是安全上限，不是预分配内存。当前 32 GiB 主机为快照任务保留至少 1 GiB `MemAvailable`，并把峰值写入 `event_map_snapshot.peak_rss_bytes`；调高时必须同步核对同机竞争和 cgroup 上限。
- `ANALYSIS_STREAM_BATCH_SIZE`
  - 事件地图输入冻结、投影 memmap、IncrementalPCA 和对象写库的批大小，代码默认 `2000`；`.env.example` 可给部署提供更保守的覆盖值。
- `EVENT_EXTRACTION_CHUNK_MAX_CHARS`
  - `video.extract_events` 读取 `plain` transcript 后的 LLM 分块字符上限，默认 `12000`。
- `EVENT_EXTRACTION_OLLAMA_STREAM`
  - 当有效 LLM URL 为 Ollama `/api/generate` 时，事件抽取是否使用流式响应，默认 `true`。
- `EVENT_EXTRACTION_OLLAMA_NUM_PREDICT`
  - Ollama 事件抽取请求的 `options.num_predict`，默认 `4000`，用于限制 JSON 生成不会无限延长。当无法解析的响应以 `done_reason=length` 结束，或输出 token 数达到该上限时，任务会明确记录为输出截断；不再使用相同上限调用 JSON 修复，也不重复执行确定性相同的 job attempt。
- `EVENT_EXTRACTION_OLLAMA_IDLE_TIMEOUT_SECONDS`
  - Ollama 事件抽取流式读取时的无输出读超时，默认 `120`。超过该时间没有任何流式输出会失败并进入任务重试/重排链路。
- `EVENT_EXTRACTION_OLLAMA_BUSY_DEFER_SECONDS`
  - 同一个 Ollama endpoint + model 已有事件抽取请求在运行时，后续事件抽取任务重排的延后秒数，默认 `15`。
- `EVENT_EXTRACTION_OLLAMA_CHUNK_MAX_CHARS`
  - Ollama 事件抽取的 transcript 分块上限封顶值，默认 `6000`。实际分块上限取 `EVENT_EXTRACTION_CHUNK_MAX_CHARS` 与该值的较小者，控制单次 prompt 规模。
- `AUTO_EXTRACT_NEW_VIDEO_EVENTS`
  - 是否在新视频 transcript 生成后自动投递 `video.extract_events`，默认 `true`。
  - 关闭时不会影响播放列表页面手动触发的 `playlist.backfill_events` 历史回填。

### Worker 心跳与孤儿任务回收

- `WORKER_RESTART_DELAY_SECONDS`
  - `devctl.sh` 和 Docker 单容器入口用于自动拉起崩溃 worker 子进程的等待秒数，默认 `5`。
  - 该配置只影响 worker 子进程；API 或 scheduler 这类关键进程退出时，Docker 单容器入口仍会退出，让外层进程管理器处理整体故障。
- `WORKER_HEARTBEAT_INTERVAL_SECONDS`
  - worker 进程心跳线程写入 `worker_heartbeat.updated_at` 的间隔，默认 `5` 秒。
- `WORKER_STALE_AFTER_SECONDS`
  - 进程心跳超过该阈值未更新时，其他 worker 可将其 `running` 任务回收到 `pending`，默认 `20` 秒。
- `WORKER_EXECUTION_STALE_AFTER_SECONDS`
  - 同步/下载类任务额外检查主执行线程活动心跳 `worker_heartbeat.active_at`，默认 `120` 秒。
  - 该阈值用于发现“心跳线程仍活着，但主执行循环已经卡死”的情况；YouTube 全量同步按 yt-dlp 的真实分页与条目日志刷新执行心跳，下载任务按下载进度刷新。
  - 同步/下载类 worker 超过该阈值未推进时会主动退出，由 supervisor 重启并释放对应并发锁。
- `ORPHAN_REQUEUE_PRIORITY_BUMP`
  - 孤儿 `running` 任务被回收后提升的优先级基数，默认 `1000`，用于让回收任务回到队头。

### ASR / LLM / Embedding

本地模式：

- `ASR_URL`
- `ASR_ENDPOINT`
- `ASR_MODEL`
- `ASR_PROMPT`
- `ASR_LANGUAGE`
- `ASR_TEMPERATURE`
- `ASR_RESPONSE_FORMAT`
- `ASR_TIMEOUT_SECONDS`
- `LLM_URL`
- `LLM_MODEL`
- `LLM_API_KEY`
- `LLM_HEADERS_JSON`
- `LLM_TIMEOUT_SECONDS`
- `LLM_OLLAMA_NUM_CTX`
  - 所有 Ollama `/api/generate` 请求统一使用的 `options.num_ctx`，默认 `32768`。事件抽取、文字稿润色和简报生成共享该值，避免同一模型因上下文参数变化反复卸载重载；旧变量 `EVENT_EXTRACTION_OLLAMA_NUM_CTX` 仍作为兼容别名读取。修改后需重启 `ai` worker。

事件抽取 / 事件图谱：

- `EVENT_EXTRACTION_CHUNK_MAX_CHARS`
- `EVENT_EXTRACTION_OLLAMA_STREAM`
- `EVENT_EXTRACTION_OLLAMA_NUM_PREDICT`
- `EVENT_EXTRACTION_OLLAMA_IDLE_TIMEOUT_SECONDS`
- `EVENT_EXTRACTION_OLLAMA_BUSY_DEFER_SECONDS`
- `EVENT_EXTRACTION_OLLAMA_CHUNK_MAX_CHARS`
- `AUTO_EXTRACT_NEW_VIDEO_EVENTS`
- `EMBEDDING_URL`
- `EMBEDDING_ENDPOINT`
- `EMBEDDING_MODEL`
  - 事件 embedding 默认模型为 `Qwen/Qwen3-Embedding-4B`。模型改变后，旧模型向量不能继续与新模型向量混用，应通过可恢复的全量回填任务重算。
- `EMBEDDING_DIM`
  - 默认 `1024`；事件向量、地图快照与登录门面匿名样本的迁移必须保持该维度一致。
- `EMBEDDING_TIMEOUT_SECONDS`
- `EMBEDDING_WORKER_CONCURRENCY`
- `ANALYSIS_CPU_THREADS`
- `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES`
- `ANALYSIS_MAX_RSS_BYTES`
- `ANALYSIS_STREAM_BATCH_SIZE`
- `AUTO_GENERATE_BRIEFS`

Embedding 健康检查固定探测 `${EMBEDDING_URL}/health`；实际向量请求才使用 `EMBEDDING_ENDPOINT`，默认 `/v1/embeddings`。

火山模式默认值：

- `VOLCENGINE_LLM_URL`
- `VOLCENGINE_LLM_MODEL`
- `VOLCENGINE_LLM_API_KEY`
- `VOLCENGINE_LLM_TIMEOUT_SECONDS`
- `VOLCENGINE_ASR_URL`
- `VOLCENGINE_ASR_MODEL`
- `VOLCENGINE_ASR_APP_KEY`
- `VOLCENGINE_ASR_ACCESS_KEY`
- `VOLCENGINE_ASR_RESOURCE_ID`
- `VOLCENGINE_ASR_TIMEOUT_SECONDS`

说明：

- `local` 模式只读取本地 `.env` 与自托管推理服务配置。
- `volcengine` 模式优先读取运行时配置 `app_config`；若某些字段未配置，则回退到对应的 `VOLCENGINE_*` 环境变量默认值。
- `ASR_LANGUAGE` 是本地 OpenAI-compatible ASR 的可选输入语言提示，默认空值。空值时请求体不传 `language`，由模型自动识别；明确知道音频全英文时可设为 `en`；混合语音应保持空值。
- `video.asr_transcribe` 保存 `qwen3-asr` 的 `plain` / `segments` transcript 时，优先使用 ASR 响应里的实际语言；响应未返回语言时才使用 `ASR_LANGUAGE` 提示值。两者都没有时，资产 `language` 为空，S3 路径使用 `transcript/und/`。
- 现有 `video.polish_transcript` 提示词仍是中文整理口径；自动 ASR 链路只在 transcript 语言为 `zh` 时继续投递 polish，非中文 plain transcript 先保持原文。
- ASR / LLM / Embedding 的健康检查、容量门控、实际请求与配置测试连接都会忽略进程环境中的 `HTTP_PROXY` / `HTTPS_PROXY`；对应 URL 应直接指向可达服务地址。
- UI 不直接修改 `.env`；保存设置后只影响新任务，不会中断正在运行的任务。

### Worker 进程选择

- `WORKER_ROLE`
  - 支持 `download_youtube`、`download_bilibili`、`audio`、`process`、`asr`、`sync`、`embedding`、`analysis`、`ai`、`all`
- `WORKER_TYPES`
  - 逗号分隔的 job type 列表，优先级高于 `WORKER_ROLE`

### MCP HTTP

- `MCP_BASE_PATH`
- `MCP_ALLOWED_HOSTS`
  - 逗号分隔的 Host 白名单，用于 MCP SDK 的 DNS rebinding 防护。
  - 反向代理公网访问时，需要把外部 Host 加进去，例如 `raelyn.example.com:234`。
- `MCP_ALLOWED_ORIGINS`
  - 逗号分隔的 Origin 白名单。
  - 反向代理公网访问时，通常与 `MCP_ALLOWED_HOSTS` 对应，例如 `https://raelyn.example.com:234`。

## 运行时配置（`app_config`）

### 平台 Cookies

- `ytdlp_cookies_youtube`
- `ytdlp_cookies_bilibili`

值结构：

```json
{ "text": "<netscape cookies.txt>" }
```

说明：

- 通过 `/api/config/{key}` 写入。
- `GET /api/config` 只返回 `{ "configured": true|false }`，不会回显 Cookie 内容。
- 服务运行时会把内容写到 `tmp/ytdlp_cookies_*.txt` 供 yt-dlp / profile fetch 使用。
- 更新后若系统是因为 Cookies 失效被自动暂停，会尝试自动恢复。
- YouTube cookies / bot check / PO Token 的运行策略见 [YouTube yt-dlp 同步与 Cookies 策略](youtube-ytdlp-strategy.md)。

### 字幕与会员视频

- `ytdlp_subtitles`
- `ytdlp_members_only`

值结构：

```json
{ "enabled": true }
```

说明：

- `ytdlp_subtitles` 控制是否下载字幕 / 自动字幕。
- 字幕下载默认请求明确语言码 `zh-Hant`、`zh-Hans`、`zh-CN`、`zh-TW`、`zh-HK`、`zh`、`en`，避免使用通配符抓取大量机器翻译派生字幕。
- B 站自动字幕在 yt-dlp metadata 中可能暴露为 `ai-zh` / `ai-en`；字幕回补任务会在 B 站目标语言列表里显式加入这些语言码。
- 显式字幕回补任务 `video.backfill_subtitles.*` 不受 `ytdlp_subtitles.enabled` 限制：它只用 yt-dlp `skip_download` 抓字幕 / 自动字幕，不下载视频文件，成功后写入 `asset(type=subtitle, source=ytdlp, variant=raw)`，再投递 `video.normalize_subtitle`。
- `ytdlp_members_only` 控制是否尝试下载 YouTube 会员专享视频。

### 下载格式

- `ytdlp_format`

值结构：

```json
{ "preset": "1080|720|custom", "text": "<yt-dlp format selector>" }
```

说明：

- 若存在此项，下载时优先于环境变量 `YTDLP_FORMAT`。
- YouTube 自定义格式若优先选择 `bestvideo+bestaudio` 这类 DASH video-only 组合，可能重新触发媒体 URL `HTTP Error 403`。排障时优先尝试 `best[ext=mp4][height<=1080]` 或 `best[protocol^=m3u8][height<=1080]`。
- 格式类型、selector 顺序和 403 排障步骤见 [yt-dlp 视频 / 音频格式选择策略](ytdlp-format-selection.md)。

### 转写润色提示词

- `llm_transcript_polish_prompt`

值结构：

```json
{ "text": "<prompt template>" }
```

说明：

- 用于 `video.polish_transcript`。
- 建议保留 `{chunk}` 占位符；`{index}` / `{total}` 可选。
- 默认提示词定位为“可回溯证据的保真整理稿”：按原文顺序近逐句整理，保留金融、宏观、政策、地缘、企业、行业、资产价格、利率、信用、商品、库存、财报、订单、指引、风险偏好等证据粒度；不得把长转写压缩成摘要；数字表达按原文保留，不做中文数字改写或单位换算。
- `video.polish_transcript` 默认按约 `1500` 字符切分原始转写后逐段调用 LLM；若单段调用超时，会继续二分该段，直到最小重试粒度仍失败时再让任务失败，降低保真输出过长导致单次调用超时的概率。
- 调用 LLM 前会对阿拉伯数字和英文数字短语做临时占位保护，返回后还原原文数字表达，避免模型把 `four point seven eight percent`、`ten billion` 等证据改写成中文数字或换算金额。

### 事件抽取提示词

- `llm_event_extraction_prompt`

值结构：

```json
{ "text": "<prompt template>" }
```

说明：

- 用于 `video.extract_events` / `video.extract_events_batch` 从视频标题、描述与 transcript source map 抽取结构化市场原子事件。
- 当前事件抽取最终请求会追加 v3 输出协议：顶层为 `videos[]`，事件证据使用 `evidence_source_ids` 引用输入 source id，后端再写入 verified provenance。自定义 prompt 若描述旧 `events[]` 或 `evidence_quotes` 格式，以最终追加的 v3 协议为准。
- v3 关系契约把语义命题与图端点分开：`cause` / `effect` 保持可独立阅读的自然语言命题；`source_entity_key` / `target_entity_key` 只能精确引用同事件 `entities / assets / sectors / macro_variables` 中对象由后端规则生成的唯一 `normalized_key`，无对应端点时用空字符串。后端不会拿命题文本、近似名称或跨事件实体猜端点。
- 默认 `prompt_version` 基线为 `llm_event_v4_explicit_relation_endpoints`。升级不会自动重抽历史视频；只有新视频分析或显式重新投递的抽取任务使用新契约。
- 默认提示词要求 `title`、`summary`、`assets`、`sectors`、`entities` 的对象口径一致；房地产、住房、楼市等具体市场对象不能泛化成单独的“市场”。
- 默认提示词要求股票事件在可可靠判断时写明上市市场、交易所或代码，并拆分发行公司、可交易证券、行业、国家和交易所实体；普通词不能误标成公司或资产。
- 默认提示词会过滤操作策略、荐股建议、观察名单、族群归类、关注提醒与纯预测；只有其中包含已发生事实变化时，才抽取事实变化本身。
- `evidence_source_ids` 必须命中本次请求 source map；若原文使用 `the market`、`market`、`市场` 这类泛称，应选择包含足够上下文的 source id，让读者能判断具体市场对象。
- 修改并保存该配置后会改变 `prompt_version`；重新投递播放列表事件抽取时，系统会按新的 prompt / model 口径生成事件。

### 简报调度策略

- `brief_generation_policy`

值结构：

```json
{
  "latest_cooldown_minutes": 120,
  "historical_daily_run_time": "04:00"
}
```

说明：

- 控制最新周期的冷却时间与历史周期批处理时间。
- 默认值可通过 `GET /api/config/defaults` 获取。

### 推理模式

- `inference_mode`
- `volcengine_inference_config`

值结构：

```json
{ "value": "local" }
```

```json
{
  "api_key": "<ark api key>",
  "llm_model": "doubao-seed-1-6-thinking-250715",
  "asr_model": "bigmodel",
  "asr_app_key": "<app key>",
  "asr_access_key": "<access key>",
  "llm_timeout_seconds": 600,
  "asr_timeout_seconds": 600
}
```

说明：

- 通过 `GET /api/config/inference`、`PUT /api/config/inference`、`POST /api/config/inference/test` 管理。
- 运行时优先级固定为 `app_config > .env`。
- 返回给前端的密钥字段会被脱敏，前端留空保存时会保留已存密钥。
- v1 只公开 `local` / `volcengine` 两种模式；底层仍保持 ASR / LLM 适配层解耦。

## 当前配置边界

- 环境变量偏“进程级 / 基础设施级”。
- `app_config` 偏“运行时行为开关”。
- 并不是所有环境变量都有 UI 页面；也不是所有 `app_config` 都有独立 UI 标签。
- 平台 Cookies、字幕、会员视频、下载格式、转写润色提示词目前都有现成 UI；简报调度策略目前主要通过 API 管理。
