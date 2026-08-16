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

`event_map_canonical.has_uncertainty` 是审核位的物化布尔列。场景流和待审核列表直接读取该列，不再对 `uncertainty_flags` 做逐行 JSONB 数组判断。时间窗、类型、实体和主题筛选分别由类型化列、`event_map_entity_index` 与 `event_map_topic_member` 支撑；主题成员下钻使用 `(snapshot_id, topic_id, level)` 复合索引。

当前服务会在首次请求某个不可变快照时从类型化列流式编码场景。只有当实测表明冷启动编码而非网络或 GPU 成为瓶颈时，才进一步把完整二进制场景固化为对象存储资产；在没有观测证据前不维护第二份同义场景事实。

## 连续观察与长期身份

- `domain_observation_cursor`：保存每个观测域的快照、认知时间、事件时间窗、模式、筛选、镜头和选择对象。
- `event_map_change`：保存 canonical、story 与布局变化；以 `(observed_at, id)` 提供稳定分页游标。
- `event_map_canonical_history_revision`：保存 canonical 的长期修订正文，不外键依赖可能被裁剪的大快照。
- `event_map_canonical_history_member`：正规化长期修订到来源修订的关系，为证据和来源记录反查提供 `(playlist_id, record_revision_id, canonical_id)` 索引。
- `event_map_story_identity` / `event_map_story_history_revision`：保存稳定故事身份及跨快照版本。
- `event_map_story_history_evidence`：正规化故事边到来源修订的关系，支持来源记录反查具体故事关系。
- `story_read_state`：保存关注状态、最后阅读快照与故事位置。
- `brief.snapshot_id` / `brief.generation_basis` / `brief_reference`：固定简报的生成口径和可导航引用。

变化记录保存 before/after 修订。显式、带证据的 `corrects` 故事边会形成独立 correction 变化；普通文本修订不推断为纠正。retire 对象的链接固定到最后包含它的历史快照，观察流因此仍能打开对象并展示本次差异。

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

- `/api/domains/{domain_id}/observation`：当前观察状态与四类覆盖率。
- `/api/domains/{domain_id}/changes`：稳定变化游标。
- `/api/domains/{domain_id}/observation/feed`：新发生、新入图、故事更新和待审核。
- `/api/domains/{domain_id}/canonicals`：当前快照的类型化线性事件视图。
- `/api/domains/{domain_id}/canonicals/{canonical_id}/history`：canonical 修订与谱系。
- `/api/domains/{domain_id}/topics/{topic_id}`：当前或显式历史快照的主题层级、成员和代表事件。
- `/api/domains/{domain_id}/stories/{story_identity_id}`：故事历史和所选快照的完整航迹。
- `/api/domains/{domain_id}/evidence/{revision_id}`：证据上下文与精确播放/字符区间语义。
- `/api/domains/{domain_id}/source-records/{video_id}/semantic-references`：来源记录反查语义对象与简报。

完整参数见 [REST API](../api/rest.md)，数据表细节见 [数据模型](data-model.md)。
