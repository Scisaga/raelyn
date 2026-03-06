# raelyn（MVP）

一个基于 `yt-dlp + ffmpeg` 的单用户媒体订阅与视频采集系统：支持 YouTube / B站媒体同步、增量发现新视频、下载视频/字幕、分离音频、字幕标准化文本（可选 ASR）、按播放列表按天生成 Markdown 简报（LLM：Ollama 或 OpenAI-compatible 在线推理）。

详细技术方案见：`01.md`。

---

## 1) 运行形态

- `api`：FastAPI（提供 `/api/*` 与 `/` UI）
- `worker`：执行 Job（建议按队列拆分：download / process / sync / ai）
- `scheduler`：分钟级投递 `media.sync_videos`

最少启动 3 个进程（或用 docker compose 一次拉起）。推荐在本地把 worker 拆分为多个角色，避免不同类型任务互相“饿死”。

---

## 2) 推荐：Docker 一键启动（含 Postgres + MinIO）

说明：本项目的 `Dockerfile` 会直接 **COPY 开发环境已下载的 `./bin/*` 工具二进制**（避免在镜像构建时重新下载）。因此请确保你在 **Linux/WSL** 环境下先准备好这些文件：
```bash
./scripts/dev/download-ytdlp.sh
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh
```

```bash
docker compose up --build
```

打开：`http://127.0.0.1:8000/`

---

## 3) 本地启动（无 Docker）

你需要自行准备：
- Python 3.12+
- PostgreSQL
- MinIO（或任意 S3 兼容存储）
- `ffmpeg`（系统安装或使用脚本下载）

### 3.1 Python 依赖
优先使用 venv（避免 PEP 668 的 `externally-managed-environment` 限制）：
```bash
./scripts/dev/bootstrap-python.sh
```

若系统缺少 `python3-venv` / `ensurepip`（需要 sudo）：
```bash
./scripts/dev/bootstrap-ubuntu.sh
```

### 3.2 工具准备（开发必需）
本项目在本地开发/运行（无 Docker）时，建议用脚本把依赖工具统一放到 `./bin/`，并通过 `scripts/dev/load-env.sh` 将其加入 `PATH`（优先使用项目内二进制）。

#### 3.2.1 `yt-dlp` + `ffmpeg`（必需）
```bash
# A) 推荐：下载到 ./bin/
./scripts/dev/download-ytdlp.sh
./scripts/dev/download-ffmpeg.sh

# B) 或：系统安装（确保在 PATH 里）
```

#### 3.2.2 Node.js + npm（必需，用于 UI 构建）
推荐在 WSL 里安装 Node（避免使用 `/mnt/c/...` 的 Windows npm 导致构建失败）：
```bash
./scripts/dev/bootstrap-node-wsl.sh
```

如果你只想把 `node` 放到 `./bin/node`（不改系统环境），可用：
```bash
./scripts/dev/download-node.sh
```
> 注意：该脚本只提供 `node`，不包含 `npm`；UI 首次安装依赖仍需要可用的 `npm`。

#### 3.2.3 `yt-dlp-ejs`（必需，Python 包）
说明：项目通过 `backend/requirements.txt` 使用 `yt-dlp[default]`，会自动安装 `yt-dlp-ejs`（用于 YouTube 的 EJS/JS challenge）。

如果日志出现类似：
- `n challenge solving failed`
- `Only images are available for download`

通常意味着 EJS 解析失败（版本过旧或环境缺依赖）。可直接执行脚本进行升级/自检：
```bash
./scripts/dev/install-ytdlp-ejs.sh
```

### 3.3 配置
```bash
cp .env.example .env
```

建议在手动运行命令前先加载环境变量（`run-*.sh` / `devctl.sh` 检测到 `.env` 时也会自动加载）：
```bash
source ./scripts/dev/load-env.sh
```
说明：`load-env.sh` 会导出 `.env` 里的变量，并将 `./bin` 放到 `PATH` 最前（优先使用下载到 `./bin/` 的 `ffmpeg/yt-dlp/node` 等）。

#### 可选：配置平台 Cookies（YouTube / bilibili，推荐）
很多 429/风控/年龄验证/登录态相关的问题，用 cookies 可以显著改善（B 站常见报错：352 风控拦截）。

1) 在浏览器里登录对应平台（YouTube / bilibili，建议用单独账号）
2) 导出 **Netscape 格式** `cookies.txt`（Chrome/Firefox 常用扩展：`Get cookies.txt`）
3) 打开 UI -> **设置** -> `YTDLP_COOKIES（cookies.txt）`，把内容粘贴进去并保存（不会写入 `.env`）。

### 3.4 启动（多个终端）
```bash
./scripts/dev/run-api.sh
./scripts/dev/run-worker-download-youtube.sh
./scripts/dev/run-worker-download-bilibili.sh
./scripts/dev/run-worker-audio.sh
./scripts/dev/run-worker-process.sh
./scripts/dev/run-worker-asr.sh
./scripts/dev/run-worker-sync.sh
./scripts/dev/run-worker-ai.sh
./scripts/dev/run-scheduler.sh
```

打开 UI：`http://127.0.0.1:8000/`

也可以用一个脚本统一管理（后台启动/停止/重启），默认会启动多个 worker：
```bash
./scripts/dev/devctl.sh start
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
./scripts/dev/devctl.sh restart
./scripts/dev/devctl.sh stop
```

说明：`devctl.sh start/restart` 会先执行一次 UI 构建（等价于 `./scripts/dev/build-ui.sh`）。如需跳过可设置 `SKIP_UI_BUILD=1`。

---

## 4) UI 构建（离线/无 CDN）

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

---

## 5) 目录结构

- `backend/raelyn/`：后端（FastAPI + Worker + Scheduler）
- `scripts/dev/`：开发环境与下载脚本
- `ui/`：Tailwind + Alpine 的离线 UI 构建脚手架
- `static/`：静态资源（构建产物与 `index.html`）

---

## 6) 验证

- API 健康检查：`GET /api/health`
- UI 首页：`GET /`

---

## 7) 数据迁移（Postgres + MinIO）

将当前 `.env` 指向的 **源** PostgreSQL/MinIO 数据，迁移到 `.env.migrate` 指向的 **目标** PostgreSQL/MinIO。

1) 准备目标环境配置：
```bash
cp .env.migrate.example .env.migrate
```
按需修改 `.env.migrate` 里的 `DATABASE_URL` / `S3_*` 为目标环境。

2) Dry-run（不写入）：
```bash
./.venv/bin/python backend/raelyn/tools/migrate_data.py
```

3) 真正执行（会清空目标 DB + 目标桶对象，再全量迁移）：
```bash
./.venv/bin/python backend/raelyn/tools/migrate_data.py --yes
```

说明：
- 默认要求 `.env.migrate` 的 `S3_BUCKET` 与 `.env` 相同（保持原桶名）；如需跳过校验可用 `--allow-bucket-mismatch`。
- 可用 `--db` / `--s3` 只迁移其中一项。
