# 运行与部署

本文档承接仓库根目录 `README.md` 中的运行、构建、迁移等技术细节。`README.md` 只保留项目定位、核心能力与快速入口。

## 运行形态

- `api`：FastAPI（提供 `/api/*` 与 `/` UI）
- `worker`：执行 Job（建议按队列拆分：download / process / sync / ai）
- `scheduler`：分钟级投递 `media.sync_videos`，并按小时投递 `system.capture_usage_snapshot`
- `mcp`：挂载在主 API 进程内的 MCP HTTP 入口（可选，默认 `/mcp`）

最少启动 3 个进程（或用 docker compose 一次拉起）。推荐在本地把 worker 拆分为多个角色，避免不同类型任务互相“饿死”。

## 推荐：Docker 一键启动（含 Postgres + MinIO）

说明：本项目的 `Dockerfile` 会直接 `COPY` 开发环境已下载的 `./bin/*` 外部工具二进制，避免在镜像构建时重新下载。`yt-dlp` 本身通过 `backend/requirements.lock.txt` 安装到 Python 环境中；这里需要你先准备的是 `ffmpeg` / `ffprobe` / `node`：

```bash
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh
```

启动：

```bash
cp .env.example .env
# 至少填写 API_BEARER_TOKEN、POSTGRES_PASSWORD、S3_ACCESS_KEY、S3_SECRET_KEY。
# 四项都应使用彼此独立的随机值；不要直接使用公开示例值。
${EDITOR:-nano} .env

docker compose up --build
```

打开：`http://127.0.0.1:8000/`

Docker 单容器入口会直接守护 worker 子进程：某个 worker 崩溃退出时，只重启该 worker，默认等待 `WORKER_RESTART_DELAY_SECONDS=5` 秒；API 或 scheduler 退出仍视为关键进程故障，容器会退出并交给外层 Docker/Compose 策略处理。

### 对象存储传输并发

`upload_file` / `download_file` 会显式使用同一组 boto3 transfer 配置。当前 4 块 HDD 的生产默认值是：

- 单文件最多 `2` 个分片并发；
- 文件达到 64 MiB 后使用 multipart；
- multipart 分片大小为 64 MiB。

对应环境变量为 `S3_TRANSFER_MAX_CONCURRENCY`、`S3_TRANSFER_MULTIPART_THRESHOLD_BYTES` 和 `S3_TRANSFER_MULTIPART_CHUNKSIZE_BYTES`。这些限制按“单个文件、单个 API/worker 进程”计算；例如两个 worker 同时传输大文件时，最多可能产生约 4 个活跃分片请求，并非全局只允许 2 个。增加 worker 数或调高单文件并发前，应先观察 HDD 的 I/O wait、队列深度和 MinIO 吞吐。

每次 S3 操作只在发起调用的进程内创建 client，项目不缓存可跨 fork 或跨进程复用的 client。调整传输环境变量后，需要重启 API、scheduler 和相关 worker；无需修改数据库，也无需重建已有对象。

### 资源用量采集

资源用量页包含三条彼此独立的数据路径：

- LLM、ASR、Embedding 的真实外部请求完成或失败时，调用方立即把调用数、成功 / 失败、耗时和可用 token 累加到 `ExternalServiceUsageDaily`；这不是 Uvicorn access log，也不统计浏览器或站内 `/api/*` 请求。
- Scheduler 在 `usage.legacy_llm_backfill` 完成标记版本过旧时幂等投递 `system.backfill_legacy_usage`；`sync` worker 从终态 Job 结果恢复历史 LLM 调用与 token，并从带 `attempt` 的 ASR 请求事件恢复可核验的历史转写调用。两类数据都用独立 `legacy.*` operation 精确重建；没有请求事件的旧 ASR 任务不推算调用，不读取重复的事件抽取运行记录。任务可安全重试，且不会删除或覆盖实时采集行。
- Scheduler 每小时检查最近一次 `ResourceUsageDaily.captured_at`。到期且没有活跃同类任务时，只投递一个低优先级 `system.capture_usage_snapshot`；`sync` worker 执行实际的视频行数、逻辑资产和数据库规模查询，并幂等写入当天快照。

Scheduler 不直接扫描库存，也不在调度循环中持有长数据库事务。运行资源趋势必须同时保持 scheduler 与至少一个 `sync` worker 在线；若 `sync` worker 不可用，快照任务会留在统一 Job 队列中等待，不会由 API 请求线程代跑。系统暂停期间 Scheduler 不新增快照任务。

资源日桶固定使用 `Asia/Shanghai`。同一天可生成多次快照，但只有 `captured_at` 较新的结果覆盖当日旧值；因此当天趋势点是截至最近快照的部分日数据。LLM 调用可从旧 Job 结果回填；旧结果没有保存耗时时不推测耗时，页面标明“历史未记录”。资源实测规模从快照功能启用后开始形成历史，不回填不存在的旧采样。

