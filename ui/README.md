# UI（离线）

技术栈：TailwindCSS + Alpine.js（全部本地化，不使用 CDN）。

构建产物输出到根目录 `static/`：
- `static/css/tailwind.min.css`
- `static/vendor/alpine.min.js`
- `static/index.html`（由 `ui/templates/app/**` 组装生成）

HTML 源码位置：
- 入口：`ui/templates/app/index.html`
- 组件拆分：`ui/templates/app/partials/**`

## 安装与构建

```bash
cd ui
npm ci
npm run ui:build
```

## Watch

```bash
cd ui
npm run ui:watch
```

说明：构建阶段需要 Node（运行时不需要）。在 WSL 环境如果 `node` 不在 PATH，脚本会自动尝试使用 Windows 安装的 `node.exe`（`/mnt/c/Program Files/nodejs/node.exe`）。

## 后端引用

```html
<link rel="stylesheet" href="/static/css/tailwind.min.css" />
<script defer src="/static/vendor/alpine.min.js"></script>
```
