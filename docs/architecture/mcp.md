# MCP 集成设计

## 当前实现

项目已经实现挂载在主 API 进程内的 MCP HTTP 服务：

- 主应用入口：[backend/raelyn/main.py](../../backend/raelyn/main.py)
- MCP 注册层：[backend/raelyn/mcp/](../../backend/raelyn/mcp/)
- 默认入口：`/mcp`
- 健康检查：`GET /mcp/health`
- Transport：官方 Python `mcp` SDK 的 `FastMCP` + `Streamable HTTP`

MCP 作为主 API 的子应用挂载，但它直接复用现有 DB / model / service / job enqueue 能力，不通过 `/api/*` 再走一层 HTTP。

## 安全边界

- `API_BEARER_TOKEN` 非空时才会挂载 MCP；为空时主 API 仍可正常启动，但 `/mcp` 不可用。
- `/api` 与 `/mcp` 复用同一个 Bearer Token；浏览器 cookie 只服务 `/api`，MCP 仍只接受 `Authorization: Bearer <token>`。
- `/mcp/health` 允许匿名访问。
- `/mcp` 下的所有请求都要求 `Authorization: Bearer <token>`。
- MCP 与主 API 共用同一个监听端口，不额外占用独立端口。
- 远程使用时仍应配合主机防火墙、受控网段或反向代理。

## 能力范围

首版范围固定为 `Tools + Resources`：

- 只读查询
- 长文本 transcript 分块读取
- 安全的异步任务触发

首版不实现：

- OAuth
- Prompts
- 全文检索 / 向量检索
- 播放列表写操作
- 媒体删除
- 配置写入

## 共享业务逻辑

MCP 没有复制 API 路由逻辑，而是复用了抽出的共享 helper：

- [backend/raelyn/services/periods.py](../../backend/raelyn/services/periods.py)：`day/week/month` 周期计算
- [backend/raelyn/services/transcripts.py](../../backend/raelyn/services/transcripts.py)：transcript 选择与文本读取
- [backend/raelyn/services/media_actions.py](../../backend/raelyn/services/media_actions.py)：媒体同步任务投递
- [backend/raelyn/services/video_actions.py](../../backend/raelyn/services/video_actions.py)：下载与重转写任务投递
- [backend/raelyn/services/brief_actions.py](../../backend/raelyn/services/brief_actions.py)：简报生成任务投递

现有 REST API 也改为复用这些 helper，避免规则分叉。

## Tools

当前已注册的 MCP tools：

- `list_media`
- `get_media`
- `list_videos`
- `get_video`
- `get_video_transcript`
- `list_video_assets`
- `list_playlists`
- `get_playlist`
- `get_playlist_videos`
- `list_briefs`
- `get_brief`
- `get_playlist_brief`
- `get_playlist_latest_brief`
- `list_latest_briefs`
- `list_jobs`
- `get_job`
- `get_video_context`
- `get_playlist_summary`
- `sync_media`
- `download_video`
- `retranscribe_video`
- `generate_brief`

约定：

- 所有返回值都是 JSON 可序列化对象
- `UUID/date/datetime` 统一序列化为字符串
- 任务型 tools 只返回 `accepted + job_id/job_ids`，不阻塞等待执行完成
- 视频时间查询默认使用内容时间轴 `coalesce(content_published_at, published_at)`；`list_videos`、`get_playlist_videos`、`get_playlist_summary` 支持 `time_basis=content|platform`。
- 视频 payload 保留平台时间 `published_at`，并返回 `content_published_at`、`timeline_at`、`time_source`、`time_status`、`time_confidence`。

## Resources

当前已注册的 resources：

- `raelyn://media/{media_id}`
- `raelyn://video/{video_id}`
- `raelyn://video/{video_id}/transcript`
- `raelyn://video/{video_id}/transcript/chunks/{chunk_index}`
- `raelyn://video/{video_id}/assets`
- `raelyn://playlist/{playlist_id}`
- `raelyn://brief/{brief_id}`
- `raelyn://brief/{brief_id}/body`
- `raelyn://playlist/{playlist_id}/briefs/by-date/{date}`
- `raelyn://playlist/{playlist_id}/briefs/by-date/{date}/body`
- `raelyn://job/{job_id}`

设计约束：

- resources 只负责稳定对象读取
- 列表与复杂筛选统一走 tools
- 大文件资产默认返回元信息和短时 presigned URL，不把二进制直接塞进模型上下文

## Transcript 规则

transcript 选择顺序统一为：

