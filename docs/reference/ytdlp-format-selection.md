# yt-dlp 视频 / 音频格式选择策略

本文记录 `yt-dlp` 下载时的视频 / 音频格式选择经验，尤其是 YouTube 的 combined、HLS、DASH video-only、audio-only 路径差异。它面向运行排障和配置决策，不替代 `yt-dlp` 官方格式选择文档。

## 核心结论

格式选择是独立于 cookies / 代理 / PO Token 的一层问题。认证态正确、PO Token 生成成功，并不保证所有媒体 URL 都能下载。

对本项目来说，当前默认策略是：

- YouTube 下载优先使用 combined MP4 / HLS，再回退 DASH video-only。
- 不要把 `HTTP Error 403: Forbidden` 直接归因于 cookies 失效。必须先确认 403 发生在网页解析、player API、PO Token 生成，还是实际媒体 URL 下载阶段。
- 如果 403 只发生在 video-only GVS URL，而 audio-only 或 HLS 可下载，应优先调整格式 selector，不要更换 cookies 或切无 cookies。
- 自定义 `YTDLP_FORMAT` / `ytdlp_format.text` 时，避免把 `bestvideo+bestaudio` 放在 YouTube 的第一选择，除非当前出口和目标视频已实测通过。

## 格式类型

`yt-dlp` 会从同一个视频里提取多个 format。常见类型：

- combined / progressive：一个 URL 同时包含视频和音频。常见 selector 形态是 `best[ext=mp4]`。优点是下载路径接近浏览器普通播放，合并成本低；缺点是清晰度可能不如 DASH video-only。
- HLS：`protocol=m3u8_native`，通常由多个 ts/fmp4 分片组成。YouTube 样本中 format `96`、`95` 属于这类 combined MP4/HLS 路径。优点是本次 403 样本中可用；缺点是碎片多、下载耗时更长。
- DASH video-only：例如 `137`、`399`、`248` 等，只有视频没有音频，通常需要再配 `140` 这类 audio-only 并由 ffmpeg 合并。优点是清晰度和编码选择更多；缺点是更容易命中单独的 GVS 媒体 URL 限制。
- audio-only：例如 YouTube `140`，只含音频。它可以在 video-only 403 时仍然成功，因此不能用音频成功证明视频 URL 也可下载。
- storyboard / thumbnail：例如 `sb0`、`sb1` 等，不是可用正片视频，不应作为下载成功判断。

## 2026-05-18 实测

背景：Bloomberg Television 一批 YouTube 下载任务失败，错误为：

```text
ERROR: unable to download video data: HTTP Error 403: Forbidden
```

实测条件：

- `yt-dlp 2026.03.17`
- `curl_cffi 0.14.0`
- `YTDLP_PROXY=socks5://127.0.0.1:8887`
- `YTDLP_YOUTUBE_IMPERSONATE=chrome`
- `YTDLP_POT_BGUTIL_BASE_URL=http://127.0.0.1:4416`
- 已保存 YouTube cookies
- 同一 worker 环境下加载 `NO_PROXY`，避免本机 bgutil `/ping` 被全局 `HTTP_PROXY` 劫持

实测结论：

- `yt-dlp` 能找到 YouTube cookies。
- `node` JS challenge 成功。
- `bgutil:http` 被识别，并成功生成 `gvs PO Token`。
- 403 发生在实际 `googlevideo.com/videoplayback` 媒体 URL 下载阶段。
- 同一视频内，audio-only format `140` 返回 `206`。
- 低清 video-only format `133`、`394`、`395` 可返回 `206`。
- 360p 及以上 DASH video-only URL 返回 `403`。
- HLS / combined MP4 路径可下载：`96`、`95`、`best[protocol^=m3u8][height<=1080]`、`best[ext=mp4][height<=1080]` 均通过测试下载。

这说明：该批 403 不是 cookies 整体失效，也不是 bgutil 未接入，而是格式路径选择问题。

## 当前默认 Selector

YouTube 当前优先 selector：

```text
best[ext=mp4][height<=1080]/best[height<=1080]/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best
```

若运行时配置是历史 DASH-first selector，代码会识别并在 YouTube 路径上改为 HLS / combined-first，同时保留原来的高度上限。例如旧 720p 配置：

```text
bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/bestvideo[height<=720]+bestaudio/best[height<=720]/best
```

会优先尝试：

```text
best[ext=mp4][height<=720]/best[height<=720]/bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best
```

## Retry 规则

同一个 YouTube 下载任务内允许格式回退：

1. 首选 YouTube HLS / combined MP4。
2. 回退到 DASH MP4。
3. 回退到 `bv*+ba/b`。
4. 回退到 `b`。

触发回退的条件：

- `Requested format is not available`
- YouTube 媒体 URL 下载阶段的 `HTTP Error 403: Forbidden`

注意：这里的 403 回退只改变 format selector，不改变认证态，不改 cookies，不写 `_download_without_cookies`。

## 排障步骤

遇到 YouTube 下载 403 时按下面顺序查：

1. 看错误发生位置。如果是 `Sign in to confirm you're not a bot` / `LOGIN_REQUIRED`，按 cookies / bot check 策略处理；如果是 `unable to download video data: HTTP Error 403`，优先查 format。
2. 用 `yt-dlp -v` 或 Python API verbose 确认 `node` JS challenge 是否成功、`bgutil:http` 是否生成了 `gvs PO Token`。
3. 对比同一视频的 audio-only、DASH video-only、HLS / combined MP4。不要只测一个 format。
4. 对媒体 URL 做 range 请求，只取前 1KB 即可判断是否 206 / 403，避免完整下载造成额外压力。
5. 如果 HLS / combined 可用而 DASH video-only 403，调整 selector，重试失败任务。
6. 如果所有格式都 403，再回到代理出口、频率、账号、PO Token、cookies 导出环境排查。

示例 selector：

```bash
# 优先 combined MP4 / HLS，限制 1080p
best[ext=mp4][height<=1080]/best[height<=1080]/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best

# 直接验证 HLS / combined 是否可用
best[protocol^=m3u8][height<=1080]

# 容易命中 DASH video-only 路径，不建议作为 YouTube 默认首选
bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]
```

## 配置建议

- `YTDLP_FORMAT` 留空时，使用代码默认值。
- 若要限制清晰度，优先改 height 上限，不要把 `bestvideo+bestaudio` 调到第一位。
- `ytdlp_format.text` 会优先于环境变量 `YTDLP_FORMAT`，因此 UI 保存过旧 selector 时，也要检查数据库运行时配置。
- 如果为了节省带宽选择 720p，应使用 combined/HLS-first 的 720p selector：

```text
best[ext=mp4][height<=720]/best[height<=720]/bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best
```

## 与其他策略的边界

- Cookies 策略解决认证态、登录态、bot check 和导出方式问题。
- PO Token Provider 解决 YouTube BotGuard / PO Token 方向的问题。
- 浏览器 impersonation 解决部分 TLS / 指纹路径差异。
- 本文解决的是“已经拿到媒体格式和 URL 后，选哪条 URL 下载更稳”的问题。

不要把这些层混在一起。一个格式 403 不等于 cookies 失效；一个 cookies 有效也不等于所有 DASH URL 都能下载。