资源用量页对首次快照前的逻辑资产规模按现存资产的创建日期累计已知大小，保留窗口之前的起始库存，并与每日快照绘制为同一条“逻辑资产”曲线。该计算在只读查询中进行，不需要迁移、回填任务或额外数据库写入；采集开始后的缺口不做回溯。删除记录与替换前的大小没有完整历史，API 仍保留数据来源区分，页面将口径集中在统计说明中；数据库占用仍只展示真实快照。新增回溯计算需要构建 UI 并重启 API；仅调整曲线呈现与样式时只需重新构建 UI。

逻辑资产容量仅为已知 `Asset.size_bytes` 的和，数据库容量在 PostgreSQL 下使用 `pg_database_size(current_database())`。当前没有能同时适用于宿主机、Docker volume 和远端 S3/MinIO 的物理磁盘容量来源，因此不展示主机磁盘总量、剩余空间或占用百分比。

## 本地启动（无 Docker）

启动时的 V2 历史检查使用 `playlist_id + snapshot_id` 确认关系数据是否存在，以命中现有以观测域为首列的索引；不要退回只按 `snapshot_id` 扫描历史成员或故事证据表，否则大型历史库会显著拖慢 API 启动。

你需要自行准备：

- Python 3.12+
- PostgreSQL
- MinIO（或任意 S3 兼容存储）

### Python 依赖

如果当前 Ubuntu/WSL 缺少 `python3-venv` / `python3-pip` / `ensurepip`，再使用下面的脚本安装系统包后复用 `bootstrap-python.sh`（需要 sudo）：

```bash
./scripts/dev/bootstrap-ubuntu.sh
```

推荐先创建或修复项目自己的 `.venv`，避免 PEP 668 的 `externally-managed-environment` 限制：

```bash
./scripts/dev/bootstrap-python.sh
```

说明：

- `bootstrap-python.sh` 负责创建或修复 `.venv`，并安装 `backend/requirements.lock.txt`
- `bootstrap-ubuntu.sh` 只负责补齐 Python 启动所需的系统包；它不会替你安装项目运行所需的 `ffmpeg`

### 工具准备（开发必需）

本项目在本地开发/运行（无 Docker）时，推荐把外部工具统一放到 `./bin/`。`scripts/dev/load-env.sh` 会将 `./bin` 加到 `PATH` 最前；当前项目里主要是 `ffmpeg` / `ffprobe` / `node` 走这条路径，`yt-dlp` 则通过 `.venv` 内的 Python 包提供。

#### `ffmpeg`（必需）

推荐直接下载到项目目录：

```bash
./scripts/dev/download-ffmpeg.sh
```

如果你已经在系统里安装了 `ffmpeg`，也可以不下载到 `./bin/`；但这只是 fallback 路径，不是推荐开发方式。

#### Node.js + npm（必需，用于 UI 构建）

推荐在 WSL 里安装 Node，避免使用 `/mnt/c/...` 的 Windows npm 导致构建失败：

当前 yt-dlp EJS 要求 Node.js 22 或更高版本；项目脚本默认安装 Node.js 22.23.1。旧的 Node.js 20 会导致 YouTube `n challenge` 返回 `no solutions`，最终只剩图片格式、无法下载视频。

```bash
./scripts/dev/bootstrap-node-wsl.sh
```

如果你只想把 `node` 放到 `./bin/node`（不改系统环境），可用：

```bash
./scripts/dev/download-node.sh
```

注意：该脚本只提供 `node`，不包含 `npm`；UI 首次安装依赖仍需要可用的 `npm`。

#### `yt-dlp` / `yt-dlp-ejs`（必需，Python 包）

项目通过 `backend/requirements.txt` 声明顶层依赖，并通过 `backend/requirements.lock.txt` 安装锁定版本；其中 `yt-dlp[default]` 会安装 `yt-dlp` 与 `yt-dlp-ejs`（用于 YouTube 的 EJS/JS challenge）。
默认配置还会向 yt-dlp Python API 传入 `YTDLP_REMOTE_COMPONENTS=ejs:github`，对应 CLI 里的 `--remote-components ejs:github`。
EJS 只解决 YouTube JS / n challenge；若 cookies 很快再次触发“确认你不是聊天机器人”，还需要启用 PO Token Provider。

如果日志出现类似：

- `n challenge solving failed`
- `Only images are available for download`
- `No video formats found`

前两类通常意味着 EJS / JS challenge 解析失败（版本过旧或环境缺依赖）。`No video formats found` 表示 yt-dlp 没拿到任何可播放 formats，除 EJS 外还要检查 `YTDLP_PROXY` 出口、PO Token Provider、cookies 导出会话和视频访问限制。可先执行脚本升级 `.venv` 里的 `yt-dlp[default]` 与 `yt-dlp-ejs`，并做基础自检：

