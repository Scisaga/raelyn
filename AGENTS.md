# Agent instructions

本文件只保留高频、全局、必须立即生效的规则，详细细则按“通用规则 / 项目规则 / 专项附录”分层维护：
- 通用协作与编码细则见 [docs/agent-rules/general.md](docs/agent-rules/general.md)。

## 通用规则
- 默认使用中文回复；代码注释、代码示例使用中文。
- 先读接口说明与业务逻辑，再写代码；没有文档、抓包、日志或代码依据时，不得凭经验猜接口行为。
- 分析时先回答用户当前真正要解决的问题，再进入抽象范式讨论；禁止用架构分层、概念分类或通用方法论替代对当前需求的直接结论。
- 代码结构优先直线化与可读性；避免过度抽象、过深嵌套、复杂控制流。
- 数据任务默认优先“简单、可验证的全量实现”；只有观测到真实瓶颈后，再引入分片、并行、流水线等复杂优化。
- 修复 bug 必须定位根因；禁止用绕路方案代替根因修复。
- 禁止无依据兜底、无依据校验、吞错和过度防御性代码。
- 涉及外部数据源、第三方 SDK、数据库驱动、消息队列客户端等有状态依赖时，必须先区分并证明“认证态 / 连接态 / 并发态 / 缓存态”的生命周期；禁止在未证明副作用前，用 `reset`、`close`、重建 client、强制重连等方式直接压连接数或资源占用。
- 若某个资源治理改动会改变“是否复用已认证会话 / 是否复用已有连接 / 是否触发重新登录 / 是否改变上游风控行为”，该改动按行为变更处理；必须先有源码、官方文档、抓包或最小实测依据，才能实现。
- 高优先级代理规则：
  - YouTube 的 `yt-dlp` 同步/下载请求可显式使用 `YTDLP_PROXY`。
  - 其他外部请求默认都不走代理，也不得隐式读取进程环境中的 `HTTP_PROXY` / `HTTPS_PROXY`，包括非 `yt-dlp` 的资料/头像抓取、B 站请求、ASR、LLM、Embedding 与健康检查。
  - 修改代理行为必须按行为变更处理；需要同步检查代码、测试、`.env.example`、配置说明和运行说明。
- 禁止在回复、日志、命令输出中暴露敏感信息。
- 禁止执行会覆盖、删除工作区内容的操作，例如 `git restore .`、`git reset --hard`、`git clean -fd`。
- 新增或调整测试时，默认优先真实集成测试，不默认使用 mock；只有用户明确要求时才使用 mock。
- 集成测试输入优先复用上游真实任务产物；不要在测试里手工插入上游 domain 数据。若前置数据不存在，直接 `skip` 并说明生成方式。
- 测试范围必须与改动风险成比例：默认只运行直接相关的最小定向测试、必要构建和一次冒烟验证，禁止把全量 UI、全量后端或全仓测试当作小改动的例行步骤。只有发布验收、共享基础设施发生广泛变更、定向测试暴露跨模块回归，或用户明确要求时才运行全量测试，并在执行前说明触发原因。
- 修改代码时必须同步检查并更新相关文档、配置示例、运行说明或文档索引；若判断无需更新文档，需在回复中说明依据。

## PLAN 规则

- 必须区分“事实/证据”和“推断/猜测”；证据不足时先澄清问题。
- 方案与选项输出遵守帕累托/支配关系，默认只给最优集合。若保留非 dominant 方案，必须同时写清保留原因、适用边界、以及它为何在该场景仍可能最优。
- 在 PLAN 输出方案时，通过 API 创建 GitLab Issue。Issue 常规至少包含：背景、目标、范围边界、接口依据、任务拆分、验收标准、风险，以及需要用户确认的问题；若涉及数据模型，再补充表结构。标签遵循 [skills/gitlab-issue/SKILL.md](skills/gitlab-issue/SKILL.md) 中的“优先级 + 主题”约定。

## 文档协作约定

* 核心入口
  - 项目愿景与功能范围维护在 [docs/vision.md](docs/vision.md)。
  - 架构总览维护在 [docs/architecture/overview.md](docs/architecture/overview.md)。
  - README 演示素材（星域录屏、产品导览片、字幕与过场）由 [scripts/record/README.md](scripts/record/README.md) 的脚本流水线生成；分镜、字幕文案与选择器的唯一真源是 `scripts/record/tour.config.mjs`。UI 变更导致素材过期时重跑该流水线，不手工录屏，也不把录制脚本散落到其他目录。

* 文档分类
  - 专题文档入口与分类以 [docs/README.md](docs/README.md) 为准。

* 维护原则
  - 代码行为、配置项、API、任务流程、部署/运行方式发生变化时，必须同步更新对应文档；禁止只改代码不改文档。
  - 修改专题内容时，优先更新对应专题文档，而不是把信息堆回 [docs/architecture/overview.md](docs/architecture/overview.md)。
  - [docs/architecture/overview.md](docs/architecture/overview.md) 只保留高层概览与导航，不承载实现细节。
  - 若新增重要文档入口，需同步检查 [docs/README.md](docs/README.md) 的文档导航是否需要更新。
  - 若文档路径变更，需同步更新 [docs/README.md](docs/README.md) 中的相关链接。

*  ADR 边界
  - 未来明确的架构决策记录放在 [docs/adr/](docs/adr/)。
  - ADR 不用于承载产品愿景、功能范围或接口清单。

## 项目内 Skills 使用约定

* Skills 入口
  - 项目内 skill 统一放在 [skills/](skills/) 目录。
  - GitLab issue 协作相关 skill 入口是 [skills/gitlab-issue/SKILL.md](skills/gitlab-issue/SKILL.md)。
  - 任务系统相关 skill 入口是 [skills/job-system-design/SKILL.md](skills/job-system-design/SKILL.md)。
  - 静态 UI 相关 skill 入口是 [skills/static-ui/SKILL.md](skills/static-ui/SKILL.md)。
  - Python Web 服务重构相关 skill 入口是 [skills/python-web-refactor/SKILL.md](skills/python-web-refactor/SKILL.md)。
  - skills 总览与适用边界见 [skills/README.md](skills/README.md)。

* 触发场景
  - 涉及当前仓库对应 GitLab 项目的 issue 创建、读取、列表、更新、评论、删除，或需要从 `git origin + .env` 推导 GitLab 项目/凭证时，优先使用 [skills/gitlab-issue/SKILL.md](skills/gitlab-issue/SKILL.md)。
  - 涉及任务系统设计、worker、scheduler、job observability、retry、lease、claim、parent-child 编排等工作时，优先使用 [skills/job-system-design/SKILL.md](skills/job-system-design/SKILL.md)。
  - 涉及静态 UI、页面设计、Tailwind/Alpine 前端实现、`static/` 目录下界面维护与扩展等工作时，优先使用 [skills/static-ui/SKILL.md](skills/static-ui/SKILL.md)。
  - 涉及 Python Web 服务重构、FastAPI 服务整理、旧 Web 服务向 FastAPI + Alpine SPA 迁移、入口收敛、目录治理、配置治理、测试与文档补齐、异步执行边界梳理等工作时，优先使用 [skills/python-web-refactor/SKILL.md](skills/python-web-refactor/SKILL.md)。

*  维护边界
  - `docs` 承载项目文档与规则索引，`skills` 承载专项执行方法；避免全文重复维护。
  - 仅供某个 skill 内部使用的辅助脚本，放在对应 `skills/<skill>/scripts/`；不要放到项目根目录伪装成项目级 CLI。
