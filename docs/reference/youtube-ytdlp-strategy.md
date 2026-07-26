# YouTube yt-dlp 同步与 Cookies 策略

本文记录 YouTube `yt-dlp` 同步失败、cookies 失效、bot check 和 PO Token 相关的当前判断。它面向运行排障和配置决策，不替代 yt-dlp 官方文档。媒体 URL 下载阶段的 format 选择详见 [yt-dlp 视频 / 音频格式选择策略](ytdlp-format-selection.md)。

## 当前结论

不要把“长期稳定依赖 YouTube cookies”当成唯一方案。YouTube 会轮换 cookies、限制账号或 IP，并逐步要求 PO Token；但这不等于“默认不用 cookies”。当前下载实测中，`android_vr`、`web_safari`、`mweb` player response 都返回 `LOGIN_REQUIRED` 时，无 cookies 路径已经不是可行主路径。

对本项目来说，最稳妥的默认策略是：

- YouTube 普通 `media.sync_videos` 和 `video.download.youtube` 当前都应使用已保存的 `YTDLP_COOKIES_YOUTUBE`。只有在相同 yt-dlp 版本、相同出口、相同目标类型下做过最小实测，并证明无 cookies 路径稳定成功时，才能讨论局部关闭 cookies。
- 当 provider 已因 `ytdlp_cookies_*`、`youtube_bot_check` 或 `youtube_auth_check` 暂停时，scheduler 可投递 `media.sync_videos public_discovery=true`，显式无 cookies 抓公开视频 flat 列表。该降级只用于发现公开视频，不解除下载、字幕回补或 `video.enrich_metadata.youtube` 的 provider pause 门控。
- YouTube 的 `yt-dlp` 资料同步、频道头像下载和视频同步 / 下载可显式使用 `YTDLP_PROXY`；这只影响 YouTube yt-dlp 访问出口，不代表 ASR / LLM / Embedding 或其他头像抓取也走代理。
- 不要从“cookies 会被轮换 / 会增加账号风险”推导出“cookies 不需要”。正确结论是：降低同步频率、降低并发、保持导出 cookies 的浏览器环境干净，并增加 PO Token Provider / impersonation，而不是盲目切到无 cookies。
- `video.download.youtube` 默认使用 `YTDLP_COOKIES_YOUTUBE`，并通过 `YTDLP_YOUTUBE_IMPERSONATE=chrome` 启用浏览器 impersonation；当前实测这是比单纯重启代理更接近浏览器成功路径的组合。
- YouTube 频道 flat 列表有时只返回 `id/title/url/duration`，不返回 `timestamp/upload_date`。`media.sync_videos` 不再在同步循环里内联单视频 metadata 解析；若 flat 条目缺少发布时间，系统会投递低优先级 `video.enrich_metadata.youtube`，异步 best-effort 填充 `video.published_at/raw_info`，避免单个频道同步因逐条补 metadata 而超过执行心跳阈值。
- 当同步任务持续触发 bot check 或 cookies 失效时，优先降低请求波峰，而不是反复更换 cookies。

## 2026-05-18 排障复盘

本次排障中曾错误判断“YouTube 采集应默认不用 cookies”。这是一个严重错误，原因不是结论保守程度不够，而是推理链断了：

- 把外部 issue / 官方说明里的“cookies 可能失效、可能被账号风控”误读成“cookies 对公开内容不是必要条件”。前者只是风险提示，不能推出后者。
- 混淆了频道列表同步、播放页解析和实际媒体下载。不同 yt-dlp extractor / player client 会走不同路径；浏览器能打开、频道页能列出、媒体流能下载不是同一个事实。
- 忽略了本项目当前实测证据：无 cookies 下载时 `android_vr`、`web_safari`、`mweb` player response 均为 `LOGIN_REQUIRED`。这已经足以否定“无 cookies 可作为默认下载路径”。
- 把“减少账号登录态消耗”当成实现方向，而没有先证明当前代码是否真的无 cookies 成功。风险治理建议不能替代最小实测。
- 没有及时检查任务持久化参数。旧逻辑在 `HTTP Error 403: Forbidden` 后写入 `_download_without_cookies=true`，导致更新 cookies、重启进程组后，历史任务仍继续无 cookies 下载并触发 bot check。
- 没有先区分认证态、连接态、并发态和风控态。`403` 可能来自出口 IP、PO Token、下载频率、播放器客户端、cookies 使用环境等，不能直接用“去掉 cookies”作为兜底。