```bash
./scripts/dev/install-ytdlp-ejs.sh
```

若你从旧版项目升级，建议确认 `.env` 里保留：

```bash
YTDLP_REMOTE_COMPONENTS=ejs:github
YTDLP_YOUTUBE_IMPERSONATE=chrome
```

#### 可选：YouTube PO Token Provider（bgutil）

当 YouTube cookies 保存后仍快速触发 bot check 或部分 403 时，启用 bgutil PO Token Provider。项目依赖中已包含 `bgutil-ytdlp-pot-provider` 插件，但仍需要单独运行 bgutil HTTP server。

当前 Python 插件和服务端镜像均锁定 `2.0.0`。`<2.0.0` 的 HTTP 服务受 [GHSA-qpv9-8xfj-xx9m](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/security/advisories/GHSA-qpv9-8xfj-xx9m) 影响；升级时需要同时更新 `backend/requirements.txt`、`backend/requirements.lock.txt` 与 Compose 镜像。两端主版本不同会被插件拒绝。

Docker Compose 会自动启动 `bgutil-pot` 服务，并给 app 注入：

```bash
YTDLP_POT_BGUTIL_BASE_URL=http://host.docker.internal:4416
```

`docker-compose.yml` 中的 `bgutil-pot` 使用 `restart: unless-stopped`，避免宿主机或 Docker daemon 重启后 provider 长期停在 exited 状态。

`2.0.0-node` 镜像默认命令仍是 `--host 0.0.0.0`。Compose 显式覆盖为 `--host 127.0.0.1,::1,host.docker.internal`，并通过 `host-gateway` 将最后一个名称映射到 Docker 内部网关，使宿主机原生进程和 Compose app 都可访问，而无需监听 LAN 地址或通配地址。Docker 内部网关只应供可信的本机容器访问，不应向外部网络转发 `4416`。

如果 `YTDLP_PROXY` 是宿主机上的本地代理，例如 `socks5://127.0.0.1:8887`，bgutil Docker 容器也必须能访问宿主机网络；否则 provider 生成 PO Token 时会在容器内访问自己的 `127.0.0.1` 并失败。Docker Compose 中的 `bgutil-pot` 使用 host 网络以保持这条路径一致。

如果宿主机 shell 或 systemd 环境设置了 `HTTP_PROXY` / `HTTPS_PROXY`，还要确认 `NO_PROXY` / `no_proxy` 包含 `127.0.0.1,localhost,::1,host.docker.internal`。否则访问 `http://127.0.0.1:4416/ping` 也可能被环境代理劫持并显示假性的 `502 Bad Gateway`。

本机运行时可单独启动 provider server，例如：

```bash
docker run --name bgutil-provider -d --init --net=host brainicism/bgutil-ytdlp-pot-provider:2.0.0-node --host 127.0.0.1,::1
```

然后在 `.env` 中配置：

```bash
YTDLP_POT_BGUTIL_BASE_URL=http://127.0.0.1:4416
```

验证插件和 provider 是否被 yt-dlp 识别：

```bash
NO_PROXY=127.0.0.1,localhost .venv/bin/python -m yt_dlp -v "https://www.youtube.com/watch?v=VIDEO_ID" 2>&1 | grep "PO Token Providers\\|Generating a .* PO Token"
```

输出应包含类似 `bgutil:http`；真正生成 token 时还应出现 `Generating a ... PO Token ... via bgutil HTTP server`。启用后需重启 `download_youtube` / `sync` worker。

升级前先下载依赖和镜像，停止应用进程后再同步安装插件、重建 `bgutil-pot`，最后重启应用，避免新旧主版本交叉运行。只改 Python 依赖时可用 `SKIP_UI_BUILD=1 ./scripts/dev/devctl.sh start` 跳过 UI 构建。验证至少包括：直连 `/ping` 返回 `2.0.0`、Compose 网络内能访问 `host.docker.internal:4416`、监听地址没有 `0.0.0.0` / `::`，以及通过原有 Cookies 和 `YTDLP_PROXY` 完成一次真实 PO Token 生成。重启会清空 provider 的进程内 token 缓存，但不会替换应用保存的 YouTube Cookies。

### 配置

```bash
cp .env.example .env
```

`run-*.sh`、`run-scheduler.sh`、`devctl.sh` 检测到 `.env` 时会自动加载环境变量并把 `./bin` 放到 `PATH` 最前，因此正常启动服务时不需要手动执行 `source ./scripts/dev/load-env.sh`。

如果你要在当前 shell 里直接运行零散命令（例如手动执行 `python` / `ffmpeg` / `node`），再手动加载：

```bash
source ./scripts/dev/load-env.sh
```

说明：`load-env.sh` 会导出 `.env` 里的变量，并将 `./bin` 放到 `PATH` 最前（优先使用下载到 `./bin/` 的 `ffmpeg` / `node` 等）。

