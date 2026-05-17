# 运行与部署

本文档承接仓库根目录 `README.md` 中的运行、构建、迁移等技术细节。`README.md` 只保留项目定位、核心能力与快速入口。

## 运行形态

- `api`：FastAPI（提供 `/api/*` 与 `/` UI）
- `worker`：执行 Job（建议按队列拆分：download / process / sync / ai）
- `scheduler`：分钟级投递 `media.sync_videos`
- `mcp`：挂载在主 API 进程内的 MCP HTTP 入口（可选，默认 `/mcp`）

最少启动 3 个进程（或用 docker compose 一次拉起）。推荐在本地把 worker 拆分为多个角色，避免不同类型任务互相“饿死”。

## 推荐：Docker 一键启动（含 Postgres + MinIO）

说明：本项目的 `Dockerfile` 会直接 `COPY` 开发环境已下载的 `./bin/*` 外部工具二进制，避免在镜像构建时重新下载。`yt-dlp` 本身通过 `backend/requirements.txt` 安装到 Python 环境中；这里需要你先准备的是 `ffmpeg` / `ffprobe` / `node`：

```bash
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh
```

启动：

```bash
docker compose up --build
```

打开：`http://127.0.0.1:8000/`

## 本地启动（无 Docker）

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

- `bootstrap-python.sh` 负责创建或修复 `.venv`，并安装 `backend/requirements.txt`
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

```bash
./scripts/dev/bootstrap-node-wsl.sh
```

如果你只想把 `node` 放到 `./bin/node`（不改系统环境），可用：

```bash
./scripts/dev/download-node.sh
```

注意：该脚本只提供 `node`，不包含 `npm`；UI 首次安装依赖仍需要可用的 `npm`。

#### `yt-dlp` / `yt-dlp-ejs`（必需，Python 包）

项目通过 `backend/requirements.txt` 使用 `yt-dlp[default]`，会自动安装 `yt-dlp` 与 `yt-dlp-ejs`（用于 YouTube 的 EJS/JS challenge）。
默认配置还会向 yt-dlp Python API 传入 `YTDLP_REMOTE_COMPONENTS=ejs:github`，对应 CLI 里的 `--remote-components ejs:github`。
EJS 只解决 YouTube JS / n challenge；若 cookies 很快再次触发“确认你不是聊天机器人”，还需要启用 PO Token Provider。

如果日志出现类似：

- `n challenge solving failed`
- `Only images are available for download`

通常意味着 EJS 解析失败（版本过旧或环境缺依赖）。可直接执行脚本升级 `.venv` 里的 `yt-dlp[default]` 与 `yt-dlp-ejs`，并做基础自检：

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

Docker Compose 会自动启动 `bgutil-pot` 服务，并给 app 注入：

```bash
YTDLP_POT_BGUTIL_BASE_URL=http://host.docker.internal:4416
```

如果 `YTDLP_PROXY` 是宿主机上的本地代理，例如 `socks5://127.0.0.1:8887`，bgutil Docker 容器也必须能访问宿主机网络；否则 provider 生成 PO Token 时会在容器内访问自己的 `127.0.0.1` 并失败。Docker Compose 中的 `bgutil-pot` 使用 host 网络以保持这条路径一致。

如果宿主机 shell 或 systemd 环境设置了 `HTTP_PROXY` / `HTTPS_PROXY`，还要确认 `NO_PROXY` / `no_proxy` 包含 `127.0.0.1,localhost,::1,host.docker.internal`。否则访问 `http://127.0.0.1:4416/ping` 也可能被环境代理劫持并显示假性的 `502 Bad Gateway`。

本机运行时可单独启动 provider server，例如：

