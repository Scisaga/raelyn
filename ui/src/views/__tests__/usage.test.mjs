import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createAppInitMethods } from "../../app/init-model.js";
import { createUsageModule } from "../../app/modules/usage.js";
import { createNavItems } from "../../app/navigation.js";
import { createUrlStateMethods } from "../../services/url-state.js";
import { createUsageViewMethods } from "../usage-model.js";

function usagePayload() {
  return {
    generated_at: "2026-09-03T08:30:00Z",
    timezone: "Asia/Shanghai",
    window: { days: 90, start: "2026-06-06", end: "2026-09-03" },
    summary: {
      video_count: 125,
      downloaded_video_count: 118,
      video_downloaded: 7,
      external_calls: null,
      llm_calls: null,
      asr_calls: null,
      embedding_calls: null,
      llm_input_tokens: null,
      llm_output_tokens: null,
      llm_total_tokens: null,
      asset_count: 18,
      asset_size_bytes: 2048,
      asset_missing_size_count: 2,
      database_size_bytes: null,
    },
    series: [
      {
        date: "2026-09-01",
        video_downloaded: 1,
        external_calls: null,
        llm_calls: null,
        asr_calls: null,
        embedding_calls: null,
        llm_input_tokens: null,
        llm_output_tokens: null,
        llm_total_tokens: null,
        asset_size_bytes: null,
        database_size_bytes: null,
        usage_sampled: false,
        resource_sampled: false,
      },
      {
        date: "2026-09-02",
        video_downloaded: 2,
        external_calls: 0,
        llm_calls: 0,
        asr_calls: 0,
        embedding_calls: 0,
        llm_input_tokens: 0,
        llm_output_tokens: 0,
        llm_total_tokens: 0,
        asset_size_bytes: 0,
        database_size_bytes: null,
        usage_sampled: true,
        resource_sampled: true,
      },
    ],
    service_breakdown: [
      {
        service: "llm",
        operation: "summarize",
        provider: "openai",
        model: "gpt-test",
        calls: 2,
        successes: 1,
        failures: 1,
        input_tokens: 120,
        output_tokens: 30,
        total_tokens: 150,
        duration_ms: 2400,
        usage_missing_calls: 1,
        last_called_at: "2026-09-02T10:00:00Z",
      },
    ],
    asset_breakdown: [
      { asset_type: "video", count: 4, size_bytes: 2048, missing_size_count: 2 },
    ],
    collection: {
      usage_started_at: null,
      last_usage_at: null,
      resource_started_at: "2026-09-02",
      last_resource_snapshot_at: "2026-09-02T23:00:00Z",
      physical_storage_available: false,
      physical_storage_reason: "未接入部署侧指标",
    },
  };
}

function usageContext() {
  return {
    ...createUsageModule(),
    ...createUsageViewMethods(),
    activeView: "usage",
    globalStatus: "",
    formatInteger(value) { return String(value); },
    formatCompactInteger(value) { return String(value); },
    formatBytes(value) { return `${value} B`; },
  };
}

test("系统导航把资源用量放在运行中心与智能体接入之间，并支持 /usage 直达", () => {
  const navItems = createNavItems();
  assert.deepEqual(
    navItems.filter((item) => item.system && !item.hidden).map((item) => item.label),
    ["运行中心", "资源用量", "智能体接入", "设置"],
  );

  globalThis.window = { location: { pathname: "/usage", search: "" } };
  const ctx = { ...createUrlStateMethods({ settingsTabs: [] }), navItems };
  assert.equal(ctx._parseViewFromLocation(), "usage");
  assert.equal(ctx._viewPath("usage"), "/usage");
});