1. 先按 variant 选择：`polished` 优先于 `plain`
2. 同一 variant 内优先 `subtitle` 的中文字幕：`zh`、`zh-hant`、`zh-hans`、`zh-cn`、`zh-tw`、`zh-hk`
3. 然后是 `qwen3-asr + zh`
4. 然后是历史遗留 source：`speaches + zh`
5. 以上都没有时，回退到最新的 transcript asset

其中 `qwen3-asr` 新资产不再强制保存为 `zh`；ASR 自动识别到的 `en` 或未知语言资产会通过第 5 步回退返回。

MCP transcript 输出字段固定包含：

- `ok`
- `status`
- `video_id`
- `asset_id`
- `language`
- `source`
- `variant`
- `polish_method`
- `total_chars`
- `chunk_index`
- `chunk_count`
- `has_more`
- `next_chunk_index`
- `next_uri`
- `text`

分块规则：

- 默认 `chunk_size=12000`
- 最大 `50000`
- 优先在 chunk 末尾附近按最后一个换行切分
- 越界块返回 `ok=false, status=not_found, reason=chunk_out_of_range`

## 聚合能力

首版除了基础对象读取，还补了两个面向 LLM 的高层 tool：

### `get_video_context`

返回：

- `video`
- `media`
- `assets`
- transcript 首块

适合“这个视频是否已经可分析”“先拿上下文再决定是否继续深入读取”。

### `get_playlist_summary`

返回：

- 播放列表概要
- 周期边界
- 周期内视频列表
- 每个视频的 transcript 就绪状态
- 可选 transcript 首块
- 对应 brief 状态

适合“总结这个播放列表某一天所在周期的内容”。

### Brief 读取语义

- `get_brief(brief_id)`：真正按 `brief_id` 读取简报对象。
- `get_playlist_brief(playlist_id, date)`：按播放列表当前 `brief_granularity`，定位“包含该日期的那个周期”的简报。
- `get_playlist_latest_brief(playlist_id)`：读取该播放列表当前粒度下最新可定位周期的简报。
- `list_latest_briefs`：按播放列表维度返回 latest brief 列表。
- brief JSON 会显式返回 `body_readable`、`body_resource_uri`、`body_mime_type`，正文内联字段统一为 `body_markdown`。
- 简报正文资源优先走 `raelyn://brief/{brief_id}/body` 或 `raelyn://playlist/{playlist_id}/briefs/by-date/{date}/body`，资源类型固定为 `text/markdown`。

## 与现有 REST 的关系

MCP 与 REST 的关系不是一比一镜像，而是：

- 基础实体读取能力尽量复用现有模型与 service 语义
- 与 LLM 使用体验强相关的聚合能力放在 MCP 层
- 异步操作仍然落到现有 Job 体系

典型映射包括：

- `download_video` <-> `POST /api/videos/{video_id}/download`
- `retranscribe_video` <-> `POST /api/videos/{video_id}/transcript/retranscribe`
- `generate_brief` <-> `POST /api/briefs/generate`

但 MCP 不直接调用 FastAPI route handler。

## 返回与错误语义

当前约定：

- 对象不存在：抛 `not_found`
- 参数非法：抛 `invalid_argument`
- 当前状态不允许操作：抛 `conflict`
- 产物还没就绪：返回 `ok=false, status=not_ready`

这意味着“还没转写好”“简报尚未生成”不会被伪装成 404。

## 运行与开发

本地开发入口：

- [backend/raelyn/main.py](../../backend/raelyn/main.py)
- [scripts/dev/devctl.sh](../../scripts/dev/devctl.sh)

`devctl.sh start` 只启动 API/worker/scheduler；如果 `API_BEARER_TOKEN` 非空，MCP 会随 API 一起挂载到 `/mcp`。如果为空，则 `/mcp` 与 `/mcp/health` 返回 `404`。

## 测试覆盖

当前已补的回归测试覆盖了以下高风险部分：

- 周期计算 helper
- transcript 选择顺序与 chunking
- 下载 / 重转写任务投递分支
- `get_brief` / `get_playlist_brief` / `get_playlist_latest_brief` / `get_video_context` / `get_playlist_summary`
- `/health` 和 Bearer 鉴权
- `Streamable HTTP` 协议最小链路：`list_tools`、`call_tool`、`read_resource`

## 后续扩展

当前未做、但后续仍值得评估的方向：

- `search_content`
- transcript 基于时间戳的切片读取
- Prompts
- 远程认证方案（如果以后不再局限于受信任局域网）
- 更强的检索能力（全文 / 混合 / 向量）

这些取舍如果以后变成明确架构决策，再进入 `docs/adr/`。