#### 高优先级代理规则

- YouTube 的 `yt-dlp` 资料同步、频道头像下载和视频同步/下载请求可通过 `.env` 中的 `YTDLP_PROXY` 显式使用代理。
- 非 `yt-dlp` 的资料/头像抓取、B 站请求、ASR、LLM、Embedding 与健康检查不使用 `YTDLP_PROXY`。
- 应用默认不隐式读取 shell、systemd 或容器环境中的 `HTTP_PROXY` / `HTTPS_PROXY`；如果这些变量存在，也不能假设 ASR / LLM / Embedding 或 B 站请求会走代理。
- 如果 YouTube cookies 是在代理出口下导出的，建议让 `YTDLP_PROXY` 使用同一个出口，避免 cookies 使用 IP 与导出 IP 不一致。

#### 可选：配置平台 Cookies（YouTube / bilibili）

B 站常见 352 风控、年龄验证、会员或私有内容等登录态相关问题，可以通过 cookies 改善。近期 B 站 412 还可能由浏览器 JS 验证、数据中心出口 IP 风控，或 yt-dlp B 站提取器尚未发布的 `playinfo` 参数修复触发；如果更新 B 站 cookies 后仍然 412，优先降低 `BILIBILI_SYNC_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY`、更换更接近真实浏览器访问的网络，或等待 yt-dlp 官方发布包含修复的版本。不要在生产默认依赖中直接切到未合并的第三方 fork，除非只是在隔离环境做临时验证。

B 站资料同步不再调用会返回 `-799` / 412 的旧 `/x/space/acc/info` API，而是通过项目已有的 `curl_cffi` 浏览器模拟优先读取 `/x/web-interface/card`；该端点返回名称、简介、粉丝数、视频数和有效 `/bfs/face/` 头像。端点不可用时才回退公开空间页，并且不会为了补头像继续回退到 yt-dlp。上述请求与 B 站头像下载均显式 `trust_env=false`，不会读取环境代理。头像和 yt-dlp 请求同时复用当前安装版本的 yt-dlp 默认浏览器 UA，不再固定使用 Chrome 120/122。升级 yt-dlp 或 `curl_cffi` 后需重启对应 worker 才会加载新的请求实现。

YouTube cookies 不能被当成唯一稳定保障，但也不能被理解成“公开采集默认不用 cookies”。当前 YouTube 普通同步 / 下载都会使用已保存的 `YTDLP_COOKIES_YOUTUBE`；只有 provider 因 cookies / bot check / auth check 暂停时，`media.sync_videos` 才可按 `SYNC_PUBLIC_DISCOVERY_ENABLED=true` 进入 `public_discovery` 降级模式，显式无 cookies 抓公开视频 flat 列表。该模式只减少漏入库风险，下载、字幕和 metadata 补全仍等 provider 恢复后执行。完整判断与排障步骤见 [YouTube yt-dlp 同步与 Cookies 策略](youtube-ytdlp-strategy.md)。
当前实测的下载路径在无 cookies 时会直接触发 `LOGIN_REQUIRED`，因此 YouTube 下载任务固定使用已保存的 `YTDLP_COOKIES_YOUTUBE`，并通过 `YTDLP_YOUTUBE_IMPERSONATE=chrome` 尽量贴近浏览器请求形态。
如果错误只是 `Sign in to confirm you're not a bot` 或频道 / 播放列表鉴权检查失败，系统会暂停 YouTube provider，但不再直接归类为 `YTDLP_COOKIES_YOUTUBE` 失效；只有 yt-dlp 明确报 cookies no longer valid 或 cookies 格式错误时，才提示更新 cookies。
保存有效非空 YouTube / B 站 cookies 后，系统会清除对应 provider pause，并为该 provider 的受监控媒体补投一次 `max_entries=SYNC_COOKIE_RECOVERY_MAX_ENTRIES` 的同步追赶任务，默认 `200`。这些恢复任务会均匀分布在一个 `SYNC_INTERVAL_MINUTES` 周期内；已有 pending 同步（包括 public discovery）会原地转换为认证恢复并重新排期，避免 Cookie 更新后集中扫描全部媒体。
若失败信息是 `ERROR: unable to download video data: HTTP Error 403: Forbidden`，先检查格式选择器是否优先选中了 YouTube DASH video-only。2026-05-18 实测中，Bloomberg 样本的 360p+ DASH video-only URL 返回 403，但 HLS / combined MP4 format `96` 可下载；默认配置已改为优先 combined MP4/HLS。
格式选择与验证步骤见 [yt-dlp 视频 / 音频格式选择策略](ytdlp-format-selection.md)。

