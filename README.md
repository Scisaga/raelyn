<div align="center">

<img src="logo.png" width="112" alt="Raelyn" />

# RAELYN

### 私人语义观测站

**持续观察你选择的公开信源，把散落在视频中的事实凝结成真实事件，并让它们生长为一张可回放、可追踪、可回到证据的事件星域。**

[![License](https://img.shields.io/badge/license-Apache--2.0-2f6feb.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776ab.svg)](Dockerfile)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%C2%B7%20PostgreSQL%20%C2%B7%20S3-0b7285.svg)](docs/architecture/overview.md)
[![Self-hosted](https://img.shields.io/badge/deploy-self--hosted-16a34a.svg)](docs/reference/run-and-deploy.md)
[![MCP](https://img.shields.io/badge/agents-MCP%20ready-7c3aed.svg)](docs/architecture/mcp.md)

`自托管` · `证据优先` · `单用户` · `无外部 CDN` · `可被智能体使用`

[快速开始](#快速开始) · [完整导览](docs/assets/tour/raelyn-tour-zh-2026.mp4) · [系统架构](#架构与智能体接入) · [完整文档](docs/README.md)

</div>

---

https://github.com/user-attachments/assets/ac977c8c-3d1e-4eb7-8374-8e51788970ed

<p align="center"><sub>运行中实例的真实界面。节选 10 秒：回放 12 个月观察窗 → 事件按语义聚合成主题 → 从星群下钻到真实事件与原始来源。<br><a href="docs/assets/tour/raelyn-tour-zh-2026.mp4">观看 57 秒完整导览（1440×960，含配音与字幕）</a></sub></p>

---

## Raelyn 是什么

Raelyn 不是视频下载器，而是一个完整、可运行的自托管观测系统。你定义一个**观测域**——一组信源和真正关心的问题；系统持续同步、转写和分析公开视频，把重复报道保守归并为真实事件，再组织成主题、故事与周期简报。

它长期回答四个问题：

- **发生了什么？** 多条报道归并成同一件真实事件，而不是堆成重复条目。
- **自上次观察以来变化了什么？** 分开记录事件发生时间与系统认知时间，回放新增事件和故事更新。
- **事情如何延续？** 语义邻近只形成主题；故事只连接有证据的延续、因果、响应或更正关系。
- **结论来自哪里？** 星点、故事关系与简报引用都能下钻到事件记录、转写区间和原始视频。

当前打磨最完整的是**市场与公共事件观测**。对于能映射到现有事件协议的事实密集领域，通常可以先调整抽取口径而不重做数据模型；访谈、评论和教程类内容的事实陈述较少，形成的事件星域也会更稀疏。

## 已实现的核心能力

- **星域与变化回放**：每个观测域拥有独立的三维语义空间、尽量跨快照连续的坐标、时间轴、两级主题、实体筛选和不依赖 WebGL 的列表视图。
- **真实事件与故事**：重复报道保守归并，保留合并 / 拆分谱系；跨快照故事拥有稳定身份、阅读进度和按需加载的关系证据。
- **简报与来源复盘**：按日 / 周 / 月生成简报；星域快照可用时附加结构化引用，并在同一时间上下文中播放视频、阅读转写、追回来源。
- **私有事件库**：事件、实体、关系、证据和模型口径均结构化保存，可通过 PostgreSQL、REST 或 MCP 供下游使用。
- **持续运行**：同步、下载、字幕、ASR、事件抽取、Embedding 和星域分析进入统一 Job 体系，具备进度、重试、暂停、心跳、孤儿回收与资源记账。
- **人机共享对象语义**：Web、REST、WebSocket 与 MCP 使用同一套对象身份，智能体返回的事件可以直接在浏览器中定位。

尚未补齐的能力如实记录在 [V2 现状能力差距](docs/product/capability-gap-v2.md)，包括故事人工修订、段落级简报引用、转写 offset 迁移、混合语义召回与规模验证。

## 核心界面

<table>
  <tr>
    <td align="center" width="33%"><a href="docs/assets/tour/readme-story.webp"><img src="docs/assets/tour/readme-story.webp" width="300" alt="故事线：跨快照事件脉络与关系证据" /></a><br /><strong>故事线</strong><br /><sub>跨快照追踪事件脉络，展开关系即可核对证据</sub></td>
    <td align="center" width="33%"><a href="docs/assets/tour/readme-playlist.webp"><img src="docs/assets/tour/readme-playlist.webp" width="300" alt="播放列表：原始视频、来源记录与转写" /></a><br /><strong>播放列表</strong><br /><sub>在同一时间上下文中复盘原始视频、记录与转写</sub></td>
    <td align="center" width="33%"><a href="docs/assets/tour/readme-mcp.webp"><img src="docs/assets/tour/readme-mcp.webp" width="300" alt="MCP 介绍页：智能体客户端与连接参数" /></a><br /><strong>MCP 智能体接入</strong><br /><sub>按客户端查看连接步骤、资源与工具边界</sub></td>
  </tr>
</table>

## 从信源到理解

<p align="center">
  <img src="docs/assets/diagrams/source-to-understanding.svg" alt="Raelyn 从公开信源到 Web、REST 与 MCP 的双行处理流程图；每行四个节点" />
</p>

简报正文基于周期内 transcript 聚合生成；若当前星域快照可用，生成后才为本次输入中具有已核验证据的对象附加结构化引用，并非由星域反向生成正文。

## 适用场景与边界

Raelyn 适合：

- 长期跟踪财经、行业或区域频道，形成可回放、可追溯的个人情报站；
- 把公开视频转成带时间、实体和证据的私有事件库，供图谱或研究系统继续建模；
- 给智能体提供一个能回到原始证据的语义世界，而不是一组脱离来源的摘要。

当前边界：

- 单用户、自托管，不做多租户与复杂权限；
- 默认抽取口径面向市场与公共事件；产业链上下游等领域强关系需要重新定义抽取口径；
- 每个观测域独立构建 canonical 身份，不拼接虚假的全局宇宙；
- 持续异步更新，不承诺实时处理；
- 不提供投资预测、交易建议、收益率分析或无证据的趋势结论；
- 不添加装饰星点、虚构关系，AI 输出也不能替代证据审核。

## 快速开始

当前脚本化 Docker 快速开始支持 x86_64 Linux / WSL2。其他平台需要自行准备与 Linux 容器架构匹配的 `bin/ffmpeg`、`bin/ffprobe` 和 `bin/node`。

```bash
git clone https://github.com/Scisaga/raelyn.git raelyn
cd raelyn

# 首次构建 UI 需要 Node.js 22+ 与 npm；Ubuntu / WSL 可用项目脚本安装。
./scripts/dev/bootstrap-node-wsl.sh
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
./scripts/dev/build-ui.sh

# 为 API_BEARER_TOKEN、POSTGRES_PASSWORD、S3_ACCESS_KEY、S3_SECRET_KEY
# 分别设置彼此独立的随机值；数据库密码必须是 URI-safe 字符串。
cp .env.example .env
${EDITOR:-nano} .env

# Dockerfile 使用预先下载的 ffmpeg / ffprobe / Node。
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh

docker compose up --build
```

打开 `http://127.0.0.1:8000/`。Docker Compose 包含 PostgreSQL、MinIO 和 bgutil PO Token Provider，但不包含 ASR、LLM 与 Embedding 推理服务。

本地开发、后台运行、升级与排障见[运行与部署](docs/reference/run-and-deploy.md)，全部环境变量与运行时配置见[配置项说明](docs/reference/configuration.md)。

## 第一次使用

1. **新建观测域**：在侧边栏为要长期观察的世界命名；它拥有自己的信源、简报设置与星域。
2. **添加信源**：粘贴 YouTube 频道主页或 B 站 UP 主空间 URL。系统不接受单个视频链接，同一频道会在不同观测域之间复用。
3. **启用监控**：新信源默认不启用监控；在资料库中打开后，Scheduler 才会按配置周期发现新视频。

之后，同步发现、下载、字幕处理和转写会自动进入任务系统。不同能力的前置条件彼此独立：

- 平台字幕可用时，资料库、来源播放与转写不需要推理服务；没有字幕时才需要 ASR。
- 事件抽取与简报生成需要 LLM；只有具有事件时间、证据与足够置信度的已接受事件，完成 Embedding 后才会进入新的星域快照。
- 从转写首次生成或更新星域，需要 `ai`、`embedding`、`analysis` Worker 正常运行；浏览已有的 ready 快照不要求这些 Worker 持续在线。

## 架构与智能体接入

```mermaid
flowchart TD
    Client["Web / PWA<br/>REST / WebSocket<br/>MCP"] --> API["FastAPI"]
    API -->|"查询、配置、投递 Job"| DB[("PostgreSQL<br/>领域数据与 Job 队列")]
    API --> S3[("S3 / MinIO")]
    Scheduler["Scheduler"] -->|"投递到期 Job"| DB
    Workers["采集、媒体处理、同步<br/>AI、Embedding、分析 Worker"] -->|"原子领取 Job · 写回状态与结果"| DB
    Workers --> S3
    Workers --> External["YouTube / Bilibili<br/>ASR / LLM / Embedding 服务"]
```

技术形态为 `FastAPI + Alpine.js SPA + TailwindCSS + PostgreSQL + S3/MinIO + Worker/Scheduler + MCP`。ASR 与 LLM 共享 `local` / `volcengine` 推理模式，Embedding 单独配置；三类能力均可连接部署环境中实际支持的服务。

组件职责、数据模型与星域二进制热路径见[架构总览](docs/architecture/overview.md)和[V2 观察与存储架构](docs/architecture/v2-observation.md)。

配置 `API_BEARER_TOKEN` 后，主 API 会挂载 `/mcp`；否则 Web 与 REST 仍可运行，但智能体入口不启用。最小客户端配置：

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

MCP 可以读取信源、来源记录、transcript、观测状态、变化集、真实事件历史、稳定故事、结构化简报与证据上下文，也保留少量安全的异步动作。完整契约见 [MCP 集成设计](docs/architecture/mcp.md)。

## 按你的领域改造

对于能映射到现有事件协议的领域，通常先评估事实密度，再调整全局事件抽取口径与观测域级简报结构。事件记录按视频共享；运行中心按观测域“全部重抽”只是以该域筛选视频，若同一视频被其他观测域复用，也会影响它们后续生成的星域。观测域级提示词目前仅用于简报。

具体入口、改动成本和事实密度边界见[按领域改造](docs/reference/domain-customization.md)。

## 文档

- **安装、运行与排障**：[运行与部署](docs/reference/run-and-deploy.md) · [配置项说明](docs/reference/configuration.md)
- **产品与当前边界**：[V2 项目愿景](docs/vision.md) · [V2 信息架构](docs/product/information-architecture-v2.md) · [V2 现状能力差距](docs/product/capability-gap-v2.md)
- **架构与数据**：[架构总览](docs/architecture/overview.md) · [数据模型](docs/architecture/data-model.md) · [事件语义星域](docs/architecture/event-graph-analysis.md) · [任务系统](docs/architecture/job-system.md)
- **接口与智能体**：[REST API](docs/api/rest.md) · [MCP 集成设计](docs/architecture/mcp.md)
- **全部文档**：[文档导航](docs/README.md)

## 贡献、合规与许可

欢迎提交 Issue 与 Pull Request。较大改动前请先读[产品愿景](docs/vision.md)与[协作约定](AGENTS.md)；证据优先、不伪造确定性、不把星域退回成仪表盘，是评审中的核心产品规则。

使用者应只处理自己有权访问、下载、归档和分析的内容，并自行遵守目标平台条款、版权要求与当地法律法规。平台 Cookies、账号能力和访问限制由部署者负责。

除另有说明外，Raelyn 原创源代码与文档按 [Apache License 2.0](LICENSE) 提供。

Apache-2.0 不包含对 Raelyn 名称与标识的商标授权；客户端图标、新闻画面、视频缩略图等第三方素材不纳入该许可，仍适用各权利人的条款。FFmpeg、Node 及 Python / 前端依赖适用各自许可证。

已随仓库分发的第三方浏览器组件、品牌素材与演示媒体声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
