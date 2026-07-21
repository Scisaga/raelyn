# 事件图谱与事件语义分析

本文记录播放列表级事件图谱、事件 embedding 与语义时间演化链路。事实源不是整段 transcript embedding，而是视频级 LLM 原子事件。

## 产品边界

- 页面与文档统一使用“事件图谱”“语义快照”“语义变化”，不把结果解释为市场 `Regime`。现存 `event_regime_*` 表、`/regime/*` 路由和任务名属于兼容标识，暂不做破坏性迁移。
- 一个播放列表作为一个完整语义语料库分析。当前不按媒体、频道或其它来源继续切分，也不做来源权重；拆分后样本不足，不能稳定支撑当前统计。后续不得把“缺少来源加权”当作缺陷反复提出，除非播放列表语义或数据规模约束发生变化。
- 本项目不引入收益率、波动率、流动性、人工 `Regime` 标签或样本外市场验证，也不以这些指标决定功能去留。输出只描述事件内容、事件密度与 embedding 语义随事件时间的变化。
- 历史批量上架不是同日事件。平台发布时间、采集时间和 `available_at` 只用于溯源审计；语义时间轴以已抽取或推断的 `event_time_start` 为准。

## 核心口径

- `market_event` 是事实源：每条视频 transcript 先抽取结构化事件，再进入实体、证据、关系和语义分析。
- `event_time_start/end` 表示事件目标时间，即事件本身声称发生、预计发生或覆盖的时间；语义信号按 `event_time_start` 聚合。
- `available_at` 表示该事件在系统中最早可被观察到的时间，仅用于溯源和导出审计，不作为事件图谱时间轴。
- `confidence >= 0.8` 且至少有 1 条 verified source-id provenance 的事件自动进入 `accepted`；低置信或无合法 provenance 的事件进入 `draft`，只展示，不进入语义快照。
- 没有 `event_time_start` 的 accepted 事件不进入语义快照。时间精度还会决定事件能进入哪个聚合尺度，避免把“仅知道年份”的事件伪装成某年 1 月 1 日的日级峰值。

## 事件抽取

任务：

- `video.extract_events`：读取单个视频的 `plain` transcript，按 `EVENT_EXTRACTION_CHUNK_MAX_CHARS` 分块调用 LLM；当有效 LLM URL 为 Ollama `/api/generate` 时，实际分块上限还会被 `EVENT_EXTRACTION_OLLAMA_CHUNK_MAX_CHARS` 封顶，避免本地 35B 模型在小上下文下吃进过大的 prompt。
- `video.extract_events_batch`：短视频批量抽取任务。任务可以携带多个 video id，但执行时每次 LLM 请求只包含 1 个视频；单视频 source 字符数超过 3500 或 transcript 需要分块时回退到 `video.extract_events`。
- `playlist.backfill_events`：按播放列表内容时间轴规划月份范围任务。
- `playlist.backfill_events_range`：查询单个月份范围内已有 `plain` transcript 的视频，按 source 字符数投递 `video.extract_events_batch` 或 `video.extract_events` 子任务；范围任务优先级低于它投递的视频抽取任务。
- `playlist.mark_event_regime_dirty`：由 `analysis` worker 延迟合并播放列表语义快照 dirty 标记，不在 `ai` worker 的事件抽取热路径直接更新兼容表 `event_regime_state`。

抽取输入包含：

- 视频标题
- 媒体名
- 内容时间
- 平台发布时间
- source 列表：`v1.title`、`v1.desc`、`v1.t001...`。标题、经过确定性清洗的描述和 transcript 片段都按 source id 进入 prompt；LLM 不能直接返回证据文本。描述清洗会移除 URL、hashtag、频道推广、会员/社群导流、推荐影片列表和免责声明等对事件抽取无帮助的噪音。

抽取输出必须是 JSON，顶层为 `videos[]`。每个 `videos[].video_id` 对应输入 video id，例如 `v1`。单个事件包含：

- `title` / `summary`：必须保留具体市场对象；不能把 `housing market`、`real estate market`、房地产市场、住房市场、楼市等具体对象泛化为单独的“市场”。股票事件必须在可可靠判断时写明上市市场、交易所或代码，并覆盖触发因素、持续性、估值/风险或影响对象。
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
- `evidence_source_ids`
- `confidence`