test("直接进入资源用量会加载接口，浏览器后退离开时会执行清理", async () => {
  let loadCount = 0;
  const route = {
    ...createUrlStateMethods({ settingsTabs: [] }),
    activeView: "usage",
    _disconnectJobsWs() {},
    _destroyJobsDoneChart() {},
    async loadUsage() { loadCount += 1; },
  };
  await route.refreshActive();
  assert.equal(loadCount, 1);

  const listeners = {};
  globalThis.window = {
    location: { pathname: "/settings", search: "" },
    addEventListener(type, listener) { listeners[type] = listener; },
  };
  let leaveCount = 0;
  let refreshCount = 0;
  const shell = {
    ...createAppInitMethods(),
    activeView: "usage",
    navItems: createNavItems(),
    _applySidebarMode() {},
    playlistCalendarUpdateCount() {},
    _parseViewFromLocation() { return "settings"; },
    leaveUsagePage() { leaveCount += 1; },
    _applyQueryFromLocation() {},
    refreshActive() { refreshCount += 1; },
  };
  shell._initShellListeners();
  listeners.popstate();
  assert.equal(leaveCount, 1);
  assert.equal(shell.activeView, "settings");
  assert.equal(refreshCount, 1);
});

test("资源用量启动只加载专用接口，不再后台扫描旧概览统计", async () => {
  let refreshCount = 0;
  let statsCount = 0;
  const shell = {
    ...createAppInitMethods(),
    activeView: "usage",
    async refreshActive() { refreshCount += 1; },
    async loadStats() { statsCount += 1; },
  };

  await shell._refreshProtectedData({ statsReady: false });

  assert.equal(refreshCount, 1);
  assert.equal(statsCount, 0);
});

test("资源用量 payload 按契约映射，并保留可空统计", () => {
  const ctx = usageContext();
  ctx.applyUsagePayload(usagePayload());

  assert.equal(ctx.usageSummary.downloadedVideoCount, 118);
  assert.equal(ctx.usageSummary.videoDownloaded, 7);
  assert.equal(ctx.usageSummary.externalCalls, null);
  assert.equal(ctx.usageSummary.asrCalls, null);
  assert.equal(ctx.usageSummary.embeddingCalls, null);
  assert.equal(ctx.usageSummary.llmTotalTokens, null);
  assert.equal(ctx.usageSummary.databaseSizeBytes, null);
  assert.deepEqual(ctx.usageServiceBreakdown[0], {
    service: "llm",
    operation: "summarize",
    provider: "openai",
    model: "gpt-test",
    calls: 2,
    successes: 1,
    failures: 1,
    inputTokens: 120,
    outputTokens: 30,
    totalTokens: 150,
    durationMs: 2400,
    usageMissingCalls: 1,
    lastCalledAt: "2026-09-02T10:00:00Z",
  });
  assert.deepEqual(ctx.usageAssetBreakdown[0], {
    assetType: "video",
    count: 4,
    sizeBytes: 2048,
    missingSizeCount: 2,
  });
  assert.equal(ctx.usageCollection.physicalStorageAvailable, false);
  assert.equal(ctx.usageDateLabel("2026-09-02"), "2026/09/02");
  assert.match(ctx.usageGeneratedLabel(), /^更新于 /);
  assert.equal(ctx.usageCollectionModeLabel(), "实时采集中");
  assert.equal(ctx.usageOperationLabel("legacy.event_extraction"), "历史事件抽取");
  assert.equal(ctx.usageProviderLabel({ operation: "legacy.event_extraction" }), "历史任务记录");
  assert.equal(ctx.usageDurationLabel(0, "legacy.event_extraction"), "历史未记录");
  assert.equal(ctx.usageAssetLabel("video"), "视频");
  assert.equal(ctx.usageSuccessRateLabel(ctx.usageServiceBreakdown[0]), "50.0%");

  assert.equal(ctx.usageAssetShare(ctx.usageAssetBreakdown[0]), 100);
});