1. 在浏览器里登录对应平台（YouTube / bilibili，建议用单独账号）
2. 如果 YouTube 刚更新 cookies 后仍立刻触发“确认你不是聊天机器人”，先清空浏览器里的 YouTube / Google 相关站点数据，再重新访问 YouTube 登录并导出；不要在原会话里直接重复导出。
3. 导出 Netscape 格式 `cookies.txt`（Chrome / Firefox 常用扩展：`Get cookies.txt`）
4. 打开 UI -> 设置：
   - `YTDLP_COOKIES_YOUTUBE（cookies.txt）`：粘贴 YouTube cookies
   - `YTDLP_COOKIES_BILIBILI（cookies.txt）`：粘贴 B站 cookies

两者都不会写入 `.env`，而是保存在数据库配置中。

### 启动（多个终端）

```bash
./scripts/dev/run-api.sh
./scripts/dev/run-worker.sh download_youtube
./scripts/dev/run-worker.sh download_bilibili
./scripts/dev/run-worker.sh audio
./scripts/dev/run-worker.sh process
./scripts/dev/run-worker.sh asr
./scripts/dev/run-worker.sh sync
./scripts/dev/run-worker.sh embedding
./scripts/dev/run-worker.sh analysis
./scripts/dev/run-worker.sh ai
./scripts/dev/run-scheduler.sh
```

说明：

- `./scripts/dev/run-worker.sh download_youtube` / `download_bilibili` 每执行一次，只会启动 1 个对应 provider 的下载 worker 实例。
- 手动多终端运行 `run-worker.sh` 时不会自动拉起崩溃进程；需要自动拉起时使用下面的 `devctl.sh`。
- 若你手动多终端启动，并且希望兑现 `YOUTUBE_DOWNLOAD_CONCURRENCY=N` / `BILIBILI_DOWNLOAD_CONCURRENCY=N` 的真实下载并发，需要把对应 `download_*` worker 命令至少启动 `N` 次。
- 若你手动多终端启动，并且希望兑现 `ASR_WORKER_CONCURRENCY=N` 的 ASR 请求并发，需要把 `./scripts/dev/run-worker.sh asr` 至少启动 `N` 次。
- 若你手动多终端启动，并且希望兑现 `AI_WORKER_CONCURRENCY=N` 的 LLM 任务并发，需要把 `./scripts/dev/run-worker.sh ai` 至少启动 `N` 次。
- `sync` worker 除媒体资料与视频同步外，也执行一次性 `system.backfill_legacy_usage` 和周期性 `system.capture_usage_snapshot`。独立资源用量趋势要求至少保留 1 个 sync worker；Scheduler 只负责幂等入队，不会自行执行回填或快照。
- `ai` worker 负责 `video.extract_events`、`video.extract_events_batch`、`playlist.backfill_events` 与 `playlist.backfill_events_range`，播放列表回填父任务先按月拆分范围任务，范围任务再按 source 字符数投递批量或单视频抽取；事件抽取读取 `plain` transcript 并调用 LLM。Ollama `/api/generate` 事件抽取会使用 endpoint + model 级 advisory lock，锁忙时重排任务，因此提高 `AI_WORKER_CONCURRENCY` 不会让同一个本地大模型的事件抽取并发增加。
- `embedding` worker 负责 `event.embed` 与可恢复的 `event.backfill_embeddings`；前者为单条 accepted 事件生成结构化 embedding，后者以 64 条为一批原位迁移存量向量，并在每批提交后持久化进度、吞吐、ETA 与 lease。
- `analysis` worker 负责 `playlist.mark_event_map_dirty`、`playlist.build_event_map_snapshot` 与 `playlist.prune_event_map_snapshots`。至少保留 1 个 analysis worker，才能让新增视频在 embedding ready 后按微批自动更新地图，并在构建成功后保留 current 与上一版 ready 快照、分批清理更旧快照。构建按 `ANALYSIS_STREAM_BATCH_SIZE` 流式冻结输入、生成 canonical/topic/story、执行 IncrementalPCA，并在独立子进程运行 UMAP。
- `ANALYSIS_CPU_THREADS=2` 是每个 analysis worker 的 CPU 线程预算。Python worker 入口会在导入 NumPy / sklearn 前，将它统一设置给 `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS` 和 `MKL_NUM_THREADS`，因此 `run-worker.sh`、`devctl.sh`、Docker 与直接执行 `python -m raelyn.worker` 的行为一致。all-types 或 `WORKER_TYPES` 显式包含 analysis 任务的 worker 也应用该预算，其他专职 worker 不受影响。配置变更不会热加载，修改后必须重启对应 worker。
- 12 GiB 是父子进程树安全上限而非预分配。32 GiB 主机应观察 `event_map_snapshot.peak_rss_bytes` 与全机 `MemAvailable`；systemd / cgroup `MemoryMax` 应设置为相同或略高的硬上限。
- 事件地图的场景协议、后端和静态前端必须同一版本发布：三维场景协议 v2 固定为 56 字节。二维快照不能伪装成三维数据，升级后必须完整重建一次 UMAP3 快照；新 ready 原子切换前旧快照不受影响。应用 schema 迁移会增加 z/三维主题字段，并删除旧二维 topic polygon 与 Atlas/LOD 数据链。
- 升级先创建 `event_map_*` 表并为目标播放列表构建、验收首个 ready 快照。确认新地图后，再显式执行 `python -m raelyn.tools.migrate_data --drop-legacy-event-analysis --yes` 删除旧分析表；应用启动不会自动做该破坏性操作。

