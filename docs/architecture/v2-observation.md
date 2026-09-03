# V2 观察与存储架构

本文描述 V2 连续观察层、星域读取热路径、长期历史和上线迁移边界。产品定义仍以 [项目愿景](../vision.md) 和 [信息架构](../product/information-architecture-v2.md) 为准。

## 核心原则

- 星域快照是不可变的空间投影；`event_map_state.current_snapshot_id` 是当前快照的唯一事实源。
- 事件发生时间 `occurred_at` 与系统认知时间 `observed_at` 分开保存、筛选和展示。
- canonical 与 story 使用稳定身份；快照内版本和长期身份不是同一个概念。
- 高频筛选、排序、连接和渲染字段使用类型化关系列及索引。
- JSONB 只保存低频读取、结构会演进且需要完整审计的修订正文、证据上下文和界面状态。
- 大体积内容继续进入 S3 / MinIO；PostgreSQL 保存索引、状态与引用。

## 星域显示热路径

```text
event_map_state.current_snapshot_id
  → event_map_canonical / topic_member（类型化列）
  → API 按 point_index 流式扫描
  → 56 字节固定宽度二进制记录
  → 浏览器 ArrayBuffer / typed arrays
  → WebGL GPU 时间窗裁剪与绘制
```

场景记录包含 `point_index`、canonical UUID、x/y/z、事件起止日、事件类型码、时间精度码、审核位、成员数和两级主题索引。接口使用不可变快照 ETag 和长期私有缓存；前端不会下载并逐点解析 canonical JSONB。

星域首个可见帧只依赖本地 Three.js、compact manifest，以及没有已知观测域时的一次 compact 域身份查询。客户端优先从 URL 或本地上次选择恢复观测域；无可恢复值时立刻以 `/domains?compact=true` 确定默认域，并与系统状态和资源通道探测并行。域一确定即并行预热渲染器与读取 compact manifest；完整域目录、健康检查、播放列表详情、轻量观察游标和媒体选项不参与该关键路径。compact manifest 的最终边界、默认时间窗与主题几何先以空点 scene 创建正式 WebGL 网格；首帧可见后再读取 4,096 个均匀抽样的真实预览点并逐批显现，预览点落下后才并行读取完整不可变 scene 和不含覆盖统计的主题 manifest。三个点集阶段复用同一 renderer、canvas、网格和首帧镜头，首次镜头不执行固定飞行动画，preview/full/metadata 到达均不再触发自动适配。语义膜密度在完整点集与主题元数据都完成前冻结为 compact 形态，从而消除两项响应顺序造成的背景差异；后续用户时间窗操作再恢复密度过渡。完整 scene 就绪前不应用搜索定位、变化卡定位、实体索引或对象深链，完整主题 manifest 就绪前不恢复主题深链；点集与主题元数据分别清理各自请求，compact 轮询恢复未完成阶段，若同轮兄弟请求仍悬挂则取消旧请求并重新补齐。历史快照在隐藏 mount 准备时不抢占当前预览的点集与标签补齐，只有新快照原子提交成功才中断旧请求；可见星图上的新选择会取消 staged scene/metadata 并恢复导航基线，避免旧下载继续占用约数秒的交互阶段。“返回当前星域”显式请求未固定版本的 latest manifest，并受同一导航与选择代次约束。延后到达的完整域目录还会复核本地恢复域，避免已删除 ID 持续驱动请求。覆盖计数、实体排行和变化列表均在交互开放后加载。包含四项独立处理与产出覆盖统计的 `/observation` 只在设置和运行中心等确实需要覆盖信息的页面读取，不进入星域首屏关键路径。前端仍可按当前窗口活动点计算“适配”镜头边界，但不修改全局坐标或裁剪历史事件。

这里的“默认时间窗”由客户端根据 manifest 时间边界计算：普通 current 入口固定为截至今天的 12 个自然月；只有显式历史快照或 `window_start/window_end` 深链接使用请求中的历史范围，不再从观察游标恢复旧截止日。

`event_map_canonical.has_uncertainty` 是审核位的物化布尔列。场景流和待审核列表直接读取该列，不再对 `uncertainty_flags` 做逐行 JSONB 数组判断。时间窗、类型、实体和主题筛选分别由类型化列、`event_map_entity_index` 与 `event_map_topic_member` 支撑；主题成员下钻使用 `(snapshot_id, topic_id, level)` 复合索引。

当前服务会在首次请求某个不可变快照时从类型化列流式编码场景。只有当实测表明冷启动编码而非网络或 GPU 成为瓶颈时，才进一步把完整二进制场景固化为对象存储资产；在没有观测证据前不维护第二份同义场景事实。

## 连续观察与长期身份

- `domain_observation_cursor`：保存每个观测域的快照、认知时间、事件时间窗、筛选、镜头和选择对象；`view_mode` 仅保留兼容值。普通进入 current 星域不再用游标的历史时间窗覆盖“截至今天的 12 个月”，只有显式历史快照或 URL 时间窗恢复历史范围。
- `event_map_change`：保存 canonical、story 与布局变化；以 `(observed_at, id)` 提供稳定分页游标。
- `event_map_canonical_history_revision`：保存 canonical 的长期修订正文，不外键依赖可能被裁剪的大快照。
- `event_map_canonical_history_member`：正规化长期修订到来源修订的关系，为证据和来源记录反查提供 `(playlist_id, record_revision_id, canonical_id)` 索引。
- `event_map_story_identity` / `event_map_story_history_revision`：保存稳定故事身份及跨快照识别版本；身份只在相同故事算法版本、相同叙事锚点和稳定成员重叠下延续。`stable_title` 在身份首次形成时冻结，`last_material_snapshot_id / last_material_changed_at` 只随事实结构的实质变化推进。
- `event_map_story_history_evidence`：正规化故事边到来源修订的关系，支持来源记录反查具体故事关系。
- `story_read_state`：保存关注状态、最后显式标记已读的快照与故事位置；打开详情或只更新位置不改变未读。
- `brief.snapshot_id` / `brief.generation_basis` / `brief_reference`：固定简报的生成口径和可导航引用。

