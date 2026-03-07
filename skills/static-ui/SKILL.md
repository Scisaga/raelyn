---
name: static-ui
description: Maintain or extend the RAELYN static admin UI in this repo. Use when editing ui/templates/app/**, ui/input.css, ui/scripts/**, static/app.js, or related static assets while preserving the current dark console design and local-only build pipeline.
---

# Static UI

用于维护当前仓库的静态管理后台 UI。这个 skill 只描述项目里真实在用的前端形态，不覆盖旧草稿页面。

## 适用范围

- 生产 UI 的源码入口在 `ui/templates/app/index.html`
- 页面由 `ui/templates/app/partials/**` 通过 `ui/scripts/html.mjs` 拼成 `static/index.html`
- 样式入口是 `ui/input.css`，Tailwind 输出到 `static/css/tailwind.min.css`
- 运行时交互集中在 `static/app.js` 的 `RaelynApp()`
- 本地运行依赖从 `ui/node_modules` 复制到 `static/vendor`
- `ui/templates/index.html` 是早期示例，不要把它当成现行设计基准

## 工作方式

1. 先看当前视图所在 partial 和 `static/app.js` 里的对应状态/动作。
2. 优先修改源码文件，不直接手改生成文件。
3. 改了模板、Tailwind 输入或 vendor 依赖后，运行 `cd ui && npm run ui:build`。
4. 只改必要文件；保持 Alpine 结构和现有命名风格一致。

## 不要直接编辑的文件

- `static/index.html`：由 `ui/templates/app/**` 生成
- `static/css/tailwind.min.css`：由 `ui/input.css` 构建

除非用户明确要求，否则不要把生成文件当作主要编辑目标。

## 设计语言

### 总体气质

- 深色、克制、偏运营后台/控制台
- 高信息密度，但层级要清楚
- 以可读性和状态表达为先，不做营销页式的大留白

### 配色

- 主背景：`bg-slate-950`
- 主面板：`bg-slate-900/40` 或 `bg-slate-950/20`
- 边框/分割线：`border-slate-800`、`divide-slate-800`
- 主文字：`text-slate-100` 到 `text-slate-300`
- 弱文字/说明：`text-slate-400`、`text-slate-500`
- 主强调色：emerald
- 危险/错误：rose

不要引入新的高饱和主色做第二套视觉中心。

### 布局

- 固定应用壳：左侧栏 + 顶栏 + 主内容区
- 顶栏高度固定 `h-14`，半透明深色并带 `backdrop-blur`
- 侧栏支持展开/折叠，宽度以 `w-48` / `w-14` 为主
- 主内容区优先使用“全高面板”而不是居中卡片页面
- 大多数业务页使用 `h-full flex flex-col min-h-0`，把滚动放到内容区内部

推荐面板骨架：

```html
<div class="bg-slate-900/40 h-full flex flex-col min-h-0 divide-y divide-slate-800">
  <div class="shrink-0 px-4 py-3">...</div>
  <div class="flex-1 min-h-0 overflow-auto raelyn-scrollbar">...</div>
</div>
```

### 组件风格

- 按钮以暗色描边按钮为默认，主操作才用 emerald 实心
- 危险操作用 rose 弱底，不要做亮红大按钮
- 输入框/选择框统一深色底、细边框、emerald focus ring
- 列表优先 `divide-y`，hover 用 `bg-slate-900/50`
- badge/pill 用小字号、圆角、边框、弱底色
- 弹窗遮罩固定 `bg-black/60`，内容层使用深色背景 + 边框 + blur

### 排版

- 正文通常 `text-sm`
- 辅助信息通常 `text-xs` 或 `text-[11px]`
- 时间、计数、ID 这类元信息优先 `font-mono` 或 `tabular-nums`
- 标题不要过大；当前 UI 基本停留在 `text-sm` 到 `text-2xl`

### 动效

- 只用克制的小过渡，常见范围 150ms 到 200ms
- Alpine 过渡优先 `x-transition.opacity`
- 大卡片 hover 可以有轻微抬升，但要像 `video-card` 一样只在重点卡片上使用
- 涉及明显位移/缩放时，要兼容 `prefers-reduced-motion`

## 当前页面模式

- `overview`：统计卡 + 最近对象列表，允许更强的视觉层次和图片预览
- `media` / `playlists` / `jobs`：工具条 + 全高滚动列表
- `videos`：筛选条 + 响应式卡片网格
- `playlist`：沉浸式背景图 + 深色遮罩 + 多区块联动
- `settings`：页签切换 + 表单/文本编辑器面板
- `modals/*`：统一挂在 `app.html` 根层，避免局部 stacking context 问题

## Alpine 约束

- 根状态在 `static/app.js` 的 `RaelynApp()`
- 导航项、标题、视图切换逻辑都在这里维护
- 新视图优先复用已有状态组织方式，不要再造第二套页面控制器
- 常用模式：
  - `x-show` 切视图
  - `x-ref` 处理输入和图表容器
  - `@click.away` 处理下拉/弹层
  - `x-transition.opacity` 处理显隐

## 新增页面/模块时

1. 在 `ui/templates/app/partials/views/` 或 `ui/templates/app/partials/modals/` 新增 partial。
2. 在 `ui/templates/app/partials/main.html` 或 `ui/templates/app/partials/app.html` 接入 include。
3. 在 `static/app.js` 里补 `navItems`、标题、状态和事件。
4. 如果新增 Tailwind class 来源路径，确认 `ui/input.css` 的 `@source` 覆盖到了。
5. 运行 `cd ui && npm run ui:build` 更新产物。

## 资源与依赖约束

- 禁止外部 CDN，运行时资源必须走本地 `static/`
- Alpine 和 lightweight-charts 通过 `ui/scripts/build.mjs` 复制到 `static/vendor`
- 保持现有 Tailwind v4 + Alpine.js 组合，不引入 Vite/React/额外前端框架
- 图标当前以内联 SVG 为主；新增图标时优先延续这一做法

## 实现时的判断标准

- 看起来应该像同一个产品，而不是新拼进去的一页
- 优先贴边的面板化结构，不要默认套 `max-w-*` 的营销站布局
- 响应式下，工具条允许换行，按钮文字可以按断点隐藏
- 如果一个改动会破坏 `ui/templates/app/** -> static/index.html` 的生成链路，就不是合格方案