手动按播放列表时间范围投递事件抽取：

```bash
./scripts/enqueue-playlist-events.py a811f131-365e-44fe-8872-983a280299c7
```

默认按内容时间轴抽取最近 365 天。可用 `--since YYYY-MM-DD --until YYYY-MM-DD` 指定本地日期闭区间，用 `--dry-run` 先查看命中视频数。
脚本会向数据库 `job` 表投递 `video.extract_events` 任务；播放列表页面的历史回填仍会使用 `video.extract_events_batch` 管理短视频任务，但每次 LLM 请求只包含 1 个视频。脚本默认按 `--progress-every` 的批大小分批提交，避免长时间运行时已投递任务不可见。

事件 embedding 模型全量原位迁移：

```bash
./scripts/enqueue-event-embedding-backfill.py
./scripts/enqueue-event-embedding-backfill.py --yes
```

默认命令只读统计 accepted 事件、目标模型 ready 数、旧模型行数和受影响播放列表；只有显式 `--yes` 才投递 `event.backfill_embeddings`。当前迁移固定从 `Qwen/Qwen3-Embedding-8B` 到 `Qwen/Qwen3-Embedding-4B`，维度 `1024`、批大小 `64`。同一源模型、目标模型和维度只允许一个 pending/running Job。每批 HTTP 请求期间不持有数据库事务，写入前重新校验 `worker_id + execution_token` 所有权；已完成批次原位提交，重试或进程重启时只扫描剩余事件。

迁移前应暂停 `analysis`，让 current 旧快照继续服务；向量全部 ready 后，任务会清理残留 8B 行、为所有受影响播放列表各投递一次 dirty 重建，并仅在暂停原因为 `embedding_model_migration` 时自动恢复 `analysis`。新 ready 快照会原子替换 current，既有 prune 任务继续只保留 current 与上一版 ready。

事件 v2 清库重抽维护命令：

```bash
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.reset_event_extraction_v2
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.reset_event_extraction_v2 --yes
```

默认命令只 dry-run 并输出将删除的事件、事件地图快照对象、事件管线 job 与 `video_event_extraction_run` 计数；只有显式 `--yes` 才执行删除。执行前应保持 `ai`、`embedding`、`analysis` 队列暂停，并确认没有事件抽取管线 running；该命令只清理事件管线 job，不删除 ASR、下载、字幕润色或简报任务。

视频下载与事件抽取存量修复：

```bash
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.repair_video_event_pipeline
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.repair_video_event_pipeline --yes
```

该工具必须在本次终态与重试修复代码部署后运行。第一条命令默认只读 dry-run，输出待回填的下载状态数和待重新投递的事件抽取数；确认数量后才执行带 `--yes` 的第二条命令。

- 下载侧只处理“最新下载 job 已终止失败、视频仍为 `discovered/downloading`、没有 `video` asset、也没有 pending/running 下载 job”的记录，将状态改为 `failed` 并带回最新错误；不会新增下载任务。
- 事件侧只处理“最新 `video_event_extraction_run` 为 `failed`、视频仍存在、也没有 pending/running 抽取 job”的记录，投递或复用 `video.extract_events(force=false)`，避免在成功解析前删除旧事件。
- 同一数据库状态下可重复执行；已修复下载状态会退出候选，已有 active job 和 pending dedupe 会阻止紧邻重复执行扩张队列。若重新投递的抽取任务再次终止失败，后续维护运行仍会把它重新识别为候选。

YouTube 257 字节空音频存量修复：

```bash
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.repair_empty_audio_assets
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.repair_empty_audio_assets --limit 100 --yes
```

默认命令只读，统计 `provider=youtube` 且 `type=audio / format=m4a / source=ffmpeg / variant=raw / size_bytes=257` 的本次事故资产，并排除已有 pending/running 下载、音频提取或 ASR 任务的视频。只有显式传入 `--yes` 才投递强制下载；强制链路会在新视频通过音视频 packet 校验后替换原视频资产，随后替换音频资产并强制重新投放 ASR，即使本次下载同时获得了字幕。修复下载默认优先级为 `20`，高于常规下载的 `10`，可通过 `--priority` 调整。建议先小批量 `--limit 100 --yes`，确认下载、音频提取和 ASR 成功率后再扩大批次。

手动探测并投递字幕回补：