解析规则：

- 解析器会剥离 Markdown 代码块与 `<think>`。
- JSON 必须严格可解析。
- `evidence_source_ids` 必须命中本次请求的 source map；非法 source id 会被丢弃并写入 job warning。
- `market_event_evidence.evidence_text` 由后端写入 source 原文段，`evidence_json` 写入 `schema_version=event_provenance_v1`、`source_id`、`source_kind`、`source_label`、`char_start/end`、`source_sha256`、`verified=true`。
- `video_event_extraction_run` 记录 `video_id/source_hash/prompt_version/model/status/event_count`；`force=false` 命中 succeeded run 时跳过，即使 `event_count=0` 也不重复抽取。
- `event_time` 中非法年月日（例如不存在的日期或月份）按不可解析处理，写入时变为 `time_precision=unknown`，不让单条 LLM 坏日期导致整个 `video.extract_events` 失败。
- 单条坏事件只丢弃该事件并记录 job warning，不影响同视频其它有效事件。
- 同一视频内重复事件按 `event_key` 去重。
- `title`、`summary`、`assets`、`sectors`、`entities` 的对象口径应一致；例如实体为 `US Housing Market` / `US Real Estate` 时，标题应写“美国房地产市场”或同等具体口径，而不是单独写“市场”。
- 股票事件要求拆分发行公司与可交易证券：`company` 表示发行公司，`asset` 表示股票/ETF/商品等可交易资产，`country` / `institution` 表示上市市场或交易所；普通词不能误标成公司或资产。
- 操作策略、荐股建议、观察名单、族群归类、关注提醒、条件式交易计划和纯预测不单独构成事件；若同段内容包含已发生事实变化，只抽取事实变化本身，例如股价创新高、营收公布、资金流、公司行动或政策结果。

执行边界：

- `video.extract_events` 只在读取 video / media / transcript asset 元数据和最终写入事件时短暂持有数据库事务；S3 transcript 读取与 JSON 解析不持有业务行锁。Ollama `/api/generate` 事件抽取在 LLM 调用期间会持有一个 session-level advisory lock，key 为 endpoint + model，用来把同一模型的事件抽取请求限制为 1；锁忙时任务重排，而不是进入 Ollama 内部长队列。
- Ollama `/api/generate` 事件抽取固定使用紧凑 JSON schema、`think=false`、`format=json`、`options.temperature=0`，并带上 `EVENT_EXTRACTION_OLLAMA_NUM_CTX` / `EVENT_EXTRACTION_OLLAMA_NUM_PREDICT`。紧凑 schema 保留事件时间、类型、标题、摘要、实体、资产、行业、宏观变量、方向、因果链、source-id provenance 与置信度，但限制每个视频最多 4 个核心事件和数组长度，避免本地模型生成过长 JSON 后被 `num_predict` 截断。默认 `stream=true`，调用方会流式读取并累积最终 JSON；超过 `EVENT_EXTRACTION_OLLAMA_IDLE_TIMEOUT_SECONDS` 没有任何流式输出时请求失败，交给任务系统重试。
- 任务进度使用 `total=10000`：LLM 阶段最多推进到 `9000`，事件写库后到 `9600`，投递 embedding 与 dirty 后处理任务后到 `9900`，worker 成功收尾时才到 `10000`。
- 事件写库提交后才投递 `event.embed` 与 `playlist.mark_event_regime_dirty`，避免同一事务同时持有 `market_event`、`job` 与 `event_regime_state` 相关锁。

## 关系表知识图谱

第一版不引入外部图数据库，使用关系表表达图结构：

- `market_event`：事件节点。
- `market_event_entity`：公司、人物、机构、国家、指标、资产、行业、宏观变量等实体节点。
- `market_event_evidence`：事件到视频 transcript 证据的引用。
- `market_event_relation`：事件内部的 `cause / effect / affects / mentions` 等边。

图谱 API：

- `GET /api/playlists/{playlist_id}/events/graph`

该接口由关系表生成 `nodes` 与 `edges`，用于 UI 展示和事件关系解释。

## 事件 Embedding

任务：

- `event.embed`

