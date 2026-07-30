# RAELYN

### 私人语义观测站

> 持续监听你选择的公开视频信源，把不断流入的视频凝结为可追溯的事件、故事与简报，并更新一张可以长期探索的事件语义星域。

`Self-hosted` · `Evidence-first` · `Agent-ready`

Raelyn 是一个面向单用户、自托管场景的语义观测系统。它将信源同步、下载、转写与文本整理作为感知层，将事件抽取、保守归并、主题与故事关系、周期简报和事件语义星域组织为认知层，帮助你长期回答三个问题：

- 发生了什么？
- 事情如何延续？
- 结论来自哪里？

![Raelyn 事件语义星域](docs/assets/readme/raelyn-semantic-starfield.gif)

<p align="center"><sub>真实 WebGL 星域录制：6 秒快速回放 12 个月观察窗，0.3 秒进入真实事件，展示详情 1 秒，再保持详情内容旋转观察 1 秒。星点代表保守归并后的 canonical 真实事件；三维坐标表达语义邻近，不表达时间、地理位置、因果关系或影响强度。<br><a href="docs/assets/readme/raelyn-semantic-starfield.mp4">查看 1920×1200 高清 MP4</a></sub></p>

> **V2 状态**
>
> 采集、转写、事件分析、语义星域、周期简报和统一任务体系已经构成当前底座；“今日观察”、独立故事目录、跨页面稳定引用，以及面向事件与星图的 MCP 能力正按 V2 信息架构演进。本文不会把目标体验描述为已完成能力。

## 为什么是 Raelyn

- **先看变化，不再逐条追视频**：持续整理选定信源，并明确区分事件何时发生与系统何时收录。
- **从星点回到原始证据**：从真实事件下钻到事件记录、实体、证据片段、转写和原始视频。
- **把长期关注变成一张语义地图**：在观测域内探索主题、真实事件和有证据的故事关系，而不只依赖关键词列表。
- **让数据留在自己的观测站**：自托管采集、分析、存储和任务系统，并通过 Web、REST、WebSocket 与 MCP 接入后续工作流。

## 一次完整的观测循环

`创建观测域` → `选择信源与关注问题` → `持续同步、下载与转写` → `抽取事件并保守归并` → `更新主题、故事与星域快照` → `阅读简报或探索星图` → `回到证据核验` → `调整观测范围`

## 从信源到理解

```text
信源 Media
  └─ 同步 / 下载
      └─ 来源记录 Video + Asset
          └─ 字幕 / ASR / Transcript
              ├─ 周期聚合 ───────────────→ Markdown Brief
              └─ 事件抽取 → 审核 → Embedding
                  └─ Canonical / Topic / Story
                      └─ 不可变三维星域快照
```

当前简报基于周期内 transcript 聚合生成，不是由星域快照反向生成。

## 当前核心能力

### 观测域

围绕一组信源、观测说明、简报策略和独立事件星域持续观察。当前内部对象及 API 名称仍为 `Playlist`。

### 持续感知

- 支持 YouTube / B 站信源管理与增量同步；
- 下载视频、缩略图和字幕，提取音频并标准化 transcript；
- 在缺少可用字幕时支持可选 ASR；
- 将视频、音频、字幕、转写、笔记和简报统一保存为可追踪资产。

### 事件与证据

- 从 transcript 抽取结构化市场与公共事件；
- 保存事件时间、收录时间、类型、实体、关系、置信度、模型口径和证据引用；
- 为已接受事件生成语义向量；
- 对多来源事件记录进行保守归并，证据不足时保留独立记录。

### 事件语义星域

- 每个观测域独立构建星域，不拼接为虚假的全局地图；
- 通过不可变快照保存两级主题、故事关系、实体索引和固定三维坐标；
- 支持三维旋转、缩放、时间窗口、事件类型、主题、实体和真实事件探索；
- 时间只改变当前可见窗口，不参与 X / Y / Z 语义坐标；
- 任意语义距离都不自动解释为因果、影响强度或市场关联。

### 故事与观测简报

- 阅读当前快照内由证据关系连接的故事；
- 按日、周或月聚合来源文本，生成 Markdown 简报；
- 独立故事目录、跨快照故事关注与增量提醒、简报到事件的稳定双向引用属于 V2 后续能力。

### 长期运行

- `FastAPI API + Scheduler + 分角色 Worker` 组成持续执行面；
- 所有同步、下载、处理和 AI 工作进入统一 Job 体系；
- 支持状态、进度、重试、暂停、心跳、孤儿任务回收与失败观测；
- PostgreSQL 保存领域状态，S3 / MinIO 保存大体积内容资产。

### 智能体接入

MCP 当前支持媒体、视频、transcript、播放列表、简报和任务的读取，以及安全的异步任务触发。事件、主题、故事和星域 MCP 接口属于 V2 目标能力。

## 产品对象