以后处理 YouTube cookies / bot check / 403 时，必须遵守下面的硬规则：

1. 任何改变“是否使用 cookies”的方案都属于认证态行为变更，必须先有当前代码日志、yt-dlp verbose 输出、最小实测或官方文档依据。
2. 最小实测必须在相同 `YTDLP_PROXY`、相同 yt-dlp 版本、相同 URL 类型下分别验证“带 cookies”和“无 cookies”，并记录底层 player status 或 yt-dlp 原始错误。
3. 不能把无 cookies 作为 403 的自动重试策略。403 只能进入可观测失败、降频、代理 / PO Token / impersonation 排查，不能悄悄改变认证态。
4. 排障时先查 `job.params` 是否有历史 `_download_without_cookies`，再判断 cookies 是否真的失效。更新 cookies 不会自动覆盖历史任务参数。
5. 看到“浏览器可访问 YouTube”时，必须同时说明浏览器是否登录、是否使用同一代理出口、是否有浏览器 TLS 指纹和站点数据；浏览器成功不能直接证明 yt-dlp 无 cookies 成功。
6. 文档和代码中禁止再出现“公开 YouTube 采集默认不用 cookies”这类无实测支撑的全局规则。若未来要降低 cookies 使用范围，只能按任务类型和媒体源做灰度验证。

## 同步频率

`SYNC_INTERVAL_MINUTES` 约束的是单个媒体的最短同步间隔，不表示全站每小时只投递一个同步任务。`scheduler` 每分钟扫描一次到期媒体，并按 `SYNC_BATCH_SIZE` 控制单次最多投递多少个 `media.sync_videos`。

当前推荐：

- `SYNC_INTERVAL_MINUTES=60`
- `SYNC_INTERVAL_JITTER_MINUTES=15`
- `SYNC_BATCH_SIZE=2`

这样做会让积压追赶变慢，但能显著降低同一分钟集中请求 YouTube / B 站的概率。

## 代理规则

YouTube 的 `yt-dlp` 资料同步、频道头像下载和视频同步 / 下载请求可显式使用 `YTDLP_PROXY`，同步和下载应保持同一出口。应用不会把 shell、systemd 或容器环境中的 `HTTP_PROXY` / `HTTPS_PROXY` 当成 YouTube 代理配置；需要代理时必须配置 `YTDLP_PROXY`。

YouTube 频道资料同步从 yt-dlp 返回的 `thumbnails` 中优先选择 `avatar_uncropped`，没有该标识时才选择方形频道图，避免把横幅当头像。所选头像属于本次 yt-dlp 资料同步的一部分，字节下载复用同一 `YTDLP_PROXY`、Cookies 和浏览器模拟配置；非 yt-dlp 的资料 / 头像抓取、B 站请求、ASR、LLM、Embedding 和健康检查仍默认直连，并且不应隐式继承进程环境代理。

## Cookies 导出

如果确实需要 YouTube cookies，推荐按 yt-dlp 官方 wiki 的方式导出：

1. 如果刚更新 cookies 后仍立刻触发 `confirm you're not a bot` / “确认你不是聊天机器人”，先清空浏览器里的 YouTube / Google 相关站点数据，再重新访问 YouTube；不要在原会话里直接重复导出。
2. 打开新的隐身 / 私密浏览窗口。
3. 登录 YouTube。
4. 在同一个窗口、同一个标签打开 `https://www.youtube.com/robots.txt`。
5. 导出 `youtube.com` 的 Netscape 格式 cookies。
6. 关闭该隐身窗口，避免浏览器继续使用并轮换这组 session。

不要把普通长期打开的浏览器会话作为稳定 cookies 来源。YouTube 会在打开的浏览器标签里频繁轮换账号 cookies，导出的 cookies 很容易很快失效。

## 失败处理

只有当 yt-dlp 明确返回 `provided YouTube account cookies are no longer valid`、`cookies are no longer valid` 或 cookies 文件格式错误时，系统才把失败归类为 `YTDLP_COOKIES_YOUTUBE` 失效 / 无效。

