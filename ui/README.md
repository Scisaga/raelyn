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

## 星域定向验证

普通前端回归只运行相关文件：

```bash
node --test ui/src/views/__tests__/event-map-state.test.mjs
```

真实浏览器回归复用正在运行的 API、已构建的 Three.js 资源和真实任务产物，不插入测试事件。测试访问层只转发 GET，拒绝数据库写入请求；原生 HTTP 客户端和浏览器都直接连接目标，不读取进程代理变量。

在仓库根目录运行：

```bash
npm ci --prefix scripts/record
bash -c 'source scripts/dev/load-env.sh; RAELYN_RUN_REAL_UI_TESTS=1 node --test ui/src/views/__tests__/event-map-real.test.mjs'
```

需要 Playwright Chromium；API 默认为 `http://127.0.0.1:8000`，可通过 `RAELYN_REAL_UI_BASE_URL` 指定。测试从近期成功构建任务中寻找 ready current 和不可用的旧版本，覆盖旧目录/状态缓存重入、显式请求 current、历史版本错误显示以及仍保留的历史版本，并对构建后的完整星域页面做一次冒烟验证。缺少前置产物时会 skip，并说明需先完成真实事件抽取、生成至少三版星域及旧版本回收。未设置 `RAELYN_RUN_REAL_UI_TESTS=1` 时不连接真实服务。

## 运行中心定向验证

只读浏览器回归复用真实观测域、活跃视频任务和覆盖率接口，验证首屏使用 compact 域目录、任务展示不触发逐视频详情或播放列表统计请求，并检查 6 秒首屏预算；缺少前置数据会 skip，并提示先同步信源和启动视频处理任务。需要运行中的 API 和 Playwright Chromium，地址与安装方式同上。

```bash
bash -c 'source scripts/dev/load-env.sh; RAELYN_RUN_REAL_UI_TESTS=1 node --test ui/src/views/__tests__/operations-real.test.mjs'
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
<script src="/static/vendor/lightweight-charts.min.js"></script>
<script defer src="/static/app.js"></script>
<script defer src="/static/vendor/alpine.min.js"></script>
```

故事页只展示当前发展、关系、证据和相关简报；已移除故事变化与完整快照审计。简报引用展示冻结摘要并打开当前对象或原始证据，canonical 定位直接读取当前详情。

## AI 接入设置

“设置 → AI 接入 → 本地服务”可以在“本地 LLM”和“Codex”之间切换。Codex 处理事件抽取、转写润色、简报等文本任务，ASR 继续使用本地语音转写服务。Codex 需由管理员以服务运行账号在服务器上安装并登录；页面只显示安装、登录与连接测试结果，不接收登录凭据。模型和超时仍由服务器配置，未指定模型时显示“Codex 默认模型”。

选择后可先测试当前草稿，再保存启用；未保存的选择不会沿用其他 LLM 后端的健康状态。安装与登录检查通过显示“已就绪”，真实文本生成测试通过后显示“可用”。火山引擎模式继续同时接管 LLM 和 ASR，本地 LLM 选择独立保留。

Codex 开启每日额度保护后，AI 接入面板展示今日已用、每日上限、今日可用剩余与本周已用。每日上限读取服务器配置，当前为周订阅总额度的 50%；用量依据订阅额度读数，不能用 token 数替代。达到当日上限时，顶栏和任务页显示“AI 已挂起”及具体原因，并以北京时间显示自动恢复时间；采集、下载与 ASR 继续运行。首次启用时建立用量基线，账户内其他 Codex 使用也计入观测到的当日用量。额度无法确认时显示“待确认”，不会当作零用量。额度挂起期间，任务页的 AI worker 状态不提供手动启动操作；任务保留已完成进度，次日恢复后继续，按新到旧处理。预算状态复用 `/system` 的现有轮询更新，不覆盖未保存的 AI 接入草稿。

设置页定向验证（纯状态逻辑，不连接服务或写入数据库）：

```bash
node --test ui/src/views/__tests__/settings-inference.test.mjs
```
