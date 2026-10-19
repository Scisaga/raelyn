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

普通 current 入口首次读取 compact manifest 时不指定快照版本，域目录和内存状态中的旧快照 ID 不参与版本选择；这些缓存可能早于快照回收。取得 ready 响应后，预览、完整 scene、主题元数据和覆盖统计均固定到该响应的 `snapshot_id`。显式历史导航保持指定版本，“返回当前星域”明确解除本次请求的版本固定。manifest 请求失败在空画布与状态徽标显示读取错误；只有成功读取的状态才用于判断尚未生成或正在构建，避免把读取失败误显示成首次生成状态。

`event_map_canonical.has_uncertainty` 是审核位的物化布尔列。场景流和待审核列表直接读取该列，不再对 `uncertainty_flags` 做逐行 JSONB 数组判断。时间窗、类型、实体和主题筛选分别由类型化列、`event_map_entity_index` 与 `event_map_topic_member` 支撑；主题成员下钻使用 `(snapshot_id, topic_id, level)` 复合索引。

当前服务会在首次请求某个不可变快照时从类型化列流式编码场景。只有当实测表明冷启动编码而非网络或 GPU 成为瓶颈时，才进一步把完整二进制场景固化为对象存储资产；在没有观测证据前不维护第二份同义场景事实。

## 连续观察与长期身份

只保存当前业务结果与稳定身份。每个域保留 current 加上一份前一 ready 投影，用于原子发布、布局继承和浏览器尚未完成的请求；失败投影按现有保留任务回收，不做永久全量归档。

- `domain_observation_cursor` 保存观察时间、窗口、镜头和所选对象。
- canonical 身份记录最后业务更新时间；故事身份冻结稳定标题，并仅在事件、关系依据、支持记录或成熟度实质变化时更新 `last_material_changed_at`。比较对象是当前与前一正常投影，比较结果不再写成 before/after 历史。
- `story_read_state` 保存关注、最后显式阅读时间与位置。打开故事不会清除未读。
- 简报的 `snapshot_id` 只记录生成来源。`brief_reference.context.quoted_object` 冻结实际引用摘要和故事关系，原始证据仍由 `evidence_revision_id` 指向；默认打开最新对象，对象不再存在时仍可读取引用摘要和播放原证据。

`event_map_change`、canonical/story history revision 和两个历史关系索引表已经移除。启动时不回填场景历史；每次构建也不再重复归档所有对象。来源反查直接使用 current canonical member 与 story edge，简报反查仍读取真实引用行。

## JSONB 的保留边界

适合 JSONB 的内容：

- 简报实际引用的摘要、关系依据与原始证据指针；
- 原始抽取载荷、证据 provenance、生成口径；
- 相机、筛选等低频用户状态；
- 算法版本可能扩展的快照统计。

不得依赖 JSONB 扫描的热查询：

- 星域坐标、时间窗、类型与审核筛选；
- canonical、topic、story 成员连接；
- 来源修订到 canonical 的反向引用；
- 当前更新列表、故事目录和简报对象引用。

`market_event_embedding.vector` 目前仍是快照构建输入中的 JSONB 向量，它影响构建吞吐而不影响星域显示。若构建性能观测确认向量反序列化成为主瓶颈，应采用兼容的 float32 二进制旁路或 pgvector 迁移，并保留校验和与双读迁移期；不得以重抽事件、删除旧向量或暂停现有任务作为迁移手段。

## 上线与历史兼容

- 自动启动迁移只维护当前表结构和缺失元数据，不回填全量场景历史。
- 旧快照的 `has_uncertainty` 由既有标记原位补齐，不重建快照。
- 旧全量归档通过显式维护命令迁移：备份实际引用，冻结引用摘要，核对证据完整性后删除历史表。
- 自动 schema 迁移不会取消、暂停或删除既有任务，也不会删除历史视频、资产或来源记录。
- V2 发布不修改当前 job 的 claim、lease、priority、worker role 或执行 token。
- 快照发布在同一事务内完成当前身份元数据后切换 current 指针；读者不会看到半成品。
- Web 显式指定仍保留的 `snapshot_id` 时，manifest、主题、故事航迹和主题简报反查使用该投影；指定的投影不可用就返回错误。普通故事入口和简报引用读取当前对象，不固定旧投影；故事引用另以 canonical ID 定位引用阶段。

## 读取接口

- `/api/domains/{domain_id}/observation`：当前观察状态与四项独立处理/产出覆盖；当前口径抽取按 `prompt_version + extraction_model` 的成功运行计数。
- `PUT /api/domains/{domain_id}/observation`：启用或停用持续观测；停用域保留来源归档，视频级共享分析以“至少一个关联域启用”为准，域级简报和星域构建仅面向启用域。
- `/api/domains/{domain_id}/observation/feed`：当前内容的发生与更新时间提示。
- `/api/domains/{domain_id}/observation/feed`：新发生、认知变化、故事更新和待验证；协议字段仍为 `newly_occurred / newly_mapped / story_updates / needs_review`。
- `/api/domains/{domain_id}/canonicals`：当前快照的类型化线性事件视图。
- `/api/domains/{domain_id}/event-highlights`：`scope=24h` 按服务端当前时刻的 `[now-24h, now]` 平台发布时间选择当前观测域的视频，再关联固定快照中的 canonical；高亮与视频卡使用同一发布时间范围，未知或未来发布时间不入选，不按事件发生日及其精度排除。默认 `scope=event_date` 保留冻结事件发生日筛选，供“本周事件”使用，且只接受 `second/day` 精度。两种口径均叠加观察窗口、类型、实体和主题条件。响应返回全部匹配事件的点索引用于星图高亮，再按多信源佐证与时间可靠性做可审计排序，为其中最多十个有可播放来源的唯一 canonical 返回空间视频卡，同一媒体最多占两张；达到媒体上限的事件只是不生成卡片，其真实点仍保持高亮。该顺序不称为尚无冻结字段支撑的市场影响重要度。
- `/api/domains/{domain_id}/canonicals/{canonical_id}`：当前 canonical 详情与定位。
- `/api/domains/{domain_id}/topics/{topic_id}`：当前或显式历史快照的主题层级、成员和代表事件。
- `/api/domains/{domain_id}/stories`：current ready 快照的稳定故事阅读队列；支持 `attention/followed/established/emerging/all`、搜索和分页，按最近实质变化稳定排序。首次没有基线时只推荐最近成熟故事，不制造全库未读。
- `/api/domains/{domain_id}/stories/{story_identity_id}`：返回当前可读航迹、最近实质更新时间、关注和最后显式阅读状态。
- `/api/domains/{domain_id}/stories/{story_identity_id}/edges/{edge_id}/support`：按选中关系懒加载 record revision 粒度的支持记录，并校验域、身份与快照边界。
- `/api/domains/{domain_id}/evidence/{revision_id}`：证据上下文与精确播放/字符区间语义。
- `/api/domains/{domain_id}/source-records/{video_id}/semantic-references`：来源记录反查语义对象与简报。

完整参数见 [REST API](../api/rest.md)，数据表细节见 [数据模型](data-model.md)。