遇到 `Sign in to confirm you are not a bot`、`请登录，以便我们确认你不是聊天机器人`、`[youtube:tab] ... Playlists that require authentication ... without a successful webpage download` 或类似鉴权检查失败时，系统会把 YouTube provider 暂停，但归类为 `youtube_bot_check` / `youtube_auth_check`，避免把出口 IP、请求频率、PO Token、导出会话不一致等问题误报成 cookies 失效。暂停的目的仍然是避免 `scheduler` 因 `last_video_sync_at` 未推进而每分钟反复投递同一个失败同步 / 下载任务。

其中 `youtube_auth_check` 可能由一次频道页瞬时下载失败触发：首次失败且任务仍有剩余 attempt 时，worker 只按任务退避重试，不立即暂停整个 YouTube provider；最终尝试仍失败才持久化 provider pause。`youtube_bot_check` 和明确 cookies 无效仍立即暂停，避免在真实风控下继续请求。

provider 暂停期间，若 `SYNC_PUBLIC_DISCOVERY_ENABLED=true`，自动同步会为到期媒体投递低波峰的 public discovery 任务，默认抓最新 `SYNC_PUBLIC_DISCOVERY_MAX_ENTRIES=200` 条。若无 cookies flat 抓取仍被平台挡住，任务只更新该媒体同步冷却并记录 `public_discovery_blocked`，不会覆盖原 provider pause。保存有效非空 cookies 后，配置 API 会清除对应 provider pause，并为该 provider 的受监控媒体补投 `force=true, max_entries=SYNC_COOKIE_RECOVERY_MAX_ENTRIES, download_priority=8` 的 catch-up 同步，默认 `200`。

如果 YouTube 频道同步在 yt-dlp 调用内卡住，没有及时抛出上述可识别错误，sync worker 的执行 watchdog 会先重启进程并释放锁。回收扫描会把 `media.sync_profile` / `media.sync_videos` 的执行心跳过期视为一次同步尝试失败，按 `max_attempts` 有上限地重试，且不提升 orphan 优先级；终止失败的 `media.sync_videos` 会推进该媒体的 `last_video_sync_at` 作为冷却时间，避免单个频道反复卡死时占住整个同步队列。

YouTube 媒体传输阶段若出现代理 `CONNECT ... 502`、`connection closed`、`connection reset` 或媒体 URL `HTTP 502`，下载不会只对已经失败的签名 URL 长时间盲重试。每轮解析内仍由 yt-dlp 对同一 URL 执行 `retries=2` 的短重试；本轮失败后重新创建 yt-dlp 实例，从原视频页再次解析并下载，最多执行两轮解析（首次解析加一次重新解析）。每轮使用独立临时产物，不复用上一轮 `.part` 文件；两轮保持相同的 cookies、`YTDLP_PROXY`、impersonation 和 format 选择策略，不通过切换认证态、代理或格式掩盖传输失败。重新解析会刷新签名媒体 URL，但 YouTube 仍可能返回相同的 CDN 节点，因此该策略不承诺一定换服务器；两轮均失败后才把错误交给外层 job 重试。

对 YouTube flat 条目缺失发布时间的场景，`media.sync_videos` 只做发现和幂等写入；flat 提取期间以 yt-dlp 的真实分页 / 条目日志刷新执行活动心跳，提取完成后和 entry 处理循环中继续刷新。万级频道的全量分页因此不会仅因总耗时超过 120 秒被误判卡死；如果 yt-dlp 长时间不再产生分页或条目活动，现有 watchdog 仍会回收任务。单视频详情解析由 `video.enrich_metadata.youtube` 独立执行：每个任务只处理一个 `video_id`，受 YouTube provider pause 与 sync provider advisory lock 控制，锁繁忙时延迟 30 秒重排，yt-dlp 详情解析有 45 秒可终止子进程硬超时。该子进程只回传 compact metadata，不回传 `formats`、自动字幕、缩略图数组等完整 yt-dlp `info` 大对象，避免父进程等待子进程退出时被进程队列刷写阻塞。该补全是 best-effort，失败只影响对应补全任务，不阻塞视频发现、下载或后续同步。同一视频达到 `max_attempts` 终止失败后，自动同步不再为同一 `dedupe_key` 重复投递 metadata 补全；如需重试，应在失败任务上手动重试，或等待下载/后续真实 metadata 写入补齐发布时间。

