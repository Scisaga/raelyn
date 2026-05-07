# Embedding Regime 分析设计与术语

本文记录“基于视频 transcript embedding 进行播放列表级 market regime 分析”的当前设计口径、术语定义与实现建议。

本文不是“当前已经实现”的说明，而是面向后续实现的专题设计文档。为避免把事实、推断和未来设计混在一起，本文按“已确认事实 / 设计建议 / 术语说明”分层记录。

## 已确认事实

截至 `2026-04-25`，当前环境中已确认的事实如下：

- embedding 服务地址是 `http://10.6.0.10:12302/`。
- `GET /health` 返回当前模型为 `Qwen/Qwen3-Embedding-8B`，`backend_ready=true`，推理设备目标是 `cuda`。
- 该服务当前实测支持通过 `POST /v1/embeddings` 直接输出多档维度，至少已验证 `511 / 768 / 1024 / 1536 / 2048`；同一模型默认输出仍是 `4096` 维。
- 当前分析需求已明确：
  - embedding 模型版本对用户界面固定，不在前端暴露切换项。
  - transcript 变体固定使用 `plain`。
  - 后续检测出的 regime 变点，需要平滑对接到训练时间段选择。
- 当前仓库与运行文档里尚未存在专门的 embedding / regime 分析专题文档。

## 设计目标

本专题针对的问题不是“全库语义检索”，而是：

- 从若干播放列表中聚合视频级 embedding。
- 观察 embedding 随时间的整体变化趋势。
- 找到潜在的 market regime 变化时间点。
- 输出适合对接训练时间窗选择的“稳定段 / 过渡段”结果。

对应地，首版设计优先考虑：

- 可解释
- 可复现
- 易于与训练侧对接
- 存储与计算成本可控

不优先追求：

- 通用向量搜索系统
- 跨全库的低延迟 ANN 检索
- 一开始就上最复杂的在线变点检测

## 不做 chunk embedding 的方案

### 可以做，但要明确边界

如果首版不做 chunk embedding，仍然可以做 regime 分析，但要接受几个约束：

- 每个视频只有一个 `video_embedding`。
- 该向量代表的是“整条视频文本”的整体语义，而不是视频内部各段主题变化。
- 后续只能定位到“哪条视频更像这个 regime”，不能定位到“视频里的哪一段在驱动变化”。

### 最大问题不是理论，而是输入长度

当前 embedding 服务的 `max_model_len=4096`。而长视频的 `plain transcript` 往往会超过这个长度。

所以“不做 chunk embedding”不等于“把完整 transcript 原样塞进 embedding 模型”。对于超长 transcript，必须先定义一个稳定的降长口径，否则结果会被截断策略主导。

### 首版建议

如果首版明确不做 chunk embedding，推荐用下面的保守方案：

1. 当 transcript 在模型长度预算内时，直接对整条 `plain transcript` 计算 `video_embedding`。
2. 当 transcript 超长时，不做“头部截断后直接 embedding”。
3. 对超长 transcript，先生成一个确定性的 `analysis_text`，再对 `analysis_text` 计算 `video_embedding`。

`analysis_text` 的推荐来源按优先级如下：

1. 已有的视频级 note / summary，如果其生成口径稳定且覆盖面足够。
2. 基于 transcript 做确定性压缩后的文本。

不推荐的首版方案：

- 只取 transcript 前 `N` tokens
- 人工凭经验截头去尾
- 不同视频使用不同压缩口径

因为这些做法会把“视频开头的常规自我介绍、广告、频道口播”误当成 regime 信号。

### 不做 chunk embedding 的收益

- 数据结构简单。
- 存储成本显著下降。
- 首版实现更快，更容易先把播放列表级趋势和训练时间窗跑通。

### 不做 chunk embedding 的损失

- 无法做视频内局部主题漂移解释。
- 无法做“驱动变点的关键片段”回看。
- 长 transcript 必须额外定义稳定的压缩口径。

