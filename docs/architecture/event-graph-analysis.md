# 事件语义星域

事件语义星域（内部 API 与数据模型仍称 event map）用于浏览播放列表中“发生了什么、哪些记录描述同一件事、事件如何延续、涉及哪些实体”。它不是市场 Regime 分析，也不输出收益率、波动率、流动性或人工 Regime 标签。

## 产品边界

- 播放列表内的事件视为同一内容来源集合，不再按媒体来源切分样本。
- 来源权重暂不进入 canonical、topic、story 或布局计算。该限制是明确决策，不是遗漏；引入前必须先定义可验证目标。
- 所有时间筛选、排序和月度统计只使用 `event_time_start/event_time_end`。`available_at` 只表示系统何时获得记录，不能替代事件发生时间。
- 2012-03-23 集中的 17,166 条记录是 `available_at` 批次；其中 17,106 条已有推断事件时间，60 条因缺少事件时间不能进入地图。地图不会把该批次堆到 2012-03-23。
- 页面删除“时间演化”和“变化点”。底部时间轴只控制地图窗口，不再生成 centroid drift、rolling z 或断点候选。

## 六种可见对象

| 对象 | 含义 | 何时显示 |
| --- | --- | --- |
| 主题区域 | 当前窗口内真实事件形成的三维彩色语义点云 | 远景 |
| 主题 | 一级星域与二级主题团；标签可追溯到结构化实体、事件类型和代表事件 | 远景 / 中景 |
| 真实事件 Canonical | 一件保守归并后的现实事件，是固定三维语义星点 | 近景 |
| 事件记录 Member | 视频分析产生的一条原始事件记录 | 右侧详情 |
| 故事线 Story | 有证据的 continuation/causes/response/corrects 关系 | 选中 canonical 后显示短程曲线 |
| 实体与证据 | 人物、组织、国家、行业、标题、转写片段和来源链接 | 右侧结构化详情或实体筛选 |

原始记录始终可追溯，但不会全部成为全局点。这样既保留证据，也避免重复报道把局部区域涂成无法阅读的点云。

## 事件抽取与入图边界

事件抽取只读取 `plain` transcript。空 transcript 保持宽松语义：任务返回 `skipped`，不会因为没有文字而强制失败或重试。LLM 返回合法的 `{"videos":[{"video_id":"v1","events":[]}]}` 时，会记录 `succeeded` 且 `event_count=0`；后续 `force=false` 可以复用该结果。

JSON 无法解析、顶层不是对象、缺少 `videos[]`、缺少预期视频或对应视频缺少 `events[]` 属于协议结构错误。当前每次 LLM 请求只处理一个视频；结构错误会先持久化该视频的 failed extraction run，再抛给 worker 进入任务退避重试，不删除已有事件。单条坏事件、非法字段或无法形成合法证据的条目仍按 warning 丢弃，避免因局部内容质量把整个视频判为失败。

星域输入严格限定为同时满足以下条件的事件：

- `market_event.status=accepted`；
- `event_time_start` 非空；
- 当前 embedding 模型和维度的记录为 `ready`；
- embedding 向量非空。

## Canonical 规则

归并优先高精度，允许漏合并，不允许为了减少点数而扩大误合并：

- exact 分支要求时间区间、类型和 action/entity/period 指纹兼容；
- fuzzy 分支同时要求时间区间相交、类型兼容、强实体重叠、语义 cosine 阈值和 runner-up margin；
- month 精度采用更高阈值；year 精度只允许 exact；
- 加入 cluster 前检查全体成员时间交集及实体、动作、数值冲突，禁止普通 union-find 的传递扩张；
- 证据不足时保留 singleton；普通相关性由 topic 表达，不伪造 story 边。

一对一且语义未冲突时可延续 canonical ID。merge/split 创建新 ID并写 lineage，不能为了固定坐标错误复用身份。

## 固定投影与时间窗口

首次构建把 canonical centroid 流式写入 `float32 memmap`，经 IncrementalPCA 降到最多 50 维，再以固定参数 UMAP 投影到三维：`n_components=3`、cosine、`n_neighbors=15`、`min_dist=0.1`、seed 42、单线程。X/Y/Z 都是语义坐标，时间不作为 Z 轴。

同 embedding 口径的后续快照优先继承保留 canonical 的坐标；新 canonical 按高维近邻 anchor 定位。embedding 口径改变、保留比例不足或 anchor 质量失败时执行 rebase，并通过 `layout_continuity` 明确告知坐标连续性中断。

地图只有一个固定长度的观察窗口，默认 12 个月，可切换 1/3/6/12 个月。点击、键盘、整体拖动和播放都只移动窗口，不再维护两层时间状态。点云只包含窗口内真实事件，没有全期参照、装饰星点或虚构连线。类型筛选和实体筛选不重新布局。