bgutil PO Token Provider 的健康状态与 metadata 补全子进程回传路径是两类问题：`/ping` 正常、日志能生成 PO Token，只能证明 PO Token Provider 当前可用；若同一视频直接 `ytdlp_extract_info` 能在 45 秒内返回，而 `video.enrich_metadata.youtube` 超时，应优先排查子进程结果回传、payload 体积和硬超时路径，而不是直接重启 bgutil 或更改 cookies / 代理。

如果单个下载任务因历史 retry 参数走无 cookies 下载，仍触发 YouTube bot check，则系统同样按“出口 IP / PO Token / 访问频率风控”暂停 YouTube provider，不再提示更新 `YTDLP_COOKIES_YOUTUBE`。手动重试失败任务时会清除该历史 retry 参数，恢复使用 cookies。

2026-05-18 对 Bloomberg Television 失败样本的实测结论：

- `yt-dlp` 能找到 YouTube cookies，`node` JS challenge 成功，`bgutil:http` 能生成 `gvs PO Token`。
- 403 发生在实际媒体 URL 下载阶段，不是 cookies 格式错误，也不是整体登录态不可用。
- 同一视频中，音频 format `140` 可返回 `206`，低清视频 format `133/394/395` 可返回 `206`，但 360p 及以上的 DASH video-only GVS URL 返回 `403`。
- HLS / combined MP4 路径可用：format `96`、`95`、`best[protocol^=m3u8][height<=1080]`、`best[ext=mp4][height<=1080]` 均通过测试下载。
- 因此 YouTube 下载默认应优先选择 combined MP4/HLS，再回退 DASH video-only；遇到 YouTube 媒体 URL 403 时，可以在同一个任务内切换到下一个格式 selector，不能切换认证态或改成无 cookies。
- 详细的格式类型、验证方法和 selector 建议见 [yt-dlp 视频 / 音频格式选择策略](ytdlp-format-selection.md)。

遇到 YouTube 频道页 / 视频页返回 `HTTP Error 404` 且 yt-dlp 明确报 `Requested entity was not found` 或 `Unable to download API page` 时，系统按“单个媒体源不可用”处理：自动关闭该媒体的 `monitor_enabled`，并在媒体列表展示“来源不可用”标签；不会暂停整个 YouTube provider。

排障时按下面顺序处理：

1. 先看原始 yt-dlp 错误和 player status；如果出现 `LOGIN_REQUIRED`，不要切无 cookies。
2. 检查失败任务的 `job.params` 是否带历史 `_download_without_cookies`，必要时清理后再重试。
3. 确认 `SYNC_BATCH_SIZE` 是否足够低；当前推荐 `2`，必要时可临时降到 `1`。
4. 若新 cookies 保存后几秒内再次触发 bot check，先清空浏览器站点数据，再重新访问 YouTube、登录并导出 cookies。
5. 确认当前出口 IP 是否已经被 YouTube 风控；必要时更换网络或代理。
6. 确认 bgutil PO Token Provider 和 `YTDLP_YOUTUBE_IMPERSONATE=chrome` 是否生效。
7. 如果错误是 `ERROR: unable to download video data: HTTP Error 403: Forbidden`，先验证当前 selector 是否选中了 DASH video-only；优先改为 `best[ext=mp4][height<=1080]` 或 `best[protocol^=m3u8][height<=1080]` 路径，而不是更换 cookies。

## PO Token Provider

yt-dlp 官方 PO Token Guide 当前推荐用 PO Token Provider plugin，尤其是在 YouTube 持续要求 PO Token / SABR 的场景。`bgutil-ytdlp-pot-provider` 是官方 wiki 提到的 provider 之一。

本项目只接入 bgutil HTTP server 模式，不使用每次调用 yt-dlp 都拉起脚本的模式。HTTP server 模式更适合持续同步 / 下载任务，也更容易统一观测和重启。

启用方式：

1. 运行 bgutil HTTP server，默认端口 `4416`。
2. 本机运行配置 `YTDLP_POT_BGUTIL_BASE_URL=http://127.0.0.1:4416`；Docker Compose 的 app 容器内配置 `http://host.docker.internal:4416`。
3. 重启 `download_youtube` / `sync` worker。
4. 用 `yt-dlp -v <YouTube URL>` 验证输出中出现 `[youtube] [pot] PO Token Providers: bgutil:http...`。