### 当前建议

如果首版目标是“先把 regime 分段和训练时间窗选取跑通”，可以先不做 chunk embedding。

但要把这件事明确记为“首版范围收敛”，而不是把它误写成“最终数据模型”。

## 向量维度策略

### 当前事实

- 同一模型默认输出 `4096` 维。
- 截至 `2026-04-25` 的实测，该服务支持直接输出多档维度，至少已验证 `511 / 768 / 1024 / 1536 / 2048`。

### 4096 维是不是太多

从建模角度说，`4096` 维不算“错误”，它只是更重：

- 单条向量更占存储。
- 相似度计算更重。
- 周期聚合、聚类、投影和回放也更重。

对于首版 regime 分析，`1024` 维通常已经足够。

### 推荐口径

首版建议直接把服务输出维度固定到 `1024`，理由如下：

- 当前服务已经实测支持。
- 比 `4096` 更节省存储与计算。
- 对周期级 drift / clustering / projection 更友好。
- 未来如果确实发现信息损失，再考虑回升到更高维度。

### 数据库里仍要保留版本字段

即便前端不暴露切换项，数据层仍建议保留：

- `embedding_model`
- `embedding_dim`
- `transcript_variant`
- `embedding_generated_at`
- `analysis_version`

这样做的目的不是给用户切换，而是保证：

- 将来模型升级时可以并存。
- 历史 regime 结果可回溯。
- 训练侧能明确知道某段样本对应哪套 embedding 口径。

## 存储建议

### 首版不建议引入 Elasticsearch

当前问题核心是“播放列表级时间聚合分析”，不是“全库向量检索服务”。

因此首版更适合：

- 结构化元数据和周期结果放 `PostgreSQL`
- 大体积原始中间产物按需要放对象存储

### 不做 chunk embedding 时的存储建议

如果首版只有 `video_embedding` 和 `period_embedding`，那 `PostgreSQL` 通常可以承载：

- `video_embedding`
- `playlist_period_embedding`
- `regime_segment`
- `regime_change_point`

如果后续引入全量 `chunk_embedding`，再重新评估是否需要把 chunk 级向量迁到对象存储或专门向量检索系统。

## 核心术语与指标

### `centroid`

某个时间窗口内，所有视频 embedding 聚合后的“中心向量”。

如果窗口 `t` 内有视频向量 `v_1 ... v_n`，则：

```text
c_t = mean(normalize(v_i))
```

通常建议先对视频向量做单位化，再求均值，避免不同向量范数把中心方向带偏。

`centroid` 表示“这个 period 的主导语义方向”。

### `drift_score`

`drift_score` 用于描述相邻两个时间窗口的中心向量偏移了多少。

最常见的定义是：

```text
drift_score_t = 1 - cosine(c_t, c_(t-1))
```

解释方式：

- 越接近 `0`，说明两个窗口整体语义越接近。
- 越大，说明整体语义方向变化越明显。

它反映的是“整体方向是否变了”，不是“窗口内部是否混乱”。

### `dispersion_score`

`dispersion_score` 用于描述某个时间窗口内部的视频 embedding 有多分散。

一个常见定义是：

```text
dispersion_score_t = mean_i (1 - cosine(v_i, c_t))
```

解释方式：

- 越小，说明该 period 内的视频更像在围绕同一个主题。
- 越大，说明该 period 内部内容更杂、更混。

它反映的是“窗口内部是否一致”，不是“与上一个窗口相比是否变了”。

### `centroid drift 时间序列`

把每个 period 的 `drift_score` 按时间顺序连起来，就得到 `centroid drift` 时间序列。

例如按周聚合时：

- 第 2 周与第 1 周比较，得到一个 `drift_score`
- 第 3 周与第 2 周比较，再得到一个 `drift_score`
- 按此类推，形成整条时间序列

这条序列是首版 regime 检测最直接的主图。

它回答的问题是：

- “什么时候整体语义方向发生了跳变”

