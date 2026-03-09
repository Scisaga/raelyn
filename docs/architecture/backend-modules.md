# 后端模块

## 媒体管理（Media）

能力：

- 添加 / 删除媒体
- 同步媒体资料（头像、名称、描述、订阅数等）
- 同步媒体视频列表，增量发现新视频

建议的同步实现（MVP 可先粗后精）：

- YouTube：优先用 `yt-dlp` extractor 获取 channel / playlist 的 flat 列表，再按视频补全
- B 站：按 UP 主视频列表做增量抓取
- `sync_cursor` 保存“已同步到的最新发布时间 / 页码 / last_video_id”等 provider 特定游标

输出：

- 新视频落库为 `video.status=discovered`
- 可配置是否自动投递下载任务，MVP 建议默认开启

## 视频下载（yt-dlp）

`video.download` 任务：

- 输入：`video_id`
- 行为：
  - 生成临时工作目录（按 `job_id`）
  - 使用 `yt-dlp` 下载视频文件、字幕、缩略图、`info.json`
  - 将 video 文件作为 `asset(type=video)` 上传
  - 写入或更新 `video.status`

注意点：

- 失败原因要落到 `job.error_message / error_stack` 与 `video.error_message`
- 下载流程可以重试，但产物写入必须保持幂等，避免重复上传

## 音频分离（ffmpeg）

`video.extract_audio` 任务：

- 输入：`video_id` + `video_asset_id`
- 输出：`asset(type=audio)`

策略：

- 优先无损抽取（copy）或最小转码，视容器和编码情况决定
- 音频既服务于 ASR，也服务于后续分析，体积与质量的权衡可配置

## 字幕处理（Normalize Subtitle -> Transcript）

`video.normalize_subtitle` 任务：

- 输入：字幕 asset（`vtt / srt / ass`），优先选择中文（`zh / zh-Hans / zh-CN`）
- 输出：
  - `asset(type=transcript, format=json, variant=segments)`：分段 + 时间戳
  - `asset(type=transcript, format=txt, variant=plain)`：纯文本

规则建议：

- 保留时间戳，便于回放定位
- 合并过碎片段，过滤纯标点或明显噪声

## ASR（qwen3-asr，可选）

触发条件：

- 未检测到中文字幕 transcript
- 已配置 ASR 服务地址

`video.asr_transcribe` 任务：

- 输入：audio asset + 期望语言（可选）
- 输出：`asset(type=transcript, source=qwen3-asr, format=json/txt)`

接口抽象建议：

- `POST /v1/audio/transcriptions`
- 通过 multipart 上传音频，返回 segments + text（OpenAI-compatible）

## 按天简报（LLM -> Markdown）

`brief.generate_daily` 任务：

- 输入：`playlist_id` + `date`（本地日历日）+ `timezone`
- 数据选择：
  - 取播放列表包含媒体在当日发布的视频
  - 只选择“已就绪”的 transcript，优先中文字幕，其次 ASR
  - 对超长文本做 chunking，并保留来源引用（`video_id / title / link`）

输出：

- 调用 LLM 生成 Markdown，包含标题、要点、主题分类、重要引用或建议行动项
- 生成后上传为 `asset(type=brief, format=md, source=llm)`
- 将 `daily_brief.status` 更新为 `ready` 并关联 `markdown_asset_id`