`video.extract_events` 会先提交事件写入并释放 `market_event` 相关锁，再投递 accepted 事件的 `event.embed` 任务，避免事件写入事务同时持有 `market_event` 与 `job` 锁。若事件抽取重试时命中同一 `source_hash` 缓存，也会补投 accepted 事件缺失的 embedding 任务。

输入文本由结构化事件字段确定性生成，包含：

- event type
- event time
- title / summary
- direction
- magnitude
- surprise_or_delta
- entities

只对 `accepted` 事件生成 embedding。事件状态从 `draft` 改为 `accepted` 时，会自动投递 `event.embed`，并投递所在播放列表的 `playlist.mark_event_regime_dirty`。

`event.embed` 在 embedding 状态变为 `ready`、`failed` 或 `skipped` 时也会投递 `playlist.mark_event_regime_dirty`。dirty job 使用 `playlist_event_dirty:{playlist_id}` 作为 pending dedupe key，默认延迟 60 秒执行，用来合并同一播放列表在回填期间产生的多次事件抽取、embedding 状态变化和人工状态修改。批量 dirty 投递按 playlist id 稳定排序，并复用任务系统的 pending dedupe advisory lock，避免多个 worker 在同一批 playlist key 上通过唯一索引反向等待。

## 语义快照

任务：

- `playlist.build_event_regime_snapshot`

消费条件：

- 事件状态为 `accepted`
- `event_time_start` 可解析
- 对应当前 embedding 口径的 `market_event_embedding.status=ready` 且向量有效

输出表：

- `event_regime_run`
- `event_regime_state`
- `event_regime_signal`
- `event_regime_candidate`

Dirty 标记：

- 高频路径统一投递 `playlist.mark_event_regime_dirty`，包括事件抽取结果变化、事件 embedding 状态变化与人工修改事件状态。
- `playlist.mark_event_regime_dirty` 归属 `analysis` worker；handler 仅在 `event_regime_state` 不存在或 `analysis_dirty != true` 时写入。若状态已 dirty，则直接 no-op，不刷新 `updated_at`，避免大型同播放列表回填时反复竞争同一热行。
- 强制回填取消、手动 rebuild 等低频显式操作仍可直接更新 `event_regime_state`，保证人工操作即时可见。

当前聚合口径：

- `day / week / month` 三个尺度；播放列表整体视为一个语料库，不按来源拆分或加权。
- 每条事件按 `event_time_start` 归入对应周期。`second/day` 精度可进入日、周、月尺度，`month` 精度只进入月尺度；`year/unknown` 不进入当前三个细尺度，并单独计入 `event_scale_excluded`，不能落到 1 月 1 日制造伪峰。
- 每个周期对事件 embedding 求 centroid。
- `drift_score` 使用相邻周期 centroid cosine distance。
- `drift_rolling_z` 用前序窗口计算 rolling z。
- week / month 级 z 分数达到阈值时生成语义变化点；兼容表名仍为 `event_regime_candidate`。

覆盖率口径：

- `event_total`：当前 accepted 事件总数。
- `event_embedded`：当前 embedding 口径下 ready 的 accepted 事件数。
- `event_eligible`：同时具备 `event_time_start` 和有效 ready vector、可进入语义快照的事件数。
- `event_skipped = event_total - event_eligible`：页面显示为“不可入图”。它与 embedding coverage 分开，不能用旧 run 的历史计数覆盖实时状态。
- `event_scale_excluded`：已满足整体入图条件、但时间精度不足以进入当前日/周/月细尺度的事件数；不重复计入 `event_skipped`。

构建方式：

- 按 `ANALYSIS_STREAM_BATCH_SIZE` 从数据库顺序流式读取，不把全部 Python 向量和 ORM 行同时留在内存。
- 第一遍只维护当前周期的向量和、计数与紧凑 centroid；第二遍基于 centroid 流式计算周期内 dispersion。空间复杂度由“全量事件向量”降为“全部周期 centroid + 数据库读取批次 + 当前单个周期的距离标量”，不再随全量事件向量数线性增长；分位数仍要求保留当前周期的距离数组。
- 每批检查进程 RSS 与 `/proc/meminfo` 的 `MemAvailable`；`ANALYSIS_MAX_RSS_BYTES` 和 `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES` 分别是应用内最大 RSS 与最低可用内存门槛。

候选证据：