### `rolling mean`

`rolling mean` 是滑动均值，用于平滑原始序列。

例如使用过去 `4` 个 period 计算当前点的滑动均值：

```text
rolling_mean_t = mean(x_(t-k+1) ... x_t)
```

在 regime 分析里，`x_t` 常常是：

- `drift_score`
- `dispersion_score`
- 某个 cluster share 的变化量

作用：

- 去掉局部毛刺
- 帮助观察背景趋势
- 给阈值检测提供更稳定的基线

### `rolling z-score`

`rolling z-score` 是用滑动窗口内的均值和标准差来衡量“当前点有多异常”。

```text
z_t = (x_t - rolling_mean_t) / rolling_std_t
```

解释方式：

- `z_t = 0` 表示当前点和近期平均水平差不多
- `z_t = 2` 表示当前点大约比近期基线高 `2` 个标准差
- 值越大，越像一个异常跳点

在 regime 检测里，它比“固定绝对阈值”更稳，因为不同播放列表的整体波动水平可能不同。

### “最小间隔 + 阈值”找候选变点

这是首版最推荐的变点检测方法。

步骤如下：

1. 先计算目标序列，例如 `drift_score` 或其 `rolling z-score`。
2. 找出所有超过阈值的时间点。
3. 规定相邻两个候选点之间必须至少间隔 `gap` 个 period。
4. 如果两个候选点离得太近，只保留更高的那个。

常见配置示例：

- 序列：`rolling z-score(drift_score)`
- 阈值：`>= 2.0`
- 最小间隔：`4` 个 period

优点：

- 非常容易解释
- 易于调参
- 输出结果很适合直接变成图上的“变点标记线”

缺点：

- 不能保证全局最优分段
- 对窗口大小和阈值有一定敏感性

### “点标记线”

在可视化里，通常会在时间序列图上用竖线标出候选变点。

这条线本身不是算法，而是检测结果的表现形式。它通常对应：

- 一个候选 regime 变化时间点
- 一个高置信度异常漂移点
- 或一个经后处理确认的段落边界

建议在图上区分：

- 候选变点：细线 / 弱色
- 确认边界：粗线 / 强色

这样可以把“算法初筛”和“最终用于训练切段的边界”分开。

### `PELT`

`PELT` 是一种离线变点检测算法，英文全称是 `Pruned Exact Linear Time`。

它的特点是：

- 输入一整段历史序列
- 寻找一组整体最优的切分点
- 通过 penalty 控制切分数量

它更适合的问题是：

- 历史回放
- 批量重算
- 需要得到一组较干净的最终分段

优点：

- 分段结果通常比“逐点阈值法”更整齐
- 适合最后产出稳定 regime 段

缺点：

- 需要定义 cost function 和 penalty
- 对业务方来说不如阈值法直观
- 首版上线调试成本更高

建议定位：

- 不是首版必需
- 可以作为第二阶段的“离线精修算法”

### `Bayesian Online Change Point Detection`

`Bayesian Online Change Point Detection` 常缩写为 `BOCPD`，是一类在线贝叶斯变点检测方法。

它的核心思想是：

- 随着新数据进入，持续更新“当前已持续了多久没发生变点”的概率分布
- 一旦这个分布出现明显重置倾向，就认为可能发生了变点

它更适合：

- 流式数据
- 持续监控
- 需要边到边更新变点概率的场景

优点：

- 适合实时监控
- 理论上更适配持续新增视频的场景

缺点：

- 实现复杂度高
- 需要明确观测模型与先验
- 调参与解释成本都更高

建议定位：

- 如果后续要做“每周自动重算并实时提示可能的 regime shift”，可以再考虑
- 不建议作为首版主算法

### `HDBSCAN`

`HDBSCAN` 是一种密度聚类方法，适合 embedding 空间这类簇形状不规则、簇数不固定的数据。

它的特点是：