快照按 `clamp(round(sqrt(N)/16), 16, 32)` 生成一级星域，每个星域再按约 800 个 canonical 拆分二级主题团。每个事件恰好归属一级和二级各一个主题。缩放只切换一级标签、二级标签和真实事件近景；数字聚合节点、二维 polygon geometry 与实体卫星不属于业务对象，已从快照、协议和 UI 中删除。

三维前端使用本地固定版本 Three.js。主画面只保留单层清晰事件点，不再使用大尺寸光晕、bloom 或模糊星云；点的局部疏密表达当前窗口的事件聚集程度。点云后方的低透明度弯曲坐标网格只依据快照一级星域中心与规模生成一次，用作无刻度的空间参照；它不是新的业务对象，不随时间窗口重算，也不赋予 UMAP 的 X/Y/Z 物理含义。事件类型在 API 中映射为八个稳定语义族并着色，避免几十个原始类型造成随机彩噪；选中事件用白芯金环与带引线标签在原坐标标明，实体筛选命中为绿色，故事关系为紫色或青色。选中主题时，其当前窗口成员保持语义色并加亮、绘制细金边，其他事件仅降低不透明度，因此主题选中不依赖虚构连线或重新布局。直接点选星点、主题和代表事件不改变镜头；只有右侧“定位”或搜索结果可以显式聚焦。右侧按一级星域、二级主题、当前窗口代表事件逐层下钻。镜头只保留三维透视的旋转、缩放、平移和按当前窗口适配；二维精确阅读由线性列表承担，不保留缺少独立分析能力的平面正交分支。

每个点的发生时间区间作为 GPU attribute 常驻显存，窗口边界作为 shader uniform 更新。拖动时间轴时只更新四个窗口参数即可实时预览，不上传十万级透明度数组；释放拖动后再更新 CPU 侧标签、统计和实体查询。播放和手动提交窗口时执行约 220ms 原位交叉淡化，不改变相机矩阵和事件坐标。

Three.js controller、scene、camera、material 和矩阵对象不得作为 Alpine 深层响应式对象使用；页面状态读取 controller 时必须先通过 `Alpine.raw` 解包。否则浏览器会代理 Three.js 的只读矩阵字段，出现“标签更新但粒子停止渲染”的分裂状态。

## 快照与任务

任务只有：

- `playlist.mark_event_map_dirty`
- `playlist.build_event_map_snapshot`
- `playlist.prune_event_map_snapshots`

dirty 使用 generation 防止构建期间的新变化丢失，具体约束如下：

- 只有可能改变上述可入图集合或其内容的变化才投递 dirty：播放列表成员变化、事件进入或退出 accepted+时间+ready-vector 集合、已有可入图事件内容被替换，以及 ready 向量或 checksum 变化。新发现视频、尚无可入图事件的发布时间变化、事件刚抽取但 embedding 尚未 ready，以及原本就未入图的 embedding 进入 failed/skipped，都不会单独触发无效构建；已入图向量转为失败仍会因退出集合而触发更新。
- 源数据变化与 `playlist.mark_event_map_dirty` outbox 在同一短事务提交。复用同一播放列表的 pending dirty job 时会锁定该 job 行到源事务提交，避免 worker 在源数据提交前抢走旧信号；多个 reason 最多保留最近 8 个用于排障。
- dirty handler 锁定 `event_map_state` 后递增 `dirty_generation`。自动构建等待最后一次 dirty 后 120 秒安静期；若变化持续不断，最晚在第一次 dirty 后 900 秒启动。手动重建不等待 quiet window。
- 自动 build 领取后会先在 state 行锁下检查 generation；`dirty_generation <= built_generation` 时直接返回 `skipped_generation_current`，不创建 staging，也不读取输入。
- 真正读取输入时使用同一个 PostgreSQL `REPEATABLE READ READ ONLY` 快照，同时冻结 `dirty_generation`、current snapshot、active dirty job 和全部事件/embedding 输入。该视图内若仍有 pending/running dirty job，构建返回 `superseded_by_active_dirty`，等待该 outbox 提交并由后续 build 收敛，避免用“新 generation + 旧输入”错误宣告完成。
- 冻结输入按 event id、内容 hash 和向量 checksum 计算 `input_fingerprint/build_key`。若同播放列表已有相同算法与模型口径的 ready 快照，当前 staging 会取消并复用该 ready 快照，同时把 `built_generation` 推进到冻结 generation，不再执行 PCA、近邻、UMAP 和对象写入。

构建完成后以短事务原子切换 ready 指针。若切换时 live `dirty_generation` 大于冻结的 `input_generation`，当前快照仍保持自洽，并继续排一个 follow-up build；否则清空 dirty 时间窗。

手动重建直接提交 build job。每次 claim 生成独立 `execution_token`，并以 `(job_id, execution_token)` 隔离 staging snapshot；失败、取消、worker 失联、任务重领或内存超限都不能改变 `event_map_state.current_snapshot_id`。

