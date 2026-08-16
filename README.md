# RAELYN

### 私人语义观测站

> 一张持续演化、可以回到证据的私人语义宇宙。

`Self-hosted` · `Evidence-first` · `Agent-ready`

Raelyn 是一个面向单用户、自托管场景的语义观测系统。它持续观察你选择的公开信源，把散落在视频中的事实凝结成会生长的事件星域：你可以回放变化、追踪故事，并从任意结论返回原始证据。

信源同步、下载、转写与文本整理是观测站的感知层；事件、故事、简报与语义星域是认知层。Raelyn 帮助你长期回答四个问题：

- 发生了什么？
- 自上次观察以来，什么发生了变化？
- 事情如何延续？
- 结论来自哪里？

![Raelyn 事件语义星域](docs/assets/readme/raelyn-semantic-starfield.gif)

<p align="center"><sub>真实 WebGL 星域录制：前 6 秒快速回放 12 个月观察窗，0.3 秒推进星域，0.5 秒展开真实事件详情，再保持详情内容旋转观察 1.2 秒。星点代表保守归并后的 canonical 真实事件；三维坐标表达语义邻近，不表达时间、地理位置、因果关系或影响强度。<br><a href="docs/assets/readme/raelyn-semantic-starfield.mp4">查看 1920×1200 高清 MP4</a></sub></p>

> **V2 状态**
>
> V2 的核心闭环已经落入主干：星域是默认操作空间，观察游标区分事件发生时间与系统认知时间，稳定故事跨快照延续，变化流、结构化简报与证据可以互相定位；信源和来源记录统一进入资料库，运行状态收敛到运行中心。仍待扩展的能力与边界见 [V2 现状能力差距](docs/product/capability-gap-v2.md)。

## 为什么是 Raelyn

- **看见一个领域如何变化**：从上次观察的位置播放到现在，直观看见事件进入、星群增密和故事延伸。
- **把长期关注变成空间记忆**：在稳定语义地理中记住主题在哪里、故事从哪里发生，而不只依赖关键词列表。
- **从故事回到原始证据**：从航迹和真实事件下钻到事件记录、证据片段、转写和原始视频。
- **让简报解释星域**：用带结构化引用的阶段性叙事解释当前星域，而不是生成与事件世界割裂的摘要文件。
- **让数据留在自己的观测站**：自托管采集、分析、存储和任务系统，并通过 Web、REST、WebSocket 与 MCP 接入后续工作流。

## 一次完整的观测循环

`建立观测域` → `持续感知并形成事件` → `更新星域与变化集` → `从上次观察播放到现在` → `进入故事或真实事件` → `回到证据核验` → `通过简报形成阶段性理解` → `继续长期观察`

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

- 以稳定故事身份阅读跨快照修订、关注故事并保存阅读位置；
- 在故事模式中沿当前快照的完整事件航迹观察延续关系；
- 按日、周或月聚合来源文本，生成 Markdown 简报；
- 简报保存生成快照、生成口径和结构化引用；真实事件、证据与来源记录支持双向返回。

### 长期运行

- `FastAPI API + Scheduler + 分角色 Worker` 组成持续执行面；
- 所有同步、下载、处理和 AI 工作进入统一 Job 体系；
- 支持状态、进度、重试、暂停、心跳、孤儿任务回收与失败观测；
- PostgreSQL 保存领域状态，S3 / MinIO 保存大体积内容资产。

### 智能体接入

MCP 支持信源、来源记录、transcript、观测域、观察状态、变化集、canonical 历史、稳定故事、结构化简报、证据上下文和任务读取，也保留安全的异步任务触发。语义对象搜索和 Web 深链接与 REST 使用同一套对象语义。

## 产品对象

| 产品概念 | 当前实现对象 | 职责 |
| --- | --- | --- |
| 观测域 | `Playlist` | 组织信源、提示词、简报策略与独立星域 |
| 信源 | `Media` | 定义系统持续观察的频道或账号 |
| 来源记录 | `Video` + `Asset` | 保存视频、字幕、音频、转写与来源链接 |
| 事件记录 | `MarketEvent` | 保存一次内容分析形成的结构化事件陈述 |
| 真实事件 | `EventMapCanonical` | 保守归并多条事件记录 |
| 主题与故事 | `EventMapTopic` / `EventMapStoryIdentity` | 表达语义归属与跨快照、有证据的事件关系 |
| 观测简报 | `Brief` / `BriefReference` | 按周期聚合来源文本并保存结构化引用 |
| 事件语义星域 | `EventMapSnapshot` | 保存观测域内固定三维语义空间 |

星域热路径不把全量对象编码成 JSONB：坐标、时间区间、类型码、主题归属、审核标记和点索引使用类型化关系列，API 将它们流式打包为固定宽度二进制记录，浏览器直接解析为 WebGL typed arrays。JSONB 只承载低频、结构会演进的修订正文和证据上下文；详见 [V2 观察与存储架构](docs/architecture/v2-observation.md)。

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
- [V2 现状能力差距](docs/product/capability-gap-v2.md)
- [V2 核心界面线框规范](docs/ui/wireframes-v2.md)
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