- 不需要预先指定簇数
- 能把稀疏点识别成噪声
- 对复杂分布通常比简单的 `k-means` 更稳

在本场景中的作用是：

- 把视频 embedding 自动分成若干 topic cluster
- 让后续的 regime 解释不只依赖单个 centroid

### `topic-cluster share shift`

有了 cluster 之后，可以对每个 period 统计 cluster 占比。

例如某个 period 的分布可能是：

- Cluster A: `60%`
- Cluster B: `25%`
- Cluster C: `15%`

下一个 period 变成：

- Cluster A: `25%`
- Cluster B: `20%`
- Cluster C: `55%`

这说明虽然整体中心向量可能只是“偏了一些”，但主题结构已经明显切换。

因此，`topic-cluster share shift` 指的是：

- 比较相邻 periods 的 topic cluster 占比分布
- 观察主题组成是否发生显著变化

它回答的问题是：

- “不是整体方向略微漂移，而是主题构成真的换了没有”

## 指标之间的关系

这些指标不要混为一谈：

- `drift_score`：看 period 与上一个 period 的整体方向变化
- `dispersion_score`：看当前 period 内部是否混杂
- `centroid drift 时间序列`：把 `drift_score` 连成时间线
- `rolling mean / rolling z-score`：用于平滑与异常检测
- `HDBSCAN / topic-cluster share shift`：看主题结构变化，而不是只看整体中心

一个常见解释模板如下：

- `drift 高 + dispersion 低`
  - 更像“整体干净地切到新 regime”
- `drift 高 + dispersion 高`
  - 更像“混乱过渡期”
- `drift 低 + dispersion 高`
  - 整体中心没明显换，但内容开始变杂
- `drift 高 + cluster share shift 高`
  - 不只是方向偏移，主题结构也变了

## 首版推荐算法栈

首版建议按下面顺序推进，而不是一开始全部上齐：

1. `video_embedding`
2. `playlist_period_embedding`
3. `drift_score`
4. `dispersion_score`
5. `rolling mean`
6. `rolling z-score`
7. “最小间隔 + 阈值”筛候选变点
8. 候选变点后处理，输出稳定段与过渡段

第二阶段再考虑：

- `PELT`
- `HDBSCAN`
- `topic-cluster share shift`

第三阶段再考虑：

- `BOCPD`

## 面向训练时间窗的输出约定

如果后续要把 regime 检测结果接到训练时间段选择，输出不能只停留在“某天是变点”。

更适合训练侧消费的对象是“时间段”。

### 推荐输出实体

建议最终输出 `regime_segment`，至少包含：

- `playlist_id`
- `segment_id`
- `segment_start`
- `segment_end`
- `segment_type`
- `stability_score`
- `change_confidence`
- `boundary_left_reason`
- `boundary_right_reason`
- `embedding_model`
- `embedding_dim`
- `transcript_variant`
- `analysis_version`

其中：

- `segment_type` 推荐至少区分：
  - `stable`
  - `transition`

### 为什么要有 `transition`

变点附近通常最不稳定。

如果训练侧直接把变点前后紧邻的数据都当成同一种 regime 的纯净样本，很容易把“切换噪声”喂进训练集。

因此建议在变点两侧预留缓冲区，把它单独标成 `transition`。

例如：

- 变点在 `2026-03-15`
- 前后各留 `1` 周缓冲
- `2026-03-08 ~ 2026-03-22` 这一段作为 `transition`

这样训练侧就可以：

- 优先选 `stable` 段做主训练样本
- 排除 `transition`
- 或者把 `transition` 单独作为 regime 切换样本

### 稳定度口径

`stability_score` 可以来自以下信号的组合：

- 该段内部 `dispersion_score` 的均值和波动
- 该段内部 `drift_score` 的均值和峰值
- topic cluster share 是否稳定

稳定段的理想特征是：

- 段内漂移小
- 段内分散度低
- 主题占比稳定

## 数据表建议

