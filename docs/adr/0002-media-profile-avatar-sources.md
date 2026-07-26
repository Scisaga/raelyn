# ADR-0002：媒体资料同步补齐可缓存头像来源

- 状态：已采纳
- 日期：2026-07-26

## 背景

一批新增媒体的 `media.sync_profile` 均已成功执行，但 10 条记录都没有 `avatar_url` 和 `avatar_asset_id`：

- 7 个 YouTube 频道的普通 OpenGraph 请求超时，yt-dlp flat 资料同步成功，但 handler 只读取 `channel_thumbnail` / `thumbnail`。
- 3 个 B 站账号的公开空间页返回名称和简介，没有可用的 `/bfs/face/` OpenGraph 头像。

前端只有在 API 返回 `avatar_asset` 时才显示图片，因此本问题不是资源展示失败，而是资料同步成功边界没有覆盖实际头像字段与可达下载路径。

## 接口与实测依据

2026-07-26 在当前运行环境完成最小只读实测：

1. YouTube 频道 yt-dlp flat 结果包含 9 个 `thumbnails`，其中有 `avatar_uncropped` 和方形 900×900 频道头像，也包含多张横幅；旧 handler 没有读取该列表。
2. `yt3.googleusercontent.com` 头像直连 15 秒超时；通过项目已有的 YouTube yt-dlp 网络栈和显式 `YTDLP_PROXY` 请求同一头像返回 HTTP 200。
3. B 站 `/x/web-interface/card?mid=...` 在 `curl_cffi` Chrome 模拟、相同 Cookies 和直连出口下返回 HTTP 200、业务码 `0`，并包含名称、简介、粉丝数、视频数和有效的 `hdslb.com/bfs/face/` 头像。
4. B 站头像继续通过不读取环境代理的普通头像缓存器直连下载，样本返回 HTTP 200。

## 决策

1. YouTube 资料同步保留 yt-dlp flat 提取，头像选择顺序为：
   - `channel_thumbnail`
   - `thumbnails` 中带 `avatar` 标识的图片
   - `thumbnails` 中宽高比接近 1:1 的图片
   - 顶层 `thumbnail` 仅作为最后回退
2. 不使用 YouTube 频道横幅作为头像。
3. YouTube 选中的频道头像字节通过 yt-dlp 网络栈下载，复用该任务已有的 Cookies、浏览器模拟和显式 `YTDLP_PROXY`；这不改变其他资料、头像或服务请求的直连规则。
4. B 站资料同步优先读取 `/x/web-interface/card`；端点不可用时回退公开空间页，不恢复受限的 `/x/space/acc/info`，也不为缺失头像回退到 yt-dlp。
5. B 站资料与头像请求继续显式禁用环境代理。
6. 上游明确返回头像 URL 但头像资产缓存失败时，`media.sync_profile` 进入既有有限重试，不再以成功状态掩盖缺失资产。
7. 头像资产继续使用 `media.avatar_asset_id` 和 `replace_standalone_asset()` 幂等替换；不新增表或任务类型。

## 影响

- 新增或重新同步的 YouTube、B站媒体可以生成 `avatar_asset`，前端无需修改。
- YouTube 资料同步在需要下载频道头像时会额外执行一次小型 yt-dlp 资源请求，但仍受现有 provider 同步并发门控和最多两次任务尝试约束。
- B 站正常资料同步由“公开空间页一次请求”调整为“card 端点一次请求”；只有 card 不可用时才增加空间页回退。
- 已有缺失头像的媒体需要重新投递 `media.sync_profile` 才会补齐。

## 实现位置

- [资料来源与 B 站 card 解析](../../backend/raelyn/services/profile_fetch.py)
- [YouTube 头像选择与任务成功边界](../../backend/raelyn/jobs/handlers/media_sync.py)
- [头像资产缓存](../../backend/raelyn/jobs/handlers/common.py)
- [YouTube yt-dlp 字节下载](../../backend/raelyn/services/ytdlp.py)