| 产品概念 | 当前实现对象 | 职责 |
| --- | --- | --- |
| 观测域 | `Playlist` | 组织信源、提示词、简报策略与独立星域 |
| 信源 | `Media` | 定义系统持续观察的频道或账号 |
| 来源记录 | `Video` + `Asset` | 保存视频、字幕、音频、转写与来源链接 |
| 事件记录 | `MarketEvent` | 保存一次内容分析形成的结构化事件陈述 |
| 真实事件 | `EventMapCanonical` | 保守归并多条事件记录 |
| 主题与故事 | `EventMapTopic` / `EventMapStory` | 表达语义归属与有证据的事件关系 |
| 观测简报 | `Brief` | 按周期聚合来源文本 |
| 事件语义星域 | `EventMapSnapshot` | 保存观测域内固定三维语义空间 |

## 系统架构

```text
Web / PWA ─┐
REST / WS ─┼─→ FastAPI ──→ PostgreSQL
MCP ───────┘      │       └→ S3 / MinIO
                  │
Scheduler ─→ Job Queue ─→ Worker Roles
                            ├─ YouTube / Bilibili
                            ├─ ffmpeg / subtitle
                            ├─ ASR / transcript
                            ├─ LLM event / brief
                            ├─ Embedding
                            └─ Canonical / topic / story / 3D snapshot
```

当前实现基于 `FastAPI + Alpine SPA + PostgreSQL + S3/MinIO + worker/scheduler + MCP`。UI 构建产物不依赖外部 CDN，运行时不需要 Node。

## 快速开始

完整配置与排障说明见[运行与部署](docs/reference/run-and-deploy.md)。

### Docker Compose

```bash
git clone <REPO_URL> raelyn
cd raelyn

# 复制配置，并设置 API Token、数据库密码和 MinIO 凭据。
cp .env.example .env
${EDITOR:-nano} .env

# Dockerfile 会复制这些预先下载的外部二进制。
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh

# 构建 UI 需要 npm；Ubuntu / WSL 可使用项目脚本准备。
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/build-ui.sh

docker compose up --build
```

打开 `http://127.0.0.1:8000/`。

后台运行：

```bash
docker compose up -d --build
docker compose logs -f app
```

停止：

```bash
docker compose down
```

### 本地开发

```bash
git clone <REPO_URL> raelyn
cd raelyn

# 先配置 Docker 依赖所需的密码和对象存储凭据。
cp .env.example .env
${EDITOR:-nano} .env

# 启动本地依赖。
docker compose up -d postgres minio minio-init bgutil-pot

# 准备 Python、Node/npm 与 ffmpeg。
./scripts/dev/bootstrap-python.sh
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/download-ffmpeg.sh

# 构建 UI。
./scripts/dev/build-ui.sh

# 启动 API、各角色 Worker 与 Scheduler。
./scripts/dev/devctl.sh start
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
```

如果系统缺少 `python3-venv`、`pip` 或 `ensurepip`，先执行 `./scripts/dev/bootstrap-ubuntu.sh`。

## 启用 AI 与星域能力

Docker Compose 默认包含 PostgreSQL、MinIO 和 bgutil PO Token Provider，不包含 ASR、LLM 与 Embedding 推理服务。请在 `.env` 中配置实际可达的服务，详见[配置项说明](docs/reference/configuration.md)。

星域不会随空数据库直接出现。至少需要：

1. 观测域中已有可用 transcript；
2. 事件抽取产生具有事件时间的已接受事件；
3. 对应事件的 embedding 已就绪；
4. `ai`、`embedding` 与 `analysis` Worker 正常运行；
5. 当前观测域已有构建成功的 ready 星域快照。

只有配置 `API_BEARER_TOKEN` 时，主 API 才会挂载 `/mcp`；否则 Web 与 REST 仍可运行，但 MCP 入口不启用。

## 产品边界

- 当前是单用户、自托管系统，不提供多租户和复杂权限；
- 当前事件抽取面向结构化市场与公共事件，不等同于通用知识图谱；
- 当前星域按观测域独立构建，不存在跨观测域统一 canonical 身份；
- 系统持续异步更新，但不承诺实时处理；
- 不提供投资预测、交易建议、收益率分析、Regime、语义漂移或变化点结论；
- AI 输出不能替代证据审核。

## 文档

- [V2 项目愿景](docs/vision.md)
- [V2 信息架构](docs/product/information-architecture-v2.md)
- [首页与星图低保真线框](docs/ui/wireframes-v2.md)
- [文档导航](docs/README.md)
- [架构总览](docs/architecture/overview.md)
- [事件语义星域](docs/architecture/event-graph-analysis.md)
- [REST API](docs/api/rest.md)
- [MCP 集成设计](docs/architecture/mcp.md)
- [运行与部署](docs/reference/run-and-deploy.md)
- [配置项说明](docs/reference/configuration.md)

## 合规边界

- 本项目默认面向你有权访问、下载、归档和处理的内容；
- 平台 Cookies、账号能力与访问限制由部署者自行负责；
- 使用者需要遵守目标平台条款、版权要求和当地法律法规。

## 许可证

本项目使用 [Apache License 2.0](LICENSE)。打包的第三方浏览器组件许可见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