本文不直接替代 [数据模型与存储布局](data-model.md)，这里只记录专题建议。

如果后续落地，推荐新增如下表：

### `video_embedding`

建议字段：

- `id`
- `video_id`
- `transcript_variant`
- `embedding_model`
- `embedding_dim`
- `embedding_vector`
- `text_selector`
- `text_checksum`
- `generated_at`

说明：

- 即使 `transcript_variant` 当前固定为 `plain`，也仍建议保留该字段。
- `text_selector` 用于记录该 embedding 是否来自“完整 transcript”还是“压缩后的 analysis_text”。

### `playlist_period_embedding`

建议字段：

- `id`
- `playlist_id`
- `granularity`
- `period_start`
- `period_end`
- `video_count`
- `embedding_model`
- `embedding_dim`
- `centroid_vector`
- `drift_score`
- `dispersion_score`
- `generated_at`

### `regime_change_point`

建议字段：

- `id`
- `playlist_id`
- `granularity`
- `change_at`
- `score`
- `score_type`
- `min_gap`
- `threshold`
- `confidence`
- `generated_at`

### `regime_segment`

建议字段：

- `id`
- `playlist_id`
- `granularity`
- `segment_start`
- `segment_end`
- `segment_type`
- `stability_score`
- `change_confidence`
- `embedding_model`
- `embedding_dim`
- `transcript_variant`
- `analysis_version`
- `generated_at`

## 可视化建议

首版可视化不需要一上来就做成大型通用 embedding atlas。

当前展示顺序是：

1. `趋势` tab 中的统一时间轴。
2. `day / week / month` 多尺度语义漂移 z 时间序列。
3. `dispersion_mean` 不确定性曲线。
4. 候选断点标记与 PCA 投影图。趋势页首屏先用 `signals.linked_event_id` 画断点，随后用 `analysis/candidates.boundary_z` 渐进增强断点强度；长时间窗口下断点按屏幕位置聚合为密度簇，短窗口再展开单个断点；PCA 焦点窗口支持 3 个月、6 个月、1 年、3 年切换。
5. `事件` tab 中的事件序列、类型、摘要、关键词与证据视频。

其中：

- 时间序列图主要回答“什么时候出现候选断点”
- 投影图主要回答“断点附近的日级语义分布是什么”
- 代表视频列表主要回答“到底是哪些内容在驱动变化”
- 训练窗口、验证窗口与回测 horizon 不属于事件发现层，由 quant-lab 在实验侧决定

## 运行护栏

