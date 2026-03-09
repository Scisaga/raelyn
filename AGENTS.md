# Agent instructions (raelyn)

## 禁止使用 Git 重置/回滚命令

- 默认**禁止使用任何** `git` 命令（包括 `git status` / `git diff` / `git log` 等），除非用户在当次对话中明确要求。
- 严禁运行任何可能重置、回滚、清理工作区或改写历史的命令，包括但不限于：
  - `git reset`（所有模式）
  - `git checkout` / `git switch`
  - `git restore`
  - `git clean`
  - `git rebase`
- 如用户确需执行上述命令，必须先说明影响（会丢失哪些改动/会改写哪些提交）并获得用户明确同意后再执行。

## 文档协作约定

- 项目愿景与功能范围维护在 `docs/vision.md`。
- 架构总览维护在 `docs/architecture/overview.md`。
- 详细专题文档维护在 `docs/architecture/`、`docs/api/`、`docs/ui/`、`docs/roadmap/`、`docs/reference/`。
- 修改专题内容时，优先更新对应专题文档，而不是把信息堆回 `overview.md`。
- `docs/architecture/overview.md` 只保留高层概览与导航，不承载实现细节。
- 若新增重要文档入口，需同步检查 `README.md` 的文档导航是否需要更新。
- 若文档路径变更，需同步更新 `README.md` 中的相关链接。
- 未来明确的架构决策记录放在 `docs/adr/`。
- ADR 不用于承载产品愿景、功能范围或接口清单。

## 项目内 Skills

- 项目内 skill 统一放在 `skills/` 目录。
- 涉及任务系统设计、worker、scheduler、job observability、retry、lease、claim、parent-child 编排等工作时，优先使用 `skills/job-system-design/`。
- `job-mgmt-prompt.md` 的内容已吸收进项目内 skill，不再作为长期入口。
- skill 与 `docs` 不应维护两份完整原则全文；`docs` 负责项目化说明，skill 负责通用执行原则。
