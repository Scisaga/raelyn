# UI 主题配色

本文维护项目当前实际使用的 UI 配色。它只负责“项目级主题重载”，不重复布局、组件和交互规范。

## 适用关系

- `skills/static-ui/SKILL.md` 中的配色表是默认定义，保证 skill 脱离当前仓库时仍可独立使用。
- 当前项目若存在本文档，则配色以本文为准；与 skill 默认值冲突时，按本文重载。
- 若未来只调整主题色，优先修改本文，不要直接改 skill 中的默认配色描述。

## 当前主题

### Base

- 页面背景：`bg-slate-950`
- 面板背景：`bg-slate-900/40`
- 弱底背景：`bg-slate-950/20`
- 顶栏背景：`bg-slate-950/70`
- 边框 / 分割线：`border-slate-800`、`divide-slate-800`
- Hover：`hover:bg-slate-900/50`
- 主文：`text-slate-200`
- 次级文案：`text-slate-300`
- 弱化文案：`text-slate-400`、`text-slate-500`
- 占位：`placeholder-slate-500`

### Primary

- 语义：主交互 / 主行动 / 选中态
- 主色系：Emerald
- 实心按钮：`bg-emerald-500 text-slate-950 hover:bg-emerald-400`
- 弱底强调：`bg-emerald-500/10 text-emerald-200 border-emerald-500/30`
- Focus ring：`focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500/30`
- 状态点：`bg-emerald-400`

### Danger

- 语义：危险操作 / 错误状态
- 主色系：Rose
- 弱底按钮 / 块：`bg-rose-500/20 text-rose-200 hover:bg-rose-500/25`
- 错误文案：`text-rose-300`
- 状态点：`bg-rose-400`

### Warning

- 语义：警告 / 需关注但未失败
- 主色系：Amber
- 弱底：`bg-amber-500/15 text-amber-200 border-amber-500/25`
- 文案：`text-amber-300`

### Info

- 语义：信息提示 / 中性状态
- 主色系：Sky
- 弱底：`bg-sky-500/15 text-sky-200 border-sky-500/25`
- 文案：`text-sky-300`

### Overlay

- 弹窗遮罩：`bg-black/60`
