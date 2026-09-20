<div align="center">

<img src="logo.png" width="112" alt="Raelyn" />

# RAELYN

### 私人语义观测站

**Raelyn 持续观察你选择的公开信源，把散落在视频中的事实凝结成真实事件，并让它们生长为一张可回放、可追踪、可回到证据的事件星域。**

[![License](https://img.shields.io/badge/license-Apache--2.0-2f6feb.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776ab.svg)](Dockerfile)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%C2%B7%20PostgreSQL%20%C2%B7%20S3-0b7285.svg)](docs/architecture/overview.md)
[![Self-hosted](https://img.shields.io/badge/deploy-self--hosted-16a34a.svg)](docs/reference/run-and-deploy.md)
[![MCP](https://img.shields.io/badge/agents-MCP%20ready-7c3aed.svg)](docs/architecture/mcp.md)

`自托管` · `证据优先` · `可被智能体使用` · `无 CDN、无遥测、无账号体系`

[English](README.md) · **简体中文**

</div>

---

![Raelyn 事件语义星域](docs/assets/readme/raelyn-semantic-starfield.gif)

<p align="center"><sub>真实 WebGL 录制，不是概念动画：前 6 秒快速回放 12 个月观察窗，再展开一个真实事件详情。星点是保守归并后的 canonical 真实事件；三维坐标只表达语义邻近，不表达时间、地理位置、因果关系或影响强度。<br><a href="docs/assets/readme/raelyn-semantic-starfield.mp4">查看 1920×1200 高清 MP4</a></sub></p>

---

## 这个项目想解决什么

信息流给你条目，搜索给你关键词。但它们都无法让你看见：一个你跟了两年的领域**长什么形状**、一条故事究竟**怎么延续下来**、以及你上个月读到的那个结论**凭什么成立**。

Raelyn 服务的是另一种使用方式：**对一个你自己选定的世界做长期观测**。

你定义一个**观测域**——一组信源，加上你真正关心的问题。此后系统在你自己的机器上持续运行：同步信源、下载与转写、抽取结构化事件、把描述同一件事的重复报道保守归并、投影到稳定的三维语义空间、把有证据的延续关系连成故事，并生成引用这一切的周期简报。

四个可以长期回答的问题：

|  |  |
| --- | --- |
| **发生了什么？** | 由多条报道归并成的 canonical 真实事件 |
| **自上次观察以来变化了什么？** | 观察游标 + 变化集，明确区分“事件何时发生”与“系统何时认知” |
| **事情如何延续？** | 故事只由有证据的 `continuation` / `causes` / `response` / `corrects` 关系构成 |
| **结论来自哪里？** | 任意星点、关系边与简报语句都能下钻到事件记录、转写区间和原始视频 |

## 一次典型的使用过程

```
打开观测域 → 星域停在你上次离开的位置
  → 从上次观察播放到现在，看新事件依发生时间进入
    → 一条航迹被点亮：故事新增了成员、证据或一次纠正
      → 打开真实事件，查看它的成员记录与实体
        → 跳到精确的转写区间并播放来源视频
          → 阅读解释这一时间切片的周期简报，每个结论都有引用
            → 调整信源范围，继续长期观察
```

星域是默认首页。任务、存储与模型健康退到背景，只有真的需要你处理时才浮现。

## 它可能值得你试一次的理由

- **看见变化，而不是库存**：首页回答“这个世界发生了什么变化”，而不是“下载了多少文件”。
- **空间会变成记忆**：语义坐标在快照之间尽量保持连续，几个月后你会记得某个主题在哪一片、某条故事从哪里延伸；必须 rebase 时产品会明确说明，不静默洗牌。
- **故事是被证明出来的，不是猜出来的**：语义邻近只形成主题；一条故事边要求两端都有冻结证据，并具备明确的延续、因果、响应或更正表述。
- **简报是星域的解释层，不是孤立 Markdown**：每篇简报固化生成快照与生成口径，并携带 canonical / story / evidence / source 四类结构化引用，双向可跳转。
- **不确定性是可见的产品状态**：待验证、证据不足、时间不精确、身份合并与拆分都会显示出来，而不是用视觉效果掩盖。
- **数据始终属于你**：采集、数据库、对象存储与模型端点都在自己手里；Web、REST、WebSocket 与 MCP 共用同一套对象语义。

## 当前已经实现的能力

九个一级页面，共用同一套对象模型：

| 页面 | 能力 |
| --- | --- |
| **星域** | 每个观测域独立的三维 WebGL 语义场：旋转/缩放/平移、月度时间轴、事件类型与实体筛选、主题与真实事件检查器、故事航迹，以及区分“新发生 / 认知变化 / 故事更新 / 待验证”的星域变化层。类型化列表视图是同一组数据不依赖 WebGL 的替代主视图。 |
| **故事** | 面向阅读而非审计：值得阅读 / 已关注 / 已形成 / 初步线索四个队列，准确未读、“从新增处继续”、现实时间跨度、成熟度，以及按需加载的关系支持记录。 |
| **播放列表** | 当前观测域的来源复盘工作台：按日/周/月的周期导航、本期可播放记录、播放器与转写右栏，并在同一条导航上承接本期简报。 |
| **资料库** | 信源与来源记录，可限定当前观测域或切到全局：导入导出、同步、状态、按域增删、删除保护，以及从一条记录反查它支撑的 canonical、故事关系与简报引用。 |
| **观测域设置** | 身份、头图、简报周期、域提示词，四项相互独立的覆盖率（可分析转写 / 当前口径抽取 / 星域收录 / 证据核验），持续观测开关，以及带影响报告的安全删除。 |
| **运行中心** | 任务、Worker、处理覆盖率、失败与跳过原因、实时刷新、取消与重试，以及当前域的数据维护（事件补齐、重新抽取、构建星域）。 |
| **资源用量** | 外部调用、LLM Token、下载量与存储的 90 天趋势；按 service/operation/provider/model 的服务画像，含成功率、耗时与缺失用量口径。 |
| **智能体接入** | MCP 连接地址、Bearer 鉴权与现成的客户端接入示例。 |
| **设置** | 推理端点（本地 OpenAI 兼容、Ollama 或火山引擎 Ark / 语音）、Cookies、下载与资产分发策略、各级暂停开关。 |

页面之下：

- **信源**：支持 YouTube 与 B 站，增量同步带抖动、Cookie 恢复、PO Token Provider 与下载失败熔断。
- **采集**：下载视频、缩略图与字幕，ffmpeg 提取音频，字幕标准化；无可用字幕时可选 ASR；所有产物作为可追踪资产保存。
- **事件**：从 `plain` transcript 抽取结构化事件，保存事件时间、收录时间、类型、实体、关系、置信度、模型口径与证据区间；为已接受事件生成向量；canonical 保守归并，宁可保留 singleton 也不冒险误合并，并保留 merge/split 谱系。
- **快照**：不可变的按域快照，冻结两级主题、故事结构、实体索引与固定三维坐标。
- **简报**：按日 / 周 / 月聚合为 Markdown，带结构化引用与安全渲染。
- **运行**：`FastAPI + Scheduler + 9 种角色 Worker`，所有同步、下载、处理与 AI 工作进入统一 Job 体系——状态、进度、重试、分级暂停（系统 / 来源 / 角色）、心跳、孤儿任务回收与失败观测。

尚未补齐的能力如实记录在 [V2 现状能力差距](docs/product/capability-gap-v2.md)：故事人工修订、段落级简报引用、转写 offset 迁移、混合语义召回与规模验证。

## 从信源到理解

```text
信源 Media
  └─ 同步 / 下载
      └─ 来源记录 Video + Asset
          └─ 字幕 / ASR / Transcript
              ├─ 周期聚合 ───────────────→ Markdown 简报（含结构化引用）
              └─ 事件抽取 → 审核 → Embedding
                  └─ Canonical / Topic / Story
                      └─ 不可变三维星域快照
```

当前简报基于周期内 transcript 聚合生成，不是由星域快照反向生成。

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

> API 与数据模型内部仍使用 `Playlist` / `event map` 命名；产品语言已经迁移为“观测域”与“事件语义星域”。

## 系统架构

```text
Web / PWA ─┐
REST / WS ─┼─→ FastAPI ──→ PostgreSQL
MCP ───────┘      │       └→ S3 / MinIO
                  │
Scheduler ─→ Job Queue ─→ Worker 角色
                            ├─ download_youtube / download_bilibili
                            ├─ audio / process（ffmpeg、字幕）
                            ├─ asr
                            ├─ sync
                            ├─ embedding
                            ├─ analysis（canonical / topic / story / 三维快照）
                            └─ ai（事件抽取、转写润色、简报）
```

技术形态为 `FastAPI + Alpine.js SPA + TailwindCSS + PostgreSQL + S3/MinIO + worker/scheduler + MCP`。UI 构建产物不依赖外部 CDN，运行时不需要 Node。

**星域热路径不把全量对象编码成 JSONB**：坐标、时间区间、类型码、主题归属、审核标记和点索引使用类型化关系列，API 将它们流式打包为 56 字节固定宽度二进制记录，浏览器直接解析为 WebGL typed arrays。JSONB 只承载低频、结构会演进的修订正文与证据上下文；详见 [V2 观察与存储架构](docs/architecture/v2-observation.md)。

## 快速开始

完整配置与排障说明见[运行与部署](docs/reference/run-and-deploy.md)。

### Docker Compose

```bash
git clone https://github.com/Scisaga/raelyn.git raelyn
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

```bash
docker compose up -d --build    # 后台运行
docker compose logs -f app      # 查看日志
docker compose down             # 停止
```

### 本地开发

```bash
git clone https://github.com/Scisaga/raelyn.git raelyn
cd raelyn

cp .env.example .env
${EDITOR:-nano} .env

# 启动本地依赖。
docker compose up -d postgres minio minio-init bgutil-pot

# 准备 Python、Node/npm 与 ffmpeg。
./scripts/dev/bootstrap-python.sh
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/download-ffmpeg.sh

./scripts/dev/build-ui.sh

# 启动 API、各角色 Worker 与 Scheduler。
./scripts/dev/devctl.sh start
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
```

如果系统缺少 `python3-venv`、`pip` 或 `ensurepip`，先执行 `./scripts/dev/bootstrap-ubuntu.sh`。

## 启用 AI 与星域能力

Docker Compose 默认包含 PostgreSQL、MinIO 和 bgutil PO Token Provider，**不包含** ASR、LLM 与 Embedding 推理服务。请在 `.env` 中配置实际可达的端点（任意 OpenAI 兼容服务、Ollama 或火山引擎），详见[配置项说明](docs/reference/configuration.md)。

星域不会随空数据库直接出现。至少需要：

1. 观测域中已有可用 transcript；
2. 事件抽取产生具有事件时间的已接受事件；
3. 对应事件的 embedding 已就绪；
4. `ai`、`embedding` 与 `analysis` Worker 正常运行；
5. 当前观测域已有构建成功的 ready 星域快照。

只有配置 `API_BEARER_TOKEN` 时，主 API 才会挂载 `/mcp`；否则 Web 与 REST 仍可运行，但智能体入口不启用。

## 智能体接入（MCP）

Raelyn 提供 32 个 MCP tools 与 18 个 resources，与 REST 共用同一套语义——信源、来源记录、transcript、观测域、观察状态、变化集、canonical 历史、稳定故事、结构化简报、证据上下文、任务与分组语义搜索，并保留少量安全的异步动作（`sync_media`、`download_video`、`retranscribe_video`、`generate_brief`）。

```jsonc
{
  "mcpServers": {
    "raelyn": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer YOUR_API_BEARER_TOKEN" }
    }
  }
}
```

搜索结果与 Web 深链接共用同一对象身份，因此智能体给出的答案和你浏览器里的那一个星点是同一个对象。设计说明见 [MCP 集成设计](docs/architecture/mcp.md)。

## 明确不做的事

- 不做多租户与复杂权限，当前是单用户、自托管系统；
- 不做通用知识图谱，事件抽取当前面向结构化市场与公共事件；
- 不做跨观测域统一 canonical 身份，每个域独立构建星域，不拼接虚假的全局宇宙；
- 不承诺实时处理，系统持续异步更新；
- 不提供投资预测、交易建议、收益率分析、Regime 或语义漂移结论；
- 不添加装饰星点、虚构关系或会被误认为事实的动画；
- AI 输出不能替代证据审核。

## 文档

- [V2 项目愿景](docs/vision.md) · [V2 信息架构](docs/product/information-architecture-v2.md) · [V2 现状能力差距](docs/product/capability-gap-v2.md)
- [架构总览](docs/architecture/overview.md) · [V2 观察与存储架构](docs/architecture/v2-observation.md) · [数据模型](docs/architecture/data-model.md) · [任务系统](docs/architecture/job-system.md)
- [事件语义星域](docs/architecture/event-graph-analysis.md) · [UI 设计总览](docs/ui/overview.md) · [核心线框规范](docs/ui/wireframes-v2.md)
- [REST API](docs/api/rest.md) · [MCP 集成设计](docs/architecture/mcp.md)
- [运行与部署](docs/reference/run-and-deploy.md) · [配置项说明](docs/reference/configuration.md) · [文档导航](docs/README.md)

## 参与贡献

欢迎提交 Issue 与 Pull Request。较大改动前请先读 [docs/vision.md](docs/vision.md) 与 [AGENTS.md](AGENTS.md)：其中的产品规则（证据优先、不伪造确定性、不把星域退回成仪表盘）在评审中最为关键。后端测试位于 `backend/raelyn/tests/`，UI 测试位于 `ui/src/views/__tests__/`。

## 合规边界

- 本项目默认面向你有权访问、下载、归档和处理的内容；
- 平台 Cookies、账号能力与访问限制由部署者自行负责；
- 使用者需要遵守目标平台条款、版权要求和当地法律法规。

## 许可证

本项目使用 [Apache License 2.0](LICENSE)。打包的第三方浏览器组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
