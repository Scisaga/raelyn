# video-sync（MVP）

一个基于 `yt-dlp + ffmpeg` 的单用户媒体订阅与视频采集系统：支持 YouTube / B站媒体同步、增量发现新视频、下载视频/字幕、分离音频、字幕标准化文本（可选 ASR）、按播放列表按天生成 Markdown 简报（Ollama）。

详细技术方案见：`01.md`。

---

## 1) 运行形态

- `api`：FastAPI（提供 `/api/*` 与 `/` UI）
- `worker`：执行 Job（建议按队列拆分：download / process / sync / ai）
- `scheduler`：分钟级投递 `media.sync_videos`

最少启动 3 个进程（或用 docker compose 一次拉起）。推荐在本地把 worker 拆分为多个角色，避免不同类型任务互相“饿死”。

---

## 2) 推荐：Docker 一键启动（含 Postgres + MinIO）

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

### 3.2 下载工具（二选一）
你可以使用系统安装的 `yt-dlp/ffmpeg`，或用脚本下载到 `./bin/`（可选）：
```bash
./scripts/dev/download-ytdlp.sh
./scripts/dev/download-ffmpeg.sh
```

### 3.3 配置
```bash
cp .env.example .env
```

### 3.4 启动（3 个终端）
```bash
./scripts/dev/run-api.sh
./scripts/dev/run-worker-download.sh
./scripts/dev/run-worker-process.sh
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

- `backend/videosync/`：后端（FastAPI + Worker + Scheduler）
- `scripts/dev/`：开发环境与下载脚本
- `ui/`：Tailwind + Alpine 的离线 UI 构建脚手架
- `static/`：静态资源（构建产物与 `index.html`）

---

## 6) 验证

- API 健康检查：`GET /api/health`
- UI 首页：`GET /`