```bash
./scripts/analyze-subtitle-availability.py --scope downloaded --provider youtube --media-limit 10 --limit-per-media 2
./scripts/enqueue-subtitle-backfill.py --scope downloaded --target-language auto --limit 100 --yes
```

说明：

- `analyze-subtitle-availability.py` 只用 yt-dlp metadata 提取探测字幕语言，不下载媒体文件；输出按媒体统计目标字幕命中、人工字幕命中、自动字幕命中和错误数。
- `enqueue-subtitle-backfill.py` 默认 dry-run，必须传 `--yes` 才会向 `job` 表投递 `video.backfill_subtitles.youtube` / `video.backfill_subtitles.bilibili`。
- `--scope downloaded` 只处理已有 raw video asset 的视频；也可用 `--scope transcribed` 优先处理已有 plain transcript 的视频，或用 `--scope all` 扫描全部视频记录。
- `--target-language auto` 会按 provider、视频标题 / 描述、媒体名称 / 描述推断中文或英文；YouTube 中文目标会请求 `zh-Hant`、`zh-Hans`、`zh-CN`、`zh-TW`、`zh-HK`、`zh`，英文目标请求 `en`；B 站还会针对中文 / 英文分别请求 `ai-zh` / `ai-en`。
- 精确验证或回补单个视频时可加 `--video-id <uuid>`。

打开 UI：`http://127.0.0.1:8000/`

也可以用一个脚本统一管理（后台启动/停止/重启），默认会启动多个 worker：

```bash
./scripts/dev/devctl.sh start
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
./scripts/dev/devctl.sh restart
./scripts/dev/devctl.sh restart-api
./scripts/dev/devctl.sh stop
```

说明：

- `devctl.sh start/restart` 会先执行一次 UI 构建（等价于 `./scripts/dev/build-ui.sh`）。如需跳过可设置 `SKIP_UI_BUILD=1`。
- `devctl.sh restart-api` 只重启 API 进程并保留 worker / scheduler 运行；它同样会先执行一次 UI 构建，适合只更新 Web/API 代码后的快速重启。
- 首次启用 V2 时，API 会从仍保留的 ready 星图快照幂等回填长期历史。schema 事务会在回填前独立提交，避免长回填持续占用业务表 DDL 锁；已有历史的后续重启只做快速存在性检查。
- `devctl.sh start/restart/restart-api` 会等待 API 本机轻量 readiness 最多 120 秒；该检查只确认 API 已完成启动并可响应，不把 ASR / Embedding / LLM 等完整依赖健康检查作为启动门槛。完整健康状态仍通过 `GET /api/health` 查看。
- `devctl.sh start/restart` 会按 `YOUTUBE_DOWNLOAD_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY` 自动扩展对应 provider 的下载 worker 数。
- `devctl.sh start/restart` 会按 `ASR_WORKER_CONCURRENCY` / `EMBEDDING_WORKER_CONCURRENCY` / `ANALYSIS_WORKER_CONCURRENCY` / `AI_WORKER_CONCURRENCY` 自动扩展 asr / embedding / analysis / ai worker 数，默认均为 `1`；其中 `EMBEDDING_WORKER_CONCURRENCY=0` / `ANALYSIS_WORKER_CONCURRENCY=0` 表示当前节点不启动对应 worker。事件图谱链路需要 dirty/build 正常推进时，不要把 `ANALYSIS_WORKER_CONCURRENCY` 设为 `0`。
- `devctl.sh` 后台进程会优先以独立进程组启动；如需停止服务，使用 `./scripts/dev/devctl.sh stop`。
- `devctl.sh` 启动的 worker 会先进入轻量 supervisor；worker 子进程崩溃后会自动拉起，默认等待 `WORKER_RESTART_DELAY_SECONDS=5` 秒，也可用旧的 `DEV_WORKER_RESTART_DELAY_SECONDS` 覆盖本地等待时间。
- 同步/下载类 worker 的主执行心跳超过 `WORKER_EXECUTION_STALE_AFTER_SECONDS` 未推进时，会主动退出并交给 supervisor 重启；YouTube 全量同步按 yt-dlp 的真实分页与条目日志刷新该心跳，下载任务按下载进度刷新。
- 只有在 `.env` 里配置了 `API_BEARER_TOKEN` 时，主 API 进程才会额外挂载 `/mcp`；否则 `/mcp` 与 `/mcp/health` 返回 `404`。
- 主 API 关闭 Uvicorn HTTP access log；WebSocket 握手日志中的 `token` / `access_token` / `api_key` query 值会被脱敏，避免 `devctl.sh logs` 暴露访问凭证。

## UI 构建（离线/无 CDN）

UI 脚手架在 `ui/`，构建后输出到根目录 `static/`：

- `static/css/tailwind.min.css`
- `static/vendor/alpine.min.js`
- `static/index.html`（由 `ui/templates/app/**` 组装生成；不要直接编辑）

运行时不需要 Node；只在开发/构建阶段需要。