test("趋势序列把缺失采样保留为空白，同时保留已采样的零值", () => {
  const ctx = usageContext();
  ctx.applyUsagePayload(usagePayload());

  assert.deepEqual(ctx._usageMetricData("externalCalls", "usageSampled"), [
    { time: "2026-09-01" },
    { time: "2026-09-02", value: 0 },
  ]);
  assert.deepEqual(ctx._usageMetricData("assetSizeBytes", "resourceSampled"), [
    { time: "2026-09-01" },
    { time: "2026-09-02", value: 0 },
  ]);
  assert.equal(ctx.usageCountLabel(ctx.usageSummary.externalCalls), "暂无采样");
  assert.equal(ctx.usageBytesLabel(ctx.usageSummary.databaseSizeBytes), "暂无采样");
  assert.equal(ctx.usageDatabaseSizeLabel(), "不可用");
});

test("外部调用长窗口使用移动均值，视频下载量保留每日实际值", () => {
  const ctx = usageContext();
  ctx.usageDays = 30;
  ctx.usageSeries = [
    { date: "2026-09-01", llmCalls: 0, videoDownloaded: 1, usageSampled: true },
    { date: "2026-09-02", llmCalls: 3, videoDownloaded: 8, usageSampled: true },
    { date: "2026-09-03", llmCalls: 6, videoDownloaded: 2, usageSampled: true },
    { date: "2026-09-04", llmCalls: null, videoDownloaded: 5, usageSampled: false },
    { date: "2026-09-05", llmCalls: 9, videoDownloaded: 3, usageSampled: true },
  ];

  assert.equal(ctx.usageCallTrendWindow(), 3);
  assert.equal(ctx.usageCallTrendGranularityLabel(), "3 日移动均值");
  assert.deepEqual(ctx._usageCallTrendData("llmCalls", "usageSampled"), [
    { time: "2026-09-01", value: 0 },
    { time: "2026-09-02", value: 1.5 },
    { time: "2026-09-03", value: 3 },
    { time: "2026-09-04" },
    { time: "2026-09-05", value: 9 },
  ]);
  assert.deepEqual(ctx._usageMetricData("videoDownloaded"), [
    { time: "2026-09-01", value: 1 },
    { time: "2026-09-02", value: 8 },
    { time: "2026-09-03", value: 2 },
    { time: "2026-09-04", value: 5 },
    { time: "2026-09-05", value: 3 },
  ]);

  ctx.usageDays = 90;
  assert.equal(ctx.usageCallTrendWindow(), 7);
  assert.equal(ctx.usageCallTrendGranularityLabel(), "7 日移动均值");
  ctx.usageDays = 7;
  assert.equal(ctx.usageCallTrendWindow(), 1);
  assert.equal(ctx.usageCallTrendGranularityLabel(), "每日原值");
});

test("7/30/90 天切换调用 usage API，离开页面会丢弃在途结果", async () => {
  const ctx = usageContext();
  assert.equal(ctx.usageDays, 90);
  const calls = [];
  let updateCount = 0;
  ctx.api = async (path) => {
    calls.push(path);
    return usagePayload();
  };
  ctx._updateUsageCharts = () => { updateCount += 1; };
  ctx.globalStatus = "error: 404: Not Found";

  await ctx.loadUsage({ days: 90 });
  assert.deepEqual(calls, ["/usage?days=90"]);
  assert.equal(ctx.usageDays, 90);
  assert.equal(updateCount, 1);
  assert.equal(ctx.globalStatus, "");
  assert.equal(await ctx.loadUsage({ days: 14 }), null);
  assert.equal(calls.length, 1);

  let resolveRequest;
  ctx.usagePayload = null;
  ctx.api = () => new Promise((resolve) => { resolveRequest = resolve; });
  const pending = ctx.loadUsage({ days: 7 });
  ctx.leaveUsagePage();
  resolveRequest(usagePayload());
  assert.equal(await pending, null);
  assert.equal(ctx.usagePayload, null);
  assert.equal(ctx.usageLoading, false);
});

