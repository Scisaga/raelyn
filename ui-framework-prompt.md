你是一个资深前端/全栈工程师，要在现有 Python 项目中新增一个“前端 UI 脚手架”，要求可私有化部署（禁止 CDN），运行时不依赖 Node，但允许在开发/构建阶段用 Node 编译 Tailwind。请在项目根目录下创建 ui/ 文件夹，并生成完整可运行的脚手架文件（包含固定版本依赖与构建脚本）。

# 目标
- UI 技术栈：TailwindCSS + Alpine.js（都必须离线本地化，不允许 CDN）
- 后端：接口层由 Flask / FastAPI 提供（本提示词只生成 ui/ 及其资源，不修改后端代码）
- 私有化部署：所有静态资源都从本地 /static/ 路径加载
- 交互框架：提供统一的“应用壳（App Shell）”布局，包含顶部状态栏 + 左侧菜单 + 主体内容区
  - 顶部状态栏：展示当前页面标题、全局状态（例如：同步状态/连接状态）、右侧操作区（例如：用户/设置入口）
  - 左侧菜单：固定宽度，可滚动；至少包含 3 个一级菜单项；支持高亮当前项
  - 主体内容区：通过左侧菜单切换主体内容（无需刷新页面），使用 Alpine 的状态变量控制视图切换（例如 `activeView`），并用 `x-show`/`x-transition` 实现基础过渡
- 依赖固定版本号：所有 NPM 依赖必须锁定具体版本，禁止使用 latest、^、~ 等浮动版本；必须生成 lockfile
- 工具链尽量轻：不使用 Vite/webpack 等打包器（只用 Tailwind CLI + 简单复制脚本）
- 产物策略：
  - Tailwind 在构建阶段编译为单个 CSS 文件：输出到 ../static/css/tailwind.min.css
  - Alpine.js 从 node_modules 拷贝到 ../static/vendor/alpine.min.js
- 生成可执行脚本：一条命令完成构建（编译 CSS + 拷贝 Alpine），并提供 watch 模式
- 生成 README.md：说明如何安装依赖、如何构建、如何在 Flask/FastAPI 模板中引用资源
- 所有路径在 Linux 环境可用

# 依赖版本（以 2025-12-22 的最新稳定版为准，必须严格固定）
- tailwindcss：4.1.18
- @tailwindcss/postcss：4.1.18
- postcss：8.5.6
- autoprefixer：10.4.23
- alpinejs：3.15.3

# 需要创建的目录与文件
在项目根目录创建：
ui/
  package.json
  package-lock.json（必须存在，且锁定上述版本）
  tailwind.config.cjs
  postcss.config.cjs
  input.css
  scripts/
    build.mjs
    ensure-alpine.mjs
  templates/
    index.html（示例页面：Tailwind + Alpine；引用 ../static/ 下的本地资源）
  README.md

并且构建脚本必须确保输出（若目录不存在要自动创建）：
../static/css/tailwind.min.css
../static/vendor/alpine.min.js

# package.json 脚本要求
- npm run ui:build：执行 node ui/scripts/build.mjs
  - 编译 Tailwind -> ../static/css/tailwind.min.css（minify）
  - 拷贝 Alpine -> ../static/vendor/alpine.min.js
- npm run ui:watch：
  - 先执行 ensure-alpine.mjs（确保 Alpine 已拷贝）
  - 然后运行 tailwind CLI watch，将输出持续写入 ../static/css/tailwind.min.css

# Tailwind v4 注意事项
- 采用 Tailwind v4 推荐的 PostCSS 插件方式（使用 @tailwindcss/postcss）
- postcss.config.cjs 必须使用 @tailwindcss/postcss + autoprefixer
- tailwind.config.cjs 仅用于 content 扫描与主题扩展；content 必须覆盖：
  - ../templates/**/*.html（或你的后端模板目录）
  - ../static/js/**/*.js（如有）
  - ui/templates/**/*.html（本脚手架示例页）

# 示例页面要求（ui/templates/index.html）
- 必须有一个卡片组件：rounded-2xl + shadow
- 必须体现“顶部状态栏 + 左侧菜单 + 主体内容区”的整体布局
  - 左侧菜单点击后，切换主体内容区的不同页面片段（例如：概览/信号/任务/设置），并高亮当前菜单项
  - 顶部状态栏标题随主体内容切换而变化
- Alpine 提供 x-data 状态（如 `activeView`、`count`），包含按钮交互
- 页面引用必须为本地资源（不可 CDN）：
  <link rel="stylesheet" href="/static/css/tailwind.min.css">
  <script defer src="/static/vendor/alpine.min.js"></script>

# 输出形式
- 直接给出每个文件的完整内容（可复制粘贴落地）
- 所有路径、脚本、命令必须可执行且彼此一致
- 不要输出多余解释；仅输出脚手架内容与 README 的必要说明
