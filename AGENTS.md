# Agent instructions

本文件只保留高频、全局、必须立即生效的规则，详细细则按“通用规则 / 项目规则 / 专项附录”分层维护：
- 通用协作与编码细则见 [docs/agent-rules/general.md](docs/agent-rules/general.md)。

## 通用规则
- 默认使用中文回复；代码注释、代码示例使用中文。
- 先读接口说明与业务逻辑，再写代码；没有文档、抓包、日志或代码依据时，不得凭经验猜接口行为。
- 代码结构优先直线化与可读性；避免过度抽象、过深嵌套、复杂控制流。
- 数据任务默认优先“简单、可验证的全量实现”；只有观测到真实瓶颈后，再引入分片、并行、流水线等复杂优化。
- 修复 bug 必须定位根因；禁止用绕路方案代替根因修复。
- 禁止无依据兜底、无依据校验、吞错和过度防御性代码。
- 禁止在回复、日志、命令输出中暴露敏感信息。
- 禁止执行会覆盖、删除工作区内容的操作，例如 `git restore .`、`git reset --hard`、`git clean -fd`。
- 新增或调整测试时，默认优先真实集成测试，不默认使用 mock；只有用户明确要求时才使用 mock。
- 集成测试输入优先复用上游真实任务产物；不要在测试里手工插入上游 domain 数据。若前置数据不存在，直接 `skip` 并说明生成方式。

## PLAN 规则

- 必须区分“事实/证据”和“推断/猜测”；证据不足时先澄清问题。
- 方案与选项输出遵守帕累托/支配关系，默认只给最优集合。若保留非 dominant 方案，必须同时写清保留原因、适用边界、以及它为何在该场景仍可能最优。
- 在 PLAN 输出方案时，通过 API 创建 GitLab Issue。Issue 常规至少包含：背景、目标、范围边界、接口依据、任务拆分、验收标准、风险，以及需要用户确认的问题；若涉及数据模型，再补充表结构。

## 文档协作约定

* 核心入口
  - 项目愿景与功能范围维护在 [docs/vision.md](docs/vision.md)。
  - 架构总览维护在 [docs/architecture/overview.md](docs/architecture/overview.md)。

* 文档分类
  - 专题文档入口与分类以 [docs/README.md](docs/README.md) 为准。

* 维护原则
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
  - 任务系统相关 skill 入口是 [skills/job-system-design/SKILL.md](skills/job-system-design/SKILL.md)。
  - 静态 UI 相关 skill 入口是 [skills/static-ui/SKILL.md](skills/static-ui/SKILL.md)。
  - Python Web 服务重构相关 skill 入口是 [skills/python-web-refactor/SKILL.md](skills/python-web-refactor/SKILL.md)。

* 触发场景
  - 涉及任务系统设计、worker、scheduler、job observability、retry、lease、claim、parent-child 编排等工作时，优先使用 [skills/job-system-design/SKILL.md](skills/job-system-design/SKILL.md)。
  - 涉及静态 UI、页面设计、Tailwind/Alpine 前端实现、`static/` 目录下界面维护与扩展等工作时，优先使用 [skills/static-ui/SKILL.md](skills/static-ui/SKILL.md)。
  - 涉及 Python Web 服务重构、FastAPI/Flask 服务整理、入口收敛、目录治理、配置治理、测试与文档补齐、异步执行边界梳理等工作时，优先使用 [skills/python-web-refactor/SKILL.md](skills/python-web-refactor/SKILL.md)。

*  维护边界
  - `docs` 承载项目文档与规则索引，`skills` 承载专项执行方法；避免全文重复维护。