变化记录保存 before/after 修订。故事首次形成、成员或顺序、关系或判定依据、支持记录、显式纠正与成熟度变化是阅读器使用的实质变化；标题/摘要重写、质量分波动和相同结果复核只进入技术审计。显式、带证据的 `corrects` 故事边会形成独立 correction 变化，普通文本修订不推断为纠正。retire 对象的链接固定到最后包含它的历史快照，星域变化因此仍能打开对象并展示本次差异。

长期历史与当前大快照分离，因此快照保留策略可以回收旧的全量空间投影，而不抹除对象修订、变化语义、阅读状态和简报引用。

## JSONB 的保留边界

适合 JSONB 的内容：

- canonical/story 的完整历史修订正文；
- 原始抽取载荷、证据 provenance、生成口径；
- 相机、筛选等低频用户状态；
- 算法版本可能扩展的快照统计。

不得依赖 JSONB 扫描的热查询：

- 星域坐标、时间窗、类型与审核筛选；
- canonical、topic、story 成员连接；
- 来源修订到 canonical 的反向引用；
- 变化流分页、故事目录和简报对象引用。

`market_event_embedding.vector` 目前仍是快照构建输入中的 JSONB 向量，它影响构建吞吐而不影响星域显示。若构建性能观测确认向量反序列化成为主瓶颈，应采用兼容的 float32 二进制旁路或 pgvector 迁移，并保留校验和与双读迁移期；不得以重抽事件、删除旧向量或暂停现有任务作为迁移手段。

## 上线与历史兼容

- 建表、加列、加索引和历史投影均为加法迁移。
- 旧快照的 `has_uncertainty` 由既有标记原位补齐，不重建快照。
- 仍保留的 ready 快照会幂等补齐长期 canonical/story 修订、变化集和历史成员索引。
- 自动 schema 迁移不会取消、暂停或删除既有任务，也不会删除历史视频、资产或来源记录。
- V2 发布不修改当前 job 的 claim、lease、priority、worker role 或执行 token。
- 快照发布仍在同一事务内写完对象、历史和变化后再切换 current 指针；读者不会看到半成品。
- Web 历史深链接将 `snapshot_id` 贯穿 manifest、主题、故事航迹和主题简报反查；显式历史快照不可用时返回错误，不自动展示 current 数据。

## 读取接口

- `/api/domains/{domain_id}/observation`：当前观察状态与四项独立处理/产出覆盖；当前口径抽取按 `prompt_version + extraction_model` 的成功运行计数。
- `PUT /api/domains/{domain_id}/observation`：启用或停用持续观测；停用域保留来源归档，视频级共享分析以“至少一个关联域启用”为准，域级简报和星域构建仅面向启用域。
- `/api/domains/{domain_id}/changes`：稳定变化游标。
- `/api/domains/{domain_id}/observation/feed`：新发生、认知变化、故事更新和待验证；协议字段仍为 `newly_occurred / newly_mapped / story_updates / needs_review`。
- `/api/domains/{domain_id}/canonicals`：当前快照的类型化线性事件视图。
- `/api/domains/{domain_id}/event-highlights`：先在固定快照中按 canonical 的冻结发生日筛选今日或本周事件，再叠加观察窗口、类型、实体和主题条件；视频只是每个入选事件的关联展示资源，不以发布时间反向决定事件是否属于今日。日级焦点只接受 `second/day` 精度，月、年和未知精度不会扩展成每天的“今日事件”。响应返回全部精确匹配事件的点索引用于星图高亮，再按多信源佐证与时间可靠性做可审计排序，为其中最多十个有可播放来源的唯一 canonical 返回空间视频卡，同一媒体最多占两张；达到媒体上限的事件只是不生成卡片，其真实点仍保持高亮。该顺序不称为尚无冻结字段支撑的市场影响重要度。
- `/api/domains/{domain_id}/canonicals/{canonical_id}/history`：canonical 修订与谱系。
- `/api/domains/{domain_id}/topics/{topic_id}`：当前或显式历史快照的主题层级、成员和代表事件。
- `/api/domains/{domain_id}/stories`：current ready 快照的稳定故事阅读队列；支持 `attention/followed/established/emerging/all`、搜索和分页，按最近实质变化稳定排序。首次没有基线时只推荐最近成熟故事，不制造全库未读。
- `/api/domains/{domain_id}/stories/{story_identity_id}`：返回完全固定到所选 revision 的可读航迹、相对最后阅读位置的具名更新、实质历史与聚合审计。
- `/api/domains/{domain_id}/stories/{story_identity_id}/edges/{edge_id}/support`：按选中关系懒加载 record revision 粒度的支持记录，并校验域、身份与快照边界。
- `/api/domains/{domain_id}/evidence/{revision_id}`：证据上下文与精确播放/字符区间语义。
- `/api/domains/{domain_id}/source-records/{video_id}/semantic-references`：来源记录反查语义对象与简报。

完整参数见 [REST API](../api/rest.md)，数据表细节见 [数据模型](data-model.md)。