如果 `YTDLP_PROXY` 指向宿主机本地代理，例如 `socks5://127.0.0.1:8887`，bgutil Docker 容器必须使用 host 网络或其他可达的宿主机代理地址。否则 bgutil 会在容器内部访问自己的 `127.0.0.1:8887`，日志中出现 `ECONNREFUSED 127.0.0.1:8887`。

如果 shell / systemd 环境还设置了 `HTTP_PROXY` / `HTTPS_PROXY`，需要让 `NO_PROXY` / `no_proxy` 包含 `127.0.0.1,localhost,::1,host.docker.internal`。否则本机 `http://127.0.0.1:4416/ping` 可能被环境代理劫持，显示假性的 `502 Bad Gateway`；加上 `NO_PROXY` 后应返回 bgutil 的 JSON 版本信息。

注意：PO Token Provider 不能保证绕过所有 bot check。若 cookies、出口 IP 或账号本身已被 YouTube 风控，仍可能失败；但它是 cookies 很快失效后的下一层必要方案。

## 浏览器 Impersonation

yt-dlp 官方 README 把 `curl_cffi` 列为推荐的浏览器 impersonation 支持库，可用于需要浏览器 TLS 指纹的站点。当前项目依赖固定 `curl_cffi>=0.15,<0.16`；`yt-dlp 2026.07.04` 的 `curl_cffi` 请求后端支持 `0.10.x` 到 `0.15.x`。

当前默认：

- `YTDLP_YOUTUBE_IMPERSONATE=chrome`
- 空值表示不启用 impersonation。
- 该设置只注入 YouTube 的 `yt-dlp` 资料同步、频道头像下载和视频同步 / 下载请求，不改变 B 站、ASR、LLM、Embedding 或非 yt-dlp 资料 / 头像抓取请求。

## 本项目当前状态

- 本地 `yt-dlp` 版本已更新到 `2026.07.04`，对应 PyPI 包版本 `2026.7.4`；`yt-dlp-ejs` 当前为 `0.8.0`。
- 当前配置已有 `YTDLP_REMOTE_COMPONENTS=ejs:github`，用于 YouTube EJS / JS challenge 组件。
- 当前支持通过 `YTDLP_POT_BGUTIL_BASE_URL` 启用 bgutil PO Token Provider HTTP server。
- 当前 YouTube 同步 / 下载固定注入已保存的 YouTube cookies；不再提供全局无 cookies 下载开关。
- 历史 `_download_without_cookies` 任务参数只作为兼容清理对象存在，不允许作为新的自动重试策略。
- 当前 YouTube `yt-dlp` 调用默认启用 `YTDLP_YOUTUBE_IMPERSONATE=chrome`。
- 当前项目本地 Node.js 默认版本为 22.23.1，满足 EJS 的 Node.js 22+ 要求；Node.js 20 不再受支持，会导致 `n challenge` 求解失败。
- 当前 YouTube 下载格式优先 combined MP4/HLS，避免优先命中已实测 403 的 360p+ DASH video-only GVS URL。
- 当前会将 YouTube `No video formats found` 识别为“formats 为空”的解析失败，并提示优先检查依赖版本、`YTDLP_PROXY`、PO Token Provider、cookies 导出会话和视频访问限制；不会把它误报成 cookies 失效。
- 已将 `SYNC_BATCH_SIZE` 推荐值降为 `2`，减少同步任务波峰。

## 参考来源

- yt-dlp YouTube cookies 导出说明：<https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies>
- yt-dlp FAQ cookies 用法：<https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp>
- yt-dlp Known Issues / FAQ：<https://github.com/yt-dlp/yt-dlp/issues/3766>
- yt-dlp PO Token Guide：<https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide>
- bgutil PO Token provider：<https://github.com/Brainicism/bgutil-ytdlp-pot-provider>
- cookies 仍失败的相关 issue：<https://github.com/yt-dlp/yt-dlp/issues/15392>
- 带 cookies 反而异常的相关 issue：<https://github.com/yt-dlp/yt-dlp/issues/16229>