- 分析页只展示 `analysis_dirty` 的待刷新状态，不自动投递 `playlist.build_analysis_snapshot`。
- 只有用户明确点击“重建分析”时，API 才创建或复用分析任务；同一播放列表已有 `pending/running` 分析重建时不重复投递。
- coverage 统计走数据库聚合查询，不把播放列表全量视频 ORM 对象加载到 API 进程。
- 历史 embedding 补算默认只扫描缺失、失败、非 ready 或无向量的候选视频；`force=true` 才全量重算。
- 同一播放列表同一时间只允许一个历史 embedding 补算任务；分析摘要会返回活跃补算任务信息，前端展示任务状态并禁用新的补算入口，用户需要等待任务结束或先停止当前任务。
- 历史补算用 `EMBEDDING_TRANSCRIPT_PREFETCH_WORKERS` 控制 transcript 并发预取，用 `EMBEDDING_BACKFILL_HTTP_INFLIGHT` 控制远端 embedding HTTP batch 并发；batch 同时受 `EMBEDDING_BATCH_SIZE` 与 `EMBEDDING_BATCH_MAX_CHARS` 约束。
- 每个 embedding batch 成功后立即提交已写入向量、`last_committed_video_id` 和任务进度；后续远端 `502/503/504` 等临时错误不会回滚此前成功 batch，重试时已 ready 行会被 SQL 候选过滤排除。
- 快照构建按批读取 ready embedding 所需的最小字段：视频 ID、媒体 ID、标题、媒体名、发布时间与向量。
- `playlist.build_analysis_snapshot` 采用多尺度聚合：按 `day / week / month` 生成 signal panel；语义断点使用 `two_window_centroid_drift_v1`，先用月级前后各 2 个 period 的 centroid 对比提名，再在候选附近用周级前后各 4 个 period 的 centroid 对比细化。日级 signal 不再作为主断点来源，只用于证据、PCA 分布和局部解释。
- 开放式主题爆发与语义断点分离：`open_topic_burst_v1` 基于发布时间、媒体来源和已有 ready embedding 的视频元数据生成 `burst` 型候选。短爆发使用 3 日窗口，持续主题使用 14 日窗口；窗口内部先按 embedding 高响应维度生成自动语义桶，再用完整向量的 cohesion、媒体覆盖、视频数和活跃天数过滤。媒体覆盖门槛按候选窗口附近 90 天实际活跃媒体数自适应，避免把 2020 年前媒体覆盖稀疏的历史片段按近年多媒体覆盖口径过滤；当自适应门槛低于常规跨 3 媒体口径时，还要求该语义桶的窗口密度明显高于附近背景密度，避免把长期单源栏目误判为事件。最终候选选择对单一年份设置上限，避免近年高密度媒体覆盖挤掉历史年份。它不接入 LLM，不做 transcript chunk 抽取，不使用标题 seed，也不使用固定事件目录；标题 term 只作为候选生成后的解释标签。
- `open_topic_burst_v1` 的证据视频优先保留跨媒体代表标题，同一媒体近似标题只作为弱证据；`two_window_centroid_drift_v1` 的证据仍解释断点前后 centroid shift。
- `analysis` worker 在任务开始和处理中检查 `MemAvailable` 与当前进程 `VmRSS`；低于 `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES` 或高于 `ANALYSIS_MAX_RSS_BYTES` 时直接失败并记录原因。`playlist.build_analysis_snapshot` 这类长任务会在批处理检查点刷新任务进度和 `lease_expires_at`，避免计算阶段超过租约后被回收器误判为失联。
- 快照成功后会清理同播放列表旧的非运行中 run；当前 `last_ready_run` 与仍在 `pending/running` 的 run 不会被删除，避免历史快照长期堆积。

## 当前推荐结论

基于截至 `2026-04-29` 的已知事实，当前更推荐的首版口径是：

- 不做 chunk embedding
- transcript 固定 `plain`
- embedding 模型版本对前端固定
- 服务输出维度固定为 `1024`
- 数据库仍保留 `embedding_model / embedding_dim / transcript_variant / analysis_version`
- 先做 `two_window_centroid_drift_v1` 候选断点检测，`drift_score / drift_rolling_z / dispersion_score` 作为趋势解释与兼容字段；再做 `open_topic_burst_v1` 开放式主题检测，用于补足语义断点漏报的短窗口与持续主题事件候选
- 对 quant-lab 暴露 `events / signals / evidence`，不在分析页或主接口中输出训练 / 验证 / 测试窗口
- 投影采用 PCA 二维投影作为解释层，事件分数来自断点两侧 centroid 的 `boundary_z`；PCA 不作为事件分数来源，PCA 内的前后 centroid 箭头只表达方向，不按真实距离缩放

## 后续需要真实验证的问题

虽然当前设计已经有足够多的基础事实，但在真正实现前，仍需继续验证这些问题：

- 当前播放列表中的典型 transcript 长度分布如何，超出 `4096` 长度预算的比例是多少。
- 不做 chunk embedding 时，`analysis_text` 的口径应该基于 transcript 压缩，还是基于已有 note 资产。
- `1024` 维与 `4096` 维在实际 regime 切段结果上的差异有多大。
- quant-lab 在不同实验目标下应如何选择 `day / week / month` signals。
- 事件类型 `burst / transition / regime` 的阈值是否需要按播放列表自适应。
