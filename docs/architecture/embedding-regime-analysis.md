# 事件 Regime 分析设计与术语

本文记录当前播放列表市场分析链路。新的事实源不是整段 transcript embedding，而是视频级 LLM 原子事件。

## 核心口径

- `market_event` 是事实源：每条视频 transcript 先抽取结构化事件，再进入实体、证据、关系与 Regime 分析。
- `event_time_start/end` 表示事件发生在市场时间线上的位置。
- `available_at` 表示下游最早可以观察到该事件的时间，回测与训练窗口不得早于它。
- `confidence >= 0.8` 的事件自动进入 `accepted`；低置信事件进入 `draft`，只展示，不进入自动 Regime。
- `time_precision=unknown` 或没有 `event_time_start` 的事件不进入自动 Regime，即使状态是 `accepted`。

## 事件抽取

任务：

- `video.extract_events`：读取单个视频的 `plain` transcript，按 `EVENT_EXTRACTION_CHUNK_MAX_CHARS` 分块调用 LLM。
- `playlist.backfill_events`：扫描播放列表历史视频，投递缺失或强制重抽的 `video.extract_events` 子任务。

抽取输入包含：

- 视频标题
- 媒体名
- 内容时间
- 平台发布时间
- description 摘要
- transcript chunk

抽取输出必须是 JSON，顶层为 `events[]`。单个事件包含：

- `event_time`
- `available_at_basis`
- `event_type`
- `entities`
- `assets`
- `sectors`
- `macro_variables`
- `direction`
- `magnitude`
- `surprise_or_delta`
- `cause_effect_chain`
- `evidence_quotes`
- `confidence`

解析规则：

- 解析器会剥离 Markdown 代码块与 `<think>`。
- JSON 必须严格可解析。
- 单条坏事件只丢弃该事件并记录 job warning，不影响同视频其它有效事件。
- 同一视频内重复事件按 `event_key` 去重。

## 关系表知识图谱

第一版不引入外部图数据库，使用关系表表达图结构：

- `market_event`：事件节点。
- `market_event_entity`：公司、人物、机构、国家、指标、资产、行业、宏观变量等实体节点。
- `market_event_evidence`：事件到视频 transcript 证据的引用。
- `market_event_relation`：事件内部的 `cause / effect / affects / mentions` 等边。

图谱 API：

- `GET /api/playlists/{playlist_id}/events/graph`

该接口由关系表生成 `nodes` 与 `edges`，用于 UI 展示和后续 Regime 解释。

## 事件 Embedding

任务：

- `event.embed`

输入文本由结构化事件字段确定性生成，包含：

- event type
- event time
- title / summary
- direction
- magnitude
- surprise_or_delta
- entities

只对 `accepted` 事件生成 embedding。事件状态从 `draft` 改为 `accepted` 时，会自动投递 `event.embed` 并把所在播放列表标记为 event-regime dirty。

## Regime 快照

任务：

- `playlist.build_event_regime_snapshot`

消费条件：

- 事件状态为 `accepted`
- `event_time_start` 可解析
- 对应 `market_event_embedding.status=ready`

输出表：

- `event_regime_run`
- `event_regime_state`
- `event_regime_signal`
- `event_regime_candidate`

当前聚合口径：

- `day / week / month` 三个尺度。
- 每个周期对事件 embedding 求 centroid。
- `drift_score` 使用相邻周期 centroid cosine distance。
- `drift_rolling_z` 用前序窗口计算 rolling z。
- week / month 级 z 分数达到候选阈值时生成 `event_regime_candidate`。

候选证据：

- `evidence_event_ids` 保存候选窗口内的事件。
- `evidence_video_ids` 保存这些事件对应的视频。
- `evidence_json` 保存检测粒度、事件数量等元数据。

## API

事件层：

- `POST /api/playlists/{playlist_id}/events/extract`
- `GET /api/playlists/{playlist_id}/events/summary`
- `GET /api/playlists/{playlist_id}/events`
- `GET /api/playlists/{playlist_id}/events/{event_id}`
- `PATCH /api/playlists/{playlist_id}/events/{event_id}`
- `GET /api/playlists/{playlist_id}/events/graph`

Regime 层：

- `POST /api/playlists/{playlist_id}/regime/rebuild`
- `GET /api/playlists/{playlist_id}/regime/summary`
- `GET /api/playlists/{playlist_id}/regime/signals`
- `GET /api/playlists/{playlist_id}/regime/candidates`
- `GET /api/playlists/{playlist_id}/regime/candidates/{candidate_id}`
- `PATCH /api/playlists/{playlist_id}/regime/candidates/{candidate_id}`
- `GET /api/playlists/{playlist_id}/regime/export/events`

## UI

播放列表主界面：

- 原简报区域默认替换为事件抽取 / 证据面板。
- 顶部展示抽取覆盖率、accepted / draft / rejected 数量。
- 支持历史事件抽取、Regime 重建、事件状态过滤、实体过滤、详情查看、确认与拒绝。

分析页：

- 标题为“事件 / Regime 分析”。
- 趋势图消费 event-regime signals。
- 候选详情使用 LLM 事件、实体与证据解释。

## 迁移边界

- 启动迁移会删除旧 transcript embedding / playlist analysis 表。
- 旧 `video.embed_transcript`、`playlist.backfill_embeddings`、`playlist.build_analysis_snapshot` 的 pending/running job 会被标记为 `canceled`，原因写入 `job_event`。
- brief 后端表与 API 继续保留；只是播放列表主 UI 不再默认展示每日简报。
