# UI 主题配色

本文维护项目当前实际使用的 UI 配色。它只负责“项目级主题重载”，不重复布局、组件和交互规范。

## 适用关系

- `skills/static-ui/SKILL.md` 中的配色表是默认定义，保证 skill 脱离当前仓库时仍可独立使用。
- 当前项目若存在本文档，则配色以本文为准；与 skill 默认值冲突时，按本文重载。
- 若未来只调整主题色，优先修改本文，不要直接改 skill 中的默认配色描述。

## 当前主题

### 深空表面系统

V2 不再只依靠 `slate` 边框划分区域。应用壳、页面、工作表面、浮层和数据画布使用不同亮度与不同强度的深色渐变，形成稳定的空间层级；事件类型、状态和选择色仍只承担语义表达，不作为大面积装饰背景。

- 应用环境：`#061020 → #020611 → #050817`，叠加低透明度青色与靛紫径向辉光。
- 页面环境：默认 `#07101f → #030817 → #020611`；各页面只改变低透明度气氛色，不改变基础亮度关系。
- 普通工作表面：`#0d1b32 → #040a18`，用于列表、设置与左侧目录。
- 阅读表面：`#0d1930 → #050b1b`，用于故事正文、简报正文和转写区域。
- 抬升表面：`#0a152a → #030816`，用于详情轨道、观察变化与浮层。
- 数据画布：保持接近 `#01040b`，不覆盖会干扰星点类别、密度和选择状态的彩色渐变。
- 时间控制台：`#081226 → #020713`，只在底部加入微弱青色辉光。

共享实现类：

- `raelyn-app-environment`：全局环境背景。
- `raelyn-view` + `raelyn-view--*`：页面背景与页面气氛色。
- `raelyn-surface-base`：普通工作表面。
- `raelyn-surface-toolbar` / `raelyn-toolbar-band`：全宽控制栏与局部工具区。
- `raelyn-surface-reading`：长文本阅读与转写区域。
- `raelyn-surface-raised`：详情轨道与观察变化面板。
- `raelyn-surface-timeline`：星域时间轴。
- `raelyn-modal-surface`：弹窗和全局搜索结果。

页面气氛色保持固定语义：星域与资料库使用青蓝，故事使用靛紫并辅以极弱琥珀，简报使用靛蓝，播放与媒体使用天蓝，运行中心使用青绿，资源观测站使用等权的青紫遥测信号，设置使用中性蓝灰。气氛色透明度应低于状态色，不能改变数据颜色的判读。

### Base

- 页面背景：`raelyn-app-environment` / `raelyn-view`，纯色回退为 `bg-slate-950`
- 面板背景：`raelyn-surface-base`，纯色回退为 `bg-slate-900/40`
- 弱底背景：`bg-slate-950/20`
- 顶栏背景：`raelyn-main-header` / `raelyn-surface-toolbar`，纯色回退为 `bg-slate-950/70`
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
