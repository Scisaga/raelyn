# ADR-0001：B 站资料同步使用浏览器模拟读取公开空间页

- 状态：已被 [ADR-0002](0002-media-profile-avatar-sources.md) 部分取代
- 日期：2026-07-26

ADR-0002 保留本 ADR 的浏览器模拟、直连和不回退 yt-dlp 边界，但将 B 站资料主来源改为已实测可返回有效头像的 `/x/web-interface/card`。

## 背景

`media.sync_profile` 需要补齐 B 站媒体的名称、简介和头像。旧实现先请求
`/x/space/acc/info`，失败后再通过普通 `httpx` 请求空间页，必要时继续回退到
yt-dlp。资料 API、空间页和 yt-dlp 请求分别固定使用 Chrome 120/122 UA。

2026-07-26 的同出口最小实测得到以下证据：

1. Cookies 从相同网络出口的无痕登录会话导出，结构完整且未过期；同步和下载并发均为 `1`。
2. 普通 `httpx` 即使改用 Chrome 147 UA，请求旧资料 API 仍返回 HTTP 412。
3. `curl_cffi` 使用 `impersonate=chrome` 请求旧资料 API 时，HTTP 状态变为 200，但业务码仍为 `-799`，没有资料数据。
4. 相同 Cookies 和出口下，`curl_cffi` 请求公开空间页返回 HTTP 200，并能解析出名称和简介。
5. 空间页未必提供符合 `/bfs/face/` 约束的头像；旧 handler 会因此继续回退到 yt-dlp，增加一次不必要的风控请求。

因此，问题不能仅通过更新 UA 解决。旧资料 API 和“为了补头像继续回退”的请求链路都是本次立即暂停的组成原因。

## 决策

1. B 站 `media.sync_profile` 直接通过 `curl_cffi` 的 Chrome 浏览器模拟读取公开空间页，不再调用旧 `/x/space/acc/info` API。
2. 浏览器模拟客户端固定 `trust_env=false`，不得隐式读取 `HTTP_PROXY`、`HTTPS_PROXY` 或其他进程环境代理。
3. 空间页解析出名称、简介或有效头像中的任意一项，即视为资料抓取成功。
4. 缺少头像时不为补头像继续回退到 yt-dlp。B 站头像仍只接受 `hdslb.com` 下的 `/bfs/face/` URL，站点图标和其他图片不写入媒体头像。
5. B 站空间页明确返回 HTTP 412/429 时，继续触发 provider pause，避免反复请求扩大风控。
6. B 站 yt-dlp 与头像请求不再固定使用 Chrome 120/122，而是复用当前安装版本的 yt-dlp 默认浏览器 UA。

## 未采用的方案

### 只更新 Chrome UA

不采用。相同出口和 Cookies 下，Chrome 147 UA 配合普通 `httpx` 仍返回 HTTP 412，无法解决当前问题。

### 保留旧资料 API 并增加浏览器 TLS 模拟

不采用。`curl_cffi` 能把 HTTP 412 变为 HTTP 200，但接口仍返回业务风控码 `-799`，没有可用资料。

### 缺少头像时继续回退到 yt-dlp

不采用。名称和简介已经满足资料同步的基本目标；为可选头像增加请求会扩大风控面，并可能再次暂停整个 B 站 provider。

### 为 B 站资料请求增加代理

不采用。本次 Cookies 已从相同出口导出，证据不支持出口不一致是直接原因；增加代理还会改变认证会话和上游风控行为，超出本决策范围。

## 影响

- B 站资料同步不再依赖受限的旧资料 API，当前实测目标可成功写入名称和简介。
- 头像、粉丝数或视频数可能暂时保持为空或保留旧值；本决策优先保证资料同步不因可选字段扩大风控。
- `curl_cffi` 成为 B 站空间页资料同步的运行依赖。升级 yt-dlp 或 `curl_cffi` 后，需要重启 sync worker 才会加载新的浏览器实现。
- 本决策不改变 B 站视频发现、视频下载、Cookies 存储格式和 provider pause 的整体边界。

## 验证与复审条件

当前实现通过相关 `unittest` 51 项，并在不解除 provider pause、不写业务数据的受控请求中验证了空间页资料解析成功。

出现以下情况时应复审本决策：

- B 站提供有文档、可稳定使用的资料 API；
- 公开空间页不再提供名称或简介；
- `curl_cffi` 的浏览器模拟不再通过 B 站验证；
- 产品明确要求头像、粉丝数或视频数必须强一致，并且已有可验证的新数据源。

## 实现位置

- [B 站空间页资料抓取](../../backend/raelyn/services/profile_fetch.py)
- [浏览器模拟客户端](../../backend/raelyn/services/http_client.py)
- [资料同步成功边界](../../backend/raelyn/jobs/handlers/media_sync.py)
- [B 站 yt-dlp 请求头](../../backend/raelyn/services/ytdlp.py)
