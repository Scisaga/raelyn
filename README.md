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

## 它比“下载器”多走了一步

- 它不是把所有事情都塞进一个进程里，而是把 `api / scheduler / worker` 拆成清晰的执行面，便于长期运行和维护
- 它会按 provider 与处理阶段分配不同 worker，让下载、处理、转写与生成各自保持节奏
- 它会持续记录任务状态、进度、重试、暂停、心跳与恢复信息，让系统在运行过程中始终可观察、可追踪
- 它关心的不只是把文件取回本地，也包括播放列表聚合、文本处理、简报生成，以及后续可继续交给智能体使用的内容资产

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

推荐直接查看 [运行与部署](docs/reference/run-and-deploy.md)。如果你只想快速启动：

- 准备 `ffmpeg` / `ffprobe` / `node`
- 复制配置：`cp .env.example .env`
- Docker 启动：`docker compose up --build`
- 或本地启动：按 [运行与部署](docs/reference/run-and-deploy.md) 中的 `api / worker / scheduler` 方式拆分进程

常用入口：

```bash
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh
cp .env.example .env
docker compose up --build
```

打开：`http://127.0.0.1:8000/`

更多技术细节：

- 运行形态、Docker、本地启动、UI 构建、迁移：见 [运行与部署](docs/reference/run-and-deploy.md)
- 环境变量与运行期开关：见 [配置项说明](docs/reference/configuration.md)
