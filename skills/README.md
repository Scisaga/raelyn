# Skills 说明

`skills/` 用来沉淀已经稳定、可复用、适合按固定步骤执行的项目级工作流。

## 当前状态

- `docs/` 负责项目事实、架构边界、规则与导航
- `skills/` 负责专项执行方法与可复用脚本
- skill 可以先于对应业务模块存在，但不能把未来 skill 当成当前实现事实

## 当前可用 skills

- [gitlab-issue/SKILL.md](gitlab-issue/SKILL.md)
  - 适用于当前仓库 GitLab 项目的 issue 创建、读取、更新、评论、删除，以及 PLAN 阶段的标签补齐
- [job-system-design/SKILL.md](job-system-design/SKILL.md)
  - 适用于设计或评审任务系统；项目内落地约束以 `docs/architecture/job-system.md` 为准
- [python-web-refactor/SKILL.md](python-web-refactor/SKILL.md)
  - 适用于 Python Web 服务整理、FastAPI 边界梳理、入口收敛、配置治理、测试与文档补齐
- [static-ui/SKILL.md](static-ui/SKILL.md)
  - 适用于 `static/` 目录下的 Tailwind/Alpine 静态前端维护与扩展

## 什么时候值得新增 skill

当某类工作同时满足以下条件时，再考虑沉淀新的具体 skill：

- 输入稳定，能够明确说明需要哪些上下文
- 输出稳定，能够明确说明交付物长什么样
- 步骤可复用，不依赖大量一次性人工判断
- 已在项目中反复出现，值得标准化

## skill 与 docs 的边界

- `docs/` 负责记录项目事实、架构边界、规则与导航
- `skills/` 负责记录可执行的方法模板
- 不能用 skill 代替项目事实文档
- 不能把只对单次任务有效的临时说明沉淀成 skill

## 后续约定

若后续新增具体 skill，应至少补充：

- 适用场景
- 必要输入
- 标准输出
- 执行步骤
- 与项目文档的关联入口