- `evidence_event_ids` 最多保存 20 条最接近候选周期 centroid 的代表事件，不是候选窗口全部事件，也不按来源加权。
- `evidence_video_ids` 保存这些代表事件对应的视频。
- `evidence_json` 保存检测粒度、周期事件总数和 `sampling=centroid_nearest_top_20_v1`，使 UI 能区分“代表样本”与全量证据。
- 候选详情 API 会把 `evidence_video_ids` 展开为 `evidence.videos`，包含视频标题、媒体名、发布时间与 URL，供前端展示证据视频列表。
- 周、月变化点的 `event_start/end` 分别覆盖完整周或完整月，不把周期起点误写成单日区间。

## API

事件层：

- `POST /api/playlists/{playlist_id}/events/extract`
- `GET /api/playlists/{playlist_id}/events/summary`
- `GET /api/playlists/{playlist_id}/events`
- `GET /api/playlists/{playlist_id}/events/{event_id}`
- `PATCH /api/playlists/{playlist_id}/events/{event_id}`
- `GET /api/playlists/{playlist_id}/events/graph`

语义快照层（路由保留 `/regime` 仅为兼容）：

- `POST /api/playlists/{playlist_id}/regime/rebuild`
- `GET /api/playlists/{playlist_id}/regime/summary`
- `GET /api/playlists/{playlist_id}/regime/signals`
- `GET /api/playlists/{playlist_id}/regime/candidates`
- `GET /api/playlists/{playlist_id}/regime/candidates/{candidate_id}`
- `PATCH /api/playlists/{playlist_id}/regime/candidates/{candidate_id}`
- `GET /api/playlists/{playlist_id}/regime/export/events`

## UI

播放列表主界面：

- 原简报区域默认替换为事件审核 / 证据面板。
- 顶部展示抽取覆盖率、accepted / draft / rejected 数量。
- 支持事件状态过滤、实体过滤、详情查看、确认与拒绝。

播放列表设置页：

- 提供事件图谱数据维护区，支持补齐事件抽取、全部重新抽取、停止当前抽取任务与构建语义快照；只有存在 ready run 时才显示“语义快照已就绪”，否则明确显示“尚未构建”，不能把空结果写成“未发现变化点”。
- 全部重新抽取使用 `force=true`，会先取消当前播放列表相关的活跃事件抽取、月份范围任务、事件 embedding 与语义快照构建任务，再投递新的全量回填父任务；父任务按月拆分后由范围任务逐月查询并投递视频抽取任务。

分析页：

- 标题为“事件图谱”。
- “事件时间演化”消费兼容 API 返回的 signal，展示事件时间上的语义变化与不确定性；“焦点窗口周期语义轨迹”明确表示日级周期 centroid，不冒充事件级二维 embedding 地图。
- 候选详情统一称“语义变化点”，使用 LLM 事件、实体与证据解释。

## 语义地图演进方向

参考 [Apple Embedding Atlas](https://apple.github.io/embedding-atlas/) 的呈现原则与其[预计算二维投影建议](https://apple.github.io/embedding-atlas/tool.html#visualizing-embeddings)，但不直接嵌入其完整 WebGPU / Mosaic / DuckDB-WASM / Svelte 组件：

- 后端在同一全局快照中预计算稳定的事件级二维投影并记录 embedding 模型与 projection version；切换焦点时间窗只做筛选，不重新拟合坐标。
- 页面目标结构为“语义地图 / 时间演化 / 事件列表”。全量事件可作为低亮度密度背景，焦点窗口事件作为高亮前景，颜色按事件类型或方向，而不是来源。
- 地图支持点选、矩形或套索选择，并联动事件列表；详情按 event id 懒加载，API 不向浏览器传输 1024 维原始向量。
- 当前前端继续保持轻量，第一阶段不在浏览器对全量事件运行 UMAP，也不把二维视觉簇解释为原始高维空间中的严格聚类。

## 迁移边界

- 启动迁移会删除旧 transcript embedding / playlist analysis 表。
- 旧 `video.embed_transcript`、`playlist.backfill_embeddings`、`playlist.build_analysis_snapshot` 的 pending/running job 会被标记为 `canceled`，原因写入 `job_event`。
- brief 后端表与 API 继续保留；只是播放列表主 UI 不再默认展示每日简报。
