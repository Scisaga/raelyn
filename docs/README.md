# 文档导航

## 产品与范围

- [V2 产品愿景](vision.md)
- [V2 信息架构](product/information-architecture-v2.md)
- [V2 现状能力差距](product/capability-gap-v2.md)

## 协作约定

- [通用协作与编码细则](agent-rules/general.md)
- [skills 说明](../skills/README.md)

## 架构

- [架构总览](architecture/overview.md)
- [V2 观察与存储架构](architecture/v2-observation.md)
- [ADR-0001：B 站资料同步使用浏览器模拟读取公开空间页](adr/0001-bilibili-profile-fetch-browser-page.md)
- [ADR-0002：媒体资料同步补齐可缓存头像来源](adr/0002-media-profile-avatar-sources.md)
- [任务系统](architecture/job-system.md)
- 任务系统的通用设计原则由项目内 skill [../skills/job-system-design/SKILL.md](../skills/job-system-design/SKILL.md) 维护；架构文档只保留 `raelyn` 的项目化说明。
- [数据模型与存储布局](architecture/data-model.md)
- [事件语义星域](architecture/event-graph-analysis.md)
- [后端模块](architecture/backend-modules.md)
- [MCP 集成设计](architecture/mcp.md)
- [风险与处理](architecture/risks.md)

## 接口与界面

- [REST API 设计](api/rest.md)
- [UI 设计总览](ui/overview.md)
- [UI 主题配色](ui/theme.md)
- [V2 核心界面线框规范](ui/wireframes-v2.md)

## 交付与运行

- [MVP 路线图与当前状态](roadmap/mvp.md)
- [运行与部署](reference/run-and-deploy.md)
- [配置项说明](reference/configuration.md)
- [按领域改造](reference/domain-customization.md)
- [LLM 有限输出预算实验](reference/llm-output-budget-experiment.md)
- [YouTube yt-dlp 同步与 Cookies 策略](reference/youtube-ytdlp-strategy.md)
- [yt-dlp 视频 / 音频格式选择策略](reference/ytdlp-format-selection.md)
- [产品导览录制流水线](../scripts/record/README.md)