## 资源与取消

- `ANALYSIS_CPU_THREADS=2` 是每个 analysis worker 的 CPU 线程预算；Python worker 入口会在导入 NumPy / sklearn 前，将它统一设置给 `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS` 和 `MKL_NUM_THREADS`。该预算也覆盖 all-types 或显式包含 analysis 任务类型的 worker，不影响其他专职角色；修改后需重启对应 worker；
- `ANALYSIS_MAX_RSS_BYTES=12884901888`，限制父任务与 UMAP 子进程的进程树总 RSS；
- `ANALYSIS_MIN_AVAILABLE_MEMORY_BYTES=1073741824`，低于 1 GiB 可用内存立即终止；
- ANN 与 UMAP 子进程不并存；向量、PCA 和 canonical centroid 使用 memmap/分批处理；
- canonical/member/entity/topic/story 数据先顺序写入临时行缓冲，再按 2,000 行批量入库；每个对象族提交后立即删除缓冲，不在内存中同时保留百万级字典；
- 构建期间数据库只包含不可见的 staging snapshot；对象族可分事务提交，只有最后的小事务在 execution token CAS 成功后切换 current ready 指针；
- 任务在读取、归并、写入、子进程监控和最终切换前检查取消；取消后终止子进程并清理临时目录；
- 快照记录 `peak_rss_bytes` 与临时磁盘峰值。

## 快照保留与在线查询

- 每次 ready 原子切换成功后，build job 提交按播放列表去重的 `playlist.prune_event_map_snapshots` 任务；清理任务一次只删除一个快照并依赖外键级联，避免在 API 请求或 ready 切换事务中执行大规模删除。
- 默认只保留 current 与上一版 ready；`running/pending` staging 永不清理，失败和取消快照随旧 ready 逐批回收。上一版用于承接切换前已经 pin 的浏览器请求。
- 所有参与快照级联删除或 `SET NULL` 的组合外键都有对应前缀索引，避免删除 canonical、故事线和 identity 引用时反复扫描整张子表。
- `event_map_canonical` 与 `event_map_entity_index` 使用较低的自动分析阈值，使新 snapshot UUID 及时进入 PostgreSQL 统计信息；在线实体排行仍显式采用 Hash Join，避免统计短暂滞后时退化为百万次随机索引探测。
- 实体排行与实体索引查询都设置事务级查询超时。该设置只在当前请求事务生效，不会通过 SQLAlchemy 连接池泄漏到其他 API 或 worker；排行额外禁用 Nested Loop，索引查询保留选择性执行计划。

## API 一致性

`manifest` 可不带 `snapshot_id` 解析当前 ready 快照，也可显式固定仍可用的历史 ready 快照；显式版本无效时不会回退。轮询时可使用 `compact=true`，只读取状态和快照计数，避免重复装载主题数据与实时覆盖统计。`scene`、搜索、实体索引和对象详情都必须 pin `snapshot_id`，V2 历史深链接还会把同一版本传入主题、故事航迹和主题简报反查，避免一次交互混用两版数据。实体数量、搜索和窗口筛选只查询 `event_map_entity_index`，局部角色与实体关系来自冻结的 record revision，不会回连可变的 `market_event_entity/relation`。播放期间前端不请求实体排行；暂停或手动完成窗口移动后，以单飞队列只提交最后一个窗口查询。新视频关系只有在抽取载荷显式给出的 `source_entity_key` / `target_entity_key` 与同事件规范键精确且唯一匹配时才形成局部连线；自然语言 `cause` / `effect` 本身不参与端点猜测。

场景二进制协议 v2 每条 56 字节，小端布局 `<I16sfffiiBBBBIII>`：

```text
uint32 point_index
uuid canonical_id
float32 x, y, z
int32 event_start_day, event_end_day
uint8 event_type_code, time_precision_code, flags, reserved
uint32 member_count, macro_topic_index, local_topic_index
```

不向浏览器传输原始 embedding。

V2 默认首页仍复用这套不可变快照与二进制场景协议，但在其上增加观察游标、稳定故事身份、长期修订、变化流和结构化引用。星域主场景和线性替代视图只投影类型化热列；`has_uncertainty` 是物化审核位，来源记录与长期对象之间通过正规化关系表反查，不对修订 JSONB 做全表扫描。完整冷热边界和无停机迁移约束见 [V2 观察与存储架构](v2-observation.md)。

## 旧链清理

代码、页面和常规启动流程不再包含 `event_regime_*`、`event_graph_projection_point`、旧任务或 `/regime/*` 路由。旧表只在显式迁移工具中作为待删除对象出现：

```bash
python -m raelyn.tools.migrate_data --drop-legacy-event-analysis --yes
```

工具会先确认每个相关播放列表已有 current ready 新快照，并拒绝存在运行中旧任务的数据库；不会在应用启动时无条件删除历史表。
