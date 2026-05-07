# YouTube yt-dlp 同步与 Cookies 策略

本文记录 YouTube `yt-dlp` 同步失败、cookies 失效、bot check 和 PO Token 相关的当前判断。它面向运行排障和配置决策，不替代 yt-dlp 官方文档。

## 当前结论

不要把“长期稳定依赖 YouTube cookies”当成主方案。YouTube 会轮换 cookies、限制账号或 IP，并逐步要求 PO Token；cookies 更适合作为需要登录权限时的补救手段。

对本项目来说，最稳妥的默认策略是：

- `media.sync_videos` 面向公开频道 / 视频列表同步，默认应优先使用未登录态。
- YouTube 的 `yt-dlp` 同步 / 下载可显式使用 `YTDLP_PROXY`；这只影响 YouTube 访问出口，不代表 ASR / LLM / Embedding / 头像抓取也走代理。
- YouTube cookies 只用于确实需要登录权限的内容，例如私有、会员、年龄限制或账号可见内容。
- 普通公开频道同步不要消耗账号登录态，避免把账号 cookies 暴露在高频定时任务里。
- 当同步任务持续触发 bot check 或 cookies 失效时，优先降低请求波峰，而不是反复更换 cookies。

## 同步频率

`SYNC_INTERVAL_MINUTES` 约束的是单个媒体的最短同步间隔，不表示全站每小时只投递一个同步任务。`scheduler` 每分钟扫描一次到期媒体，并按 `SYNC_BATCH_SIZE` 控制单次最多投递多少个 `media.sync_videos`。

当前推荐：

- `SYNC_INTERVAL_MINUTES=60`
- `SYNC_INTERVAL_JITTER_MINUTES=15`
- `SYNC_BATCH_SIZE=2`

这样做会让积压追赶变慢，但能显著降低同一分钟集中请求 YouTube / B 站的概率。

## 代理规则

YouTube 的 `yt-dlp` 同步 / 下载请求可显式使用 `YTDLP_PROXY`，同步和下载应保持同一出口。应用不会把 shell、systemd 或容器环境中的 `HTTP_PROXY` / `HTTPS_PROXY` 当成 YouTube 代理配置；需要代理时必须配置 `YTDLP_PROXY`。

`YTDLP_PROXY` 不适用于 ASR、LLM、Embedding、B 站请求、资料抓取、头像缓存或健康检查。那些请求默认直连，并且不应隐式继承进程环境代理。

## Cookies 导出

如果确实需要 YouTube cookies，推荐按 yt-dlp 官方 wiki 的方式导出：

1. 打开新的隐身 / 私密浏览窗口。
2. 登录 YouTube。
3. 在同一个窗口、同一个标签打开 `https://www.youtube.com/robots.txt`。
4. 导出 `youtube.com` 的 Netscape 格式 cookies。
5. 关闭该隐身窗口，避免浏览器继续使用并轮换这组 session。

不要把普通长期打开的浏览器会话作为稳定 cookies 来源。YouTube 会在打开的浏览器标签里频繁轮换账号 cookies，导出的 cookies 很容易很快失效。

## 失败处理

遇到 `YTDLP_COOKIES_YOUTUBE 已失效`、`Sign in to confirm you are not a bot`、`[youtube:tab] ... Playlists that require authentication ... without a successful webpage download` 或类似鉴权检查失败时，系统会把 YouTube provider 暂停，避免 `scheduler` 因 `last_video_sync_at` 未推进而每分钟反复投递同一个失败同步任务。

遇到 YouTube 频道页 / 视频页返回 `HTTP Error 404` 且 yt-dlp 明确报 `Requested entity was not found` 或 `Unable to download API page` 时，系统按“单个媒体源不可用”处理：自动关闭该媒体的 `monitor_enabled`，并在媒体列表展示“来源不可用”标签；不会暂停整个 YouTube provider。

排障时按下面顺序处理：

1. 确认公开频道同步是否可以不使用 YouTube cookies。
2. 确认 `SYNC_BATCH_SIZE` 是否足够低；当前推荐 `2`，必要时可临时降到 `1`。
3. 检查 cookies 是否按隐身窗口 + `robots.txt` 方式导出。
4. 确认当前出口 IP 是否已经被 YouTube 风控；必要时更换网络或代理。
5. 若未登录态和正确导出的 cookies 都不稳定，再评估 PO Token Provider。

## PO Token Provider

yt-dlp 官方 PO Token Guide 当前推荐用 PO Token Provider plugin，尤其是在 YouTube 持续要求 PO Token / SABR 的场景。`bgutil-ytdlp-pot-provider` 是官方 wiki 提到的 provider 之一。

对本项目这类长期运行服务，优先评估 HTTP server 模式，而不是每次调用 yt-dlp 时拉起一次脚本。HTTP server 模式更适合持续同步任务，也更容易统一观测和重启。

## 本项目当前状态

- 本地 `yt-dlp` 版本曾观测为 `2026.03.17`；不是特别旧，但 yt-dlp master / nightly 在 2026-04 仍有新构建。
- 当前配置已有 `YTDLP_REMOTE_COMPONENTS=ejs:github`，用于 YouTube EJS / JS challenge 组件。
- 当前未内置 PO Token Provider 配置。
- 已将 `SYNC_BATCH_SIZE` 推荐值降为 `2`，减少同步任务波峰。

## 参考来源

- yt-dlp YouTube cookies 导出说明：<https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies>
- yt-dlp FAQ cookies 用法：<https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp>
- yt-dlp Known Issues / FAQ：<https://github.com/yt-dlp/yt-dlp/issues/3766>
- yt-dlp PO Token Guide：<https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide>
- bgutil PO Token provider：<https://github.com/Brainicism/bgutil-ytdlp-pot-provider>
- cookies 仍失败的相关 issue：<https://github.com/yt-dlp/yt-dlp/issues/15392>
- 带 cookies 反而异常的相关 issue：<https://github.com/yt-dlp/yt-dlp/issues/16229>
