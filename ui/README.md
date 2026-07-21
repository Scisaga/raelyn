# UI（离线）

技术栈：TailwindCSS + Alpine.js（全部本地化，不使用 CDN）。

构建产物输出到根目录 `static/`：
- `static/css/tailwind.min.css`
- `static/vendor/alpine.min.js`
- `static/app.js`（由 `ui/src/**` 打包生成）
- `static/index.html`（由 `ui/templates/app/**` 组装生成）

HTML 源码位置：
- 入口：`ui/templates/app/index.html`
- 布局：`ui/templates/app/layout/**`
- 组件：`ui/templates/app/components/**`
- 视图：`ui/templates/app/views/**`

JS 源码位置：
- 入口：`ui/src/app/index.js`
- stores：`ui/src/stores/**`
- services：`ui/src/services/**`
- views：`ui/src/views/**`
- components：`ui/src/components/**`

## 安装与构建

```bash
cd ui
npm ci
npm run ui:build
```

构建会根据 `static/app.js` 与 `static/css/tailwind.min.css` 内容生成静态资源版本参数并写入 `static/index.html`。服务端对 SPA HTML 与 `/static/*` 返回 `Cache-Control: no-cache`，浏览器刷新后会重新验证资源，避免继续运行旧前端产物。

## Watch

```bash
cd ui
npm run ui:watch
```

说明：构建阶段需要 Node（运行时不需要）。在 WSL 环境如果 `node` 不在 PATH，脚本会自动尝试使用 Windows 安装的 `node.exe`（`/mnt/c/Program Files/nodejs/node.exe`）。

## 后端引用

```html
<link rel="stylesheet" href="/static/css/tailwind.min.css" />
<script src="/static/vendor/lightweight-charts.min.js"></script>
<script defer src="/static/app.js"></script>
<script defer src="/static/vendor/alpine.min.js"></script>
```