```bash
cd ui
npm ci
npm run ui:build
```

也可以直接运行：

```bash
./scripts/dev/build-ui.sh
```

如果你暂时没有 Node，但希望先让 UI 能跑起来（无 Tailwind 样式），至少下载 Alpine：

```bash
./scripts/dev/download-alpine.sh
```

WSL 提示：如果你的 `npm` 指向 Windows 安装路径（如 `/mnt/c/Program Files/nodejs/npm`），在 WSL 中运行构建经常会因路径翻译失败。推荐在 WSL 内安装 Node：

```bash
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/build-ui.sh
```

## 目录结构

- `backend/raelyn/`：后端（FastAPI + Worker + Scheduler）
- `scripts/dev/`：开发环境与下载脚本
- `ui/`：Tailwind + Alpine 的离线 UI 构建脚手架
- `static/`：静态资源（构建产物与 `index.html`）

## 验证

- API 健康检查：`GET /api/health`
- UI 首页：`GET /`
- MCP 健康检查：`GET http://127.0.0.1:8000/mcp/health`
- MCP endpoint：`http://127.0.0.1:8000/mcp`（需要 `Authorization: Bearer <API_BEARER_TOKEN>`）

任务入队与会话锁的 PostgreSQL 定向集成验证：

```bash
RAELYN_JOB_TEST_DATABASE_URL='<独立 PostgreSQL 测试库连接串>' \
  ./.venv/bin/python -m unittest backend.raelyn.tests.test_job_postgres_integration
```

测试只在显式指定的隔离库创建临时 schema，退出后清理该 schema；`.env` 中的业务库只读，用于复用真实媒体同步生成的 2000 条 YouTube 视频参数。缺少隔离库或上游产物时会 skip，并说明准备方式。验证覆盖批量入队的锁数量、并发去重、重试预算保留、事务回滚，以及业务 SQL 失败后保留首个异常并释放门控锁。可在独立 PostgreSQL 实例以 `max_connections=20`、`max_locks_per_transaction=10` 运行，验证低锁容量下批量投递仍完成。

任务入队和门控代码由 worker 在启动时导入；应用更新后需让相关 worker 重新启动才能生效。运行中的任务先正常结束，再重启对应执行进程；按 [任务系统](../architecture/job-system.md) 保留 provider 并发门控与租约回收语义。

## 数据迁移（Postgres + MinIO）

将当前 `.env` 指向的源 PostgreSQL / MinIO 数据，迁移到 `.env.migrate` 指向的目标 PostgreSQL / MinIO。

1. 准备目标环境配置：

```bash
cp .env.migrate.example .env.migrate
```

填写 `.env.migrate` 里的 `DATABASE_URL`、`S3_ACCESS_KEY` 和 `S3_SECRET_KEY`，并按需调整其余 `S3_*` 目标环境配置；示例文件不提供默认密码。

2. Dry-run（不写入）：

```bash
./.venv/bin/python backend/raelyn/tools/migrate_data.py
```

3. 真正执行（会清空目标 DB + 目标桶对象，再全量迁移）：

```bash
./.venv/bin/python backend/raelyn/tools/migrate_data.py --yes
```

说明：

- 默认要求 `.env.migrate` 的 `S3_BUCKET` 与 `.env` 相同；如需迁移到不同桶名可用 `--allow-bucket-mismatch`
- 使用 `--allow-bucket-mismatch` 时，脚本会把对象复制到目标桶，并将数据库里的 `asset.s3_bucket` 改写为目标桶名
- 可用 `--db` / `--s3` 只迁移其中一项

### LLM 有限预算定向实验

输出预算与修复依据见 [实验记录](llm-output-budget-experiment.md)。加载此次修复需要重启 AI worker；通过角色暂停门控等待当前任务结束后重启，不中断正在调用模型的任务。新版本不会自动重投全部历史失败任务。

先采集明确指定的真实失败任务（支持重复 `--job-id`），只读业务库与字幕对象，不执行 handler、不写业务结果：

```bash
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.capture_llm_budget_cases \
  --output-dir /tmp/llm-budget-cases --job-id <失败任务UUID>
```

显式执行有限额度的真实模型实验，逐次记录响应，不记录认证头；不写外部服务用量表或 domain 表：

```bash
RAELYN_LLM_BUDGET_CASES_DIR=/tmp/llm-budget-cases \
RAELYN_RUN_LLM_BUDGET_LIVE=1 \
PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_llm_budget_integration
```

响应按提示词、schema、模型名称、上下文与生成参数的散列缓存。省略 `RAELYN_RUN_LLM_BUDGET_LIVE` 时只校验已有真实响应，缺少产物则 skip；需要对可变模型别名的新权重重新实测时，采集到新的独立目录。验证范围包括全部事件分段的 JSON/视频唯一性/事件数/证据来源，以及简报摘要、章节、链接和 token 上限。
