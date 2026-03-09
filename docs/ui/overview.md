# UI 设计总览

约束（参考 `ui-framework-prompt.md`）：

- 技术栈：TailwindCSS + Alpine.js
- 禁止 CDN，静态资源从 `/static/` 加载
- 应用壳采用顶部状态栏 + 左侧菜单 + 主体内容区
- 左侧菜单切换视图基于 `activeView` 配合 `x-show / x-transition`

## 全局布局（App Shell）

顶部状态栏：

- 左：当前视图标题（Media / Videos / Jobs / Playlists / Briefs / Settings）
- 中：全局状态（如最近一次同步时间、队列 pending 数、worker 在线 / 离线）
- 右：快速操作（如“添加媒体”“立即同步全部”“生成今日简报”）

左侧菜单建议包含：

- 概览（Overview）
- 媒体（Media）
- 视频（Videos）
- 任务（Jobs）
- 播放列表（Playlists）
- 简报（Briefs）
- 设置（Settings）

## 概览页（Overview）

卡片组件建议展示：

- 今日新增视频数
- `pending / running` job 数
- 最近失败任务数（点击跳转 Jobs 并过滤 `failed`）
- 存储占用（可选）

## 媒体页（Media）

功能：

- 媒体列表：头像、名称、provider、订阅数、最近同步时间
- 搜索框：按名称或描述模糊搜索
- 操作：添加媒体、删除、立即同步
- 可选的媒体详情：展示最近 N 条视频与“批量下载全部”

交互：

- 添加媒体弹窗：`provider + URL`
- 同步按钮触发 `/api/media/{id}/sync`，并给出“已投递任务”的提示

## 视频页（Videos）

功能：

- 按时间范围、媒体、状态筛选
- 列表列展示标题、媒体、发布时间、时长、状态、产物图标
- 可选的详情抽屉 / 弹窗：展示可播放链接、字幕或文本下载

## 任务页（Jobs）

功能：

- 列表展示 `type / status / scheduled_for / started / finished / attempt / progress / error`
- 支持按 `status / type` 过滤
- 支持 `retry / cancel`
- 详情展示 `params / result / job_events`

## 播放列表页（Playlists）

MVP 功能：

- 创建 / 删除播放列表
- 在播放列表中添加 / 移除媒体（多选弹窗）
- 展示播放列表包含的媒体数量与最近简报日期

## 简报页（Briefs）

功能：

- 选择播放列表与日期范围
- 列表展示日期、状态、生成时间
- 查看简报：Markdown 渲染
- 操作：生成指定日期简报

## 设置页（Settings）

配置项建议与 `app_config` 对应：

- 同步间隔（分钟）
- 自动下载开关
- 下载并发（按 provider）
- ASR 服务地址（可空）
- LLM 推理 endpoint URL（可空）
- 存储 bucket / 前缀（可选）
