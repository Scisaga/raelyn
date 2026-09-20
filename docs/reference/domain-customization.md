# 按领域改造

Raelyn 默认的事件抽取口径针对市场与公共事件。对于能映射到现有 v3 事件协议的事实密集领域，通常可以先调整抽取口径而不修改数据模型；访谈、评论与教程类内容的事实密度通常更低，同等信源规模下形成的事件星域会更稀疏。若领域对象、事件类型或关系无法由现有协议表达，就需要连同数据模型和下游分析一起设计。

## 推荐顺序

### 1. 调整事件抽取口径

事件抽取提示词保存在全局 `AppConfig` 的 `llm_event_extraction_prompt` 中，不是观测域级配置。事件记录按 `Video` 共享；运行中心按观测域执行“全部重抽”只是用该域筛选视频，若同一视频被其他观测域复用，重抽也会影响它们后续生成的星域。

非 Ollama `/api/generate` 路径会在运行时读取自定义提示词，修改口径不需要改代码；新生成的事件记录会保存 `prompt_version`。全部重抽会替换视频的当前事件，执行前需要确认影响范围。Ollama `/api/generate` 事件抽取固定使用紧凑提示词，不能仅靠这项运行时配置切换领域口径。

默认提示词与配置键见 [`event_analysis.py`](../../backend/raelyn/services/event_analysis.py)；配置结构见[配置项说明](configuration.md#事件抽取提示词)。

### 2. 调整事件收录门槛

事件只有同时满足置信度门槛并具有证据记录时才会被接受。当前门槛是 [`event_analysis.py`](../../backend/raelyn/services/event_analysis.py) 中的代码常量 `EVENT_ACCEPT_CONFIDENCE`；修改后需要部署代码，并在运行中心执行“全部重抽”。普通“事件补齐”可能复用同一 source hash、prompt 与 model 的成功运行，不会让历史视频应用新门槛。

降低门槛会增加事件数量，也会扩大低质量事件进入归并、主题与故事分析的范围。没有真实数据评估前，不应把它当作补足星域密度的默认手段。

### 3. 调整简报结构

`brief_prompt` 是当前唯一按观测域保存的提示词；为空时使用 [`brief_prompt.py`](../../backend/raelyn/services/brief_prompt.py) 中的默认模板。简报正文按周期内 transcript 聚合；当前星域快照可用时，生成后再为本次输入中具有已核验证据的对象附加结构化引用，并非由星域反向生成正文。

### 4. 更换推理服务

三类推理能力的配置边界不同：

- ASR 与 LLM 共享 `local` / `volcengine` 推理模式；本地端点读取启动环境变量，火山配置可由运行时配置覆盖环境变量。
- Ollama `/api/generate` 只适用于 LLM，不适用于 ASR 或 Embedding。
- Embedding 独立读取启动时的 `EMBEDDING_*` 环境变量。

默认 `docker-compose.yml` 只向容器传入其中显式列出的环境项，不会自动注入根 `.env` 的任意推理配置。Compose 部署需要在 environment 或 override 文件中显式传入新增端点。完整键名、鉴权和模型参数见[配置项说明](configuration.md)。

更换模型后，应分别验证转写、事件抽取与简报输出。更换 Embedding 模型或维度时，旧、新向量不能混用，必须执行可恢复的全量回填并重建星域快照；维度还需要与快照和匿名样本保持一致。

### 5. 接入新信源

接入新平台是改动最大的一项，通常同时涉及：

- URL 与 provider 身份识别；
- 频道资料和视频发现；
- 视频、缩略图与字幕下载；
- 发布时间、会员状态等平台元数据；
- Cookies、风控、限流和失败分类；
- 对应任务处理、配置、测试与运行文档。

现有入口可从 [`provider.py`](../../backend/raelyn/services/provider.py)、[`media_sources.py`](../../backend/raelyn/services/media_sources.py) 与 `backend/raelyn/jobs/handlers/` 开始阅读。

第三方平台行为必须以当前代码、官方文档、抓包或最小实测为依据，不能只复制另一个 provider 的假设。

## 验证建议

领域改造应使用真实信源产物逐层验证：先确认 transcript 的事实密度与证据定位，再检查事件口径和接受率，随后为已接受事件生成 Embedding，最后构建包含 canonical、主题与故事的星域快照，并确认只有具有 `event_time_start` 的已接受事件进入其中。若前置数据不存在，应先生成真实上游产物，不在测试中手工拼造领域数据绕过链路。
