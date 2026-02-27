# Agent instructions (video-sync)

## 禁止使用 Git 重置/回滚命令

- 默认**禁止使用任何** `git` 命令（包括 `git status` / `git diff` / `git log` 等），除非用户在当次对话中明确要求。
- 严禁运行任何可能重置、回滚、清理工作区或改写历史的命令，包括但不限于：
  - `git reset`（所有模式）
  - `git checkout` / `git switch`
  - `git restore`
  - `git clean`
  - `git rebase`
- 如用户确需执行上述命令，必须先说明影响（会丢失哪些改动/会改写哪些提交）并获得用户明确同意后再执行。