```bash
docker run --name bgutil-provider -d --init --net=host brainicism/bgutil-ytdlp-pot-provider:1.3.1-node
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

- YouTube 的 `yt-dlp` 同步/下载请求可通过 `.env` 中的 `YTDLP_PROXY` 显式使用代理。
- 非 `yt-dlp` 的资料/头像抓取、B 站请求、ASR、LLM、Embedding 与健康检查不使用 `YTDLP_PROXY`。
- 应用默认不隐式读取 shell、systemd 或容器环境中的 `HTTP_PROXY` / `HTTPS_PROXY`；如果这些变量存在，也不能假设 ASR / LLM / Embedding 或 B 站请求会走代理。
- 如果 YouTube cookies 是在代理出口下导出的，建议让 `YTDLP_PROXY` 使用同一个出口，避免 cookies 使用 IP 与导出 IP 不一致。

#### 可选：配置平台 Cookies（YouTube / bilibili）

B 站常见 352 风控、年龄验证、会员或私有内容等登录态相关问题，可以通过 cookies 改善。近期 B 站 412 还可能由浏览器 JS 验证、数据中心出口 IP 风控，或 yt-dlp B 站提取器尚未发布的 `playinfo` 参数修复触发；如果更新 B 站 cookies 后仍然 412，优先降低 `BILIBILI_SYNC_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY`、更换更接近真实浏览器访问的网络，或等待 yt-dlp 官方发布包含修复的版本。不要在生产默认依赖中直接切到未合并的第三方 fork，除非只是在隔离环境做临时验证。

YouTube cookies 不能被当成唯一稳定保障，但也不能被理解成“公开采集默认不用 cookies”。当前 YouTube 同步 / 下载都会使用已保存的 `YTDLP_COOKIES_YOUTUBE`；是否局部关闭 cookies 必须经过相同 yt-dlp 版本、相同代理出口、相同目标类型的最小实测。完整判断与排障步骤见 [YouTube yt-dlp 同步与 Cookies 策略](youtube-ytdlp-strategy.md)。
当前实测的下载路径在无 cookies 时会直接触发 `LOGIN_REQUIRED`，因此 YouTube 下载任务固定使用已保存的 `YTDLP_COOKIES_YOUTUBE`，并通过 `YTDLP_YOUTUBE_IMPERSONATE=chrome` 尽量贴近浏览器请求形态。

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
- 若你手动多终端启动，并且希望兑现 `YOUTUBE_DOWNLOAD_CONCURRENCY=N` / `BILIBILI_DOWNLOAD_CONCURRENCY=N` 的真实下载并发，需要把对应 `download_*` worker 命令至少启动 `N` 次。
- 若你手动多终端启动，并且希望兑现 `ASR_WORKER_CONCURRENCY=N` 的 ASR 请求并发，需要把 `./scripts/dev/run-worker.sh asr` 至少启动 `N` 次。
- `embedding` worker 负责 `video.embed_transcript` 与 `playlist.backfill_embeddings`；历史补算只扫描缺失/失败/无向量的候选项，按 transcript 预取 + embedding HTTP batch 的有限流水线处理，空 transcript 会跳过。
- `analysis` worker 负责 `playlist.build_analysis_snapshot`，默认单进程串行，避免多个重聚合任务同时压数据库和 CPU；快照成功后会自动清理旧 run；生产环境建议给该 worker 单独配置 systemd / cgroup `MemoryMax=6G`，与应用内 `ANALYSIS_MAX_RSS_BYTES` 保持一致。

打开 UI：`http://127.0.0.1:8000/`

也可以用一个脚本统一管理（后台启动/停止/重启），默认会启动多个 worker：

```bash
./scripts/dev/devctl.sh start
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
./scripts/dev/devctl.sh restart
./scripts/dev/devctl.sh stop
```

说明：

- `devctl.sh start/restart` 会先执行一次 UI 构建（等价于 `./scripts/dev/build-ui.sh`）。如需跳过可设置 `SKIP_UI_BUILD=1`。
- `devctl.sh start/restart` 会按 `YOUTUBE_DOWNLOAD_CONCURRENCY` / `BILIBILI_DOWNLOAD_CONCURRENCY` 自动扩展对应 provider 的下载 worker 数。
- `devctl.sh start/restart` 会按 `ASR_WORKER_CONCURRENCY` / `EMBEDDING_WORKER_CONCURRENCY` / `ANALYSIS_WORKER_CONCURRENCY` 自动扩展 asr / embedding / analysis worker 数，默认均为 `1`；其中 `EMBEDDING_WORKER_CONCURRENCY=0` / `ANALYSIS_WORKER_CONCURRENCY=0` 表示当前节点不启动对应 worker。
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

## 数据迁移（Postgres + MinIO）

将当前 `.env` 指向的源 PostgreSQL / MinIO 数据，迁移到 `.env.migrate` 指向的目标 PostgreSQL / MinIO。

1. 准备目标环境配置：

```bash
cp .env.migrate.example .env.migrate
```

按需修改 `.env.migrate` 里的 `DATABASE_URL` / `S3_*` 为目标环境。

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