test("四类趋势复用 lightweight-charts，并在离开时释放实例", () => {
  const ctx = usageContext();
  ctx.applyUsagePayload(usagePayload());
  ctx.$refs = Object.fromEntries(
    ["usageVideoChart", "usageCallsChart", "usageTokensChart", "usageStorageChart"]
      .map((name) => [name, { clientWidth: 480, clientHeight: 192 }]),
  );

  const charts = [];
  globalThis.window = {
    LightweightCharts: {
      ColorType: { Solid: "solid" },
      CrosshairMode: { Normal: "normal" },
      AreaSeries: "area",
      LineSeries: "line",
      createChart() {
        const chart = {
          removed: false,
          fitCount: 0,
          seriesTypes: [],
          addSeries(type) {
            chart.seriesTypes.push(type);
            return { data: [], setData(data) { this.data = data; } };
          },
          timeScale() {
            return { fitContent: () => { chart.fitCount += 1; } };
          },
          remove() { chart.removed = true; },
        };
        charts.push(chart);
        return chart;
      },
    },
  };

  ctx._updateUsageCharts();
  assert.equal(charts.length, 4);
  assert.equal(ctx.usageCharts.calls.series.externalCalls, undefined);
  assert.deepEqual(ctx.usageCharts.calls.series.asrCalls.data, [
    { time: "2026-09-01" },
    { time: "2026-09-02", value: 0 },
  ]);
  assert.deepEqual(ctx.usageCharts.videos.series.videoDownloaded.data, [
    { time: "2026-09-01", value: 1 },
    { time: "2026-09-02", value: 2 },
  ]);
  assert.deepEqual(charts[0].seriesTypes, ["area"]);
  assert.deepEqual(charts[1].seriesTypes, ["line", "line", "line"]);
  assert.deepEqual(charts.map((chart) => chart.fitCount), [1, 1, 1, 1]);

  ctx.leaveUsagePage();
  assert.deepEqual(charts.map((chart) => chart.removed), [true, true, true, true]);
  assert.deepEqual(ctx.usageCharts, {});
});

test("资源观测站使用等权指标、紧凑单屏网格和本地 SVG 图标", async () => {
  const template = await readFile(new URL("../../../templates/app/views/usage.html", import.meta.url), "utf8");
  assert.match(template, /资源观测站/);
  assert.match(template, /全局遥测/);
  assert.match(template, /\[7,30,90\]/);
  assert.match(template, /ASR/);
  assert.equal((template.match(/x-ref="usage(?:Video|Calls|Tokens|Storage)Chart"/g) || []).length, 4);
  assert.equal((template.match(/raelyn-usage-hero/g) || []).length, 0);
  assert.equal((template.match(/raelyn-usage-metric/g) || []).length, 6);
  assert.match(template, /xl:grid-rows-\[82px_54px_minmax\(0,1fr\)_240px\]/);
  assert.match(template, /Token 趋势/);
  assert.match(template, /外部服务节奏/);
  assert.match(template, /视频下载量/);
  assert.match(template, /每天成功完成的下载任务数/);
  assert.match(template, /usageCallTrendGranularityLabel/);
  assert.doesNotMatch(template, /视频摄入节奏|新增视频移动均值/);
  assert.doesNotMatch(template, /全部服务与服务分类/);
  assert.match(template, /采集链路状态/);
  assert.match(template, /服务调用画像/);
  assert.match(template, /资产构成/);
  assert.match(template, /大小未知资产/);
  assert.match(template, /未记录文件大小/);
  assert.doesNotMatch(template, /元数据缺口|缺少大小的资产/);
  assert.match(template, /主机物理磁盘/);
  assert.match(template, /未接入/);
  assert.equal((template.match(/usageDateLabel\(usageCollection\.(?:usageStartedAt|resourceStartedAt)\)/g) || []).length, 2);
  assert.ok((template.match(/<svg aria-hidden="true"/g) || []).length >= 8);
  assert.doesNotMatch(template, /material-symbols|>monitoring<|>query_stats<|>video_library</);
});
