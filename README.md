# RAELYN

`RAELYN` 是一个面向私有部署、可供智能体调用的媒体采集与处理基础设施。

它把“添加媒体 -> 同步视频 -> 下载原始产物 -> 音频/字幕/转写处理 -> 播放列表聚合 -> 生成 Markdown 简报”收敛成一条可持续运行、可观测、可恢复的任务流水线。当前实现基于 `FastAPI + Alpine SPA + PostgreSQL + S3/MinIO + worker/scheduler + MCP`，支持 YouTube / B站作为 provider。部署 `RAELYN` 后，智能体可以通过 MCP 在 Codex 中直接读取媒体信息、视频信息、转写、简报等内容，并继续完成后续加工、整理与发布。

## 项目定位

- `self-hosted`：默认面向本地或受信任网络内部署
- `task-driven`：所有同步、下载、处理、AI 步骤都落在统一 Job 体系中
- `observable`：提供任务列表、实时状态、worker 在线情况、失败重试与系统暂停机制
- `pipeline-oriented`：不仅下载视频，也管理音频、字幕、文字稿、视频笔记与周期简报

## 核心能力

- 媒体管理：添加、导入、导出媒体；按 provider 同步资料与视频列表
- 下载流水线：下载视频、缩略图、字幕，随后提取音频、标准化字幕、可选 ASR
- 文本处理：转写润色、视频笔记生成、按播放列表聚合周期简报
- Worker 编排：provider 级下载并发控制、失败退避、孤儿任务回收、心跳观测
- Web 运维界面：概览、媒体、视频、播放列表、任务、设置、MCP Server 指南

## 设计取向

- 面向长期运行：`api / scheduler / worker` 拆分执行面，下载、处理、转写、分析与生成任务可以按角色独立扩展和重启
- 面向可恢复任务：所有重活都进入 Job 体系，记录状态、进度、重试、暂停、心跳与错误信息，便于排障和恢复
- 面向内容资产沉淀：围绕媒体、视频、字幕、音频、transcript、笔记和简报建立统一索引，而不是只保存一次性下载文件
- 面向智能体协作：通过 Web UI 和 MCP 暴露可检索、可引用、可继续加工的媒体上下文，让后续整理、分析和发布可以接上同一套数据

## 合规边界

- 本项目默认面向你有权访问、下载、归档和处理的内容
- 平台 Cookies、账号能力与访问限制由部署者自行负责
- 你需要自行遵守目标平台条款、版权要求和当地法律法规

## 文档

- [文档导航](docs/README.md)
- [项目愿景与范围](docs/vision.md)
- [架构总览](docs/architecture/overview.md)
- [MCP 集成设计](docs/architecture/mcp.md)
- [REST API 设计](docs/api/rest.md)
- [运行与部署](docs/reference/run-and-deploy.md)
- [配置项说明](docs/reference/configuration.md)

---

## 快速开始

下面是可直接照着执行的部署步骤；更多排障与配置细节见 [运行与部署](docs/reference/run-and-deploy.md)。

### Docker Compose 部署

```bash
git clone <REPO_URL> raelyn
cd raelyn

# 准备镜像构建需要复制进去的外部二进制。
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh

# 构建前端静态资源。若当前系统没有 npm，先执行下一行安装 Node/npm。
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/build-ui.sh

# 启动 Postgres、MinIO、bgutil PO Token Provider、API、worker、scheduler。
docker compose up --build
```

后台启动：

```bash
docker compose up -d --build
docker compose logs -f app
```

停止：

```bash
docker compose down
```

打开：`http://127.0.0.1:8000/`

### 本地部署

```bash
git clone <REPO_URL> raelyn
cd raelyn

# 启动本地依赖；如果你已有 PostgreSQL / MinIO，可跳过这一步并自行修改 .env。
docker compose up -d postgres minio minio-init bgutil-pot

# 准备 Python、Node/npm、ffmpeg/ffprobe。
./scripts/dev/bootstrap-ubuntu.sh
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/download-ffmpeg.sh

# 准备配置。
cp .env.example .env
${EDITOR:-nano} .env

# 构建 UI。
./scripts/dev/build-ui.sh

# 后台启动 api / worker / scheduler。
./scripts/dev/devctl.sh start
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
```

打开：`http://127.0.0.1:8000/`

重启 / 停止：

```bash
./scripts/dev/devctl.sh restart
./scripts/dev/devctl.sh restart-api
./scripts/dev/devctl.sh stop
```

更多技术细节：

- 运行形态、Docker、本地启动、UI 构建、迁移：见 [运行与部署](docs/reference/run-and-deploy.md)
- 环境变量与运行期开关：见 [配置项说明](docs/reference/configuration.md)
