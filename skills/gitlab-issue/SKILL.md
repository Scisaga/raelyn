---
name: gitlab-issue
description: Use this skill when creating, reading, listing, updating, commenting on, or deleting GitLab issues for this repository, especially when the current project should be inferred from git origin and the token should come from .env or GITLAB_PRIVATE_TOKEN.
---

# GitLab Issue

## 何时使用

在以下场景优先使用这个 skill：

- 需要为当前仓库所在 GitLab 项目创建 issue
- 需要读取单个 issue 或列出当前项目全部 issue
- 需要更新 issue 标题、description 或关闭/重开 issue
- 需要给 issue 回复评论
- 需要删除 issue 评论
- 需要删除 issue

## 工作流

1. 先确认当前操作对象是“当前仓库对应的 GitLab 项目”。
   - 默认直接复用 `git remote get-url origin` 推导 `base_url` 和 `project path`
   - 只有目标项目不是当前仓库 origin 时，才显式传 `--base-url` 和 `--project`
2. 先确认 token 来源。
   - 优先复用环境变量或仓库根目录 `.env` 里的 `GITLAB_PRIVATE_TOKEN`
   - 禁止在回复、日志或命令输出里打印 token
3. 优先使用 skill 私有脚本 [scripts/gitlab_issue.py](scripts/gitlab_issue.py)。
   - 创建：`create`
   - 读取：`read`
   - 列表：`list`
   - 更新：`update`
   - 回复：`comment`
   - 删除评论：`delete-note`
   - 删除：`delete`
4. 创建 issue 时默认补上合适标签。
   - 只能使用当前 GitLab 项目里**已有**的 labels，禁止借由 issue API 隐式创建新标签
   - 至少补齐“优先级 + 主题”两类标签；当前项目可优先从 `(高)/(中)/(低)`、`bug`、`enhancement`、`discussion`、`data`、`文档`、`training`、`预研` 中选择
5. 修改 labels 前，先判断是新增、替换还是保持不变。
   - 若只是补标签，优先保留已有 labels 再补充缺失项
   - 若 issue 已有准确标签，不要为了对称性反复改写
6. 修改 description 前，先判断是覆盖还是追加。
   - 延续原文：`update --append`
   - 完整改写：`update --body-file ...`
7. 删除 issue 前必须显式确认。
   - 命令需要传 `--yes`
   - 若实例或权限不支持删除，优先改用 `update --state-event close`

## 常用命令

- 创建：
  - `.venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py create --title "docs: 补齐 GitLab issue 协作 skill" --labels "(中),文档" --body-file .tmp/issue.md`
- 读取：
  - `.venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py read --iid 40 --notes`
- 列出当前项目 issue：
  - `.venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py list --state all`
- 更新：
  - `.venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py update --iid 40 --labels "(高),bug" --body-file .tmp/issue.md`
- 回复：
  - `.venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py comment --iid 40 --body "已完成"`
- 删除评论：
  - `.venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py delete-note --iid 40 --note-id 1234`
- 删除：
  - `.venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py delete --iid 40 --yes`

## 检查清单

- 当前 issue 操作是否明确针对当前仓库 origin 对应项目
- token 是否已从 `.env` 或环境变量加载，而不是硬编码进命令
- 创建 issue 时是否已补齐合适 labels，且这些 labels 已存在于当前项目
- 更新 description 时是否选对覆盖/追加语义
- 读取或列表时是否需要附带评论
- 若需清理 issue 讨论，是否先把有效结论合并回 description / 最终评论，再删除重复或过期评论
- 删除前是否已经明确确认风险
- 当前 GitLab 实例是否支持 issue 删除，且当前账号是否有对应权限
